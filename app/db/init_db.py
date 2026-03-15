import app.models  # noqa: F401 — registers all models with Base metadata

from app.core.database import engine
from app.db.base import Base


async def init_db() -> None:
    """Create all tables on startup (dev only — use Alembic for production)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose the async engine gracefully on shutdown."""
    await engine.dispose()
