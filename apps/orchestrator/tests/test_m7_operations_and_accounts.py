from __future__ import annotations
import os
import pathlib
import tempfile
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_m7_test_")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("MEDIA_ROOT", _TEMP_MEDIA_DIR)

from muse_orchestrator.main import app
from muse_orchestrator.core.config import settings
from muse_orchestrator.core.database import get_db
from muse_orchestrator.models.domain import Base, Project


@pytest.fixture()
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
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


@pytest.mark.asyncio
async def test_m7_proxy_isolation_account_pool_masking_and_diagnostics_redaction(client: AsyncClient):
    # 1. Tạo Connection Profile
    c_res = await client.post(
        "/api/v1/connections",
        json={
            "name": "muse-instance-jp-01",
            "provider_type": "mock",
            "base_url": "http://localhost:8080",
            "api_key": "sk-muse-live-secret-key-9999",
            "max_concurrency": 3,
        },
    )
    assert c_res.status_code == 201
    conn_id = c_res.json()["id"]

    # 2. Chặn hành vi tạo Proxy có bật auto_rotate_bypass_quota hoặc per_account_isolation_requested (Mục 3.10 PRD)
    bad_rotate = await client.post(
        "/api/v1/settings/proxies",
        json={
            "name": "abusive-rotating-proxy",
            "scheme": "socks5",
            "host": "198.51.100.10",
            "port": 1080,
            "auto_rotate_bypass_quota": True,
        },
    )
    assert bad_rotate.status_code == 422

    bad_per_acc = await client.post(
        "/api/v1/settings/proxies",
        json={
            "name": "fake-per-account-proxy",
            "scheme": "socks5",
            "host": "198.51.100.10",
            "port": 1080,
            "per_account_isolation_requested": True,
        },
    )
    assert bad_per_acc.status_code == 422

    # 3. Tạo Proxy Profile hợp lệ ở cấp Instance với mật khẩu nhạy cảm
    prx_res = await client.post(
        "/api/v1/settings/proxies",
        json={
            "name": "tokyo-instance-proxy-01",
            "scheme": "socks5",
            "host": "203.0.113.55",
            "port": 1080,
            "username": "proxy_op",
            "password": "UltraSecretProxyPassword!2026",
            "assigned_connection_id": conn_id,
        },
    )
    assert prx_res.status_code == 201
    prx = prx_res.json()
    assert "UltraSecretProxyPassword!2026" not in prx_res.text
    assert prx["per_account_proxy_support"] == "unsupported"
    assert prx["isolation_scope"] == "connection_instance"

    # Kiểm tra health check của Proxy
    probe_prx = await client.post(f"/api/v1/settings/proxies/{prx['id']}/test")
    assert probe_prx.status_code == 200
    assert probe_prx.json()["health_state"] == "healthy"

    # 4. Nhập Account/Cookie vào Pool qua Admin API -> Cookie phải bị mask và quota_remaining = unsupported
    raw_cookie_secret = "muse_session=eyJhbGciOiJIUzI1NiJ9.SuperSecretCookieToken888; __cf_bm=xyz"
    acc_res = await client.post(
        "/api/v1/settings/accounts/import",
        json={
            "connection_id": conn_id,
            "label": "mock_pool_acc_01",
            "raw_cookie": raw_cookie_secret,
        },
    )
    assert acc_res.status_code == 201
    assert "SuperSecretCookieToken888" not in acc_res.text
    acc_data = acc_res.json()
    assert acc_data["quota_remaining"] == "unsupported"
    assert acc_data["per_account_proxy"] == "unsupported"

    # 5. Ghi nhận Structured Log chứa thông tin nhạy cảm & Kiểm tra Diagnostics Bundle Redaction
    await client.post(
        "/api/v1/ops/logs",
        json={
            "correlation_id": "corr_m7_test_01",
            "level": "WARNING",
            "event": "upstream.cookie_refresh",
            "payload": {
                "cookie": raw_cookie_secret,
                "proxy_url": "socks5://proxy_op:UltraSecretProxyPassword!2026@203.0.113.55:1080",
                "authorization": "Bearer sk-muse-live-secret-key-9999",
            },
        },
    )

    diag_res = await client.get("/api/v1/ops/diagnostics")
    assert diag_res.status_code == 200
    diag_text = diag_res.text
    assert "SuperSecretCookieToken888" not in diag_text
    assert "UltraSecretProxyPassword!2026" not in diag_text
    assert "sk-muse-live-secret-key-9999" not in diag_text
    assert "corr_m7_test_01" in diag_text


