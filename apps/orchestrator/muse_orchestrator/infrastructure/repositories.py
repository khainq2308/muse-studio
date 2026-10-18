from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.models.domain import (
    Asset,
    AuditEvent,
    Project,
    ProjectRevision,
    ProviderConnection,
)


class StudioRepository:
    """Persistence adapter. SQLAlchemy queries and transaction boundaries stay here."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_project_bundle(
        self,
        project: Project,
        revision: ProjectRevision,
        audit_event: AuditEvent | None = None,
    ) -> Project:
        self.session.add_all([project, revision])
        if audit_event is not None:
            self.session.add(audit_event)
        await self.session.commit()
        await self.session.refresh(project)
        return project

    async def list_projects_with_asset_counts(self) -> list[tuple[Project, int]]:
        projects = (
            await self.session.execute(select(Project).order_by(Project.updated_at.desc()))
        ).scalars().all()
        counts_result = await self.session.execute(
            select(Asset.project_id, func.count(Asset.id)).group_by(Asset.project_id)
        )
        counts = {project_id: count for project_id, count in counts_result.all()}
        return [(project, int(counts.get(project.id, 0))) for project in projects]

    async def get_project(self, project_id: str) -> Project | None:
        return await self.session.get(Project, project_id)

    async def list_project_revisions(self, project_id: str) -> list[ProjectRevision]:
        result = await self.session.execute(
            select(ProjectRevision)
            .where(ProjectRevision.project_id == project_id)
            .order_by(ProjectRevision.revision_number.desc())
        )
        return list(result.scalars().all())

    async def create_connection(self, connection: ProviderConnection) -> ProviderConnection:
        self.session.add(connection)
        await self.session.commit()
        await self.session.refresh(connection)
        return connection

    async def list_connections(self) -> list[ProviderConnection]:
        result = await self.session.execute(
            select(ProviderConnection).order_by(ProviderConnection.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_connection(self, connection_id: str) -> ProviderConnection | None:
        return await self.session.get(ProviderConnection, connection_id)

    async def save_connection(self, connection: ProviderConnection) -> ProviderConnection:
        self.session.add(connection)
        await self.session.commit()
        await self.session.refresh(connection)
        return connection

    async def get_asset(self, asset_id: str) -> Asset | None:
        return await self.session.get(Asset, asset_id)

    async def create_asset(self, asset: Asset) -> Asset:
        self.session.add(asset)
        await self.session.commit()
        await self.session.refresh(asset)
        return asset

    async def has_other_asset_with_storage_path(self, storage_rel_path: str, asset_id: str) -> bool:
        result = await self.session.execute(
            select(Asset.id)
            .where(Asset.storage_rel_path == storage_rel_path, Asset.id != asset_id)
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def delete_asset(self, asset: Asset) -> None:
        await self.session.delete(asset)
        await self.session.commit()
