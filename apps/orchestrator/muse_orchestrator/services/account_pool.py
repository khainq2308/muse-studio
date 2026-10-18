from __future__ import annotations

from datetime import UTC, datetime

from muse_orchestrator.core.security import vault
from muse_orchestrator.providers.base import VideoProvider
from muse_orchestrator.providers.mock_provider import MockVideoProvider

from muse_orchestrator.models.domain import (
    ConnectionAccount,
    ConnectionHealthState,
    ProviderConnection,
)
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter

# Accounts that may receive new generation work. AUTH_ERROR / DEGRADED /
# UNAVAILABLE accounts are skipped until they pass a manual probe again.
_USABLE_HEALTH_STATES = {ConnectionHealthState.HEALTHY, ConnectionHealthState.UNKNOWN}

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def select_account(accounts: list[ConnectionAccount]) -> ConnectionAccount | None:
    """Pick the next account for a generation task.

    Strategy: least-recently-used among enabled accounts whose health is
    usable, then lowest priority value, then oldest creation time. Accounts
    that never ran (``last_used_at`` is None) go first so a freshly added
    account immediately takes a turn.
    """

    candidates = [
        account
        for account in accounts
        if account.enabled and account.health_state in _USABLE_HEALTH_STATES
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda account: (
            account.last_used_at is not None,
            account.last_used_at or _EPOCH,
            account.priority,
            account.created_at or _EPOCH,
        ),
    )


def resolve_task_account(
    accounts: list[ConnectionAccount],
    selected_account_id: str | None,
    has_external_task: bool,
) -> ConnectionAccount | None:
    """Keep upstream tasks pinned to their submitting account.

    Legacy in-flight tasks without a pin retain connection-level credentials. A task
    awaiting dispatch may reuse its selected account only if it remains healthy; this
    lets retries rotate away from accounts that hit auth or quota errors.
    """
    if has_external_task:
        if not selected_account_id:
            return None
        account = next((item for item in accounts if item.id == selected_account_id), None)
        if account is None:
            raise LookupError("The provider account pinned to this running task no longer exists.")
        return account
    if selected_account_id:
        selected = next((item for item in accounts if item.id == selected_account_id), None)
        if selected and selected.enabled and selected.health_state in _USABLE_HEALTH_STATES:
            return selected
    return select_account(accounts)


def decrypt_account_api_key(account: ConnectionAccount) -> str | None:
    return vault.decrypt(account.encrypted_api_key)


def decrypt_account_proxy_url(account: ConnectionAccount) -> str | None:
    return vault.decrypt(account.encrypted_proxy_url)


def build_provider_for_account(
    connection: ProviderConnection,
    account: ConnectionAccount | None,
) -> VideoProvider:
    """Build a provider bound to a specific account (key + proxy).

    When ``account`` is None the connection-level credentials are used,
    preserving the pre-M7 behaviour.
    """

    if connection.provider_type == "mock":
        return MockVideoProvider()
    if account is None:
        return Muse2ApiAdapter(
            base_url=connection.base_url,
            api_key=vault.decrypt(connection.encrypted_api_key),
            timeout=connection.timeout_seconds,
        )
    return Muse2ApiAdapter(
        base_url=connection.base_url,
        api_key=decrypt_account_api_key(account),
        timeout=connection.timeout_seconds,
        proxy=decrypt_account_proxy_url(account),
    )


def mark_account_used(account: ConnectionAccount) -> None:
    account.last_used_at = datetime.now(UTC)
