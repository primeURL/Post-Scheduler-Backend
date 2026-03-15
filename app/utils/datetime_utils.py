from datetime import UTC, datetime

from fastapi import HTTPException, status


def ensure_future_datetime(value: datetime) -> None:
    candidate = value if value.tzinfo else value.replace(tzinfo=UTC)
    now = datetime.now(UTC)

    if candidate <= now:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scheduled_for must be a future datetime",
        )
