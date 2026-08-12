from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.memory import episodic, policy, working
from app.memory.semantic import MissingProvenanceError
from app.memory.semantic import search as semantic_search
from app.memory.semantic import write as semantic_write
from app.memory.settings import get_setting, set_setting
from app.models.episode import PostEpisodeCreate, ThreadEpisodeCreate


# ---------------------------------------------------------------------------
# Semantic memory: source/confidence are mandatory, enforced in code
# ---------------------------------------------------------------------------


async def test_semantic_write_missing_source_is_rejected(db_session, faiss_index_path):
    with pytest.raises(MissingProvenanceError):
        await semantic_write(
            db_session,
            entity_id="user:aswin",
            fact="Prefers a direct, no-fluff brand voice.",
            source=None,
            confidence=0.9,
            index_path=faiss_index_path,
        )


async def test_semantic_write_missing_confidence_is_rejected(db_session, faiss_index_path):
    with pytest.raises(MissingProvenanceError):
        await semantic_write(
            db_session,
            entity_id="user:aswin",
            fact="Prefers a direct, no-fluff brand voice.",
            source="brand_voice_guide:v1",
            confidence=None,
            index_path=faiss_index_path,
        )


async def test_semantic_write_confidence_out_of_range_is_rejected(db_session, faiss_index_path):
    with pytest.raises(MissingProvenanceError):
        await semantic_write(
            db_session,
            entity_id="user:aswin",
            fact="Prefers a direct, no-fluff brand voice.",
            source="brand_voice_guide:v1",
            confidence=1.5,
            index_path=faiss_index_path,
        )


async def test_semantic_write_with_source_and_confidence_is_retrievable(db_session, faiss_index_path):
    record = await semantic_write(
        db_session,
        entity_id="user:aswin",
        fact="Standing research interest: Agentic AI and Hermes tooling.",
        source="onboarding_interview",
        confidence=0.95,
        category="research_interest",
        index_path=faiss_index_path,
    )
    assert record.id is not None
    assert record.source == "onboarding_interview"
    assert record.confidence == 0.95

    results = await semantic_search(
        db_session,
        entity_id="user:aswin",
        query="Agentic AI Hermes tooling research",
        index_path=faiss_index_path,
    )
    assert any(r.fact == record.fact for r in results)
    assert all(r.source and r.confidence is not None for r in results)


async def test_semantic_search_excludes_low_confidence_results(db_session, faiss_index_path):
    await semantic_write(
        db_session,
        entity_id="user:aswin",
        fact="Might be interested in blockchain, unconfirmed.",
        source="inference:weak_signal",
        confidence=0.2,
        index_path=faiss_index_path,
    )
    results = await semantic_search(
        db_session,
        entity_id="user:aswin",
        query="blockchain",
        min_confidence=0.5,
        index_path=faiss_index_path,
    )
    assert results == []


# ---------------------------------------------------------------------------
# Episodic memory: 90-day thread-content purge, 12-month post archival
# ---------------------------------------------------------------------------


async def test_thread_content_purged_after_90_days_metadata_survives(db_session):
    now = datetime.now(timezone.utc)
    old_thread = ThreadEpisodeCreate(
        entity_id="user:aswin",
        thread_id="thread-old-1",
        thread_type="dm",
        content="This is a private DM that should be purged after 90 days.",
        participants=["user:aswin", "user:recruiter-1"],
        resolution="human_edited",
        important=False,
    )
    await episodic.record_thread_episode(db_session, old_thread)

    stale_created_at = now - timedelta(days=100)
    thread = await episodic.get_thread(db_session, "thread-old-1")
    thread.created_at = stale_created_at
    await db_session.commit()

    purged_count = await policy.purge_stale_thread_content(db_session, now=now)
    assert purged_count == 1

    refreshed = await episodic.get_thread(db_session, "thread-old-1")
    assert refreshed.content is None
    assert refreshed.content_purged is True
    assert refreshed.content_purged_at is not None
    # Metadata survives the content purge
    assert refreshed.thread_id == "thread-old-1"
    assert refreshed.participants == ["user:aswin", "user:recruiter-1"]
    assert refreshed.resolution == "human_edited"


