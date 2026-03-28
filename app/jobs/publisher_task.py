"""ARQ task wrapper for publishing a single post."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from app.core.database import async_session_factory
from app.core.job_queue import get_job_by_id, mark_job_completed, mark_job_failed, mark_job_running
from app.jobs.publisher import publish_single_post

logger = logging.getLogger(__name__)


async def publish_post_job(_ctx: dict, job_id: str, post_id: str) -> None:
    """Run one publish attempt and update durable queue state."""
    queue_id = uuid.UUID(job_id)
    post_uuid = uuid.UUID(post_id)

    async with async_session_factory() as session:
        job = await get_job_by_id(session, job_id=queue_id)
        if not job:
            logger.warning("Publish job %s not found", job_id)
            return
        await mark_job_running(session, job=job)
        await session.commit()

    status, error, retryable, error_code = await publish_single_post(post_uuid)

    async with async_session_factory() as session:
        job = await get_job_by_id(session, job_id=queue_id)
        if not job:
            return

        if status == "published":
            await mark_job_completed(session, job=job)
        else:
            retry_at = datetime.now(timezone.utc) + timedelta(minutes=5)
            error_text = error or "publish failed"
            if error_code:
                error_text = f"[{error_code}] {error_text}"
            await mark_job_failed(
                session,
                job=job,
                error_message=error_text,
                retry_at=retry_at,
                retryable=retryable,
            )
        await session.commit()
