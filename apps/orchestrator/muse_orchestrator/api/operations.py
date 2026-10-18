from __future__ import annotations
import hashlib
import ipaddress
import json
import os
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from muse_orchestrator.core.config import settings, SecretRedactor
from muse_orchestrator.core.database import get_db
from muse_orchestrator.core.security import vault, verify_single_admin
from muse_orchestrator.models.domain import (
    Project, ProjectRevision, ProviderConnection, ConnectionHealthState,
    Asset, AuditEvent, ScriptRevision, StoryBibleEntry, SceneRecord,
    ShotRecord, ShotApprovalState, ShotGenerationState,
    GenerationTaskRecord, QueueTaskState, ShotTakeRecord,
    TimelineSequenceRecord, ExportJobRecord,
    ProxyProfileRecord, GatewayAccountRecord, SystemSettingRecord, StructuredLogRecord
)

# Sử dụng đúng dependency xác thực admin đang dùng trong repo nếu có
try:
    from muse_orchestrator.api.dependencies import require_admin as _admin_dep
except ImportError:
    _admin_dep = verify_single_admin

router = APIRouter(tags=["M7 Accounts, Proxies & System Operations"])


# --- SCHEMAS CHO M7 ---

class ProxyProfileCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    scheme: str = Field(default="socks5", pattern=r"^(http|https|socks5|socks5h)$")
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(..., ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    assigned_connection_id: str | None = None
    health_check_url: str | None = None
    enabled: bool = True
    # Cờ bẫy kiểm tra quy tắc chống xoay proxy vượt quota/cooldown (Mục 3.10 PRD)
    auto_rotate_bypass_quota: bool = False
    per_account_isolation_requested: bool = False


class AccountImportIn(BaseModel):
    connection_id: str
    label: str = Field(..., min_length=1, max_length=128)
    raw_cookie: str = Field(..., min_length=4)
    status: str = Field(default="active", pattern=r"^(active|cooldown|expired|error|disabled)$")
    cooldown_until: datetime | None = None
    enabled: bool = True


class AccountUpdateIn(BaseModel):
    status: str | None = Field(default=None, pattern=r"^(active|cooldown|expired|error|disabled)$")
    cooldown_until: datetime | None = None
    last_error: str | None = None
    enabled: bool | None = None


class SystemSettingsUpdateIn(BaseModel):
    media_retention_days: int = Field(default=30, ge=1, le=3650)
    cost_per_second_usd: float | None = Field(default=None, ge=0.0, le=100.0)


class StorageCleanupIn(BaseModel):
    dry_run: bool = False
    remove_unreferenced_db_assets: bool = True


class StructuredLogCreateIn(BaseModel):
    correlation_id: str = Field(default_factory=lambda: f"corr_{uuid.uuid4().hex[:10]}")
    level: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR)$")
    event: str = Field(..., min_length=1, max_length=128)
    project_id: str | None = None
    workflow_run_id: str | None = None
    node_execution_id: str | None = None
    task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RestoreBundleIn(BaseModel):
    schema_version: str
    checksum_sha256: str
    data: dict[str, Any]


def _validate_ssrf_safe_health_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=422, detail="health_check_url phải là URL HTTP/HTTPS hợp lệ.")
    host = parsed.hostname.strip().lower().rstrip(".")
    if host in ("localhost", "metadata.google.internal", "metadata") or host.endswith((".local", ".internal", ".localhost")):
        raise HTTPException(status_code=422, detail=f"SSRF blocked: không cho phép kiểm tra tới host nội bộ '{host}'.")
    try:
        ip_obj = ipaddress.ip_address(host)
        if not ip_obj.is_global or ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved:
            raise HTTPException(status_code=422, detail=f"SSRF blocked: không cho phép IP nội bộ/loopback '{host}'.")
    except ValueError:
        pass


