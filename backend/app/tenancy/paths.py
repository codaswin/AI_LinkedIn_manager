"""Per-user filesystem paths — currently just the FAISS/RAG index directory.

Every dashboard user gets their own isolated vector store under
VECTOR_DB_PATH/{user_id}/ instead of one shared index for the whole app.
"""

from __future__ import annotations

from app.config import settings
from app.tenancy.context import get_current_user_id


def user_vector_store_path() -> str:
    return f"{settings.VECTOR_DB_PATH.rstrip('/')}/{get_current_user_id()}"
