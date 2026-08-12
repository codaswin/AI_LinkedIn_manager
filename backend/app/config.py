import os

from pydantic import BaseModel


class Settings(BaseModel):
    VECTOR_DB_PATH: str = os.getenv("VECTOR_DB_PATH", "./data/faiss_index")
    RAG_EMBEDDING_DIM: int = int(os.getenv("RAG_EMBEDDING_DIM", "256"))

    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/db")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    WORKING_MEMORY_TTL_SECONDS: int = int(os.getenv("WORKING_MEMORY_TTL_SECONDS", "3600"))

    EPISODIC_POST_RETENTION_DAYS: int = int(os.getenv("EPISODIC_POST_RETENTION_DAYS", "365"))
    EPISODIC_THREAD_CONTENT_RETENTION_DAYS: int = int(os.getenv("EPISODIC_THREAD_CONTENT_RETENTION_DAYS", "90"))


settings = Settings()
