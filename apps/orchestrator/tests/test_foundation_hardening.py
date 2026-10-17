from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from muse_orchestrator.core.config import Settings
from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.main import app
from muse_orchestrator.services.asset_storage import AssetStorageError, LocalAssetStorage


@pytest.mark.asyncio
async def test_api_routes_require_admin_token_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from muse_orchestrator.core import security

    monkeypatch.setattr(security.settings, "studio_admin_token", "expected-admin-token")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        protected_routes = [
            ("GET", "/api/v1/projects"),
            ("GET", "/api/v1/connections"),
            ("DELETE", "/api/v1/assets/asset-auth-test"),
            ("GET", "/api/v1/projects/project-auth-test/story-bible"),
            ("POST", "/api/v1/projects/project-auth-test/workflows"),
            ("GET", "/api/v1/queue/tasks"),
            ("POST", "/api/v1/projects/project-auth-test/batch/tick"),
            ("GET", "/api/v1/projects/project-auth-test/timeline"),
        ]
        for method, path in protected_routes:
            response = await client.request(method, path)
            assert response.status_code == 401, f"{method} {path}: {response.text}"
            assert response.json()["detail"] == "Invalid X-Studio-Admin-Token."

        # The operational health check intentionally remains public.
        health = await client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        authorized = await verify_single_admin("expected-admin-token")
        assert authorized == "admin_operator"


def test_production_settings_reject_missing_or_default_secrets() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production")
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            studio_admin_token="configured-token",
            secret_encryption_key="dev-only-32-byte-secret-key-change-me!!",
        )
    validated = Settings(
        environment="production",
        studio_admin_token="configured-token",
        secret_encryption_key="replace-this-with-a-stable-production-secret-32",
    )
    assert validated.environment == "production"


def test_asset_storage_rejects_mime_spoofing_and_path_traversal(tmp_path) -> None:
    storage = LocalAssetStorage(str(tmp_path / "media"))
    fake_webp = b"RIFFxxxxNOTW" + b"payload"
    with pytest.raises(AssetStorageError):
        storage.verify_mime_signature(fake_webp, "image/webp")
    with pytest.raises(AssetStorageError):
        storage.verify_mime_signature(b"not-an-mp4-file" + b"ftyp", "video/mp4")

    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    with pytest.raises(AssetStorageError):
        storage.resolve_safe_path("../outside.txt")


def test_asset_storage_accepts_minimal_png_signature(tmp_path) -> None:
    storage = LocalAssetStorage(str(tmp_path / "media"))
    content = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
    saved = storage.save_bytes("project-1", "../../ref.png", content, "image/png")
    path = storage.resolve_safe_path(saved.storage_rel_path)
    assert path.read_bytes() == content
    assert "/" not in saved.safe_filename
