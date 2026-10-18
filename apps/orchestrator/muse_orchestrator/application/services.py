from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from muse_orchestrator.core.config import SecretRedactor, settings
from muse_orchestrator.core.security import SecretVault, vault
from muse_orchestrator.models.domain import (
    Asset,
    AuditEvent,
    ConnectionHealthState,
    Project,
    ProjectRevision,
    ProviderConnection,
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
    """Expected application failure translated to HTTP only at the API boundary."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


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
        return await self.repository.list_project_revisions(project_id)


class ConnectionService:
    def __init__(self, repository: StudioRepository, secret_vault: SecretVault = vault) -> None:
        self.repository = repository
        self.secret_vault = secret_vault

    async def create_connection(self, values: dict[str, Any]) -> ProviderConnection:
        name = str(values.get("name", "")).strip()
        base_url = str(values.get("base_url", "")).strip().rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        if not name:
            raise ApplicationError(422, "Connection name cannot be blank.")
        if not base_url.startswith(("http://", "https://")):
            raise ApplicationError(422, "Base URL must start with http:// or https://.")
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
            saved = self.storage.save_bytes(project_id, filename, content, declared_mime)
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

    async def delete_asset(self, asset_id: str, force: bool = False) -> None:
        asset = await self.repository.get_asset(asset_id)
        if asset is None:
            raise ApplicationError(404, "Asset không tồn tại.")
        if asset.is_locked_reference and not force:
            raise ApplicationError(409, "Asset đang được khoá làm Reference.")
        try:
            file_path: Path | None = self.storage.resolve_safe_path(asset.storage_rel_path)
        except AssetStorageError as exc:
            raise ApplicationError(500, "Stored asset path is invalid or missing.") from exc
        shared_path = await self.repository.has_other_asset_with_storage_path(
            asset.storage_rel_path, asset.id
        )
        await self.repository.delete_asset(asset)
        if not shared_path:
            file_path.unlink(missing_ok=True)
