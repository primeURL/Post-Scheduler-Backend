#!/usr/bin/env python3
"""Queue-mode smoke check for Post Scheduler backend.

What it validates:
1. Migration/schema objects for durable queue.
2. Jobs API availability and auth.
3. Per-post queue state visibility for selected posts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from dotenv import load_dotenv
from sqlalchemy import text


def _load_env() -> None:
    # Prefer queue-check env for runtime checks, then fallback to project .env.
    load_dotenv("/Users/utkarshlanjewar/Desktop/Post-Scheduler/backend/.env.queuecheck", override=False)
    load_dotenv("/Users/utkarshlanjewar/Desktop/Post-Scheduler/backend/.env", override=False)


def _build_access_token(user_id: str, email: str) -> str:
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise RuntimeError("JWT_SECRET_KEY is missing")
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


async def _schema_summary() -> dict:
    # Import after env is loaded so app settings pick correct values.
    from app.core.database import engine

    async with engine.connect() as conn:
        table_exists = await conn.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema='public' AND table_name='job_queue'
                )
                """
            )
        )

        cols = await conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema='public' AND table_name='job_queue'
                ORDER BY ordinal_position
                """
            )
        )
        indexes = await conn.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname='public' AND tablename='job_queue'
                ORDER BY indexname
                """
            )
        )
        enums = await conn.execute(
            text(
                """
                SELECT t.typname
                FROM pg_type t
                JOIN pg_namespace n ON n.oid = t.typnamespace
                WHERE n.nspname='public' AND t.typname IN ('job_type', 'job_status')
                ORDER BY t.typname
                """
            )
        )

    await engine.dispose()

    return {
        "job_queue_table_exists": bool(table_exists),
        "job_queue_columns": [r[0] for r in cols.fetchall()],
        "job_queue_indexes": [r[0] for r in indexes.fetchall()],
        "enum_types": [r[0] for r in enums.fetchall()],
    }


def _require_arg(name: str, value: str | None) -> str:
    if not value:
        raise ValueError(f"Missing required argument: --{name}")
    return value


def _print_json(title: str, payload: dict | list) -> None:
    print(f"\n{title}")
    print(json.dumps(payload, indent=2, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue-mode smoke check")
    parser.add_argument("--api-url", default="http://127.0.0.1:8010")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--scheduled-post-id", required=True)
    parser.add_argument("--published-post-id", required=True)
    args = parser.parse_args()

    _load_env()

    # Ensure backend package import works regardless invocation directory.
    backend_root = "/Users/utkarshlanjewar/Desktop/Post-Scheduler/backend"
    if backend_root not in os.sys.path:
        os.sys.path.insert(0, backend_root)

    schema = asyncio.run(_schema_summary())
    _print_json("schema_summary", schema)

    token = _build_access_token(_require_arg("user-id", args.user_id), _require_arg("email", args.email))
    headers = {"Authorization": f"Bearer {token}"}

    with httpx.Client(timeout=20) as client:
        health = client.get(f"{args.api_url}/health")
        queue_stats = client.get(f"{args.api_url}/jobs/queue-stats", headers=headers)
        scheduled_jobs = client.get(
            f"{args.api_url}/jobs/posts/{args.scheduled_post_id}",
            headers=headers,
        )
        published_jobs = client.get(
            f"{args.api_url}/jobs/posts/{args.published_post_id}",
            headers=headers,
        )

    summary = {
        "health_status": health.status_code,
        "queue_stats_status": queue_stats.status_code,
        "scheduled_jobs_status": scheduled_jobs.status_code,
        "published_jobs_status": published_jobs.status_code,
    }
    _print_json("api_status_summary", summary)

    try:
        _print_json("queue_stats", queue_stats.json())
    except Exception:
        print("\nqueue_stats\n", queue_stats.text)

    try:
        _print_json("scheduled_post_jobs", scheduled_jobs.json())
    except Exception:
        print("\nscheduled_post_jobs\n", scheduled_jobs.text)

    try:
        _print_json("published_post_jobs", published_jobs.json())
    except Exception:
        print("\npublished_post_jobs\n", published_jobs.text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
