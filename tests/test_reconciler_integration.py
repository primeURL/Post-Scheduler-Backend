import uuid
from dataclasses import dataclass
from types import SimpleNamespace

import pytest  # pyright: ignore[reportMissingImports]

from app.core import reconciler


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self._values


class _FakeSession:
    def __init__(self, *, execute_values=None, persisted_jobs=None):
        self._execute_values = execute_values or []
        self._persisted_jobs = persisted_jobs or {}
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, _stmt):
        return _ScalarResult(self._execute_values)

    async def commit(self):
        self.commits += 1

    async def get(self, _model, job_id):
        return self._persisted_jobs.get(job_id)


class _SessionFactory:
    def __init__(self, sessions):
        self._sessions = list(sessions)
        self.calls = 0

    def __call__(self):
        session = self._sessions[self.calls]
        self.calls += 1
        return session


@dataclass
class _FakeJob:
    id: uuid.UUID
    post_id: uuid.UUID


@pytest.mark.asyncio
async def test_reconciler_skips_when_lock_not_acquired(monkeypatch):
    """Duplicate prevention: second instance should skip enqueue path."""
    enqueued = []
    released = []

    async def _acquire_lock(**_kwargs):
        return False

    async def _release_lock(**kwargs):
        released.append(kwargs)
        return True

    async def _enqueue_publish_job(*, job_id, post_id):
        enqueued.append((job_id, post_id))
        return str(job_id)

    monkeypatch.setattr(reconciler.settings, "enable_arq_enqueue", True)
    monkeypatch.setattr(reconciler, "acquire_lock", _acquire_lock)
    monkeypatch.setattr(reconciler, "release_lock", _release_lock)
    monkeypatch.setattr(reconciler, "enqueue_publish_job", _enqueue_publish_job)

    await reconciler.reconcile_publish_jobs()

    assert enqueued == []
    assert released == []


@pytest.mark.asyncio
async def test_reconciler_requeues_retryable_job_after_failure(monkeypatch):
    """Crash/failure recovery: retryable failed job should be re-enqueued."""
    job_id = uuid.uuid4()
    post_id = uuid.uuid4()
    retry_job = _FakeJob(id=job_id, post_id=post_id)

    first_session = _FakeSession(execute_values=[])
    second_session = _FakeSession(persisted_jobs={job_id: retry_job})
    session_factory = _SessionFactory([first_session, second_session])

    enqueued = []
    marked = []

    async def _acquire_lock(**_kwargs):
        return True

    async def _release_lock(**_kwargs):
        return True

    async def _get_retryable_jobs(*_args, **_kwargs):
        return [retry_job]

    async def _enqueue_publish_job(*, job_id, post_id):
        enqueued.append((job_id, post_id))
        return f"task-{job_id}"

    async def _mark_job_enqueued(_session, *, job, arq_task_id):
        marked.append((job.id, arq_task_id))

    async def _create_queued_job_if_missing(*_args, **_kwargs):
        raise AssertionError("No new due posts should be queued in this test")

    monkeypatch.setattr(reconciler.settings, "enable_arq_enqueue", True)
    monkeypatch.setattr(reconciler, "async_session_factory", session_factory)
    monkeypatch.setattr(reconciler, "acquire_lock", _acquire_lock)
    monkeypatch.setattr(reconciler, "release_lock", _release_lock)
    monkeypatch.setattr(reconciler, "get_retryable_jobs", _get_retryable_jobs)
    monkeypatch.setattr(reconciler, "enqueue_publish_job", _enqueue_publish_job)
    monkeypatch.setattr(reconciler, "mark_job_enqueued", _mark_job_enqueued)
    monkeypatch.setattr(reconciler, "create_queued_job_if_missing", _create_queued_job_if_missing)

    await reconciler.reconcile_publish_jobs()

    assert enqueued == [(job_id, post_id)]
    assert marked == [(job_id, f"task-{job_id}")]
    assert first_session.commits == 1
    assert second_session.commits == 1
    assert session_factory.calls == 2
