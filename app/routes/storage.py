from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.storage import DownloadUrlResponse, UploadUrlRequest, UploadUrlResponse
from app.services.storage_r2 import create_download_url, create_upload_url

router = APIRouter(prefix="/storage", tags=["storage"])


@router.post("/upload-url", response_model=UploadUrlResponse)
async def get_upload_url(
    payload: UploadUrlRequest,
    current_user: User = Depends(get_current_user),
) -> UploadUrlResponse:
    upload = create_upload_url(current_user.id, payload.file_name, payload.content_type)
    return UploadUrlResponse(user_id=current_user.id, **upload)


@router.get("/download-url", response_model=DownloadUrlResponse)
async def get_download_url(
    file_key: str = Query(..., min_length=1),
    current_user: User = Depends(get_current_user),
) -> DownloadUrlResponse:
    expected_prefix = f"{current_user.id}/"
    if not file_key.startswith(expected_prefix):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Media key does not belong to current user",
        )

    return DownloadUrlResponse(
        download_url=create_download_url(file_key),
        file_key=file_key,
        expires_in=900,
    )
