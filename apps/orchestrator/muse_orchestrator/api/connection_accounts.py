from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from muse_orchestrator.api.dependencies import get_account_service
from muse_orchestrator.application.account_service import ConnectionAccountService
from muse_orchestrator.models.domain import ConnectionAccount, ConnectionHealthState

router = APIRouter(prefix="/api/v1/connections", tags=["Connection Accounts"])


class AccountCreateIn(BaseModel):
    label: str = Field(..., min_length=1, max_length=128)
    api_key: str = Field(..., min_length=1, max_length=4096)
    proxy_url: str | None = Field(default=None, max_length=1024)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10000)


class AccountUpdateIn(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=128)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    proxy_url: str | None = Field(default=None, max_length=1024)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)


class AccountOut(BaseModel):
    id: str
    connection_id: str
    label: str
    api_key_masked: str
    proxy_display_host: str | None
    has_proxy: bool
    enabled: bool
    priority: int
    health_state: ConnectionHealthState
    latency_ms: float | None
    last_error_redacted: str | None
    last_used_at: datetime | None
    last_checked_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


def _to_account_out(account: ConnectionAccount) -> AccountOut:
    return AccountOut(
        id=account.id,
        connection_id=account.connection_id,
        label=account.label,
        api_key_masked=account.api_key_masked,
        proxy_display_host=account.proxy_display_host,
        has_proxy=bool(account.encrypted_proxy_url),
        enabled=account.enabled,
        priority=account.priority,
        health_state=account.health_state,
        latency_ms=account.latency_ms,
        last_error_redacted=account.last_error_redacted,
        last_used_at=account.last_used_at,
        last_checked_at=account.last_checked_at,
        created_at=account.created_at,
    )


@router.post(
    "/{connection_id}/accounts",
    response_model=AccountOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_account(
    connection_id: str,
    payload: AccountCreateIn,
    service: ConnectionAccountService = Depends(get_account_service),
) -> AccountOut:
    account = await service.create_account(connection_id, payload.model_dump())
    return _to_account_out(account)


@router.get("/{connection_id}/accounts", response_model=list[AccountOut])
async def list_accounts(
    connection_id: str,
    service: ConnectionAccountService = Depends(get_account_service),
) -> list[AccountOut]:
    return [_to_account_out(a) for a in await service.list_accounts(connection_id)]


@router.patch("/{connection_id}/accounts/{account_id}", response_model=AccountOut)
async def update_account(
    connection_id: str,
    account_id: str,
    payload: AccountUpdateIn,
    service: ConnectionAccountService = Depends(get_account_service),
) -> AccountOut:
    await service.require_account_for_connection(connection_id, account_id)
    values: dict[str, Any] = {
        key: value for key, value in payload.model_dump().items() if value is not None
    }
    account = await service.update_account(account_id, values)
    return _to_account_out(account)


@router.delete(
    "/{connection_id}/accounts/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_account(
    connection_id: str,
    account_id: str,
    service: ConnectionAccountService = Depends(get_account_service),
 ) -> None:
    await service.require_account_for_connection(connection_id, account_id)
    await service.delete_account(account_id)


@router.post("/{connection_id}/accounts/{account_id}/test", response_model=AccountOut)
async def test_account(
    connection_id: str,
    account_id: str,
    service: ConnectionAccountService = Depends(get_account_service),
 ) -> AccountOut:
    await service.require_account_for_connection(connection_id, account_id)
    account = await service.test_account(account_id)
    return _to_account_out(account)
