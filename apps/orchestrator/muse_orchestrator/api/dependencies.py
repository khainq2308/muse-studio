from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.application.services import AssetService, ConnectionService, ProjectService
from muse_orchestrator.core.database import get_db
from muse_orchestrator.core.config import settings
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.services.asset_storage import LocalAssetStorage


async def get_studio_repository(
    db: AsyncSession = Depends(get_db),
) -> StudioRepository:
    return StudioRepository(db)


def get_project_service(
    repository: StudioRepository = Depends(get_studio_repository),
) -> ProjectService:
    return ProjectService(repository)


def get_connection_service(
    repository: StudioRepository = Depends(get_studio_repository),
) -> ConnectionService:
    return ConnectionService(repository)


def get_asset_service(
    repository: StudioRepository = Depends(get_studio_repository),
) -> AssetService:
    return AssetService(repository, LocalAssetStorage(settings.media_root))