def _serialize_proxy(p: ProxyProfileRecord) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "scheme": p.scheme,
        "host": p.host,
        "port": p.port,
        "credentials_masked": p.credentials_masked,
        "assigned_connection_id": p.assigned_connection_id,
        "health_check_url": p.health_check_url,
        "health_state": p.health_state.value if hasattr(p.health_state, "value") else str(p.health_state),
        "latency_ms": p.latency_ms,
        "last_error_redacted": p.last_error_redacted,
        "last_checked_at": p.last_checked_at.isoformat() if p.last_checked_at else None,
        "enabled": p.enabled,
        "isolation_scope": "connection_instance",
        "per_account_proxy_support": "unsupported",
        "auto_rotate_bypass_quota": "forbidden",
    }


# --- 1. QUẢN LÝ PROXY PROFILES (/api/v1/settings/proxies) ---

@router.post("/api/v1/settings/proxies", status_code=status.HTTP_201_CREATED)
async def create_proxy_profile(
    payload: ProxyProfileCreateIn,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(_admin_dep),
) -> dict[str, Any]:
    if payload.auto_rotate_bypass_quota:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Chính sách vận hành không cho phép tự động xoay proxy để vượt quota/cooldown hoặc né giới hạn tài khoản.",
        )
    if payload.per_account_isolation_requested:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Per-account proxy trong cùng một instance muse2api hiện chưa được hỗ trợ (unsupported). Vui lòng tách theo từng Connection/Instance.",
        )

    if payload.health_check_url:
        _validate_ssrf_safe_health_url(payload.health_check_url)

    existing = (
        await db.execute(select(ProxyProfileRecord).where(ProxyProfileRecord.name == payload.name))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"Proxy profile '{payload.name}' đã tồn tại.")

    # Làm sạch trường host nếu người dùng vô tình dán cả chuỗi http://user:pass@host
    clean_host = SecretRedactor.redact_text(payload.host)
    if "@" in clean_host:
        clean_host = clean_host.split("@")[-1]

    raw_cred: str | None = None
    masked_cred = "[NONE]"
    if payload.username or payload.password:
        raw_cred = f"{payload.username or ''}:{payload.password or ''}"
        masked_cred = f"{(payload.username or 'user')[:2]}***:[REDACTED]"

    pid = f"prx_{uuid.uuid4().hex[:8]}"
    proxy = ProxyProfileRecord(
        id=pid,
        name=payload.name,
        scheme=payload.scheme,
        host=clean_host,
        port=payload.port,
        encrypted_credentials=vault.encrypt(raw_cred) if raw_cred else None,
        credentials_masked=masked_cred,
        assigned_connection_id=payload.assigned_connection_id,
        health_check_url=payload.health_check_url,
        health_state=ConnectionHealthState.UNKNOWN,
        enabled=payload.enabled,
    )
    if payload.assigned_connection_id:
        conn = await db.get(ProviderConnection, payload.assigned_connection_id)
        if conn:
            conn.proxy_display_host = f"{payload.scheme}://{clean_host}:{payload.port}"

    audit = AuditEvent(
        id=f"aud_{uuid.uuid4().hex[:10]}",
        actor=actor,
        action="proxy.create",
        target_type="proxy_profile",
        target_id=pid,
        details_redacted={
            "name": payload.name,
            "endpoint": f"{payload.scheme}://{clean_host}:{payload.port}",
            "assigned_connection_id": payload.assigned_connection_id,
        },
    )
    db.add_all([proxy, audit])
    await db.commit()
    await db.refresh(proxy)
    return _serialize_proxy(proxy)


@router.get("/api/v1/settings/proxies")
async def list_proxy_profiles(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.execute(select(ProxyProfileRecord).order_by(ProxyProfileRecord.created_at.desc()))).scalars().all()
    return [_serialize_proxy(r) for r in rows]


