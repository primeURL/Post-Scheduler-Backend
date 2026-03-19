from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.firebase import init_firebase
from app.core.limiter import limiter
from app.core.redis import close_redis, init_redis
from app.core.scheduler import scheduler
from app.db.init_db import close_db, init_db
from app.jobs.analytics import fetch_published_analytics
from app.jobs.publisher import publish_due_posts, recover_stale_publishing
from app.routes.accounts import router as accounts_router
from app.routes.analytics import router as analytics_router
from app.routes.auth import router as auth_router
from app.routes.health import router as health_router
from app.routes.posts import router as posts_router
from app.routes.storage import router as storage_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_firebase()
    await init_db()
    await init_redis()
    scheduler.add_job(publish_due_posts, "interval", seconds=60, id="publisher")
    scheduler.add_job(recover_stale_publishing, "interval", minutes=10, id="stale_recovery")
    scheduler.add_job(fetch_published_analytics, "interval", hours=6, id="analytics_fetch")
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)
    await close_db()
    await close_redis()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
)

# --- Rate limiting ---
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    # Mirror any requesting origin in development so browser credentialed
    # requests don't get rejected by the wildcard CORS rule.
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routers ---
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(posts_router)
app.include_router(analytics_router)
app.include_router(storage_router)
