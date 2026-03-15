# Post Scheduler Backend

FastAPI backend with SQLAlchemy and a modular app layout.

## Structure

- `app/core`: configuration and app settings
- `app/db`: database base, session, and initialization
- `app/models`: SQLAlchemy models
- `app/schemas`: request/response schemas
- `app/routes`: API route modules
- `app/utils`: utility helpers

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create `.env` from `.env.example` and adjust values if needed.
4. Run the API server:

```bash
uvicorn app.main:app --reload
```

## API Endpoints

- `GET /health`
- `POST /posts`
- `GET /posts`
- `GET /posts/{post_id}`
- `PATCH /posts/{post_id}`
- `DELETE /posts/{post_id}`

## Notes

- The default DB is SQLite (`post_scheduler.db`) created automatically on startup.
- Tables are created automatically on app startup via `init_db()`.
