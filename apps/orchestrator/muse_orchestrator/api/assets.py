from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from muse_orchestrator.api.dependencies import get_asset_service
from muse_orchestrator.application.services import AssetService
from muse_orchestrator.models.domain import Asset
from muse_orchestrator.services.asset_storage import MAX_UPLOAD_SIZE_BYTES

router = APIRouter(prefix="/api/v1/assets", tags=["Assets"])


class AssetOut(BaseModel):
    id: str
    project_id: str
    asset_type: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    is_locked_reference: bool
    created_at: datetime

    model_config = {"from_attributes": True}


def _to_asset_out(asset: Asset) -> AssetOut:
    return AssetOut(
        id=asset.id,
        project_id=asset.project_id,
        asset_type=asset.asset_type,
        filename=asset.filename,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        sha256=asset.sha256,
        is_locked_reference=asset.is_locked_reference,
        created_at=asset.created_at,
    )


@router.post("/upload", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    project_id: str = Form(...),
    asset_type: str = Form(default="reference"),
    is_locked_reference: bool = Form(default=False),
    file: UploadFile = File(...),
    service: AssetService = Depends(get_asset_service),
) -> AssetOut:
    # Read at most the configured limit plus one byte to avoid unbounded memory use.
    raw_bytes = await file.read(MAX_UPLOAD_SIZE_BYTES + 1)
    asset = await service.upload_asset(
        project_id=project_id,
        filename=file.filename or "upload.bin",
        declared_mime=file.content_type or "",
        content=raw_bytes,
        asset_type=asset_type,
        is_locked_reference=is_locked_reference,
    )
    return _to_asset_out(asset)


@router.get(
    "/{asset_id}/download",
    response_class=FileResponse,
    responses={
        200: {
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            }
        }
    },
)
async def download_asset(
    asset_id: str,
    service: AssetService = Depends(get_asset_service),
) -> FileResponse:
    asset, path = await service.get_asset_file(asset_id)
    return FileResponse(
        path,
        media_type=asset.mime_type,
        filename=asset.filename,
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(
    asset_id: str,
    force: bool = False,
    service: AssetService = Depends(get_asset_service),
) -> None:
    await service.delete_asset(asset_id, force=force)
