from __future__ import annotations

import json
import os
import tempfile
import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_m7_sysops_test_")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["MEDIA_ROOT"] = _TEMP_MEDIA_DIR

from muse_orchestrator.core.database import get_db  # noqa: E402
from muse_orchestrator.main import app  # noqa: E402
from muse_orchestrator.models.domain import AuditEvent, Base, StructuredLogRecord  # noqa: E402

_SESSION_MAKER: async_sessionmaker[AsyncSession] | None = None


@pytest.fixture()
async def client(monkeypatch):
    global _SESSION_MAKER
    from muse_orchestrator.core.config import settings

    monkeypatch.setattr(settings, "media_root", _TEMP_MEDIA_DIR)
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _SESSION_MAKER = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with _SESSION_MAKER() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _db_scalar_list(stmt):
    assert _SESSION_MAKER is not None
    async with _SESSION_MAKER() as db:
        return list((await db.execute(stmt)).scalars().all())


@pytest.mark.asyncio
async def test_structured_log_redacts_secrets(client: AsyncClient):
    raw_secret = "sk-test-ultra-secret-999"
    response = await client.post(
        "/api/v1/system/logs",
        json={
            "level": "ERROR",
            "event": "provider.probe_failed",
            "correlation_id": "corr_sysops_1",
            "payload": {"api_key": raw_secret, "note": "probe failed"},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["level"] == "ERROR"
    assert body["correlation_id"] == "corr_sysops_1"
    assert raw_secret not in json.dumps(body["payload_redacted"])

    rows = await _db_scalar_list(select(StructuredLogRecord))
    assert len(rows) == 1
    assert raw_secret not in json.dumps(rows[0].payload_redacted)


@pytest.mark.asyncio
async def test_list_logs_with_filters(client: AsyncClient):
    for level, event, corr in [
        ("INFO", "job.started", "corr_a"),
        ("ERROR", "job.failed", "corr_a"),
        ("INFO", "job.started", "corr_b"),
    ]:
        response = await client.post(
            "/api/v1/system/logs",
            json={"level": level, "event": event, "correlation_id": corr, "payload": {}},
        )
        assert response.status_code == 201, response.text

    assert len((await client.get("/api/v1/system/logs")).json()) == 3
    assert len((await client.get("/api/v1/system/logs", params={"level": "ERROR"})).json()) == 1
    assert len((await client.get("/api/v1/system/logs", params={"correlation_id": "corr_a"})).json()) == 2
    assert len((await client.get("/api/v1/system/logs", params={"event": "job.started"})).json()) == 2
    assert len((await client.get("/api/v1/system/logs", params={"level": "ERROR", "limit": 1})).json()) == 1


@pytest.mark.asyncio
async def test_diagnostics_bundle_is_redacted(client: AsyncClient):
    raw_key = "sk-diag-secret-key-123"
    conn = (
        await client.post(
            "/api/v1/connections",
            json={
                "name": "diag-conn",
                "provider_type": "mock",
                "base_url": "http://localhost:8080",
                "api_key": raw_key,
            },
        )
    ).json()
    await client.post(
        f"/api/v1/connections/{conn['id']}/accounts",
        json={"label": "diag-acct", "api_key": raw_key, "proxy_url": "http://proxy.internal:8080"},
    )
    await client.post(
        "/api/v1/system/logs",
        json={"level": "WARNING", "event": "diag.probe", "payload": {"token": raw_key}},
    )

    response = await client.get("/api/v1/system/diagnostics")
    assert response.status_code == 200, response.text
    bundle = response.json()
    dumped = json.dumps(bundle)
    assert raw_key not in dumped
    assert "summary" in bundle
    assert any(a["label"] == "diag-acct" for a in bundle["accounts"])
    assert bundle["recent_logs"], "expected the created log in diagnostics"
    # masked values are present instead of raw secrets
    assert any("*" in (a.get("api_key_masked") or "") for a in bundle["accounts"])


@pytest.mark.asyncio
async def test_audit_trail_records_account_backup_and_cleanup(client: AsyncClient):
    conn = (
        await client.post(
            "/api/v1/connections",
            json={"name": "audit-conn", "provider_type": "mock", "base_url": "http://localhost:8080"},
        )
    ).json()
    account = (
        await client.post(
            f"/api/v1/connections/{conn['id']}/accounts",
            json={"label": "audit-acct", "api_key": "sk-audit-key"},
        )
    ).json()
    assert account["id"]

    project = (
        await client.post("/api/v1/projects", json={"name": "Audit Project", "aspect_ratio": "16:9", "fps": 24})
    ).json()
    backup = (
        await client.post(f"/api/v1/projects/{project['id']}/backup")
    ).json()
    assert backup["id"]

    # one stale orphan file -> real cleanup (dry_run=false) must write an audit entry
    # (cleanup only deletes inside managed subtrees such as the project's storage dir)
    orphan_dir = os.path.join(_TEMP_MEDIA_DIR, project["id"])
    os.makedirs(orphan_dir, exist_ok=True)
    orphan = os.path.join(orphan_dir, "stale_orphan.bin")
    with open(orphan, "wb") as handle:
        handle.write(b"x" * 64)
    old_mtime = time.time() - 25 * 3600
    os.utime(orphan, (old_mtime, old_mtime))
    cleanup = (
        await client.post("/api/v1/maintenance/orphans/cleanup", json={"dry_run": False})
    ).json()
    assert cleanup["deleted"] >= 1

    actions = {e.action for e in await _db_scalar_list(select(AuditEvent))}
    assert "account.create" in actions
    assert "backup.create" in actions
    assert "storage.cleanup" in actions

    diagnostics = (await client.get("/api/v1/system/diagnostics")).json()
    recent_actions = {e["action"] for e in diagnostics["recent_audit_events"]}
    assert {"account.create", "backup.create", "storage.cleanup"} <= recent_actions
