import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.connected_account import ConnectedAccount
from app.dependencies.auth import get_current_user
from app.services import x_oauth
from app.models.post import Post, PostStatus
from app.models.user import User
from app.schemas.post import PostActionResult, PostCreate, PostQuoteCreate, PostRead, PostUpdate
from app.services.storage_r2 import build_public_url, create_download_url
from app.services.x_api import XApiService
from app.core.security import decrypt_token, encrypt_token

router = APIRouter(prefix="/posts", tags=["posts"])
_TOKEN_REFRESH_BUFFER = timedelta(minutes=5)


async def _get_valid_connected_account(
    account_id: uuid.UUID | None,
    current_user: User,
    db: AsyncSession,
    platform: str,
) -> uuid.UUID | None:
    if account_id is None:
        return None

    account = await db.get(ConnectedAccount, account_id)
    if not account or account.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connected account not found")
    if account.platform != platform:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Connected account platform does not match post platform",
        )
    return account.id


def _validate_post_media(media: list[dict] | None, current_user: User) -> list[dict] | None:
    if media is None:
        return None

    validated_media: list[dict] = []
    expected_prefix = f"{current_user.id}/"
    for item in media:
        key = item["key"]
        public_url = item["public_url"]
        if not key.startswith(expected_prefix):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Media key does not belong to the current user",
            )
        if public_url != build_public_url(key):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Media public URL does not match the storage key",
            )
        validated_media.append(item)
    return validated_media


async def _upload_media_for_quote(
    media: list[dict] | None,
    api: XApiService,
) -> list[str] | None:
    if not media:
        return None

    media_ids: list[str] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
        for item in media:
            file_key = item.get("key")
            if not file_key:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Media key is required for quote uploads",
                )

            source_url = create_download_url(file_key)
            response = await client.get(source_url)
            response.raise_for_status()

            content_type = item.get("content_type") or response.headers.get("Content-Type")
            if not content_type:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Could not determine media content type",
                )

            media_id = await api.upload_media(response.content, content_type)
            media_ids.append(media_id)

    return media_ids or None


async def _get_valid_access_token(account: ConnectedAccount, db: AsyncSession) -> str:
    """Return access token and refresh it when close to expiry."""
    now = datetime.now(timezone.utc)
    if account.token_expires_at and account.token_expires_at <= now + _TOKEN_REFRESH_BUFFER:
        if not account.refresh_token_enc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="X access token expired. Reconnect your X account.",
            )

        refresh_token = decrypt_token(account.refresh_token_enc)
        token_data = await x_oauth.refresh_access_token(refresh_token)

        account.access_token_enc = encrypt_token(token_data["access_token"])
        if token_data.get("refresh_token"):
            account.refresh_token_enc = encrypt_token(token_data["refresh_token"])
        if token_data.get("expires_in"):
            account.token_expires_at = now + timedelta(seconds=token_data["expires_in"])
        if token_data.get("scope"):
            account.scopes = token_data["scope"]
        await db.flush()

    return decrypt_token(account.access_token_enc)


def _raise_mapped_x_error(exc: httpx.HTTPStatusError) -> None:
    """Map upstream X API failures to client-safe HTTP errors."""
    status_code = exc.response.status_code
    detail = str(exc)
    if status_code == status.HTTP_401_UNAUTHORIZED:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X authorization failed. Reconnect your X account and try again.",
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=detail,
    )


