from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from app import automation
from app.models.automation import ScheduledPostRecord
from app.tools import registry as registry_module

registry_module._import_all_tools()


async def test_due_scheduled_post_is_published_once(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    record = ScheduledPostRecord(
        id="scheduled-1",
        content="A scheduled post",
        publish_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        status="pending",
    )
    db_session.add(record)
    await db_session.commit()

    calls: list[str] = []

    async def fake_publish(content: str):
        calls.append(content)
        return {"status": "published"}

    monkeypatch.setattr(automation, "publish_content", fake_publish)
    first = await automation.process_due_posts()
    second = await automation.process_due_posts()

    await db_session.refresh(record)
    assert first == {"claimed": 1, "published": 1, "failed": 0}
    assert second == {"claimed": 0, "published": 0, "failed": 0}
    assert calls == ["A scheduled post"]
    assert record.status == "published"
    assert record.attempts == 1


async def test_failed_scheduled_post_records_error(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    record = ScheduledPostRecord(
        id="scheduled-2",
        content="Will fail",
        publish_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        status="pending",
    )
    db_session.add(record)
    await db_session.commit()

    async def fail_publish(content: str):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(automation, "publish_content", fail_publish)
    result = await automation.process_due_posts()

    await db_session.refresh(record)
    assert result["failed"] == 1
    assert record.status == "failed"
    assert record.last_error == "provider unavailable"
