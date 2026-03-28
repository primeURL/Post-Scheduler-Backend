"""APScheduler reconcilers that enqueue durable jobs instead of executing work directly."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.job_queue import create_queued_job_if_missing, get_retryable_jobs, mark_job_enqueued
from app.core.job_types import enqueue_analytics_job, enqueue_publish_job
from app.core.redis import acquire_lock, generate_lock_token, release_lock
from app.models.job_queue import JobQueue, JobType
from app.models.post import Post, PostStatus

logger = logging.getLogger(__name__)

_ANALYTICS_WINDOW_DAYS = 7
_ANALYTICS_BATCH_SIZE = 100


async def reconcile_publish_jobs() -> None:
    """Claim due posts and enqueue publish jobs."""
    print(f"[DEBUG] reconcile_publish_jobs called at {datetime.now(timezone.utc)}")
    if not settings.enable_arq_enqueue:
        print("[DEBUG] enable_arq_enqueue is False, returning")
        return

    lock_token = generate_lock_token()
    has_lock = await acquire_lock(
        key=settings.reconciler_publish_lock_key,
        token=lock_token,
        ttl_seconds=settings.reconciler_lock_ttl_seconds,
    )
    if not has_lock:
        print("[DEBUG] Lock not acquired for publish reconciler")
        logger.debug("Skipping publish reconcile: lock not acquired")
        return

    try:
        async with async_session_factory() as session:
            stmt = (
                update(Post)
                .where(
                    Post.status == PostStatus.scheduled,
                    Post.is_deleted.is_(False),
                    Post.scheduled_for <= datetime.now(timezone.utc),
                )
                .values(status=PostStatus.publishing)
                .returning(Post.id)
            )
            result = await session.execute(stmt)
            claimed_ids = list(result.scalars())
            print(f"[DEBUG] Found {len(claimed_ids)} posts to publish: {claimed_ids}")

            retry_jobs = await get_retryable_jobs(
                session,
                job_type=JobType.publish,
                now=datetime.now(timezone.utc),
            )

            queue_items = []
            for post_id in claimed_ids:
                job = await create_queued_job_if_missing(
                    session,
                    post_id=post_id,
                    job_type=JobType.publish,
                    max_attempts=settings.arq_max_tries,
                )
                queue_items.append((job, post_id))

            for retry_job in retry_jobs:
                queue_items.append((retry_job, retry_job.post_id))

            await session.commit()

        if not queue_items:
            return

        enqueued = 0
        for job, post_id in queue_items:
            try:
                task_id = await enqueue_publish_job(job_id=job.id, post_id=post_id)
                async with async_session_factory() as session:
                    persisted = await session.get(JobQueue, job.id)
                    if persisted:
                        await mark_job_enqueued(session, job=persisted, arq_task_id=task_id)
                        await session.commit()
                enqueued += 1
            except Exception as exc:  # pragma: no cover - network/dependency failures
                logger.exception("Failed to enqueue publish job %s for post %s: %s", job.id, post_id, exc)

        logger.info("Publish reconciler enqueued %d/%d jobs", enqueued, len(queue_items))
    finally:
        await release_lock(key=settings.reconciler_publish_lock_key, token=lock_token)


async def reconcile_analytics_jobs() -> None:
    """Queue analytics refresh jobs for recently published posts."""
    if not settings.enable_arq_enqueue:
        return

    lock_token = generate_lock_token()
    has_lock = await acquire_lock(
        key=settings.reconciler_analytics_lock_key,
        token=lock_token,
        ttl_seconds=settings.reconciler_lock_ttl_seconds,
    )
    if not has_lock:
        logger.debug("Skipping analytics reconcile: lock not acquired")
        return

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=_ANALYTICS_WINDOW_DAYS)

        async with async_session_factory() as session:
            result = await session.execute(
                select(Post.id)
                .where(
                    Post.status == PostStatus.published,
                    Post.is_deleted.is_(False),
                    Post.platform_post_id.is_not(None),
                    Post.published_at >= cutoff,
                )
                .limit(_ANALYTICS_BATCH_SIZE)
            )
            post_ids = list(result.scalars())

            retry_jobs = await get_retryable_jobs(
                session,
                job_type=JobType.analytics,
                now=datetime.now(timezone.utc),
            )

            queue_items = []
            for post_id in post_ids:
                job = await create_queued_job_if_missing(
                    session,
                    post_id=post_id,
                    job_type=JobType.analytics,
                    max_attempts=settings.arq_max_tries,
                )
                queue_items.append((job, post_id))

            for retry_job in retry_jobs:
                queue_items.append((retry_job, retry_job.post_id))

            await session.commit()

        if not queue_items:
            return

        enqueued = 0
        for job, post_id in queue_items:
            try:
                task_id = await enqueue_analytics_job(job_id=job.id, post_id=post_id)
                async with async_session_factory() as session:
                    persisted = await session.get(JobQueue, job.id)
                    if persisted:
                        await mark_job_enqueued(session, job=persisted, arq_task_id=task_id)
                        await session.commit()
                enqueued += 1
            except Exception as exc:  # pragma: no cover - network/dependency failures
                logger.exception("Failed to enqueue analytics job %s for post %s: %s", job.id, post_id, exc)

        logger.info("Analytics reconciler enqueued %d/%d jobs", enqueued, len(queue_items))
    finally:
        await release_lock(key=settings.reconciler_analytics_lock_key, token=lock_token)