async def test_thread_content_not_purged_within_90_days(db_session):
    now = datetime.now(timezone.utc)
    recent_thread = ThreadEpisodeCreate(
        entity_id="user:aswin",
        thread_id="thread-recent-1",
        thread_type="comment",
        content="A recent comment reply thread.",
        participants=["user:aswin", "user:commenter-1"],
        resolution="auto_approved",
        important=False,
    )
    await episodic.record_thread_episode(db_session, recent_thread)

    purged_count = await policy.purge_stale_thread_content(db_session, now=now)
    assert purged_count == 0

    refreshed = await episodic.get_thread(db_session, "thread-recent-1")
    assert refreshed.content == "A recent comment reply thread."
    assert refreshed.content_purged is False


async def test_important_thread_content_exempt_from_purge(db_session):
    now = datetime.now(timezone.utc)
    important_thread = ThreadEpisodeCreate(
        entity_id="user:aswin",
        thread_id="thread-important-1",
        thread_type="dm",
        content="A DM the user flagged as important, must not be purged.",
        participants=["user:aswin", "user:client-1"],
        resolution="escalated",
        important=True,
    )
    await episodic.record_thread_episode(db_session, important_thread)

    thread = await episodic.get_thread(db_session, "thread-important-1")
    thread.created_at = now - timedelta(days=365)
    await db_session.commit()

    purged_count = await policy.purge_stale_thread_content(db_session, now=now)
    assert purged_count == 0

    refreshed = await episodic.get_thread(db_session, "thread-important-1")
    assert refreshed.content is not None
    assert refreshed.content_purged is False


async def test_posts_older_than_12_months_are_archived(db_session):
    now = datetime.now(timezone.utc)
    old_post = PostEpisodeCreate(
        entity_id="user:aswin",
        post_id="post-old-1",
        content="An old post about AI agents.",
        published_at=now - timedelta(days=400),
        impressions=1000,
        likes=50,
        comments=5,
        shares=2,
        follower_delta=3,
    )
    await episodic.record_post_episode(db_session, old_post)

    archived_count = await policy.archive_old_posts(db_session, now=now)
    assert archived_count == 1

    posts = await episodic.get_recent_posts(db_session, "user:aswin", include_archived=True)
    assert posts[0].archived is True
    # Engagement stats are preserved, not deleted
    assert posts[0].impressions == 1000


# ---------------------------------------------------------------------------
# agent_settings: seeded default + read/write round-trip
# ---------------------------------------------------------------------------


async def test_research_agent_poll_interval_defaults_to_daily(db_session):
    value = await get_setting(db_session, "research_agent.poll_interval")
    assert value == "daily"


async def test_set_setting_round_trips_new_value(db_session):
    await set_setting(db_session, "research_agent.poll_interval", "hourly", updated_by="dashboard_ui:test")
    value = await get_setting(db_session, "research_agent.poll_interval")
    assert value == "hourly"


async def test_unknown_setting_key_returns_none(db_session):
    value = await get_setting(db_session, "not_a_real_setting")
    assert value is None


# ---------------------------------------------------------------------------
# Working memory (Redis): draft / thread / approval session lifecycle
# ---------------------------------------------------------------------------


async def test_working_memory_draft_lifecycle(fake_redis):
    await working.set_current_draft("task-1", {"topic": "agentic AI", "status": "drafting"})
    draft = await working.get_current_draft("task-1")
    assert draft == {"topic": "agentic AI", "status": "drafting"}

    await working.clear_current_draft("task-1")
    assert await working.get_current_draft("task-1") is None


async def test_working_memory_approval_session(fake_redis):
    await working.set_approval_session("session-1", {"tool": "publish_post", "status": "pending"})
    session_state = await working.get_approval_session("session-1")
    assert session_state["status"] == "pending"
