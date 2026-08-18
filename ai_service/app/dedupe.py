import asyncio
from typing import Protocol

from redis.asyncio import Redis


class EventStore(Protocol):
    async def claim(self, key: str) -> bool: ...


class RedisEventStore:
    def __init__(self, redis: Redis, ttl_seconds: int = 86400) -> None:
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    async def claim(self, key: str) -> bool:
        result = await self.redis.set(f"customer-service:event:{key}", "1", ex=self.ttl_seconds, nx=True)
        return bool(result)


class MemoryEventStore:
    def __init__(self) -> None:
        self.keys: set[str] = set()
        self._lock = asyncio.Lock()

    async def claim(self, key: str) -> bool:
        async with self._lock:
            if key in self.keys:
                return False
            self.keys.add(key)
            return True

