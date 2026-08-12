from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, configure_engine
from app.memory import semantic as semantic_memory
from app.memory import working as working_memory


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    configure_engine(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def fake_redis() -> AsyncIterator[fakeredis.FakeAsyncRedis]:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    working_memory.configure_client(client)
    yield client
    await client.aclose()


@pytest.fixture
def faiss_index_path(tmp_path) -> str:
    path = str(tmp_path / "semantic_test.index")
    semantic_memory.reset_index_cache()
    yield path
    semantic_memory.reset_index_cache()
