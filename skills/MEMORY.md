# Memory Skill

Working, episodic, and semantic memory — each with a defined read/write policy and traceable source/confidence.

## Working Memory (Redis — session/task scoped)
```python
# memory/working.py
import json
import redis
from app.config import settings

r = redis.from_url(settings.REDIS_URL)
TTL_SECONDS = 3600  # cleared after 1hr of inactivity, or on task completion


def get_working_memory(task_id: str) -> dict:
    raw = r.get(f"working:{task_id}")
    return json.loads(raw) if raw else {}


def set_working_memory(task_id: str, data: dict):
    r.set(f"working:{task_id}", json.dumps(data), ex=TTL_SECONDS)


def clear_working_memory(task_id: str):
    r.delete(f"working:{task_id}")
```

## Episodic Memory (Postgres — one row per meaningful interaction)
```python
# models/episode.py
from sqlalchemy import Column, Integer, String, DateTime, JSON
from sqlalchemy.sql import func
from app.database import Base


class Episode(Base):
    __tablename__ = "episodes"
    id = Column(Integer, primary_key=True)
    entity_id = Column(String, nullable=False, index=True)  # e.g. user_id or student_id
    task_id = Column(String, nullable=False)
    summary = Column(String, nullable=False)
    outcome = Column(String, nullable=True)  # e.g. "resolved", "escalated"
    raw_data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# memory/episodic.py
from sqlalchemy.orm import Session
from app.models.episode import Episode


def record_episode(db: Session, entity_id: str, task_id: str, summary: str, outcome: str, raw_data: dict):
    episode = Episode(entity_id=entity_id, task_id=task_id, summary=summary, outcome=outcome, raw_data=raw_data)
    db.add(episode)
    db.commit()


def recent_episodes(db: Session, entity_id: str, limit: int = 5) -> list[Episode]:
    return (
        db.query(Episode)
        .filter(Episode.entity_id == entity_id)
        .order_by(Episode.created_at.desc())
        .limit(limit)
        .all()
    )
```

## Semantic Memory (vector store — long-term facts, must carry source + confidence)
```python
# memory/semantic.py
import faiss
import numpy as np
import json
from datetime import datetime
from app.rag.ingest import embed  # reuse the same embedding function as RAG


def write_semantic_memory(entity_id: str, fact: str, source: str, confidence: float, index_path: str):
    """Every write requires source and confidence — untraceable memory is forbidden."""
    assert 0.0 <= confidence <= 1.0
    vector = embed([fact])
    entry = {
        "entity_id": entity_id,
        "fact": fact,
        "source": source,
        "confidence": confidence,
        "written_at": datetime.utcnow().isoformat(),
    }
    _append_to_index(vector, entry, index_path)


def read_semantic_memory(entity_id: str, query: str, top_k: int = 5, min_confidence: float = 0.5, index_path: str = "") -> list[dict]:
    results = _search_index(query, index_path, top_k=top_k * 2)
    filtered = [r for r in results if r["entity_id"] == entity_id and r["confidence"] >= min_confidence]
    return filtered[:top_k]


def _append_to_index(vector, entry, index_path):
    ...  # append to a FAISS index + parallel metadata store, same pattern as rag/ingest.py


def _search_index(query, index_path, top_k):
    ...  # same pattern as rag/retrieve.py
```

## Promotion Policy (working → episodic → semantic)
```python
# memory/policy.py
from app.memory.episodic import record_episode
from app.memory.semantic import write_semantic_memory


def promote_on_task_completion(db, state, entity_id: str):
    """Called once per completed task, not per iteration."""
    record_episode(
        db, entity_id=entity_id, task_id=state.task_id,
        summary=summarize(state), outcome=state.stop_reason.value, raw_data=state.scratchpad,
    )

    # Only promote to semantic memory if confidence is high enough to be worth remembering long-term
    for candidate_fact in extract_candidate_facts(state):
        if candidate_fact["confidence"] >= 0.7:
            write_semantic_memory(
                entity_id=entity_id, fact=candidate_fact["fact"],
                source=f"task:{state.task_id}", confidence=candidate_fact["confidence"],
                index_path=settings.VECTOR_DB_PATH,
            )


def summarize(state) -> str: ...
def extract_candidate_facts(state) -> list[dict]: ...
```

## Best Practices
- Working memory never persists past the task — if something matters beyond the task, it must be explicitly promoted
- Episodic memory is append-only; never edit past episodes, add a new one that supersedes
- Semantic memory reads are always confidence-filtered — a low-confidence "fact" shouldn't silently steer a new task
- Keep memory reads inside the token budget `context-agent` enforces — memory is an input to context, not a separate unlimited channel
