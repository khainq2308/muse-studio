from __future__ import annotations

import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from muse_orchestrator.core.config import settings
from muse_orchestrator.core.database import get_db
from muse_orchestrator.models.domain import Asset, Project
from muse_orchestrator.services.asset_storage import LocalAssetStorage, AssetStorageError

router = APIRouter(prefix="/api/v1/assets", tags=["Assets"])
storage = LocalAssetStorage(settings.media_root)


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


def _to_asset_out(a: Asset) -> AssetOut:
    return AssetOut(
        id=a.id,
        project_id=a.project_id,
        asset_type=a.asset_type,
        filename=a.filename,
        mime_type=a.mime_type,
        size_bytes=a.size_bytes,
        sha256=a.sha256,
        is_locked_reference=a.is_locked_reference,
        created_at=a.created_at,
    )


@router.post("/upload", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    project_id: str = Form(...),
    asset_type: str = Form(default="reference"),
    is_locked_reference: bool = Form(default=False),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> AssetOut:
    proj = await db.get(Project, project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project không tồn tại.")

    raw_bytes = await file.read()
    try:
        saved = storage.save_bytes(project_id, file.filename or "upload.bin", raw_bytes, file.content_type or "")
    except AssetStorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    asset = Asset(
        id=f"ast_{uuid.uuid4().hex[:10]}",
        project_id=project_id,
        asset_type=asset_type,
        filename=saved.safe_filename,
        storage_rel_path=saved.storage_rel_path,
        mime_type=saved.detected_mime,
        size_bytes=saved.size_bytes,
        sha256=saved.sha256,
        is_locked_reference=is_locked_reference,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return _to_asset_out(asset)


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(asset_id: str, force: bool = False, db: AsyncSession = Depends(get_db)) -> None:
    asset = await db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset không tồn tại.")
    if asset.is_locked_reference and not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Asset đang được khoá làm Reference.",
        )
    await db.delete(asset)
    await db.commit()