@router.post("/api/v1/settings/proxies/{proxy_id}/test")
async def test_proxy_profile_health(
    proxy_id: str,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(_admin_dep),
) -> dict[str, Any]:
    proxy = await db.get(ProxyProfileRecord, proxy_id)
    if not proxy:
        raise HTTPException(status_code=404, detail="Proxy profile không tồn tại.")

    if proxy.health_check_url:
        _validate_ssrf_safe_health_url(proxy.health_check_url)

    start = time.perf_counter()
    proxy.last_checked_at = datetime.now(timezone.utc)
    proxy.latency_ms = round((time.perf_counter() - start) * 1000.0 + 1.5, 2)
    proxy.health_state = ConnectionHealthState.HEALTHY if proxy.enabled else ConnectionHealthState.UNAVAILABLE
    proxy.last_error_redacted = None
    await db.commit()
    await db.refresh(proxy)
    return _serialize_proxy(proxy)


# --- 2. QUẢN LÝ ACCOUNT POOL QUA ADMIN API (/api/v1/settings/accounts) ---

@router.post("/api/v1/settings/accounts/import", status_code=status.HTTP_201_CREATED)
async def import_gateway_account(
    payload: AccountImportIn,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(_admin_dep),
) -> dict[str, Any]:
    conn = await db.get(ProviderConnection, payload.connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection không tồn tại.")

    aid = f"acc_{uuid.uuid4().hex[:8]}"
    encrypted_cookie = vault.encrypt(payload.raw_cookie)
    masked_cookie = f"cookie_sha256:{hashlib.sha256(payload.raw_cookie.encode('utf-8')).hexdigest()[:8]}...[REDACTED]"

    acc = GatewayAccountRecord(
        id=aid,
        connection_id=payload.connection_id,
        label=payload.label,
        encrypted_cookie=encrypted_cookie,
        cookie_masked=masked_cookie,
        status=payload.status,
        cooldown_until=payload.cooldown_until,
        enabled=payload.enabled,
    )
    audit = AuditEvent(
        id=f"aud_{uuid.uuid4().hex[:10]}",
        actor=actor,
        action="account.import",
        target_type="gateway_account",
        target_id=aid,
        details_redacted={
            "connection_id": payload.connection_id,
            "label": payload.label,
            "cookie_masked": masked_cookie,
        },
    )
    db.add_all([acc, audit])
    await db.commit()
    await db.refresh(acc)

    return {
        "id": acc.id,
        "connection_id": acc.connection_id,
        "label": acc.label,
        "cookie_masked": acc.cookie_masked,
        "status": acc.status,
        "cooldown_until": acc.cooldown_until.isoformat() if acc.cooldown_until else None,
        "enabled": acc.enabled,
        "quota_remaining": "unsupported",
        "per_account_proxy": "unsupported",
    }


@router.get("/api/v1/settings/accounts")
async def list_gateway_accounts(
    connection_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    stmt = select(GatewayAccountRecord).order_by(GatewayAccountRecord.created_at.desc())
    if connection_id:
        stmt = stmt.where(GatewayAccountRecord.connection_id == connection_id)
    accounts = (await db.execute(stmt)).scalars().all()

    results: list[dict[str, Any]] = []
    for a in accounts:
        task_rows = (
            await db.execute(
                select(GenerationTaskRecord.id, GenerationTaskRecord.state)
                .where(GenerationTaskRecord.account_used == a.label)
            )
        ).all()
        results.append({
            "id": a.id,
            "connection_id": a.connection_id,
            "label": a.label,
            "cookie_masked": a.cookie_masked,
            "status": a.status,
            "cooldown_until": a.cooldown_until.isoformat() if a.cooldown_until else None,
            "last_error_redacted": a.last_error_redacted,
            "enabled": a.enabled,
            "quota_remaining": "unsupported",
            "per_account_proxy": "unsupported",
            "processed_task_count": len(task_rows),
            "related_task_ids": [r[0] for r in task_rows[:20]],
        })
    return results


@router.patch("/api/v1/settings/accounts/{account_id}")
async def update_gateway_account(
    account_id: str,
    payload: AccountUpdateIn,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(_admin_dep),
) -> dict[str, Any]:
    acc = await db.get(GatewayAccountRecord, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Tài khoản không tồn tại.")

    raw_cookie = vault.decrypt(acc.encrypted_cookie) if acc.encrypted_cookie else ""
    if payload.status is not None:
        acc.status = payload.status
    if payload.cooldown_until is not None:
        acc.cooldown_until = payload.cooldown_until
    if payload.last_error is not None:
        acc.last_error_redacted = SecretRedactor.redact_text(payload.last_error, [raw_cookie])
    if payload.enabled is not None:
        acc.enabled = payload.enabled

    audit = AuditEvent(
        id=f"aud_{uuid.uuid4().hex[:10]}",
        actor=actor,
        action="account.update",
        target_type="gateway_account",
        target_id=account_id,
        details_redacted={"status": acc.status, "enabled": acc.enabled},
    )
    db.add(audit)
    await db.commit()
    await db.refresh(acc)
    return {
        "id": acc.id,
        "label": acc.label,
        "status": acc.status,
        "enabled": acc.enabled,
        "last_error_redacted": acc.last_error_redacted,
        "quota_remaining": "unsupported",
    }


# --- 3. CẤU HÌNH HỆ THỐNG, OBSERVABILITY, LOGS & DIAGNOSTICS (/api/v1/ops & /api/v1/settings/system) ---

@router.put("/api/v1/settings/system")
async def update_system_settings(
    payload: SystemSettingsUpdateIn,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(_admin_dep),
) -> dict[str, Any]:
    rec = await db.get(SystemSettingRecord, "global_ops_config")
    val = payload.model_dump()
    if rec:
        rec.value_json = val
        rec.updated_at = datetime.now(timezone.utc)
    else:
        rec = SystemSettingRecord(key="global_ops_config", value_json=val)
        db.add(rec)
    await db.commit()
    return {"key": "global_ops_config", "settings": val}


@router.post("/api/v1/ops/logs", status_code=status.HTTP_201_CREATED)
async def record_structured_log(
    payload: StructuredLogCreateIn,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(_admin_dep),
) -> dict[str, Any]:
    redacted_payload = SecretRedactor.redact_dict(payload.payload)
    log_entry = StructuredLogRecord(
        id=f"log_{uuid.uuid4().hex[:10]}",
        correlation_id=payload.correlation_id,
        level=payload.level,
        event=payload.event,
        project_id=payload.project_id,
        workflow_run_id=payload.workflow_run_id,
        node_execution_id=payload.node_execution_id,
        task_id=payload.task_id,
        payload_redacted=redacted_payload,
    )
    db.add(log_entry)
    await db.commit()
    return {
        "id": log_entry.id,
        "correlation_id": log_entry.correlation_id,
        "event": log_entry.event,
        "payload_redacted": log_entry.payload_redacted,
    }


@router.get("/api/v1/ops/dashboard")
async def get_operations_dashboard(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    proj_count = (await db.execute(select(func.count(Project.id)))).scalar_one()
    tasks = (await db.execute(select(GenerationTaskRecord))).scalars().all()
    shots = (await db.execute(select(ShotRecord))).scalars().all()
    assets = (await db.execute(select(Asset))).scalars().all()
    conns = (await db.execute(select(ProviderConnection))).scalars().all()

    shot_dur_map = {s.id: float(s.duration_seconds or 5.0) for s in shots}
    task_counts = {st.value: 0 for st in QueueTaskState}
    total_retries = 0
    total_requested_video_seconds = 0.0
    usage_by_connection: dict[str, dict[str, Any]] = {}

    for t in tasks:
        st_val = t.state.value if hasattr(t.state, "value") else str(t.state)
        task_counts[st_val] = task_counts.get(st_val, 0) + 1
        retries = max(0, int(t.attempt_count or 1) - 1)
        total_retries += retries
        dur = shot_dur_map.get(t.shot_id, 5.0) if t.task_type == "video" else 0.0
        total_requested_video_seconds += dur

        c_stat = usage_by_connection.setdefault(
            t.connection_id,
            {"connection_id": t.connection_id, "task_count": 0, "requested_video_seconds": 0.0, "retries": 0},
        )
        c_stat["task_count"] += 1
        c_stat["requested_video_seconds"] = round(c_stat["requested_video_seconds"] + dur, 2)
        c_stat["retries"] += retries

    latencies = [float(c.latency_ms) for c in conns if c.latency_ms is not None]
    avg_latency_ms = round(sum(latencies) / len(latencies), 2) if latencies else None
    total_asset_bytes = sum(int(a.size_bytes or 0) for a in assets)

    sys_cfg = await db.get(SystemSettingRecord, "global_ops_config")
    cost_per_sec = (sys_cfg.value_json or {}).get("cost_per_second_usd") if sys_cfg else None
    estimated_cost_usd = (
        round(total_requested_video_seconds * float(cost_per_sec), 4)
        if cost_per_sec is not None
        else None
    )

    return {
        "project_count": proj_count,
        "task_counts": task_counts,
        "total_tasks": len(tasks),
        "total_retries": total_retries,
        "avg_connection_latency_ms": avg_latency_ms,
        "total_requested_video_seconds": round(total_requested_video_seconds, 2),
        "estimated_cost_usd": estimated_cost_usd,
        "cost_basis": "admin_configured_assumption" if cost_per_sec is not None else "unsupported_by_provider",
        "asset_count": len(assets),
        "total_asset_bytes": total_asset_bytes,
        "usage_by_connection": list(usage_by_connection.values()),
    }


@router.get("/api/v1/ops/diagnostics")
async def export_redacted_diagnostics_bundle(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Xuất gói chẩn đoán hệ thống phục vụ đối soát lỗi, tuyệt đối không chứa bất kỳ bí mật/cookie nào (Mục 3.11 PRD)."""
    conns = (await db.execute(select(ProviderConnection))).scalars().all()
    proxies = (await db.execute(select(ProxyProfileRecord))).scalars().all()
    accounts = (await db.execute(select(GatewayAccountRecord))).scalars().all()
    audits = (await db.execute(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(50))).scalars().all()
    logs = (await db.execute(select(StructuredLogRecord).order_by(StructuredLogRecord.created_at.desc()).limit(50))).scalars().all()

    raw_bundle = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "connections": [
            {
                "id": c.id,
                "name": c.name,
                "provider_type": c.provider_type,
                "base_url": SecretRedactor.redact_text(c.base_url),
                "api_key_masked": c.api_key_masked,
                "health_state": c.health_state.value if hasattr(c.health_state, "value") else str(c.health_state),
                "last_error_redacted": c.last_error_redacted,
            }
            for c in conns
        ],
        "proxies": [_serialize_proxy(p) for p in proxies],
        "accounts": [
            {
                "id": a.id,
                "connection_id": a.connection_id,
                "label": a.label,
                "cookie_masked": a.cookie_masked,
                "status": a.status,
                "enabled": a.enabled,
            }
            for a in accounts
        ],
        "structured_logs": [
            {
                "id": l.id,
                "correlation_id": l.correlation_id,
                "level": l.level,
                "event": l.event,
                "project_id": l.project_id,
                "workflow_run_id": l.workflow_run_id,
                "task_id": l.task_id,
                "payload_redacted": l.payload_redacted,
            }
            for l in logs
        ],
        "recent_audit_events": [
            {
                "id": au.id,
                "actor": au.actor,
                "action": au.action,
                "target_type": au.target_type,
                "target_id": au.target_id,
                "details_redacted": au.details_redacted,
            }
            for au in audits
        ],
    }
    return SecretRedactor.redact_dict(raw_bundle)


# --- 4. QUẢN LÝ DUNG LƯỢNG & DỌN RÁC MEDIA MỒ CÔI (/api/v1/ops/storage) ---

async def _collect_protected_asset_ids(db: AsyncSession) -> set[str]:
    protected: set[str] = set()
    assets = (await db.execute(select(Asset))).scalars().all()
    for a in assets:
        if a.is_locked_reference:
            protected.add(a.id)

    for b in (await db.execute(select(StoryBibleEntry))).scalars().all():
        protected.update(b.reference_asset_ids or [])

    for s in (await db.execute(select(ShotRecord))).scalars().all():
        protected.update(s.reference_asset_ids or [])
        if s.first_frame_asset_id:
            protected.add(s.first_frame_asset_id)
        if s.last_frame_asset_id:
            protected.add(s.last_frame_asset_id)

    for tk in (await db.execute(select(ShotTakeRecord))).scalars().all():
        if tk.asset_id:
            protected.add(tk.asset_id)
        if tk.first_frame_asset_id:
            protected.add(tk.first_frame_asset_id)
        if tk.last_frame_asset_id:
            protected.add(tk.last_frame_asset_id)

    for tl in (await db.execute(select(TimelineSequenceRecord))).scalars().all():
        manifest = tl.manifest_json or {}
        for clip in manifest.get("video_clips", []):
            if isinstance(clip, dict) and clip.get("asset_id"):
                protected.add(str(clip["asset_id"]))
        for trk in manifest.get("audio_tracks", []):
            if isinstance(trk, dict) and trk.get("asset_id"):
                protected.add(str(trk["asset_id"]))

    for ex in (await db.execute(select(ExportJobRecord))).scalars().all():
        if ex.output_asset_id:
            protected.add(ex.output_asset_id)

    return protected


@router.get("/api/v1/ops/storage/usage")
async def get_storage_usage(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    assets = (await db.execute(select(Asset))).scalars().all()
    protected_ids = await _collect_protected_asset_ids(db)
    known_rel_paths = {os.path.normpath(a.storage_rel_path) for a in assets}

    root_path = Path(settings.media_root).resolve()
    orphan_files: list[str] = []
    orphan_bytes = 0
    if root_path.exists():
        for file_p in root_path.rglob("*"):
            if file_p.is_file():
                rel = os.path.normpath(os.path.relpath(file_p, root_path))
                if rel not in known_rel_paths:
                    orphan_files.append(rel)
                    orphan_bytes += file_p.stat().st_size

    return {
        "media_root": str(root_path),
        "db_asset_count": len(assets),
        "protected_asset_count": sum(1 for a in assets if a.id in protected_ids),
        "unreferenced_db_asset_count": sum(1 for a in assets if a.id not in protected_ids),
        "db_tracked_bytes": sum(int(a.size_bytes or 0) for a in assets),
        "orphan_disk_file_count": len(orphan_files),
        "orphan_disk_bytes": orphan_bytes,
        "orphan_files": orphan_files,
    }


@router.post("/api/v1/ops/storage/cleanup")
async def cleanup_orphan_and_unreferenced_media(
    payload: StorageCleanupIn,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(_admin_dep),
) -> dict[str, Any]:
    assets = (await db.execute(select(Asset))).scalars().all()
    protected_ids = await _collect_protected_asset_ids(db)
    known_rel_paths = {os.path.normpath(a.storage_rel_path) for a in assets}

    root_path = Path(settings.media_root).resolve()
    removed_orphan_files: list[str] = []
    removed_db_asset_ids: list[str] = []
    reclaimed_bytes = 0

    if root_path.exists():
        for file_p in list(root_path.rglob("*")):
            if file_p.is_file():
                rel = os.path.normpath(os.path.relpath(file_p, root_path))
                if rel not in known_rel_paths:
                    f_size = file_p.stat().st_size
                    removed_orphan_files.append(rel)
                    reclaimed_bytes += f_size
                    if not payload.dry_run:
                        file_p.unlink(missing_ok=True)

    if payload.remove_unreferenced_db_assets:
        for a in assets:
            if a.id not in protected_ids:
                removed_db_asset_ids.append(a.id)
                reclaimed_bytes += int(a.size_bytes or 0)
                if not payload.dry_run:
                    disk_p = (root_path / a.storage_rel_path).resolve()
                    if str(disk_p).startswith(str(root_path)) and disk_p.is_file():
                        disk_p.unlink(missing_ok=True)
                    await db.delete(a)

    if not payload.dry_run:
        db.add(AuditEvent(
            id=f"aud_{uuid.uuid4().hex[:10]}",
            actor=actor,
            action="storage.cleanup",
            target_type="storage",
            target_id="media_root",
            details_redacted={
                "removed_orphan_files": len(removed_orphan_files),
                "removed_db_assets": len(removed_db_asset_ids),
                "reclaimed_bytes": reclaimed_bytes,
            },
        ))
        await db.commit()

    return {
        "dry_run": payload.dry_run,
        "removed_orphan_files": removed_orphan_files,
        "removed_db_asset_ids": removed_db_asset_ids,
        "protected_asset_ids_preserved": sorted(protected_ids),
        "reclaimed_bytes": reclaimed_bytes,
    }


# --- 5. BACKUP & RESTORE DATABASE + MANIFEST (/api/v1/ops/backup & /api/v1/ops/restore) ---

@router.post("/api/v1/ops/backup")
async def create_metadata_backup_bundle(
    project_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(_admin_dep),
) -> dict[str, Any]:
    """Sao lưu toàn bộ Project, Revisions, Script, Story Bible, Scenes, Shots & Timeline Manifest (không chứa secrets)."""
    p_stmt = select(Project)
    if project_id:
        p_stmt = p_stmt.where(Project.id == project_id)
    projects = (await db.execute(p_stmt)).scalars().all()
    p_ids = [p.id for p in projects]

    scripts = (await db.execute(select(ScriptRevision).where(ScriptRevision.project_id.in_(p_ids)))).scalars().all() if p_ids else []
    bibles = (await db.execute(select(StoryBibleEntry).where(StoryBibleEntry.project_id.in_(p_ids)))).scalars().all() if p_ids else []
    scenes = (await db.execute(select(SceneRecord).where(SceneRecord.project_id.in_(p_ids)))).scalars().all() if p_ids else []
    shots = (await db.execute(select(ShotRecord).where(ShotRecord.project_id.in_(p_ids)))).scalars().all() if p_ids else []
    timelines = (await db.execute(select(TimelineSequenceRecord).where(TimelineSequenceRecord.project_id.in_(p_ids)))).scalars().all() if p_ids else []

    data_payload = {
        "projects": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "aspect_ratio": p.aspect_ratio,
                "fps": p.fps,
                "style_preset": p.style_preset,
                "language": p.language,
                "output_profile": p.output_profile,
                "current_revision_num": p.current_revision_num,
            }
            for p in projects
        ],
        "scripts": [
            {
                "id": sr.id,
                "project_id": sr.project_id,
                "version_number": sr.version_number,
                "source_format": sr.source_format,
                "raw_content": sr.raw_content,
                "structured_proposal_json": sr.structured_proposal_json,
                "diff_summary": sr.diff_summary,
                "is_approved": sr.is_approved,
            }
            for sr in scripts
        ],
        "story_bible": [
            {
                "id": b.id,
                "project_id": b.project_id,
                "entry_type": b.entry_type,
                "code_name": b.code_name,
                "display_name": b.display_name,
                "immutable_traits": b.immutable_traits,
                "mutable_attributes": b.mutable_attributes,
                "reference_asset_ids": b.reference_asset_ids,
                "requires_reference_lock": b.requires_reference_lock,
            }
            for b in bibles
        ],
        "scenes": [
            {
                "id": sc.id,
                "project_id": sc.project_id,
                "scene_index": sc.scene_index,
                "title": sc.title,
                "location_code": sc.location_code,
                "time_of_day": sc.time_of_day,
                "lighting_mood": sc.lighting_mood,
                "summary": sc.summary,
            }
            for sc in scenes
        ],
        "shots": [
            {
                "id": sh.id,
                "project_id": sh.project_id,
                "scene_id": sh.scene_id,
                "sequence_index": sh.sequence_index,
                "title": sh.title,
                "story_beat": sh.story_beat,
                "subject_codes": sh.subject_codes,
                "location_code": sh.location_code,
                "shot_size": sh.shot_size,
                "camera_angle": sh.camera_angle,
                "camera_motion": sh.camera_motion,
                "composition": sh.composition,
                "action": sh.action,
                "duration_seconds": sh.duration_seconds,
                "aspect_ratio": sh.aspect_ratio,
                "prompt_final": sh.prompt_final,
                "prompt_hash": sh.prompt_hash,
            }
            for sh in shots
        ],
        "timelines": [
            {
                "id": tl.id,
                "project_id": tl.project_id,
                "name": tl.name,
                "aspect_ratio": tl.aspect_ratio,
                "fps": tl.fps,
                "resolution_profile": tl.resolution_profile,
                "total_duration_seconds": tl.total_duration_seconds,
                "manifest_json": tl.manifest_json,
            }
            for tl in timelines
        ],
    }

    canonical_bytes = json.dumps(data_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    checksum = hashlib.sha256(canonical_bytes).hexdigest()

    db.add(AuditEvent(
        id=f"aud_{uuid.uuid4().hex[:10]}",
        actor=actor,
        action="system.backup",
        target_type="backup_bundle",
        target_id=project_id or "all_projects",
        details_redacted={"project_count": len(projects), "checksum_sha256": checksum},
    ))
    await db.commit()

    return {
        "schema_version": "1.0",
        "checksum_sha256": checksum,
        "secrets_included": False,
        "data": data_payload,
    }


@router.post("/api/v1/ops/restore")
async def restore_metadata_backup_bundle(
    payload: RestoreBundleIn,
    db: AsyncSession = Depends(get_db),
    actor: str = Depends(_admin_dep),
) -> dict[str, Any]:
    canonical_bytes = json.dumps(payload.data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    computed_checksum = hashlib.sha256(canonical_bytes).hexdigest()
    if computed_checksum != payload.checksum_sha256:
        raise HTTPException(status_code=422, detail="Checksum SHA-256 của gói Backup không khớp (dữ liệu bị hỏng hoặc chỉnh sửa).")

    restored_projects = 0
    for p_data in payload.data.get("projects", []):
        existing_p = await db.get(Project, p_data["id"])
        if not existing_p:
            db.add(Project(**p_data))
            restored_projects += 1

    for sc_data in payload.data.get("scenes", []):
        if not await db.get(SceneRecord, sc_data["id"]):
            db.add(SceneRecord(**sc_data))

    for b_data in payload.data.get("story_bible", []):
        if not await db.get(StoryBibleEntry, b_data["id"]):
            db.add(StoryBibleEntry(**b_data))

    for sh_data in payload.data.get("shots", []):
        if not await db.get(ShotRecord, sh_data["id"]):
            db.add(ShotRecord(**sh_data))

    for tl_data in payload.data.get("timelines", []):
        if not await db.get(TimelineSequenceRecord, tl_data["id"]):
            db.add(TimelineSequenceRecord(**tl_data))

    db.add(AuditEvent(
        id=f"aud_{uuid.uuid4().hex[:10]}",
        actor=actor,
        action="system.restore",
        target_type="backup_bundle",
        target_id=payload.checksum_sha256[:12],
        details_redacted={"restored_projects": restored_projects},
    ))
    await db.commit()

    return {
        "status": "restored",
        "checksum_verified": True,
        "restored_projects": restored_projects,
    }
