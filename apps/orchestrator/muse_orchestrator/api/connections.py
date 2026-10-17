from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from muse_orchestrator.core.config import SecretRedactor
from muse_orchestrator.core.database import get_db
from muse_orchestrator.core.security import vault, verify_single_admin
from muse_orchestrator.models.domain import ProviderConnection, ConnectionHealthState
from muse_orchestrator.providers.base import ProviderFailure
from muse_orchestrator.providers.mock_provider import MockVideoProvider
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter

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


def _to_connection_out(c: ProviderConnection) -> ConnectionOut:
    return ConnectionOut(
        id=c.id,
        name=c.name,
        provider_type=c.provider_type,
        base_url=c.base_url,
        api_key_masked=c.api_key_masked,
        proxy_display_host=c.proxy_display_host,
        timeout_seconds=c.timeout_seconds,
        max_concurrency=c.max_concurrency,
        enabled=c.enabled,
        health_state=c.health_state,
        latency_ms=c.latency_ms,
        capabilities_snapshot=c.capabilities_snapshot,
        last_error_redacted=c.last_error_redacted,
        last_checked_at=c.last_checked_at,
    )


@router.post("", response_model=ConnectionOut, status_code=status.HTTP_201_CREATED)
async def create_connection(
    payload: ConnectionCreateIn,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(verify_single_admin),
) -> ConnectionOut:
    cid = f"conn_{uuid.uuid4().hex[:8]}"
    conn = ProviderConnection(
        id=cid,
        name=payload.name,
        provider_type=payload.provider_type,
        base_url=payload.base_url.rstrip("/"),
        encrypted_api_key=vault.encrypt(payload.api_key),
        api_key_masked=vault.mask(payload.api_key),
        proxy_display_host=SecretRedactor.redact_text(payload.proxy_display_host) if payload.proxy_display_host else None,
        timeout_seconds=payload.timeout_seconds,
        max_concurrency=payload.max_concurrency,
        enabled=payload.enabled,
        health_state=ConnectionHealthState.UNKNOWN,
        capabilities_snapshot={"per_account_proxy": "unsupported", "quota_lookup": "unsupported", "models": []},
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return _to_connection_out(conn)


@router.get("", response_model=list[ConnectionOut])
async def list_connections(db: AsyncSession = Depends(get_db)) -> list[ConnectionOut]:
    rows = (await db.execute(select(ProviderConnection).order_by(ProviderConnection.created_at.desc()))).scalars().all()
    return [_to_connection_out(c) for c in rows]


@router.post("/{connection_id}/test", response_model=ConnectionOut)
async def test_and_probe_connection(connection_id: str, db: AsyncSession = Depends(get_db)) -> ConnectionOut:
    conn = await db.get(ProviderConnection, connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Không tìm thấy Connection Profile.")

    raw_api_key = vault.decrypt(conn.encrypted_api_key)
    provider = (
        MockVideoProvider()
        if conn.provider_type == "mock"
        else Muse2ApiAdapter(base_url=conn.base_url, api_key=raw_api_key, timeout=conn.timeout_seconds)
    )
    health = await provider.health()
    conn.latency_ms = health.latency_ms
    conn.last_checked_at = datetime.now(timezone.utc)
    if health.status_state in [s.value for s in ConnectionHealthState]:
        conn.health_state = ConnectionHealthState(health.status_state)
    else:
        conn.health_state = ConnectionHealthState.UNAVAILABLE

    models_list: list[dict[str, Any]] = []
    if health.healthy:
        try:
            discovered = await provider.list_models()
            models_list = [
                {
                    "model_id": m.model_id,
                    "modality": m.modality,
                    "verification_status": m.verification_status,
                }
                for m in discovered
            ]
        except ProviderFailure as exc:
            conn.last_error_redacted = SecretRedactor.redact_text(str(exc), [raw_api_key or ""])

    conn.capabilities_snapshot = {
        "probed_at": conn.last_checked_at.isoformat(),
        "models": models_list,
        "per_account_proxy": "unsupported",
        "quota_lookup": "unsupported",
    }
    await db.commit()
    await db.refresh(conn)
    return _to_connection_out(conn)
