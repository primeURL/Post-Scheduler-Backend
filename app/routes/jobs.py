"""Jobs routes for queue observability."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.dependencies.auth import get_current_user
from app.models.job_queue import JobQueue, JobStatus
from app.models.post import Post
from app.models.user import User
from app.schemas.jobs import JobQueueRead, JobQueueStatsRead

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _to_job_read(job: JobQueue) -> JobQueueRead:
    return JobQueueRead(
        id=job.id,
        post_id=job.post_id,
        job_type=job.job_type.value,
        status=job.status.value,
        arq_task_id=job.arq_task_id,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        error_message=job.error_message,
        enqueued_at=job.enqueued_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        next_retry_at=job.next_retry_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get("/queue-stats", response_model=JobQueueStatsRead)
async def get_queue_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobQueueStatsRead:
    result = await db.execute(
        select(JobQueue.status, func.count(JobQueue.id))
        .join(Post, Post.id == JobQueue.post_id)
        .where(Post.user_id == current_user.id)
        .group_by(JobQueue.status)
    )

    counts = {status_enum.value: count for status_enum, count in result.all()}
    return JobQueueStatsRead(
        queued=counts.get(JobStatus.queued.value, 0),
        running=counts.get(JobStatus.running.value, 0),
        completed=counts.get(JobStatus.completed.value, 0),
        failed=counts.get(JobStatus.failed.value, 0),
        dead_letter=counts.get(JobStatus.dead_letter.value, 0),
        total=sum(counts.values()),
    )


@router.get("/posts/{post_id}", response_model=list[JobQueueRead])
async def list_post_jobs(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[JobQueueRead]:
    post = await db.get(Post, post_id)
    if not post or post.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    result = await db.execute(
        select(JobQueue)
        .where(JobQueue.post_id == post_id)
        .order_by(JobQueue.created_at.desc())
    )
    rows = list(result.scalars().all())
    return [_to_job_read(job) for job in rows]


@router.get("/{job_id}", response_model=JobQueueRead)
async def get_job(
    job_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobQueueRead:
    result = await db.execute(
        select(JobQueue)
        .join(Post, Post.id == JobQueue.post_id)
        .where(JobQueue.id == job_id, Post.user_id == current_user.id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return _to_job_read(job)
