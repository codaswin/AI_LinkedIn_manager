from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from app.tools import registry as registry_module
from app.tools.registry import registry
from app.tools.search_x_posts import (
    COMPOSIO_ACTION_SCOPE,
    COMPOSIO_ACTION_SLUG,
    SearchXPostsArgs,
)
from pydantic import BaseModel, ValidationError

registry_module._import_all_tools()

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
            "publish_post", {"post_id": "abc"}, approved=False
        )

    outcome = asyncio.run(_run())
    assert outcome["status"] == "blocked"


def test_schedule_post_rejects_past_publish_at() -> None:
    entry = registry.get("schedule_post")
    with pytest.raises(ValidationError):
        entry.schema(post_id="p1", publish_at=datetime.now(timezone.utc) - timedelta(days=1))
