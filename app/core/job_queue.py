"""Helpers for durable job queue state transitions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job_queue import JobQueue, JobStatus, JobType

_OPEN_STATUSES = (JobStatus.queued, JobStatus.running)


async def get_open_job_for_post(
    session: AsyncSession,
    *,
    post_id: uuid.UUID,
    job_type: JobType,
) -> JobQueue | None:
    result = await session.execute(
        select(JobQueue).where(
            JobQueue.post_id == post_id,
            JobQueue.job_type == job_type,
            JobQueue.status.in_(_OPEN_STATUSES),
        )
    )
    return result.scalar_one_or_none()


async def create_queued_job(
    session: AsyncSession,
    *,
    post_id: uuid.UUID,
    job_type: JobType,
    max_attempts: int,
) -> JobQueue:
    job = JobQueue(
        post_id=post_id,
        job_type=job_type,
        status=JobStatus.queued,
        max_attempts=max_attempts,
    )
    session.add(job)
    await session.flush()
    return job


async def create_queued_job_if_missing(
    session: AsyncSession,
    *,
    post_id: uuid.UUID,
    job_type: JobType,
    max_attempts: int,
) -> JobQueue:
    existing = await get_open_job_for_post(session, post_id=post_id, job_type=job_type)
    if existing:
        return existing
    return await create_queued_job(
        session,
        post_id=post_id,
        job_type=job_type,
        max_attempts=max_attempts,
    )


async def mark_job_running(session: AsyncSession, *, job: JobQueue) -> None:
    job.status = JobStatus.running
    job.started_at = datetime.now(timezone.utc)
    job.attempt_count += 1
    job.error_message = None
    await session.flush()


async def mark_job_enqueued(session: AsyncSession, *, job: JobQueue, arq_task_id: str) -> None:
    job.status = JobStatus.queued
    job.next_retry_at = None
    job.arq_task_id = arq_task_id
    job.enqueued_at = datetime.now(timezone.utc)
    await session.flush()


async def mark_job_completed(session: AsyncSession, *, job: JobQueue) -> None:
    now = datetime.now(timezone.utc)
    job.status = JobStatus.completed
    job.completed_at = now
    job.next_retry_at = None
    job.error_message = None
    await session.flush()


async def mark_job_failed(
    session: AsyncSession,
    *,
    job: JobQueue,
    error_message: str,
    retry_at: datetime | None,
    retryable: bool = True,
) -> None:
    job.error_message = error_message
    if not retryable:
        job.status = JobStatus.dead_letter
        job.next_retry_at = None
    elif job.attempt_count >= job.max_attempts:
        job.status = JobStatus.dead_letter
        job.next_retry_at = None
    else:
        job.status = JobStatus.failed
        job.next_retry_at = retry_at
    await session.flush()


async def get_job_by_id(session: AsyncSession, *, job_id: uuid.UUID) -> JobQueue | None:
    result = await session.execute(select(JobQueue).where(JobQueue.id == job_id))
    return result.scalar_one_or_none()


async def get_retryable_jobs(
    session: AsyncSession,
    *,
    job_type: JobType,
    now: datetime,
) -> list[JobQueue]:
    result = await session.execute(
        select(JobQueue).where(
            and_(
                JobQueue.job_type == job_type,
                JobQueue.status == JobStatus.failed,
                JobQueue.next_retry_at.is_not(None),
                JobQueue.next_retry_at <= now,
            )
        )
    )
    return list(result.scalars())
