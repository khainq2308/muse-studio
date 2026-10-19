from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.models.domain import (
    Asset,
    AuditEvent,
    Project,
    ProjectRevision,
    ScriptRevision,
    StoryBibleEntry,
    SceneRecord,
    ShotRecord,
    ProviderConnection,
    BackupRecord,
    ConnectionAccount,
    StructuredLogRecord,
)
from muse_orchestrator.models.domain import (
    WorkflowDefinition,
    WorkflowVersion,
    WorkflowRun,
    NodeExecution,
    WorkflowCacheEntry,
)
from muse_orchestrator.models.domain import (
    ExportJobRecord,
    ShotTakeRecord,
    TimelineClipRecord,
    TimelineSequenceRecord,
    TimelineTrackRecord,
)


class StudioRepository:
    """Persistence adapter. SQLAlchemy queries and transaction boundaries stay here."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- M6 timeline persistence ---

    async def get_shot_take(self, take_id: str) -> ShotTakeRecord | None:
        return await self.session.get(ShotTakeRecord, take_id)

    async def get_timeline_sequence(
        self, sequence_id: str
    ) -> TimelineSequenceRecord | None:
        return await self.session.get(TimelineSequenceRecord, sequence_id)

    async def get_latest_timeline_sequence(
        self, project_id: str
    ) -> TimelineSequenceRecord | None:
        result = await self.session.execute(
            select(TimelineSequenceRecord)
            .where(TimelineSequenceRecord.project_id == project_id)
            .order_by(TimelineSequenceRecord.created_at.desc())
        )
        return result.scalars().first()

    async def list_timeline_clips(self, sequence_id: str) -> list[TimelineClipRecord]:
        result = await self.session.execute(
            select(TimelineClipRecord)
            .where(TimelineClipRecord.sequence_id == sequence_id)
            .order_by(TimelineClipRecord.order_index.asc())
        )
        return list(result.scalars().all())

    async def create_timeline_bundle(
        self,
        sequence: TimelineSequenceRecord,
        tracks: list[TimelineTrackRecord],
        clips: list[TimelineClipRecord],
    ) -> TimelineSequenceRecord:
        self.session.add(sequence)
        self.session.add_all([*tracks, *clips])
        await self.session.commit()
        await self.session.refresh(sequence)
        return sequence

    async def create_export_job(self, job: ExportJobRecord) -> ExportJobRecord:
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)
        return job

    async def create_export_result(
        self, asset: Asset, job: ExportJobRecord
    ) -> ExportJobRecord:
        self.session.add_all([asset, job])
        await self.session.commit()
        await self.session.refresh(job)
        return job

    # --- M3 workflow persistence ---

    async def get_workflow_definition(self, workflow_id: str) -> WorkflowDefinition | None:
        return await self.session.get(WorkflowDefinition, workflow_id)

    async def get_workflow_version(
        self, workflow_id: str, version_number: int
    ) -> WorkflowVersion:
        result = await self.session.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == workflow_id,
                WorkflowVersion.version_number == version_number,
            )
        )
        return result.scalar_one()

    async def get_workflow_run(self, run_id: str) -> WorkflowRun | None:
        return await self.session.get(WorkflowRun, run_id)

    async def list_node_executions_for_run(self, run_id: str) -> list[NodeExecution]:
        result = await self.session.execute(
            select(NodeExecution).where(NodeExecution.run_id == run_id)
        )
        return list(result.scalars().all())

    async def get_workflow_cache_entry(self, cache_key: str) -> WorkflowCacheEntry | None:
        return await self.session.get(WorkflowCacheEntry, cache_key)

    def stage_workflow_entity(self, entity: object) -> None:
        self.session.add(entity)

    def stage_workflow_entities(self, entities: list[object]) -> None:
        self.session.add_all(entities)

    async def commit_workflow_changes(self) -> None:
        await self.session.commit()

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

    async def list_project_shots(self, project_id: str) -> list[ShotRecord] | None:
        """Return stored shot state without recompiling prompts or committing writes.

        None means the project does not exist; an empty list is a valid empty project.
        """
        if await self.session.get(Project, project_id) is None:
            return None
        result = await self.session.execute(
            select(ShotRecord)
            .where(ShotRecord.project_id == project_id)
            .order_by(ShotRecord.sequence_index.asc())
        )
        return list(result.scalars().all())

    # --- M2 script-to-shot pipeline persistence ---

    async def list_story_bible_entries(self, project_id: str) -> list[StoryBibleEntry]:
        result = await self.session.execute(
            select(StoryBibleEntry).where(StoryBibleEntry.project_id == project_id)
        )
        return list(result.scalars().all())

    async def list_project_asset_ids(self, project_id: str) -> list[str]:
        result = await self.session.execute(
            select(Asset.id).where(Asset.project_id == project_id)
        )
        return list(result.scalars().all())

    async def list_script_revisions(self, project_id: str) -> list[ScriptRevision]:
        result = await self.session.execute(
            select(ScriptRevision).where(ScriptRevision.project_id == project_id)
        )
        return list(result.scalars().all())

    async def get_story_bible_entry(
        self, project_id: str, code_name: str
    ) -> StoryBibleEntry | None:
        result = await self.session.execute(
            select(StoryBibleEntry).where(
                StoryBibleEntry.project_id == project_id,
                StoryBibleEntry.code_name == code_name,
            )
        )
        return result.scalar_one_or_none()

    async def get_shot(self, shot_id: str) -> ShotRecord | None:
        return await self.session.get(ShotRecord, shot_id)

    async def delete_project_pipeline_records(self, project_id: str) -> None:
        # Preserve the original deletion order for child records and associated constraints.
        await self.session.execute(delete(ShotRecord).where(ShotRecord.project_id == project_id))
        await self.session.execute(delete(SceneRecord).where(SceneRecord.project_id == project_id))
        await self.session.execute(
            delete(StoryBibleEntry).where(StoryBibleEntry.project_id == project_id)
        )

    def stage_pipeline_entity(self, entity: object) -> None:
        self.session.add(entity)

    async def commit_pipeline_changes(self) -> None:
        await self.session.commit()

    async def refresh_pipeline_entity(self, entity: object) -> None:
        await self.session.refresh(entity)

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
