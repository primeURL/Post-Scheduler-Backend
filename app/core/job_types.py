"""ARQ enqueue helpers for publish and analytics jobs."""

from __future__ import annotations

import uuid

from app.core.config import settings

_pool = None


async def _get_arq_pool():
    global _pool
    if _pool is not None:
        return _pool

    try:
        from arq.connections import RedisSettings, create_pool  # pyright: ignore[reportMissingImports]
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("arq is not installed; add it to requirements and install deps") from exc

    _pool = await create_pool(RedisSettings.from_dsn(settings.arq_redis_url))
    return _pool


async def close_arq_pool() -> None:
    global _pool
    if _pool is not None:
        close_fn = getattr(_pool, "aclose", None)
        if close_fn is not None:
            await close_fn()
        else:  # pragma: no cover - compatibility fallback
            await _pool.close()
        _pool = None


async def enqueue_publish_job(*, job_id: uuid.UUID, post_id: uuid.UUID) -> str:
    redis = await _get_arq_pool()
    job = await redis.enqueue_job(
        "publish_post_job",
        str(job_id),
        str(post_id),
    )
    if job is None:  # pragma: no cover - defensive
        raise RuntimeError("Failed to enqueue publish job")
    return job.job_id


async def enqueue_analytics_job(*, job_id: uuid.UUID, post_id: uuid.UUID) -> str:
    redis = await _get_arq_pool()
    job = await redis.enqueue_job(
        "fetch_analytics_job",
        str(job_id),
        str(post_id),
    )
    if job is None:  # pragma: no cover - defensive
        raise RuntimeError("Failed to enqueue analytics job")
    return job.job_id
