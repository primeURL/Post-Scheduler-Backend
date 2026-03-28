"""ARQ worker settings shared by the dedicated worker process."""

from arq.connections import RedisSettings  # pyright: ignore[reportMissingImports]

from app.core.config import settings
from app.jobs.analytics_task import fetch_analytics_job
from app.jobs.publisher_task import publish_post_job


class WorkerSettings:
    functions = [publish_post_job, fetch_analytics_job]
    redis_settings = RedisSettings.from_dsn(settings.arq_redis_url)
    job_timeout = settings.arq_job_timeout_seconds
    max_tries = settings.arq_max_tries
