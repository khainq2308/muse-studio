from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from muse_orchestrator.core.security import SecretVault, vault
from muse_orchestrator.providers.base import ProviderFailure
from muse_orchestrator.providers.mock_provider import MockVideoProvider

from muse_orchestrator.application.services import ApplicationError
from muse_orchestrator.application.system_service import write_audit
from muse_orchestrator.core.config import SecretRedactor
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.models.domain import (
    ConnectionAccount,
    ConnectionHealthState,
    GenerationTaskRecord,
    QueueTaskState,
)
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter

_PROXY_SCHEMES = ("http://", "https://", "socks5://", "socks5h://")


def _validate_proxy_url(proxy_url: str | None) -> str | None:
    """Validate a proxy URL and return its display host (credentials stripped)."""
    if not proxy_url:
        return None
    cleaned = proxy_url.strip()
    if not cleaned.lower().startswith(_PROXY_SCHEMES):
        raise ApplicationError(
            422, "Proxy URL must start with http://, https://, socks5:// or socks5h://."
        )
    host = urlparse(cleaned).hostname
    if not host:
        raise ApplicationError(422, "Proxy URL has no valid host.")
    return host


class ConnectionAccountService:
    """M7: CRUD + probing for provider accounts under a connection."""

    def __init__(
        self,
        repository: StudioRepository,
        secret_vault: SecretVault = vault,
    ) -> None:
        self.repository = repository
        self.secret_vault = secret_vault

    async def _require_connection(self, connection_id: str):
        connection = await self.repository.get_connection(connection_id)
        if connection is None:
            raise ApplicationError(404, "Connection profile not found.")
        return connection

    async def require_account_for_connection(
        self, connection_id: str, account_id: str
    ) -> ConnectionAccount:
        """Validate a nested account relationship before performing side effects."""
        await self._require_connection(connection_id)
        account = await self.repository.get_account(account_id)
        if account is None or account.connection_id != connection_id:
            raise ApplicationError(404, "Connection account not found.")
        return account

    async def create_account(self, connection_id: str, values: dict[str, Any]) -> ConnectionAccount:
        connection = await self._require_connection(connection_id)
        label = str(values.get("label", "")).strip()
        api_key = str(values.get("api_key", "") or "")
        proxy_url = str(values.get("proxy_url", "") or "").strip() or None
        if not label:
            raise ApplicationError(422, "Account label cannot be blank.")
        if len(label) > 128:
            raise ApplicationError(422, "Account label is too long (max 128).")
        if not api_key:
            raise ApplicationError(422, "API key cannot be blank.")
        for existing in await self.repository.list_accounts(connection.id):
            if existing.label.lower() == label.lower():
                raise ApplicationError(409, "Account label already exists on this connection.")
        proxy_display_host = _validate_proxy_url(proxy_url)
        account = ConnectionAccount(
            id=f"acct_{uuid.uuid4().hex[:8]}",
            connection_id=connection.id,
            label=label,
            encrypted_api_key=self.secret_vault.encrypt(api_key),
            api_key_masked=self.secret_vault.mask(api_key),
            encrypted_proxy_url=self.secret_vault.encrypt(proxy_url) if proxy_url else None,
            proxy_display_host=proxy_display_host,
            enabled=bool(values.get("enabled", True)),
            priority=int(values.get("priority", 100)),
            health_state=ConnectionHealthState.UNKNOWN,
        )
        created = await self.repository.create_account(account)
        await write_audit(
            self.repository,
            action="account.create",
            target_type="connection_account",
            target_id=created.id,
            details={"connection_id": connection.id, "label": created.label},
        )
        return created

    async def list_accounts(self, connection_id: str) -> list[ConnectionAccount]:
        await self._require_connection(connection_id)
        return await self.repository.list_accounts(connection_id)

    async def update_account(
        self, account_id: str, values: dict[str, Any]
    ) -> ConnectionAccount:
        account = await self.repository.get_account(account_id)
        if account is None:
            raise ApplicationError(404, "Connection account not found.")
        if "label" in values:
            label = str(values["label"] or "").strip()
            if not label:
                raise ApplicationError(422, "Account label cannot be blank.")
            for existing in await self.repository.list_accounts(account.connection_id):
                if existing.id != account.id and existing.label.lower() == label.lower():
                    raise ApplicationError(409, "Account label already exists on this connection.")
            account.label = label
        if "enabled" in values:
            account.enabled = bool(values["enabled"])
        if "priority" in values:
            account.priority = int(values["priority"])
        if values.get("api_key"):
            api_key = str(values["api_key"])
            account.encrypted_api_key = self.secret_vault.encrypt(api_key)
            account.api_key_masked = self.secret_vault.mask(api_key)
            account.health_state = ConnectionHealthState.UNKNOWN
        if "proxy_url" in values:
            proxy_url = str(values["proxy_url"] or "").strip() or None
            account.proxy_display_host = _validate_proxy_url(proxy_url)
            account.encrypted_proxy_url = self.secret_vault.encrypt(proxy_url) if proxy_url else None
        saved = await self.repository.save_account(account)
        await write_audit(
            self.repository,
            action="account.update",
            target_type="connection_account",
            target_id=saved.id,
            details={"label": saved.label, "enabled": saved.enabled},
        )
        return saved

    async def delete_account(self, account_id: str) -> None:
        account = await self.repository.get_account(account_id)
        if account is None:
            raise ApplicationError(404, "Connection account not found.")
        active_task = (
            await self.repository.session.execute(
                select(GenerationTaskRecord.id).where(
                    GenerationTaskRecord.account_id_used == account.id,
                    GenerationTaskRecord.state.in_([QueueTaskState.QUEUED, QueueTaskState.RUNNING]),
                ).limit(1)
            )
        ).first()
        if active_task is not None:
            raise ApplicationError(
                409,
                "Cannot delete an account pinned to a queued/running generation task. "
                "Disable it or wait for the task to finish first.",
            )
        label, connection_id = account.label, account.connection_id
        await self.repository.delete_account(account)
        await write_audit(
            self.repository,
            action="account.delete",
            target_type="connection_account",
            target_id=account_id,
            details={"connection_id": connection_id, "label": label},
        )

    async def test_account(self, account_id: str) -> ConnectionAccount:
        account = await self.repository.get_account(account_id)
        if account is None:
            raise ApplicationError(404, "Connection account not found.")
        connection = await self._require_connection(account.connection_id)

        raw_api_key = self.secret_vault.decrypt(account.encrypted_api_key)
        raw_proxy_url = self.secret_vault.decrypt(account.encrypted_proxy_url)
        provider = (
            MockVideoProvider()
            if connection.provider_type == "mock"
            else Muse2ApiAdapter(
                base_url=connection.base_url,
                api_key=raw_api_key,
                timeout=connection.timeout_seconds,
                proxy=raw_proxy_url,
            )
        )
        health = await provider.health()
        account.latency_ms = health.latency_ms
        account.last_checked_at = datetime.now(UTC)
        valid_states = {state.value for state in ConnectionHealthState}
        account.health_state = (
            ConnectionHealthState(health.status_state)
            if health.status_state in valid_states
            else ConnectionHealthState.UNAVAILABLE
        )
        if health.healthy:
            account.last_error_redacted = None
        else:
            account.last_error_redacted = SecretRedactor.redact_text(
                health.status_state or "probe failed",
                [raw_api_key or "", raw_proxy_url or ""],
            )
        try:
            if health.healthy:
                await provider.list_models()
        except ProviderFailure as exc:
            account.health_state = ConnectionHealthState.DEGRADED
            account.last_error_redacted = SecretRedactor.redact_text(
                str(exc), [raw_api_key or "", raw_proxy_url or ""]
            )
        saved = await self.repository.save_account(account)
        await write_audit(
            self.repository,
            action="account.test",
            target_type="connection_account",
            target_id=saved.id,
            details={"label": saved.label, "health_state": str(saved.health_state)},
        )
        return saved
