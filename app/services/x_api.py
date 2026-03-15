"""X API v2 service — posting, thread replies, media upload (v1.1), analytics."""
from __future__ import annotations

import httpx

from app.core.config import settings

_V2_BASE = "https://api.twitter.com/2"
_V1_UPLOAD = "https://upload.twitter.com/1.1/media/upload.json"


class XApiService:
    """Async wrapper around X API v2 (v1.1 for media upload).

    Two ways to instantiate:
    - ``XApiService(user_access_token)`` — user context; can post + read all metrics.
    - ``XApiService.app_only()``          — app-level bearer token; public metrics only.
    """

    def __init__(self, access_token: str) -> None:
        self._auth_header = {"Authorization": f"Bearer {access_token}"}

    @classmethod
    def app_only(cls) -> "XApiService":
        """Return an instance authenticated with the app-level bearer token.

        Suitable for reading public tweet metrics without a per-user access token.
        Raises ``RuntimeError`` when ``X_BEARER_TOKEN`` is not configured.
        """
        token = settings.x_bearer_token
        if not token:
            raise RuntimeError("X_BEARER_TOKEN is not set — cannot use app-only auth.")
        return cls(token)

    async def create_post(
        self,
        content: str,
        media_ids: list[str] | None = None,
    ) -> str:
        """Create a tweet.  Returns the tweet ID."""
        body: dict = {"text": content}
        if media_ids:
            body["media"] = {"media_ids": media_ids}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_V2_BASE}/tweets",
                json=body,
                headers=self._auth_header,
            )
            _raise_for_x_status(resp)
            return resp.json()["data"]["id"]

    async def create_reply(
        self,
        content: str,
        reply_to_id: str,
        media_ids: list[str] | None = None,
    ) -> str:
        """Reply to a tweet (for thread chaining).  Returns the new tweet ID."""
        body: dict = {
            "text": content,
            "reply": {"in_reply_to_tweet_id": reply_to_id},
        }
        if media_ids:
            body["media"] = {"media_ids": media_ids}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_V2_BASE}/tweets",
                json=body,
                headers=self._auth_header,
            )
            _raise_for_x_status(resp)
            return resp.json()["data"]["id"]

    async def upload_media(self, file_bytes: bytes, mime_type: str) -> str:
        """Upload media via the v1.1 endpoint.  Returns the ``media_id_string``."""
        # multipart/form-data — exclude Content-Type; httpx sets it automatically.
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                _V1_UPLOAD,
                headers=self._auth_header,
                files={"media": (None, file_bytes, mime_type)},
            )
            _raise_for_x_status(resp)
            return resp.json()["media_id_string"]

    async def get_tweet_metrics(self, tweet_id: str) -> dict:
        """Return engagement metrics for a tweet.

        Requested fields:
        - ``public_metrics``: likes, retweets, replies (always available).
        - ``non_public_metrics``: impressions, clicks, profile_visits
          (requires the tweet owner's user access token; silently absent
          when using the app-only bearer token).

        Returns a dict whose keys match ``PostAnalytics`` columns:
        ``impressions``, ``likes``, ``retweets``, ``replies``,
        ``clicks``, ``profile_visits``.
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_V2_BASE}/tweets/{tweet_id}",
                params={"tweet.fields": "public_metrics,non_public_metrics"},
                headers=self._auth_header,
            )
            _raise_for_x_status(resp)
            data = resp.json()["data"]

        pub = data.get("public_metrics") or {}
        priv = data.get("non_public_metrics") or {}

        return {
            # impression_count can appear in either field depending on access tier
            "impressions": priv.get("impression_count") or pub.get("impression_count", 0),
            "likes": pub.get("like_count", 0),
            "retweets": pub.get("retweet_count", 0),
            "replies": pub.get("reply_count", 0),
            "clicks": priv.get("url_link_clicks", 0),
            "profile_visits": priv.get("user_profile_clicks", 0),
        }


def _raise_for_x_status(resp: httpx.Response) -> None:
    """Raise ``HTTPStatusError`` with X API error detail when the call fails."""
    if resp.is_error:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise httpx.HTTPStatusError(
            f"X API {resp.status_code}: {detail}",
            request=resp.request,
            response=resp,
        )
