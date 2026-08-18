import pytest

from app.dedupe import MemoryEventStore


@pytest.mark.asyncio
async def test_duplicate_event_can_only_be_claimed_once():
    store = MemoryEventStore()
    assert await store.claim("message_created:42") is True
    assert await store.claim("message_created:42") is False

