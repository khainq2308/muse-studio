# Endpoint template

```python
# api/shots.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from muse_orchestrator.api.dependencies import get_shot_service
from muse_orchestrator.application.shot_service import ShotService

router = APIRouter(prefix="/api/v1/projects/{project_id}/shots", tags=["Shots"])


class ShotListItemOut(BaseModel):
    id: str
    title: str
    approval_state: str
    generation_state: str
    model_config = {"from_attributes": True}


@router.get("", response_model=list[ShotListItemOut])
async def list_shots(
    project_id: str,
    approval_state: str | None = Query(default=None, pattern=r"^(draft|needs_review|approved|rejected)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: ShotService = Depends(get_shot_service),
) -> list[ShotListItemOut]:
    shots = await service.list_shots(project_id, approval_state, limit, offset)
    return [ShotListItemOut.model_validate(s) for s in shots]
```

```python
# application/shot_service.py
class ShotService:
    def __init__(self, repository: StudioRepository) -> None:
        self.repository = repository

    async def list_shots(self, project_id: str, approval_state: str | None, limit: int, offset: int):
        if await self.repository.get_project(project_id) is None:
            raise ApplicationError(404, "Project not found.")
        return await self.repository.list_shots(project_id, approval_state, limit, offset)
```

```python
# infrastructure/repositories.py (method on StudioRepository)
async def list_shots(self, project_id: str, approval_state: str | None, limit: int, offset: int):
    stmt = select(ShotRecord).where(ShotRecord.project_id == project_id)
    if approval_state:
        stmt = stmt.where(ShotRecord.approval_state == approval_state)
    stmt = stmt.order_by(ShotRecord.created_at).limit(limit).offset(offset)
    return (await self.session.execute(stmt)).scalars().all()
```

Check the real column names in `models/domain.py` before copying — the names above are illustrative.
