import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from cryptography.fernet import Fernet

from app.core.config import settings

_ALGORITHM = "HS256"


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(user_id: uuid.UUID, email: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_access_expire_minutes)
    payload = {"sub": str(user_id), "email": email, "exp": exp}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and verify access token. Raises jwt.PyJWTError on failure."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[_ALGORITHM])


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------

def generate_refresh_token() -> str:
    """Return a 64-char cryptographically random hex token."""
    return secrets.token_hex(32)


def hash_token(token: str) -> str:
    """SHA-256 hash of a random token (fast — token is already high-entropy)."""
    return hashlib.sha256(token.encode()).hexdigest()


def tokens_match(token: str, stored_hash: str) -> bool:
    return secrets.compare_digest(hash_token(token), stored_hash)


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Fernet encryption for stored OAuth tokens
# ---------------------------------------------------------------------------

def _fernet() -> Fernet:
    return Fernet(settings.fernet_key.encode())


def encrypt_token(value: str) -> bytes:
    return _fernet().encrypt(value.encode())


def decrypt_token(value: bytes) -> str:
    return _fernet().decrypt(value).decode()
