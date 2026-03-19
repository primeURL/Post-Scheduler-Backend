"""X OAuth 2.0 PKCE helpers for authorization, token exchange, and refresh."""
import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx

from app.core.config import settings

_AUTH_URL = "https://x.com/i/oauth2/authorize"
_TOKEN_URL = "https://api.x.com/2/oauth2/token"
_USERINFO_URL = "https://api.x.com/2/users/me"

# media.write is required to upload media before creating tweets with attachments.
SCOPES = "tweet.read tweet.write users.read offline.access media.write"


def generate_pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)``.

    - ``code_verifier``: 96 random bytes → 128-char URL-safe string.
    - ``code_challenge``: BASE64URL(SHA-256(code_verifier)), no padding.
    """
    code_verifier = secrets.token_urlsafe(96)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def build_authorization_url(state: str, code_challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": settings.x_client_id,
        "redirect_uri": settings.x_redirect_uri,
        "scope": SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "force_login": "true",
    }
    return _AUTH_URL + "?" + urlencode(params)


async def exchange_code(code: str, code_verifier: str) -> dict:
    """Exchange the authorization code + PKCE verifier for tokens."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.x_redirect_uri,
                "client_id": settings.x_client_id,
                "code_verifier": code_verifier,
            },
            auth=(settings.x_client_id, settings.x_client_secret),
        )
        resp.raise_for_status()
        return resp.json()


async def refresh_access_token(refresh_token: str) -> dict:
    """Exchange a refresh token for a fresh access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            auth=(settings.x_client_id, settings.x_client_secret),
        )
        resp.raise_for_status()
        return resp.json()


async def get_user_info(access_token: str) -> dict:
    """Return X user object with keys ``id``, ``name``, ``username``, ``subscription_type``, ``profile_image_url``."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _USERINFO_URL,
            params={"user.fields": "id,name,username,subscription_type,profile_image_url"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()["data"]
