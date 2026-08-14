from app.config import normalize_async_database_url


def test_normalizes_standard_postgres_provider_url() -> None:
    assert (
        normalize_async_database_url("postgresql://user:secret@db.example/app")
        == "postgresql+asyncpg://user:secret@db.example/app"
    )


def test_normalizes_legacy_postgres_and_psycopg2_urls() -> None:
    assert normalize_async_database_url("postgres://host/db") == "postgresql+asyncpg://host/db"
    assert (
        normalize_async_database_url("postgresql+psycopg2://host/db")
        == "postgresql+asyncpg://host/db"
    )


def test_normalizes_sqlite_and_preserves_async_urls() -> None:
    assert normalize_async_database_url("sqlite:///local.db") == "sqlite+aiosqlite:///local.db"
    assert normalize_async_database_url("sqlite+aiosqlite:///local.db") == "sqlite+aiosqlite:///local.db"
    assert normalize_async_database_url("postgresql+asyncpg://host/db") == "postgresql+asyncpg://host/db"
