from __future__ import annotations

import pytest
from app.memory import brand_voice
from app.rag.retrieve import retrieve
from app.tenancy import context as tenancy_context

_USER = "user-brand-voice-test"


@pytest.fixture(autouse=True)
def _tenancy_context():
    token = tenancy_context.set_current_user_id(_USER)
    yield
    tenancy_context.reset_current_user_id(token)


async def test_create_persists_and_is_listable(db_session, faiss_index_path) -> None:
    record = await brand_voice.create_brand_voice(
        db_session, title="Confident Founder Voice", content="Direct, short sentences, no buzzwords.",
        index_path=faiss_index_path,
    )
    assert record.title == "Confident Founder Voice"
    assert record.id

    listed = await brand_voice.list_brand_voices(db_session)
    assert [r.id for r in listed] == [record.id]


async def test_create_rejects_empty_title(db_session, faiss_index_path) -> None:
    with pytest.raises(ValueError, match="title"):
        await brand_voice.create_brand_voice(db_session, title="  ", content="some content", index_path=faiss_index_path)


async def test_create_rejects_empty_content(db_session, faiss_index_path) -> None:
    with pytest.raises(ValueError, match="content"):
        await brand_voice.create_brand_voice(db_session, title="Some Title", content="   ", index_path=faiss_index_path)


async def test_created_brand_voice_is_retrievable_via_rag(db_session, faiss_index_path) -> None:
    await brand_voice.create_brand_voice(
        db_session,
        title="Playful Startup Voice",
        content="Upbeat, uses emoji sparingly, always leads with the customer win.",
        index_path=faiss_index_path,
    )

    hits = await retrieve(query="upbeat customer win", source_types=["brand_voice"], top_k=3, index_path=faiss_index_path)
    assert len(hits) == 1
    assert "customer win" in hits[0]["text"]


async def test_list_orders_most_recent_first(db_session, faiss_index_path) -> None:
    first = await brand_voice.create_brand_voice(db_session, title="First", content="content one", index_path=faiss_index_path)
    second = await brand_voice.create_brand_voice(db_session, title="Second", content="content two", index_path=faiss_index_path)

    listed = await brand_voice.list_brand_voices(db_session)
    assert [r.id for r in listed] == [second.id, first.id]


async def test_get_returns_none_for_unknown_id(db_session) -> None:
    assert await brand_voice.get_brand_voice(db_session, "does-not-exist") is None


async def test_update_changes_title_and_content(db_session, faiss_index_path) -> None:
    record = await brand_voice.create_brand_voice(
        db_session, title="Original", content="original content", index_path=faiss_index_path
    )
    updated = await brand_voice.update_brand_voice(
        db_session, record.id, title="Updated", content="updated content", index_path=faiss_index_path
    )
    assert updated is not None
    assert updated.title == "Updated"
    assert updated.content == "updated content"

    hits = await retrieve(query="updated content", source_types=["brand_voice"], top_k=3, index_path=faiss_index_path)
    assert any("updated content" in h["text"] for h in hits)


async def test_update_returns_none_for_unknown_id(db_session, faiss_index_path) -> None:
    result = await brand_voice.update_brand_voice(
        db_session, "does-not-exist", title="x", content="y", index_path=faiss_index_path
    )
    assert result is None


async def test_delete_removes_record_and_rag_entry(db_session, faiss_index_path) -> None:
    record = await brand_voice.create_brand_voice(
        db_session, title="Temp Voice", content="temporary content to delete", index_path=faiss_index_path
    )

    deleted = await brand_voice.delete_brand_voice(db_session, record.id, index_path=faiss_index_path)
    assert deleted is True

    assert await brand_voice.get_brand_voice(db_session, record.id) is None
    hits = await retrieve(query="temporary content", source_types=["brand_voice"], top_k=3, index_path=faiss_index_path)
    assert hits == []


async def test_delete_returns_false_for_unknown_id(db_session) -> None:
    assert await brand_voice.delete_brand_voice(db_session, "does-not-exist") is False


async def test_brand_voices_are_isolated_per_user(db_session, faiss_index_path) -> None:
    record = await brand_voice.create_brand_voice(
        db_session, title="User A Voice", content="belongs to user a", index_path=faiss_index_path
    )

    other_user = "user-brand-voice-test-other"
    token = tenancy_context.set_current_user_id(other_user)
    try:
        assert await brand_voice.list_brand_voices(db_session) == []
        assert await brand_voice.get_brand_voice(db_session, record.id) is None
        assert (
            await brand_voice.update_brand_voice(
                db_session, record.id, title="hijacked", content="hijacked content", index_path=faiss_index_path
            )
            is None
        )
        assert await brand_voice.delete_brand_voice(db_session, record.id, index_path=faiss_index_path) is False
    finally:
        tenancy_context.reset_current_user_id(token)

    # Untouched by the other user's attempts.
    still_there = await brand_voice.get_brand_voice(db_session, record.id)
    assert still_there is not None
    assert still_there.title == "User A Voice"
