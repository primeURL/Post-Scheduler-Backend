import uuid
from datetime import datetime

from pydantic import BaseModel


class JobQueueRead(BaseModel):
    id: uuid.UUID
    post_id: uuid.UUID
    job_type: str
    status: str
    arq_task_id: str | None
    attempt_count: int
    max_attempts: int
    error_message: str | None
    enqueued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    next_retry_at: datetime | None
    created_at: datetime
    updated_at: datetime


class JobQueueStatsRead(BaseModel):
    queued: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    dead_letter: int = 0
    total: int = 0
