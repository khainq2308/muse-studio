from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from muse_orchestrator.api.dependencies import get_connection_service
from muse_orchestrator.application.services import ConnectionService
from muse_orchestrator.models.domain import ConnectionHealthState, ProviderConnection

router = APIRouter(prefix="/api/v1/connections", tags=["Connections"])


class ConnectionCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    provider_type: str = Field(default="muse2api", pattern=r"^(muse2api|mock)$")
    base_url: str = Field(..., min_length=4, max_length=512)
    api_key: str | None = Field(default=None)
    proxy_display_host: str | None = Field(default=None)
    timeout_seconds: float = Field(default=30.0, ge=5.0, le=120.0)
    max_concurrency: int = Field(default=2, ge=1, le=20)
    enabled: bool = True


class ConnectionOut(BaseModel):
    id: str
    name: str
    provider_type: str
    base_url: str
    api_key_masked: str
    proxy_display_host: str | None
    timeout_seconds: float
    max_concurrency: int
    enabled: bool
    health_state: ConnectionHealthState
    latency_ms: float | None
    capabilities_snapshot: dict[str, Any]
    last_error_redacted: str | None
    last_checked_at: datetime | None

    model_config = {"from_attributes": True}


def _to_connection_out(connection: ProviderConnection) -> ConnectionOut:
    return ConnectionOut(
        id=connection.id,
        name=connection.name,
        provider_type=connection.provider_type,
        base_url=connection.base_url,
        api_key_masked=connection.api_key_masked,
        proxy_display_host=connection.proxy_display_host,
        timeout_seconds=connection.timeout_seconds,
        max_concurrency=connection.max_concurrency,
        enabled=connection.enabled,
        health_state=connection.health_state,
        latency_ms=connection.latency_ms,
        capabilities_snapshot=connection.capabilities_snapshot,
        last_error_redacted=connection.last_error_redacted,
        last_checked_at=connection.last_checked_at,
    )


@router.post("", response_model=ConnectionOut, status_code=status.HTTP_201_CREATED)
async def create_connection(
    payload: ConnectionCreateIn,
    service: ConnectionService = Depends(get_connection_service),
) -> ConnectionOut:
    connection = await service.create_connection(payload.model_dump())
    return _to_connection_out(connection)


@router.get("", response_model=list[ConnectionOut])
async def list_connections(
    service: ConnectionService = Depends(get_connection_service),
) -> list[ConnectionOut]:
    return [_to_connection_out(connection) for connection in await service.list_connections()]


@router.post("/{connection_id}/test", response_model=ConnectionOut)
async def test_and_probe_connection(
    connection_id: str,
    service: ConnectionService = Depends(get_connection_service),
) -> ConnectionOut:
    return _to_connection_out(await service.test_connection(connection_id))
