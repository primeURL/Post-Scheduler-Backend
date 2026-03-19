from fastapi import APIRouter, Depends

from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.storage import UploadUrlRequest, UploadUrlResponse
from app.services.storage_r2 import create_upload_url

router = APIRouter(prefix="/storage", tags=["storage"])


@router.post("/upload-url", response_model=UploadUrlResponse)
async def get_upload_url(
    payload: UploadUrlRequest,
    current_user: User = Depends(get_current_user),
) -> UploadUrlResponse:
    upload = create_upload_url(current_user.id, payload.file_name, payload.content_type)
    return UploadUrlResponse(user_id=current_user.id, **upload)
