from __future__ import annotations

import mimetypes
import posixpath
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.client import Config
from fastapi import HTTPException, status

from app.core.config import settings

_ALLOWED_CONTENT_PREFIXES = ("image/", "video/")
_ALLOWED_CONTENT_TYPES = {"image/gif"}


def _get_r2_client():
    if not (
        settings.r2_account_id
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
        and settings.r2_bucket_name
        and settings.r2_public_base_url
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="R2 storage is not configured",
        )

    endpoint_url = f"https://{settings.r2_account_id}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def _normalize_extension(file_name: str, content_type: str) -> str:
    suffix = Path(file_name).suffix
    if suffix:
        return suffix.lower()
    guessed = mimetypes.guess_extension(content_type)
    return guessed or ""


def validate_media_type(content_type: str) -> str:
    normalized = content_type.strip().lower()
    if normalized in _ALLOWED_CONTENT_TYPES or normalized.startswith(_ALLOWED_CONTENT_PREFIXES):
        return normalized
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Only image, gif, and video uploads are supported",
    )


def build_file_key(user_id: uuid.UUID, file_name: str, content_type: str) -> str:
    extension = _normalize_extension(file_name, content_type)
    today = datetime.now(timezone.utc)
    return posixpath.join(
        str(user_id),
        f"{today:%Y}",
        f"{today:%m}",
        f"{today:%d}",
        f"{uuid.uuid4()}{extension}",
    )


def build_public_url(file_key: str) -> str:
    return f"{settings.r2_public_base_url.rstrip('/')}/{file_key}"


def create_upload_url(user_id: uuid.UUID, file_name: str, content_type: str) -> dict[str, str | int]:
    validated_content_type = validate_media_type(content_type)
    file_key = build_file_key(user_id, file_name, validated_content_type)
    client = _get_r2_client()
    upload_url = client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.r2_bucket_name,
            "Key": file_key,
            "ContentType": validated_content_type,
        },
        ExpiresIn=settings.r2_upload_url_expiry_seconds,
        HttpMethod="PUT",
    )
    return {
        "upload_url": upload_url,
        "public_url": build_public_url(file_key),
        "file_key": file_key,
        "content_type": validated_content_type,
        "expires_in": settings.r2_upload_url_expiry_seconds,
    }


def create_download_url(file_key: str, expires_in: int | None = None) -> str:
    """Create a temporary signed URL for reading an object from R2."""
    client = _get_r2_client()
    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.r2_bucket_name,
            "Key": file_key,
        },
        ExpiresIn=expires_in or settings.r2_upload_url_expiry_seconds,
        HttpMethod="GET",
    )
