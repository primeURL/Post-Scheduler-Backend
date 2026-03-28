"""Async Redis client — single shared connection pool."""
import uuid

from redis.asyncio import Redis

from app.core.config import settings

_redis: Redis | None = None


async def init_redis() -> None:
    global _redis
    _redis = Redis.from_url(settings.redis_url, decode_responses=True)


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


def get_redis() -> Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialized — call init_redis() first")
    return _redis


async def acquire_lock(*, key: str, token: str, ttl_seconds: int) -> bool:
    """Acquire a short-lived distributed lock.

    Returns ``True`` only when the lock was acquired by this caller.
    """
    redis = get_redis()
    return bool(await redis.set(key, token, ex=ttl_seconds, nx=True))


async def release_lock(*, key: str, token: str) -> bool:
    """Release a distributed lock only if token still matches owner."""
    redis = get_redis()
    script = (
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) else return 0 end"
    )
    result = await redis.eval(script, 1, key, token)
    return bool(result)


def generate_lock_token() -> str:
    return uuid.uuid4().hex