@pytest.mark.asyncio
async def test_m7_storage_orphan_cleanup_preserves_locked_references_and_backup_restore(client: AsyncClient, tmp_path):
    # 1. Tạo Project, Import Script & Render 1 Shot để có dữ liệu hoàn chỉnh
    p_res = await client.post("/api/v1/projects", json={"name": "M7 Backup & Cleanup Film"})
    proj_id = p_res.json()["id"]

    c_res = await client.post(
        "/api/v1/connections",
        json={"name": "mock-m7-conn", "provider_type": "mock", "base_url": "http://localhost:8080"},
    )
    conn_id = c_res.json()["id"]

    await client.post(
        f"/api/v1/projects/{proj_id}/scripts/import",
        json={"raw_content": "SCENE 1: Roof\nSHOT 1: Hero stands\n- Action: Watches skyline\n", "auto_apply_proposal": True},
    )

    # Upload 1 Asset được khoá làm Reference (is_locked_reference=True) và 1 Asset rác không được tham chiếu
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    locked_ast = (
        await client.post(
            "/api/v1/assets/upload",
            data={"project_id": proj_id, "asset_type": "reference", "is_locked_reference": "true"},
            files={"file": ("locked_char.png", png_bytes, "image/png")},
        )
    ).json()

    unused_ast = (
        await client.post(
            "/api/v1/assets/upload",
            data={"project_id": proj_id, "asset_type": "image", "is_locked_reference": "false"},
            files={"file": ("unused_temp.png", png_bytes + b"unused", "image/png")},
        )
    ).json()

    # Tạo thêm 1 file mồ côi trực tiếp trên ổ đĩa không nằm trong DB
    orphan_file = pathlib.Path(tmp_path) / "stray_orphan_clip.mp4"
    orphan_file.write_bytes(b"ORPHAN_DISK_BYTES_12345")

    usage = (await client.get("/api/v1/ops/storage/usage")).json()
    assert usage["orphan_disk_file_count"] == 1
    assert usage["unreferenced_db_asset_count"] >= 1

    # 2. Chạy Storage Cleanup -> Phải xoá file mồ côi và asset rác, nhưng GIỮ NGUYÊN locked_ast!
    clean_res = (
        await client.post(
            "/api/v1/ops/storage/cleanup",
            json={"dry_run": False, "remove_unreferenced_db_assets": True},
        )
    ).json()
    assert "stray_orphan_clip.mp4" in clean_res["removed_orphan_files"]
    assert unused_ast["id"] in clean_res["removed_db_asset_ids"]
    assert locked_ast["id"] in clean_res["protected_asset_ids_preserved"]
    assert not orphan_file.exists()

    # 3. Kiểm tra Dashboard Metrics & Cấu hình giả định chi phí (Cost Assumption)
    dash_default = (await client.get("/api/v1/ops/dashboard")).json()
    assert dash_default["project_count"] == 1
    assert dash_default["estimated_cost_usd"] is None
    assert dash_default["cost_basis"] == "unsupported_by_provider"

    await client.put("/api/v1/settings/system", json={"media_retention_days": 14, "cost_per_second_usd": 0.05})
    dash_cost = (await client.get("/api/v1/ops/dashboard")).json()
    assert dash_cost["cost_basis"] == "admin_configured_assumption"
    assert dash_cost["estimated_cost_usd"] is not None

    # 4. Tạo bản Backup toàn bộ Metadata + Manifest & Kiểm tra Restore + Checksum Verification
    backup_res = await client.post(f"/api/v1/ops/backup?project_id={proj_id}")
    assert backup_res.status_code == 200
    bundle = backup_res.json()
    assert bundle["secrets_included"] is False
    assert len(bundle["data"]["projects"]) == 1
    assert len(bundle["data"]["shots"]) == 1

    # Thử Restore với checksum bị làm giả -> Phải bị chặn 422
    tampered_res = await client.post(
        "/api/v1/ops/restore",
        json={"schema_version": "1.0", "checksum_sha256": "0" * 64, "data": bundle["data"]},
    )
    assert tampered_res.status_code == 422

    # Restore với gói Backup hợp lệ -> Thành công
    restore_ok = await client.post("/api/v1/ops/restore", json=bundle)
    assert restore_ok.status_code == 200
    assert restore_ok.json()["checksum_verified"] is True
