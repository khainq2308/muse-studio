from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.models.domain import (
    Asset,
    AuditEvent,
    Project,
    ProjectRevision,
    ProviderConnection,
    BackupRecord,
    ConnectionAccount,
    StructuredLogRecord,
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

    # --- M7: connection accounts ---

    async def create_account(self, account: ConnectionAccount) -> ConnectionAccount:
        self.session.add(account)
        await self.session.commit()
        await self.session.refresh(account)
        return account

    async def list_accounts(self, connection_id: str) -> list[ConnectionAccount]:
        result = await self.session.execute(
            select(ConnectionAccount)
            .where(ConnectionAccount.connection_id == connection_id)
            .order_by(ConnectionAccount.priority.asc(), ConnectionAccount.created_at.asc())
        )
        return list(result.scalars().all())

    async def list_all_accounts(self) -> list[ConnectionAccount]:
        """M7: all connection accounts (for diagnostics)."""
        result = await self.session.execute(
            select(ConnectionAccount).order_by(ConnectionAccount.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_account(self, account_id: str) -> ConnectionAccount | None:
        return await self.session.get(ConnectionAccount, account_id)

    async def save_account(self, account: ConnectionAccount) -> ConnectionAccount:
        self.session.add(account)
        await self.session.commit()
        await self.session.refresh(account)
        return account

    async def delete_account(self, account: ConnectionAccount) -> None:
        await self.session.delete(account)
        await self.session.commit()

    # --- M7: project backups ---

    async def create_backup_record(self, record: BackupRecord) -> BackupRecord:
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def list_backup_records(self, project_id: str) -> list[BackupRecord]:
        result = await self.session.execute(
            select(BackupRecord)
            .where(BackupRecord.project_id == project_id)
            .order_by(BackupRecord.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_backup_record(self, backup_id: str) -> BackupRecord | None:
        return await self.session.get(BackupRecord, backup_id)

    async def delete_backup_record(self, record: BackupRecord) -> None:
        await self.session.delete(record)
        await self.session.commit()

    async def create_audit_event(self, event: AuditEvent) -> AuditEvent:
        """M7: persist an audit trail entry (ported from PR #9)."""
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(event)
        return event

    async def list_audit_events(self, limit: int = 100) -> list[AuditEvent]:
        """M7: newest audit events first."""
        result = await self.session.execute(
            select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(max(1, limit))
        )
        return list(result.scalars().all())

    async def create_structured_log(self, entry: StructuredLogRecord) -> StructuredLogRecord:
        """M7: persist a structured operational log entry (ported from PR #9)."""
        self.session.add(entry)
        await self.session.commit()
        await self.session.refresh(entry)
        return entry

    async def list_structured_logs(
        self,
        *,
        level: str | None = None,
        event: str | None = None,
        correlation_id: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[StructuredLogRecord]:
        """M7: newest structured logs first, with optional filters."""
        stmt = select(StructuredLogRecord).order_by(StructuredLogRecord.created_at.desc())
        if level:
            stmt = stmt.where(StructuredLogRecord.level == level)
        if event:
            stmt = stmt.where(StructuredLogRecord.event == event)
        if correlation_id:
            stmt = stmt.where(StructuredLogRecord.correlation_id == correlation_id)
        if project_id:
            stmt = stmt.where(StructuredLogRecord.project_id == project_id)
        result = await self.session.execute(stmt.limit(max(1, limit)))
        return list(result.scalars().all())
