"""Firebase Admin SDK — token verification for Google sign-in."""
import asyncio

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials

from app.core.config import settings

_app: firebase_admin.App | None = None


def init_firebase() -> None:
    global _app
    if _app is not None:
        return
    cred = credentials.Certificate(settings.firebase_sa_path)
    _app = firebase_admin.initialize_app(cred)


async def verify_id_token(id_token: str) -> dict:
    """Verify a Firebase ID token and return the decoded claims."""
    return await asyncio.to_thread(firebase_auth.verify_id_token, id_token)