@router.post("", response_model=PostRead, status_code=status.HTTP_201_CREATED)
async def create_post(
    payload: PostCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Post:
    connected_account_id = await _get_valid_connected_account(
        payload.connected_account_id,
        current_user,
        db,
        payload.platform,
    )

    post = Post(
        user_id=current_user.id,
        connected_account_id=connected_account_id,
        platform=payload.platform,
        content=payload.content,
        status=PostStatus.scheduled if payload.scheduled_for else PostStatus.draft,
        scheduled_for=payload.scheduled_for,
        thread_id=payload.thread_id,
        thread_order=payload.thread_order,
        media=_validate_post_media(payload.model_dump().get("media"), current_user),
        media_keys=[item.key for item in payload.media] if payload.media else None,
    )
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return post


@router.get("", response_model=list[PostRead])
async def list_posts(
    skip: int = 0,
    limit: int = 50,
    include_deleted: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Post]:
    filters = [Post.user_id == current_user.id]
    if not include_deleted:
        filters.append(Post.is_deleted.is_(False))

    result = await db.execute(
        select(Post)
        .where(*filters)
        .order_by(Post.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


@router.get("/by-platform-id/{platform_post_id}", response_model=PostRead)
async def get_post_by_platform_id(
    platform_post_id: str,
    include_deleted: bool = True,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Post:
    filters = [
        Post.user_id == current_user.id,
        Post.platform_post_id == platform_post_id,
    ]
    if not include_deleted:
        filters.append(Post.is_deleted.is_(False))

    result = await db.execute(
        select(Post)
        .where(*filters)
        .order_by(Post.created_at.desc())
        .limit(1)
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    return post


@router.get("/{post_id}", response_model=PostRead)
async def get_post(
    post_id: uuid.UUID,
    include_deleted: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Post:
    post = await db.get(Post, post_id)
    if not post or post.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    if post.is_deleted and not include_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    return post


@router.patch("/{post_id}", response_model=PostRead)
async def update_post(
    post_id: uuid.UUID,
    payload: PostUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Post:
    post = await db.get(Post, post_id)
    if not post or post.user_id != current_user.id or post.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    update_data = payload.model_dump(exclude_unset=True)
    if "media" in update_data:
        update_data["media"] = _validate_post_media(update_data["media"], current_user)
        update_data["media_keys"] = [item["key"] for item in update_data["media"]] if update_data["media"] else None

    platform = update_data.get("platform", post.platform)
    if "connected_account_id" in update_data:
        update_data["connected_account_id"] = await _get_valid_connected_account(
            update_data["connected_account_id"],
            current_user,
            db,
            platform,
        )

    for key, value in update_data.items():
        setattr(post, key, value)

    await db.commit()
    await db.refresh(post)
    return post


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    post = await db.get(Post, post_id)
    if not post or post.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    # Strict remote-first delete for published X posts.
    # If X delete fails, do not soft-delete locally.
    should_delete_on_x = (
        post.status == PostStatus.published
        and post.platform == "x"
        and bool(post.platform_post_id)
        and bool(post.connected_account_id)
        and not post.is_deleted
    )
    if should_delete_on_x:
        account = await db.get(ConnectedAccount, post.connected_account_id)
        if not account or account.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Connected account not found for deleting post on X",
            )
        try:
            access_token = await _get_valid_access_token(account, db)
            api = XApiService(access_token=access_token)
            await api.delete_post(post.platform_post_id)
        except httpx.HTTPStatusError as exc:
            _raise_mapped_x_error(exc)

    if not post.is_deleted:
        post.is_deleted = True
        post.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{post_id}/repost", response_model=PostActionResult)
async def repost_post(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PostActionResult:
    post = await db.get(Post, post_id)
    if not post or post.user_id != current_user.id or post.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    if post.platform != "x" or not post.platform_post_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only published X posts can be reposted",
        )
    if not post.connected_account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Post is missing connected account")

    account = await db.get(ConnectedAccount, post.connected_account_id)
    if not account or account.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Connected account not found")

    try:
        access_token = await _get_valid_access_token(account, db)
        api = XApiService(access_token=access_token)
        if post.reposted_at is None:
            await api.repost(account.platform_user_id, post.platform_post_id)
            post.reposted_at = datetime.now(timezone.utc)
            message = "Reposted successfully"
        else:
            await api.undo_repost(account.platform_user_id, post.platform_post_id)
            post.reposted_at = None
            message = "Repost removed successfully"
    except httpx.HTTPStatusError as exc:
        _raise_mapped_x_error(exc)

    await db.commit()
    return PostActionResult(message=message, post_id=post.id, platform_post_id=post.platform_post_id)


@router.post("/{post_id}/quote", response_model=PostRead)
async def quote_post(
    post_id: uuid.UUID,
    payload: PostQuoteCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Post:
    source_post = await db.get(Post, post_id)
    if not source_post or source_post.user_id != current_user.id or source_post.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    if source_post.platform != "x" or not source_post.platform_post_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only published X posts can be quoted",
        )

    connected_account_id = await _get_valid_connected_account(
        payload.connected_account_id or source_post.connected_account_id,
        current_user,
        db,
        "x",
    )
    if not connected_account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Connected account is required")

    validated_media = _validate_post_media(payload.model_dump().get("media"), current_user)

    account = await db.get(ConnectedAccount, connected_account_id)
    if not account:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Connected account not found")

    quoted_post = Post(
        user_id=current_user.id,
        connected_account_id=connected_account_id,
        platform="x",
        content=payload.content,
        status=PostStatus.scheduled if payload.scheduled_for else PostStatus.published,
        scheduled_for=payload.scheduled_for,
        published_at=None,
        platform_post_id=None,
        quote_of_platform_post_id=source_post.platform_post_id,
        media=validated_media,
        media_keys=[item["key"] for item in validated_media] if validated_media else None,
    )

    if not payload.scheduled_for:
        try:
            access_token = await _get_valid_access_token(account, db)
            api = XApiService(access_token=access_token)
            media_ids = await _upload_media_for_quote(validated_media, api)
            quoted_post.platform_post_id = await api.create_quote(
                payload.content,
                source_post.platform_post_id,
                media_ids,
            )
            quoted_post.published_at = datetime.now(timezone.utc)
        except httpx.HTTPStatusError as exc:
            _raise_mapped_x_error(exc)

    db.add(quoted_post)
    await db.commit()
    await db.refresh(quoted_post)
    return quoted_post
