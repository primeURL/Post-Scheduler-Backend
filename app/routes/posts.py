import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.connected_account import ConnectedAccount
from app.dependencies.auth import get_current_user
from app.models.post import Post, PostStatus
from app.models.user import User
from app.schemas.post import PostCreate, PostRead, PostUpdate
from app.services.storage_r2 import build_public_url

router = APIRouter(prefix="/posts", tags=["posts"])


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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Post]:
    result = await db.execute(
        select(Post)
        .where(Post.user_id == current_user.id)
        .order_by(Post.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


@router.get("/{post_id}", response_model=PostRead)
async def get_post(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Post:
    post = await db.get(Post, post_id)
    if not post or post.user_id != current_user.id:
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
    if not post or post.user_id != current_user.id:
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

    await db.delete(post)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
