"""Dedicated ARQ worker process entrypoint.

Run with:
    python -m app.worker
"""

from arq import run_worker  # pyright: ignore[reportMissingImports]

from app.core.arq_worker import WorkerSettings


if __name__ == "__main__":
    run_worker(WorkerSettings)
