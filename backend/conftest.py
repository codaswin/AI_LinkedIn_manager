from __future__ import annotations

import fakeredis
import pytest

from app import shared_state


@pytest.fixture(autouse=True)
def shared_redis():
    client = fakeredis.FakeRedis(decode_responses=True)
    shared_state.configure_client(client)
    client.flushall()
    yield client
    client.flushall()
    client.close()
    shared_state.reset_client()
