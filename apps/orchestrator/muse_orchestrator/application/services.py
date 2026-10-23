from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select

from muse_orchestrator.core.config import SecretRedactor, settings
from muse_orchestrator.core.security import SecretVault, vault
from muse_orchestrator.models.domain import (
    Asset,
    AuditEvent,
    ConnectionHealthState,
    GenerationTaskRecord,
    Project,
    ProjectRevision,
    ProviderConnection,
    ShotGenerationState,
    ShotRecord,
    ShotTakeRecord,
    StoryBibleEntry,
)
from muse_orchestrator.providers.base import ProviderFailure
from muse_orchestrator.providers.mock_provider import MockVideoProvider
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter
from muse_orchestrator.services.asset_storage import (
    MAX_UPLOAD_SIZE_BYTES,
    AssetStorageError,
    LocalAssetStorage,
)
from muse_orchestrator.infrastructure.repositories import StudioRepository


class ApplicationError(Exception):
    """Expected application failure translated to HTTP only at the API boundary.

    ``category``/``retryable`` override the defaults derived from the status code in the
    shared error envelope (e.g. a 409 caused by a concurrent writer is retryable).
    """

    def __init__(
        self,
        status_code: int,
        detail: Any,
        *,
        category: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(detail if isinstance(detail, str) else str(detail))
        self.status_code = status_code
        self.detail = detail
        self.category = category
        self.retryable = retryable


def validate_provider_base_url(raw_url: str) -> str:
    """Normalise and validate a provider base URL; raise ApplicationError(422) if unsafe.

    The URL is never echoed back in the error because it may embed credentials.
    """
    base_url = raw_url.strip().rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    try:
        parts = urlsplit(base_url)
        parts.port  # noqa: B018 -- property access validates the port range/syntax
    except ValueError as exc:
        raise ApplicationError(422, "Base URL is not a valid URL.") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ApplicationError(422, "Base URL must start with http:// or https://.")
    if not parts.hostname:
        raise ApplicationError(422, "Base URL has no valid host.")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise ApplicationError(
            422, "Base URL must not contain credentials; store the API key separately."
        )
    if parts.query or parts.fragment:
        raise ApplicationError(422, "Base URL must not contain a query string or fragment.")
    if scheme == "http" and not settings.is_development and not settings.allow_insecure_provider_http:
        raise ApplicationError(
            422,
            "Base URL must use https:// outside development "
            "(set ALLOW_INSECURE_PROVIDER_HTTP=true to allow http explicitly).",
        )
    return base_url


class ProjectService:
    def __init__(self, repository: StudioRepository) -> None:
        self.repository = repository

    async def create_project(self, values: dict[str, Any], actor: str) -> Project:
        name = str(values.get("name", "")).strip()
        if not name:
            raise ApplicationError(422, "Project name cannot be blank.")
        values = {**values, "name": name}
        project_id = f"proj_{uuid.uuid4().hex[:10]}"
        project = Project(
            id=project_id,
            name=name,
            description=values.get("description", ""),
            aspect_ratio=values.get("aspect_ratio", "16:9"),
            fps=values.get("fps", 24),
            style_preset=values.get("style_preset", "2d_anime_cinematic"),
            language=values.get("language", "vi"),
            output_profile=values.get("output_profile", "1080p_h264"),
            current_revision_num=1,
        )
        revision = ProjectRevision(
            id=f"rev_{uuid.uuid4().hex[:10]}",
            project_id=project_id,
            revision_number=1,
            commit_message="Initial project snapshot",
            snapshot_json=values,
        )
        audit = AuditEvent(
            id=f"aud_{uuid.uuid4().hex[:10]}",
            actor=actor,
            action="project.create",
            target_type="project",
            target_id=project_id,
            details_redacted={"name": name},
        )
        return await self.repository.create_project_bundle(project, revision, audit)

    async def list_projects(self) -> list[tuple[Project, int]]:
        return await self.repository.list_projects_with_asset_counts()

    async def duplicate_project(self, project_id: str) -> Project:
        original = await self.repository.get_project(project_id)
        if original is None:
            raise ApplicationError(404, "Original project not found.")
        project_id_new = f"proj_{uuid.uuid4().hex[:10]}"
        cloned = Project(
            id=project_id_new,
            name=f"{original.name} (Copy)",
            description=original.description,
            aspect_ratio=original.aspect_ratio,
            fps=original.fps,
            style_preset=original.style_preset,
            language=original.language,
            output_profile=original.output_profile,
            current_revision_num=1,
        )
        revision = ProjectRevision(
            id=f"rev_{uuid.uuid4().hex[:10]}",
            project_id=project_id_new,
            revision_number=1,
            commit_message=f"Duplicated from {original.id}",
            snapshot_json={"duplicated_from": original.id},
        )
        return await self.repository.create_project_bundle(cloned, revision)

    async def list_revisions(self, project_id: str) -> list[ProjectRevision]:
        if await self.repository.get_project(project_id) is None:
            raise ApplicationError(404, "Project không tồn tại.")
        return await self.repository.list_project_revisions(project_id)


async def _video_first_frame_status(provider: Any, healthy: bool) -> str:
    """``supported`` only when the gateway advertises real first-frame conditioning.

    The mock provider writes placeholder frames, and a gateway that does not advertise
    the capability may silently ignore the frame (observed with upstream muse2api).
    """
    if isinstance(provider, MockVideoProvider):
        return "unsupported"
    if not healthy:
        return "unknown"
    advertised = await provider.first_frame_capability()
    if advertised is None:
        return "unknown"
    return "supported" if advertised else "unsupported"


class ConnectionService:
    def __init__(self, repository: StudioRepository, secret_vault: SecretVault = vault) -> None:
        self.repository = repository
        self.secret_vault = secret_vault

    async def create_connection(self, values: dict[str, Any]) -> ProviderConnection:
        name = str(values.get("name", "")).strip()
        if not name:
            raise ApplicationError(422, "Connection name cannot be blank.")
        base_url = validate_provider_base_url(str(values.get("base_url", "")))
        for existing in await self.repository.list_connections():
            if existing.name.lower() == name.lower():
                raise ApplicationError(409, f"Connection '{name}' already exists.")
        api_key = values.get("api_key")
        connection = ProviderConnection(
            id=f"conn_{uuid.uuid4().hex[:8]}",
            name=name,
            provider_type=values.get("provider_type", "muse2api"),
            base_url=base_url,
            encrypted_api_key=self.secret_vault.encrypt(api_key),
            api_key_masked=self.secret_vault.mask(api_key),
            proxy_display_host=(
                SecretRedactor.redact_text(values["proxy_display_host"])
                if values.get("proxy_display_host")
                else None
            ),
            timeout_seconds=values.get("timeout_seconds", 30.0),
            max_concurrency=values.get("max_concurrency", 2),
            enabled=values.get("enabled", True),
            health_state=ConnectionHealthState.UNKNOWN,
            capabilities_snapshot={
                "per_account_proxy": "unsupported",
                "quota_lookup": "unsupported",
                "models": [],
            },
        )
        return await self.repository.create_connection(connection)

    async def list_connections(self) -> list[ProviderConnection]:
        return await self.repository.list_connections()

    async def test_connection(self, connection_id: str) -> ProviderConnection:
        connection = await self.repository.get_connection(connection_id)
        if connection is None:
            raise ApplicationError(404, "Không tìm thấy Connection Profile.")

        raw_api_key = self.secret_vault.decrypt(connection.encrypted_api_key)
        provider = (
            MockVideoProvider()
            if connection.provider_type == "mock"
            else Muse2ApiAdapter(
                base_url=connection.base_url,
                api_key=raw_api_key,
                timeout=connection.timeout_seconds,
            )
        )
        health = await provider.health()
        connection.latency_ms = health.latency_ms
        connection.last_checked_at = datetime.now(timezone.utc)
        valid_states = {state.value for state in ConnectionHealthState}
        connection.health_state = (
            ConnectionHealthState(health.status_state)
            if health.status_state in valid_states
            else ConnectionHealthState.UNAVAILABLE
        )

        models: list[dict[str, Any]] = []
        if health.healthy:
            try:
                discovered = await provider.list_models()
                models = [
                    {
                        "model_id": item.model_id,
                        "modality": item.modality,
                        "verification_status": item.verification_status,
                    }
                    for item in discovered
                ]
                connection.last_error_redacted = None
            except ProviderFailure as exc:
                connection.last_error_redacted = SecretRedactor.redact_text(
                    str(exc), [raw_api_key or ""]
                )
        accounts = await self.repository.list_accounts(connection_id)
        connection.capabilities_snapshot = {
            "probed_at": connection.last_checked_at.isoformat(),
            "models": models,
            "per_account_proxy": "supported" if accounts else "unsupported",
            "account_count": len(accounts),
            "quota_lookup": "unsupported",
            "video_first_frame": await _video_first_frame_status(provider, health.healthy),
        }
        return await self.repository.save_connection(connection)


class AssetService:
    def __init__(
        self,
        repository: StudioRepository,
        storage: LocalAssetStorage | None = None,
    ) -> None:
        self.repository = repository
        self.storage = storage or LocalAssetStorage(settings.media_root)

    async def upload_asset(
        self,
        project_id: str,
        filename: str,
        declared_mime: str,
        content: bytes,
        asset_type: str,
        is_locked_reference: bool,
    ) -> Asset:
        if len(content) > MAX_UPLOAD_SIZE_BYTES:
            raise ApplicationError(413, "File vượt quá dung lượng cho phép.")
        if await self.repository.get_project(project_id) is None:
            raise ApplicationError(404, "Project không tồn tại.")
        try:
            # Hashing and writing up to the upload limit must not block the event loop.
            saved = await asyncio.to_thread(
                self.storage.save_bytes, project_id, filename, content, declared_mime
            )
        except AssetStorageError as exc:
            raise ApplicationError(400, str(exc)) from exc

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
        try:
            return await self.repository.create_asset(asset)
        except Exception:
            try:
                self.storage.resolve_safe_path(saved.storage_rel_path).unlink(missing_ok=True)
            except (AssetStorageError, OSError):
                pass
            raise

    async def get_asset_file(self, asset_id: str) -> tuple[Asset, Path]:
        """Return the asset row and its file, which is guaranteed to sit inside the media root."""
        asset = await self.repository.get_asset(asset_id)
        if asset is None:
            raise ApplicationError(404, "Asset không tồn tại.")
        try:
            return asset, self.storage.resolve_safe_path(asset.storage_rel_path)
        except AssetStorageError as exc:
            raise ApplicationError(404, "File của asset không còn trên máy chủ.") from exc

    async def _find_asset_references(
        self, asset: Asset
    ) -> tuple[list[ShotTakeRecord], list[ShotRecord], list[StoryBibleEntry]]:
        session = self.repository.session
        takes = list(
            (
                await session.execute(
                    select(ShotTakeRecord).where(
                        (ShotTakeRecord.asset_id == asset.id)
                        | (ShotTakeRecord.first_frame_asset_id == asset.id)
                        | (ShotTakeRecord.last_frame_asset_id == asset.id)
                    )
                )
            ).scalars().all()
        )
        content_take_ids = {take.id for take in takes if take.asset_id == asset.id}
        project_shots = await self.repository.list_project_shots(asset.project_id) or []
        shots = [
            shot
            for shot in project_shots
            if asset.id in (shot.reference_asset_ids or [])
            or shot.first_frame_asset_id == asset.id
            or shot.last_frame_asset_id == asset.id
            or (shot.selected_take_id is not None and shot.selected_take_id in content_take_ids)
        ]
        bible = [
            entry
            for entry in await self.repository.list_story_bible_entries(asset.project_id)
            if asset.id in (entry.reference_asset_ids or [])
        ]
        return takes, shots, bible

    @staticmethod
    def _describe_references(
        asset_id: str,
        takes: list[ShotTakeRecord],
        shots: list[ShotRecord],
        bible: list[StoryBibleEntry],
    ) -> str:
        def ids(values: list[str]) -> str:
            shown = ", ".join(values[:20])
            return shown + (f", ... (+{len(values) - 20})" if len(values) > 20 else "")

        parts: list[str] = []
        if takes:
            parts.append(f"takes [{ids([t.id for t in takes])}]")
        if shots:
            parts.append(f"shots [{ids([s.id for s in shots])}]")
        if bible:
            parts.append(f"story bible [{ids([b.code_name for b in bible])}]")
        return (
            f"Asset '{asset_id}' is still referenced by " + "; ".join(parts)
            + ". Retry with force=true to detach these references and delete it."
        )

    async def delete_asset(self, asset_id: str, force: bool = False) -> None:
        asset = await self.repository.get_asset(asset_id)
        if asset is None:
            raise ApplicationError(404, "Asset không tồn tại.")
        if asset.is_locked_reference and not force:
            raise ApplicationError(409, "Asset đang được khoá làm Reference.")
        candidate = (self.storage.root_path / asset.storage_rel_path).resolve()
        try:
            candidate.relative_to(self.storage.root_path)
        except ValueError as exc:
            raise ApplicationError(500, "Stored asset path escaped the media root.") from exc

        takes, shots, bible = await self._find_asset_references(asset)
        if (takes or shots or bible) and not force:
            raise ApplicationError(409, self._describe_references(asset.id, takes, shots, bible))

        shared_path = await self.repository.has_other_asset_with_storage_path(
            asset.storage_rel_path, asset.id
        )
        session = self.repository.session
        if takes or shots or bible:
            await self._detach_asset_references(asset, takes, shots, bible)
            # No relationship(): the unit of work would DELETE the asset before its takes,
            # the FK cascade would remove the takes first and the take DELETE would then
            # match 0 rows. Delete the children explicitly before the parent.
            await self.repository.flush_changes()
        await session.delete(asset)
        if takes or shots or bible:
            # Recompile lint in the SAME transaction so a shot that lost a required
            # reference is blocked before anything can enqueue it.
            from muse_orchestrator.application.pipeline_service import PipelineService

            await session.flush()
            await PipelineService(self.repository).recompile_project_shots(
                asset.project_id, commit=False
            )
        await self.repository.commit_pipeline_changes()
        if not shared_path and candidate.is_file():
            candidate.unlink(missing_ok=True)

    async def _detach_asset_references(
        self,
        asset: Asset,
        takes: list[ShotTakeRecord],
        shots: list[ShotRecord],
        bible: list[StoryBibleEntry],
    ) -> None:
        session = self.repository.session
        deleted_take_ids = {take.id for take in takes if take.asset_id == asset.id}
        for shot in shots:
            if shot.selected_take_id in deleted_take_ids:
                shot.selected_take_id = None
                if shot.generation_state == ShotGenerationState.SUCCEEDED:
                    # Never keep claiming success once the selected output is gone.
                    shot.generation_state = ShotGenerationState.NOT_STARTED
            if asset.id in (shot.reference_asset_ids or []):
                shot.reference_asset_ids = [ref for ref in shot.reference_asset_ids if ref != asset.id]
            if shot.first_frame_asset_id == asset.id:
                shot.first_frame_asset_id = None
            if shot.last_frame_asset_id == asset.id:
                shot.last_frame_asset_id = None
        for entry in bible:
            entry.reference_asset_ids = [ref for ref in entry.reference_asset_ids if ref != asset.id]
        if deleted_take_ids:
            tasks = (
                await session.execute(
                    select(GenerationTaskRecord).where(
                        GenerationTaskRecord.take_id.in_(deleted_take_ids)
                        | (GenerationTaskRecord.result_asset_id == asset.id)
                    )
                )
            ).scalars().all()
            for task in tasks:
                task.take_id = None
                task.result_asset_id = None
        for take in takes:
            if take.id in deleted_take_ids:
                # The FK would cascade on PostgreSQL; delete explicitly so every backend
                # ends in the same state.
                await session.delete(take)
            else:
                if take.first_frame_asset_id == asset.id:
                    take.first_frame_asset_id = None
                if take.last_frame_asset_id == asset.id:
                    take.last_frame_asset_id = None
