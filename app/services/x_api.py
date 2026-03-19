"""X API v2 service — posting, media upload, and analytics."""
from __future__ import annotations

import asyncio

import httpx

from app.core.config import settings

_V2_BASE = "https://api.x.com/2"
_MEDIA_UPLOAD = f"{_V2_BASE}/media/upload"
_UPLOAD_CHUNK_SIZE = 1024 * 1024
_MAX_STATUS_POLLS = 30


class XApiService:
    """Async wrapper around X API v2.

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
        """Upload media and return the media ID used by create_post/create_reply."""
        media_category = _media_category_for_type(mime_type)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                _MEDIA_UPLOAD,
                headers=self._auth_header,
                data={"media_category": media_category},
                files={"media": ("upload", file_bytes, mime_type)},
            )
            _raise_for_x_status(resp)
            media_id = _extract_media_id(resp.json())

            processing_info = _extract_processing_info(resp.json())
            if processing_info:
                await self._wait_for_media_processing(client, media_id, processing_info)
            return media_id

    async def _wait_for_media_processing(
        self,
        client: httpx.AsyncClient,
        media_id: str,
        processing_info: dict,
    ) -> None:
        info = processing_info
        for _ in range(_MAX_STATUS_POLLS):
            state = info.get("state")
            if state == "succeeded":
                return
            if state == "failed":
                error = info.get("error") or {}
                message = error.get("message") or "Media processing failed"
                raise RuntimeError(message)

            await asyncio.sleep(max(int(info.get("check_after_secs", 1)), 1))
            status_resp = await client.get(
                _MEDIA_UPLOAD,
                headers=self._auth_header,
                params={
                    "command": "STATUS",
                    "media_id": media_id,
                },
            )
            _raise_for_x_status(status_resp)
            info = status_resp.json()["data"].get("processing_info") or {"state": "succeeded"}

        raise RuntimeError("Timed out while waiting for X media processing")

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


def _media_category_for_type(mime_type: str) -> str:
    normalized = mime_type.lower()
    if normalized == "image/gif":
        return "tweet_gif"
    if normalized.startswith("video/"):
        return "tweet_video"
    return "tweet_image"


def _extract_media_id(payload: dict) -> str:
    """Support both v2 and compatibility response shapes."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        if data.get("id"):
            return str(data["id"])
        if data.get("media_id"):
            return str(data["media_id"])
    if isinstance(payload, dict):
        if payload.get("media_id_string"):
            return str(payload["media_id_string"])
        if payload.get("media_id"):
            return str(payload["media_id"])
    raise RuntimeError(f"X media upload response missing media id: {payload}")


def _extract_processing_info(payload: dict) -> dict | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        info = data.get("processing_info")
        if isinstance(info, dict):
            return info
    if isinstance(payload, dict):
        info = payload.get("processing_info")
        if isinstance(info, dict):
            return info
    return None
