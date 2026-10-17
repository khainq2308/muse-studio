from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_m7_acct_test_")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["MEDIA_ROOT"] = _TEMP_MEDIA_DIR

from muse_orchestrator.core.database import get_db

from muse_orchestrator.main import app
from muse_orchestrator.models.domain import (
    Base,
    ConnectionAccount,
    ConnectionHealthState,
)
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter
from muse_orchestrator.services.account_pool import (
    build_provider_for_account,
    select_account,
)


@pytest.fixture()
async def client(monkeypatch):
    from muse_orchestrator.core.config import settings

    monkeypatch.setattr(settings, "media_root", _TEMP_MEDIA_DIR)
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _create_connection(client: AsyncClient, name: str = "mock-conn") -> dict:
    response = await client.post(
        "/api/v1/connections",
        json={
            "name": name,
            "provider_type": "mock",
            "base_url": "http://localhost:9",
            "api_key": "conn-level-key",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _account(
    label: str,
    enabled: bool = True,
    health: ConnectionHealthState = ConnectionHealthState.UNKNOWN,
    last_used=None,
    priority: int = 100,
) -> ConnectionAccount:
    return ConnectionAccount(
        id=f"acct_{label}",
        connection_id="conn_1",
        label=label,
        encrypted_api_key="enc",
        api_key_masked="***",
        enabled=enabled,
        priority=priority,
        health_state=health,
        last_used_at=last_used,
    )


async def test_account_crud_and_secret_hygiene(client: AsyncClient):
    conn = await _create_connection(client)
    conn_id = conn["id"]

    created = await client.post(
        f"/api/v1/connections/{conn_id}/accounts",
        json={
            "label": "primary",
            "api_key": "super-secret-key-123",
            "proxy_url": "http://user:pass@proxy.example.com:8080",
            "priority": 10,
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["label"] == "primary"
    # Secrets must never leak through the API.
    assert "super-secret-key-123" not in created.text
    assert "user:pass" not in created.text
    assert body["api_key_masked"] != "super-secret-key-123"
    assert body["proxy_display_host"] == "proxy.example.com"
    assert body["has_proxy"] is True
    account_id = body["id"]

    # Duplicate labels on the same connection are rejected.
    duplicate = await client.post(
        f"/api/v1/connections/{conn_id}/accounts",
        json={"label": "Primary", "api_key": "other-key"},
    )
    assert duplicate.status_code == 409

    listed = await client.get(f"/api/v1/connections/{conn_id}/accounts")
    assert listed.status_code == 200
    assert [item["label"] for item in listed.json()] == ["primary"]

    updated = await client.patch(
        f"/api/v1/connections/{conn_id}/accounts/{account_id}",
        json={"label": "primary-renamed", "enabled": False, "priority": 5},
    )
    assert updated.status_code == 200
    assert updated.json()["label"] == "primary-renamed"
    assert updated.json()["enabled"] is False

    deleted = await client.delete(f"/api/v1/connections/{conn_id}/accounts/{account_id}")
    assert deleted.status_code == 204
    assert (await client.get(f"/api/v1/connections/{conn_id}/accounts")).json() == []


async def test_account_proxy_url_validation(client: AsyncClient):
    conn = await _create_connection(client, name="proxy-conn")
    bad = await client.post(
        f"/api/v1/connections/{conn['id']}/accounts",
        json={"label": "bad-proxy", "api_key": "key", "proxy_url": "ftp://proxy:21"},
    )
    assert bad.status_code == 422

    no_key = await client.post(
        f"/api/v1/connections/{conn['id']}/accounts",
        json={"label": "no-key", "api_key": ""},
    )
    assert no_key.status_code == 422

    missing_conn = await client.post(
        "/api/v1/connections/conn_missing/accounts",
        json={"label": "x", "api_key": "key"},
    )
    assert missing_conn.status_code == 404


async def test_select_account_prefers_never_used_then_lru(client: AsyncClient):
    now = datetime.now(UTC)
    never_used = _account("never")
    used_recently = _account("recent", last_used=now)
    used_long_ago = _account("old", last_used=datetime(2020, 1, 1, tzinfo=UTC))
    disabled = _account("disabled", enabled=False)
    auth_error = _account("bad-auth", health=ConnectionHealthState.AUTH_ERROR)

    # Never-used account goes first.
    assert select_account([used_recently, never_used, used_long_ago]).label == "never"
    # Then least-recently-used.
    assert select_account([used_recently, used_long_ago]).label == "old"
    # Disabled and unhealthy accounts are skipped.
    assert select_account([disabled, auth_error]) is None
    assert select_account([]) is None


async def test_build_provider_for_account_uses_proxy():
    account = _account("with-proxy")

    class _Conn:
        provider_type = "muse2api"
        base_url = "http://provider:8000"
        timeout_seconds = 30.0
        encrypted_api_key = "conn-enc"

    # Monkeypatch the vault decrypt to avoid depending on encryption keys.
    import muse_orchestrator.services.account_pool as pool

    account.encrypted_proxy_url = "proxy-enc"
    mapping = {"enc": "decrypted-key", "conn-enc": "conn-key", "proxy-enc": "http://proxy.example.com:8080"}
    real_decrypt = pool.vault.decrypt
    pool.vault.decrypt = lambda value: mapping.get(value, value)
    try:
        provider = build_provider_for_account(_Conn(), account)
        assert isinstance(provider, Muse2ApiAdapter)
        assert provider.proxy == "http://proxy.example.com:8080"

        provider_no_account = build_provider_for_account(_Conn(), None)
        assert isinstance(provider_no_account, Muse2ApiAdapter)
        assert provider_no_account.proxy is None
    finally:
        pool.vault.decrypt = real_decrypt


async def test_per_account_proxy_capability_flips_with_accounts(client: AsyncClient):
    conn = await _create_connection(client, name="cap-conn")
    conn_id = conn["id"]

    probed = await client.post(f"/api/v1/connections/{conn_id}/test")
    assert probed.status_code == 200, probed.text
    assert probed.json()["capabilities_snapshot"]["per_account_proxy"] == "unsupported"

    created = await client.post(
        f"/api/v1/connections/{conn_id}/accounts",
        json={"label": "a1", "api_key": "key-1"},
    )
    assert created.status_code == 201

    probed_again = await client.post(f"/api/v1/connections/{conn_id}/test")
    snapshot = probed_again.json()["capabilities_snapshot"]
    assert snapshot["per_account_proxy"] == "supported"
    assert snapshot["account_count"] == 1


async def test_account_probe_with_mock_provider(client: AsyncClient):
    conn = await _create_connection(client, name="probe-conn")
    created = await client.post(
        f"/api/v1/connections/{conn['id']}/accounts",
        json={"label": "probe-me", "api_key": "key"},
    )
    account_id = created.json()["id"]
    probed = await client.post(f"/api/v1/connections/{conn['id']}/accounts/{account_id}/test")
    assert probed.status_code == 200, probed.text
    assert probed.json()["health_state"] in ("healthy", "unknown", "degraded", "unavailable")
