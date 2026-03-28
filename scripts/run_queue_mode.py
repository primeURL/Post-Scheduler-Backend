#!/usr/bin/env python3
"""Run API + ARQ worker together for local queue-mode development.

Usage:
  python backend/scripts/run_queue_mode.py
  python backend/scripts/run_queue_mode.py --env-file backend/.env.queuecheck --port 8010
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run API and worker together")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to env file (defaults: backend/.env.queuecheck if present, else backend/.env)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="API host")
    parser.add_argument("--port", default="8010", help="API port")
    parser.add_argument("--api-only", action="store_true", help="Run only API process")
    parser.add_argument("--worker-only", action="store_true", help="Run only worker process")
    return parser.parse_args()


def _resolve_backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_env_file(backend_root: Path, env_file_arg: str | None) -> Path:
    if env_file_arg:
        return Path(env_file_arg).expanduser().resolve()

    queuecheck = backend_root / ".env.queuecheck"
    if queuecheck.exists():
        return queuecheck

    return backend_root / ".env"


def _spawn_process(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(cmd, cwd=str(cwd), env=env)


def main() -> int:
    args = _parse_args()
    if args.api_only and args.worker_only:
        print("Choose only one of --api-only or --worker-only")
        return 2

    backend_root = _resolve_backend_root()
    env_file = _resolve_env_file(backend_root, args.env_file)

    if not env_file.exists():
        print(f"Env file not found: {env_file}")
        return 2

    load_dotenv(env_file, override=True)

    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(backend_root))

    python_exe = sys.executable
    api_cmd = [
        python_exe,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    worker_cmd = [python_exe, "-m", "app.worker"]

    processes: list[subprocess.Popen] = []

    def _shutdown(_signum: int, _frame) -> None:
        for p in processes:
            if p.poll() is None:
                p.terminate()
        for p in processes:
            if p.poll() is None:
                try:
                    p.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    p.kill()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    if not args.worker_only:
        print(f"Starting API with env: {env_file}")
        processes.append(_spawn_process(api_cmd, backend_root, env))

    if not args.api_only:
        if not args.worker_only:
            time.sleep(2)
        print(f"Starting worker with env: {env_file}")
        processes.append(_spawn_process(worker_cmd, backend_root, env))

    if not processes:
        print("Nothing to run")
        return 0

    try:
        while True:
            for p in processes:
                code = p.poll()
                if code is not None:
                    _shutdown(signal.SIGTERM, None)
                    return code
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown(signal.SIGINT, None)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
