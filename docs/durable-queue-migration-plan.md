# Durable Queue Migration Plan (Redis + ARQ)

## Goal
Move publish and analytics execution from in-process scheduler jobs to durable queue-backed workers.

- APScheduler role: reconciler/scanner only.
- Postgres role: source of truth for job state and retries.
- Redis role: transport for ARQ worker execution.

## Current Status
Implemented in this iteration:

- Added feature flags and ARQ settings in app config.
- Added durable `job_queue` model and Alembic migration `007`.
- Added queue state helper module.
- Added reconciler module (publish + analytics queueing path).
- Added ARQ enqueue helpers and worker entrypoint.
- Added ARQ task wrappers for publish/analytics.
- Updated startup wiring to run legacy and/or reconciler paths by flags.
- Added Redis-based reconciler leader lock to prevent duplicate enqueueing in multi-instance setups.
- Added authenticated jobs observability routes for per-user queue inspection.
- Added follow-up migration `008` to drop obsolete `uq_job_queue_post_type_status` constraint from early rollout.
- Fixed jobs route ordering so `/jobs/queue-stats` and `/jobs/posts/{post_id}` are not shadowed by `/{job_id}`.
- Fixed ARQ enqueue id strategy to use unique task IDs per enqueue attempt.

Not yet implemented in this iteration:

- Full end-to-end load test suite.

## Config Flags
Use env vars to control cutover safely:

- `ENABLE_LEGACY_PUBLISHER=true`
- `ENABLE_LEGACY_ANALYTICS=true`
- `ENABLE_RECONCILER=false`
- `ENABLE_ARQ_ENQUEUE=false`

Recommended staged cutover:

1. Keep legacy true, reconciler false (current default).
2. Enable ARQ enqueue and start worker process.
3. Enable reconciler after worker is healthy.
4. Disable legacy publisher, then legacy analytics after validation.

## Data Model
`job_queue` captures per-post job state:

- `id`, `post_id`, `job_type`, `status`
- `arq_task_id`
- `attempt_count`, `max_attempts`
- `error_message`
- `enqueued_at`, `started_at`, `completed_at`, `next_retry_at`

Status lifecycle:

`queued -> running -> completed`
`queued -> running -> failed -> queued (retry)`
`queued -> running -> dead_letter`

## Processes
API process:

- Runs APScheduler.
- Legacy jobs may still run by feature flags.
- Reconciler scans due work and creates queue rows.
- Reconciler optionally enqueues ARQ tasks.

Worker process:

- `python -m app.worker`
- Executes `publish_post_job` and `fetch_analytics_job`.
- Updates durable job state transitions.

## Execution Checklist
1. Install new dependency: `pip install -r requirements.txt`.
2. Apply migration: `alembic upgrade head`.
3. Start API with existing defaults (legacy only).
4. Toggle `ENABLE_ARQ_ENQUEUE=true`.
5. Start worker: `python -m app.worker`.
6. Toggle `ENABLE_RECONCILER=true`.
7. Verify queue progression `queued -> running -> completed`.
8. Disable `ENABLE_LEGACY_PUBLISHER` after stable publish metrics.
9. Disable `ENABLE_LEGACY_ANALYTICS` after stable analytics freshness metrics.

## Rollback
If ARQ path misbehaves:

1. Set `ENABLE_ARQ_ENQUEUE=false`.
2. Set `ENABLE_RECONCILER=false`.
3. Set legacy flags back to true.
4. Restart API.

## Next Milestone
- Add integration tests for crash recovery and duplicate prevention.

## Retry Classification
Implemented policy:

- Retryable: network errors, timeouts, HTTP 429, and HTTP 5xx.
- Permanent (dead-letter): HTTP 400/401/403/404 and account configuration/auth scope failures.
- Publish thread predecessor-not-ready remains retryable.

Queue errors now include an error code prefix, for example:

- `[HTTP_429] ...`
- `[AUTH_OR_ACCOUNT_CONFIGURATION] ...`
- `[POST_NOT_ELIGIBLE] ...`

## Integration Test Coverage
Implemented tests:

- Reconciler duplicate-prevention test: when lock is not acquired, enqueue path is skipped.
- Reconciler recovery test: retryable failed publish jobs are re-enqueued and marked enqueued.

Location:

- `tests/test_reconciler_integration.py`

## Smoke Check Script
One-command runtime validation:

`python backend/scripts/queue_smoke_check.py --api-url http://127.0.0.1:8010 --user-id <user_uuid> --email <email> --scheduled-post-id <post_uuid> --published-post-id <post_uuid>`

The script verifies:

- `job_queue` schema objects.
- Jobs API auth + availability.
- Per-post queue records and statuses.

## Single Command Local Run
Run API + worker together:

`python backend/scripts/run_queue_mode.py`

Use a specific env file (recommended for queue mode):

`python backend/scripts/run_queue_mode.py --env-file backend/.env.queuecheck --port 8010`

Optional modes:

- API only: `python backend/scripts/run_queue_mode.py --api-only`
- Worker only: `python backend/scripts/run_queue_mode.py --worker-only`
