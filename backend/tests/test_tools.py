from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from app.tenancy import context as tenancy_context
from app.tools import registry as registry_module
from app.tools.registry import registry
from app.tools.search_x_posts import (
    COMPOSIO_ACTION_SCOPE,
    COMPOSIO_ACTION_SLUG,
    SearchXPostsArgs,
)
from pydantic import BaseModel, ValidationError

registry_module._import_all_tools()


@pytest.fixture(autouse=True)
def _tenancy_context() -> None:
    # publish_post/schedule_post/delete_post all resolve rate limits and
    # stamp ownership via the current-user tenancy context (see
    # plans/peaceful-scribbling-tiger.md Stage 3).
    token = tenancy_context.set_current_user_id("tools-test-user")
    yield
    tenancy_context.reset_current_user_id(token)

ALL_TOOL_NAMES = {
    "search_knowledge_base",
    "get_linkedin_notifications",
    "draft_post",
    "generate_analytics_report",
    "search_x_posts",
    "save_research_note",
    "like_post",
    "publish_post",
    "schedule_post",
    "delete_post",
    "reply_to_comment",
    "reply_to_dm",
    "send_connection_request",
    "search_hackernews",
    "search_reddit",
    "search_web",
    "search_github",
    "search_producthunt",
    "search_rss",
}

APPROVAL_REQUIRED_TOOL_NAMES = {
    "publish_post",
    "schedule_post",
    "delete_post",
    "reply_to_comment",
    "reply_to_dm",
    "send_connection_request",
}

NO_APPROVAL_TOOL_NAMES = ALL_TOOL_NAMES - APPROVAL_REQUIRED_TOOL_NAMES

FORBIDDEN_X_SLUG_TOKENS = (
    "CREATE",
    "POST",
    "REPLY",
    "DM",
    "MESSAGE",
    "LIKE",
    "RETWEET",
    "FOLLOW",
    "DELETE",
    "SEND",
    "PUBLISH",
)


def test_all_19_tools_registered() -> None:
    registered = set(registry.all().keys())
    assert registered == ALL_TOOL_NAMES
    assert len(registered) == 19


def test_every_tool_has_a_pydantic_schema() -> None:
    for name, entry in registry.all().items():
        assert isinstance(entry.schema, type), name
        assert issubclass(entry.schema, BaseModel), f"{name} schema is not a Pydantic BaseModel"


@pytest.mark.parametrize("name", sorted(APPROVAL_REQUIRED_TOOL_NAMES))
def test_approval_required_tools_are_flagged_true(name: str) -> None:
    entry = registry.get(name)
    assert entry is not None, f"{name} not registered"
    assert entry.definition.requires_approval is True
    assert isinstance(entry.definition.requires_approval, bool)


@pytest.mark.parametrize("name", sorted(NO_APPROVAL_TOOL_NAMES))
def test_non_approval_tools_are_flagged_false(name: str) -> None:
    entry = registry.get(name)
    assert entry is not None, f"{name} not registered"
    assert entry.definition.requires_approval is False
    assert isinstance(entry.definition.requires_approval, bool)


def test_exactly_six_tools_require_approval() -> None:
    approval_names = {
        name for name, entry in registry.all().items() if entry.definition.requires_approval is True
    }
    assert approval_names == APPROVAL_REQUIRED_TOOL_NAMES
    assert len(approval_names) == 6


def test_no_approval_flag_has_a_bypass_parameter() -> None:
    for name in APPROVAL_REQUIRED_TOOL_NAMES:
        entry = registry.get(name)
        assert entry is not None
        field_names = {f.lower() for f in entry.schema.model_fields}
        for suspicious in ("skip_approval", "override", "bypass", "force", "auto_approve"):
            assert suspicious not in field_names, f"{name} exposes a bypass field: {suspicious}"


