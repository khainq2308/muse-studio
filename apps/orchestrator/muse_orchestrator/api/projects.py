from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from muse_orchestrator.api.dependencies import get_project_service
from muse_orchestrator.application.services import ProjectService
from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.models.domain import Project, ProjectRevision

router = APIRouter(prefix="/api/v1/projects", tags=["Projects"])


class ProjectCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=2000)
    aspect_ratio: str = Field(default="16:9", pattern=r"^(16:9|9:16|1:1|21:9)$")
    fps: int = Field(default=24, ge=12, le=60)
    style_preset: str = Field(default="2d_anime_cinematic")
    language: str = Field(default="vi")
    output_profile: str = Field(default="1080p_h264")


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str
    aspect_ratio: str
    fps: int
    style_preset: str
    language: str
    output_profile: str
    current_revision_num: int
    asset_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RevisionOut(BaseModel):
    id: str
    project_id: str
    revision_number: int
    commit_message: str
    snapshot_json: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


def _project_out(project: Project, asset_count: int = 0) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        aspect_ratio=project.aspect_ratio,
        fps=project.fps,
        style_preset=project.style_preset,
        language=project.language,
        output_profile=project.output_profile,
        current_revision_num=project.current_revision_num,
        asset_count=asset_count,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateIn,
    service: ProjectService = Depends(get_project_service),
    actor: str = Depends(verify_single_admin),
) -> ProjectOut:
    project = await service.create_project(payload.model_dump(), actor)
    return _project_out(project)


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    service: ProjectService = Depends(get_project_service),
) -> list[ProjectOut]:
    return [_project_out(project, asset_count) for project, asset_count in await service.list_projects()]


@router.post("/{project_id}/duplicate", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def duplicate_project(
    project_id: str,
    service: ProjectService = Depends(get_project_service),
) -> ProjectOut:
    return _project_out(await service.duplicate_project(project_id))


@router.get("/{project_id}/revisions", response_model=list[RevisionOut])
async def list_project_revisions(
    project_id: str,
    service: ProjectService = Depends(get_project_service),
) -> list[RevisionOut]:
    revisions: list[ProjectRevision] = await service.list_revisions(project_id)
    return [RevisionOut.model_validate(revision) for revision in revisions]
