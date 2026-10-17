from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from muse_orchestrator.core.database import get_db
from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.models.domain import Project, ProjectRevision, Asset, AuditEvent

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


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateIn,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(verify_single_admin),
) -> ProjectOut:
    pid = f"proj_{uuid.uuid4().hex[:10]}"
    proj = Project(
        id=pid,
        name=payload.name,
        description=payload.description,
        aspect_ratio=payload.aspect_ratio,
        fps=payload.fps,
        style_preset=payload.style_preset,
        language=payload.language,
        output_profile=payload.output_profile,
        current_revision_num=1,
    )
    rev = ProjectRevision(
        id=f"rev_{uuid.uuid4().hex[:10]}",
        project_id=pid,
        revision_number=1,
        commit_message="Initial project snapshot",
        snapshot_json=payload.model_dump(),
    )
    audit = AuditEvent(
        id=f"aud_{uuid.uuid4().hex[:10]}",
        actor=actor,
        action="project.create",
        target_type="project",
        target_id=pid,
        details_redacted={"name": payload.name},
    )
    db.add_all([proj, rev, audit])
    await db.commit()
    await db.refresh(proj)
    return ProjectOut(
        id=proj.id,
        name=proj.name,
        description=proj.description,
        aspect_ratio=proj.aspect_ratio,
        fps=proj.fps,
        style_preset=proj.style_preset,
        language=proj.language,
        output_profile=proj.output_profile,
        current_revision_num=proj.current_revision_num,
        asset_count=0,
        created_at=proj.created_at,
        updated_at=proj.updated_at,
    )


@router.get("", response_model=list[ProjectOut])
async def list_projects(db: AsyncSession = Depends(get_db)) -> list[ProjectOut]:
    projects = (await db.execute(select(Project).order_by(Project.updated_at.desc()))).scalars().all()
    results: list[ProjectOut] = []
    for p in projects:
        asset_count = (await db.execute(select(func.count(Asset.id)).where(Asset.project_id == p.id))).scalar_one()
        results.append(
            ProjectOut(
                id=p.id,
                name=p.name,
                description=p.description,
                aspect_ratio=p.aspect_ratio,
                fps=p.fps,
                style_preset=p.style_preset,
                language=p.language,
                output_profile=p.output_profile,
                current_revision_num=p.current_revision_num,
                asset_count=asset_count,
                created_at=p.created_at,
                updated_at=p.updated_at,
            )
        )
    return results


@router.post("/{project_id}/duplicate", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def duplicate_project(project_id: str, db: AsyncSession = Depends(get_db)) -> ProjectOut:
    orig = await db.get(Project, project_id)
    if not orig:
        raise HTTPException(status_code=404, detail="Không tìm thấy dự án gốc.")
    new_id = f"proj_{uuid.uuid4().hex[:10]}"
    cloned = Project(
        id=new_id,
        name=f"{orig.name} (Copy)",
        description=orig.description,
        aspect_ratio=orig.aspect_ratio,
        fps=orig.fps,
        style_preset=orig.style_preset,
        language=orig.language,
        output_profile=orig.output_profile,
        current_revision_num=1,
    )
    rev = ProjectRevision(
        id=f"rev_{uuid.uuid4().hex[:10]}",
        project_id=new_id,
        revision_number=1,
        commit_message=f"Duplicated from {orig.id}",
        snapshot_json={"duplicated_from": orig.id},
    )
    db.add_all([cloned, rev])
    await db.commit()
    await db.refresh(cloned)
    return ProjectOut(
        id=cloned.id,
        name=cloned.name,
        description=cloned.description,
        aspect_ratio=cloned.aspect_ratio,
        fps=cloned.fps,
        style_preset=cloned.style_preset,
        language=cloned.language,
        output_profile=cloned.output_profile,
        current_revision_num=cloned.current_revision_num,
        asset_count=0,
        created_at=cloned.created_at,
        updated_at=cloned.updated_at,
    )


@router.get("/{project_id}/revisions", response_model=list[RevisionOut])
async def list_project_revisions(project_id: str, db: AsyncSession = Depends(get_db)) -> list[RevisionOut]:
    stmt = (
        select(ProjectRevision)
        .where(ProjectRevision.project_id == project_id)
        .order_by(ProjectRevision.revision_number.desc())
    )
    revs = (await db.execute(stmt)).scalars().all()
    return [
        RevisionOut(
            id=r.id,
            project_id=r.project_id,
            revision_number=r.revision_number,
            commit_message=r.commit_message,
            snapshot_json=r.snapshot_json,
            created_at=r.created_at,
        )
        for r in revs
    ]