class TestDeletePostSchema:
    def _valid_kwargs(self) -> dict:
        return {
            "post_id": "post-123",
            "post_content": "Original post text",
            "published_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "engagement_stats": {"likes": 10, "comments": 2},
        }

    def test_valid_payload_accepted(self) -> None:
        entry = registry.get("delete_post")
        assert entry is not None
        instance = entry.schema(**self._valid_kwargs())
        assert instance.post_content == "Original post text"

    def test_rejects_missing_post_content(self) -> None:
        entry = registry.get("delete_post")
        kwargs = self._valid_kwargs()
        del kwargs["post_content"]
        with pytest.raises(ValidationError):
            entry.schema(**kwargs)

    def test_rejects_missing_published_at(self) -> None:
        entry = registry.get("delete_post")
        kwargs = self._valid_kwargs()
        del kwargs["published_at"]
        with pytest.raises(ValidationError):
            entry.schema(**kwargs)

    def test_rejects_missing_engagement_stats(self) -> None:
        entry = registry.get("delete_post")
        kwargs = self._valid_kwargs()
        del kwargs["engagement_stats"]
        with pytest.raises(ValidationError):
            entry.schema(**kwargs)

    def test_rejects_post_id_only_lookup(self) -> None:
        entry = registry.get("delete_post")
        with pytest.raises(ValidationError):
            entry.schema(post_id="post-123")

    def test_rejects_batch_list_of_post_ids(self) -> None:
        entry = registry.get("delete_post")
        with pytest.raises((ValidationError, TypeError)):
            entry.schema(post_ids=["post-1", "post-2", "post-3"])

    def test_schema_has_no_list_or_batch_field(self) -> None:
        entry = registry.get("delete_post")
        for field_name in entry.schema.model_fields:
            assert "ids" not in field_name.lower(), "delete_post schema must accept exactly one post"
            assert "batch" not in field_name.lower()
            assert "posts" != field_name.lower()

    def test_fields_are_required_not_optional(self) -> None:
        entry = registry.get("delete_post")
        for field_name in ("post_content", "published_at", "engagement_stats"):
            field_info = entry.schema.model_fields[field_name]
            assert field_info.is_required(), f"{field_name} must be required, not optional"

    def test_registry_validate_all_schemas_checks_delete_post(self) -> None:
        errors = registry.validate_all_schemas()
        delete_post_errors = [e for e in errors if e.startswith("delete_post")]
        assert delete_post_errors == []

    def test_delete_post_sends_share_id_to_composio_not_post_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regression coverage: LINKEDIN_DELETE_LINKED_IN_POST's real required field is

        `share_id`, verified live against Composio's schema — our own `post_id` field
        (kept for the approval-payload safety reasons in delete_post.py's docstring) must be
        forwarded under that name, not sent through as `post_id` (which Composio doesn't accept).
        """
        import asyncio

        # execute() checks the daily rate cap before ever reaching Composio — must be set
        # explicitly here rather than relying on a real .env being present (it isn't in CI,
        # or in any hermetic test environment; this test previously passed only by accident,
        # borrowing the developer's own local .env).
        monkeypatch.setenv("LINKEDIN_API_RATE_LIMIT_DELETES_DAILY", "3")

        from app.tools import delete_post as delete_post_module

        captured = {}

        async def fake_execute_linkedin_action(action_slug: str, arguments: dict) -> dict:
            captured["action_slug"] = action_slug
            captured["arguments"] = arguments
            return {"successful": True}

        monkeypatch.setattr(delete_post_module, "execute_linkedin_action", fake_execute_linkedin_action)

        args = delete_post_module.DeletePostArgs(
            post_id="urn:li:share:123",
            post_content="a post",
            published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            engagement_stats={"likes": 1},
        )
        asyncio.run(delete_post_module.execute(args))

        assert captured["arguments"] == {"share_id": "urn:li:share:123"}


class TestPublishAndSchedulePostSchemas:
    """Both tools take the post's actual content, not an internal post_id —

    Composio's LinkedIn create/schedule actions create a new post from text,
    they don't look one up by an ID this codebase invented (regression
    coverage for the bug where content_writer.py built {post_content, topic,
    target_publish_date} arguments that neither tool's schema ever accepted,
    so every real approve-and-post attempt failed with a validation error).
    """

    def test_publish_post_accepts_content(self) -> None:
        entry = registry.get("publish_post")
        instance = entry.schema(content="Hello, LinkedIn!")
        assert instance.content == "Hello, LinkedIn!"

    def test_publish_post_rejects_missing_content(self) -> None:
        entry = registry.get("publish_post")
        with pytest.raises(ValidationError):
            entry.schema()

    def test_publish_post_rejects_empty_content(self) -> None:
        entry = registry.get("publish_post")
        with pytest.raises(ValidationError):
            entry.schema(content="")

    def test_schedule_post_accepts_content_and_future_publish_at(self) -> None:
        entry = registry.get("schedule_post")
        future = datetime.now(timezone.utc) + timedelta(days=1)
        instance = entry.schema(content="Hello, LinkedIn!", publish_at=future)
        assert instance.content == "Hello, LinkedIn!"

    def test_schedule_post_rejects_missing_content(self) -> None:
        entry = registry.get("schedule_post")
        with pytest.raises(ValidationError):
            entry.schema(publish_at=datetime.now(timezone.utc) + timedelta(days=1))

    async def test_schedule_post_persists_approved_future_post(self, db_session) -> None:
        outcome = await registry_module.execute_tool(
            "schedule_post",
            {"content": "hello", "publish_at": datetime.now(timezone.utc) + timedelta(days=1)},
            approved=True,
        )
        assert outcome["status"] == "success"
        assert outcome["result"]["status"] == "scheduled"



class TestSearchXPostsReadOnly:
    def test_action_scope_is_read(self) -> None:
        assert COMPOSIO_ACTION_SCOPE == "read"

    def test_action_slug_has_no_write_verbs(self) -> None:
        upper_slug = COMPOSIO_ACTION_SLUG.upper()
        for token in FORBIDDEN_X_SLUG_TOKENS:
            assert token not in upper_slug, f"search_x_posts action slug looks write-scoped: {token} in {upper_slug}"
        assert "SEARCH" in upper_slug or "READ" in upper_slug or "GET" in upper_slug

    def test_schema_has_no_write_capable_fields(self) -> None:
        field_names = {f.lower() for f in SearchXPostsArgs.model_fields}
        forbidden_field_tokens = (
            "text",
            "message",
            "reply",
            "dm",
            "post_content",
            "tweet_text",
            "recipient",
            "target_user",
        )
        for field_name in field_names:
            for token in forbidden_field_tokens:
                assert token not in field_name, f"search_x_posts schema has a write-shaped field: {field_name}"

    def test_search_x_posts_does_not_require_approval(self) -> None:
        entry = registry.get("search_x_posts")
        assert entry is not None
        assert entry.definition.requires_approval is False

    def test_no_x_tool_other_than_search_exists(self) -> None:
        # Registry-level guarantee that the Research Agent has no write-capable X tool at all.
        x_related = [
            name
            for name, entry in registry.all().items()
            if "x_post" in name or name.startswith("search_x") or "tweet" in name.lower()
        ]
        assert x_related == ["search_x_posts"]


def test_validate_all_schemas_cli_exits_zero() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "app.tools.registry", "--validate-all-schemas"],
        cwd=repo_root / "backend",
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "OK" in result.stdout


def test_registry_blocks_unapproved_execution() -> None:
    import asyncio

    async def _run() -> dict:
        return await registry_module.execute_tool(
            "publish_post", {"content": "hello world"}, approved=False
        )

    outcome = asyncio.run(_run())
    assert outcome["status"] == "blocked"


def test_schedule_post_rejects_past_publish_at() -> None:
    entry = registry.get("schedule_post")
    with pytest.raises(ValidationError):
        entry.schema(content="hello world", publish_at=datetime.now(timezone.utc) - timedelta(days=1))
