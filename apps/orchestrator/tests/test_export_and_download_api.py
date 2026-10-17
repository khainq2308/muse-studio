from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp(prefix="muse_export_api_test_"))

from muse_orchestrator.core.config import settings
from muse_orchestrator.core.database import get_db
from muse_orchestrator.main import app
from muse_orchestrator.models.domain import Base
from muse_orchestrator.services.timeline_service import ExportError, TimelineValidationService

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"asset-download-test"
MP4_PLACEHOLDER = b"\x00\x00\x00\x18ftypmp42" + b"placeholder, not decodable"


@pytest.fixture()
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _project(client: AsyncClient, name: str = "Export API") -> str:
    response = await client.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _upload(client: AsyncClient, project_id: str, filename: str, content: bytes, mime: str) -> dict:
    response = await client.post(
        "/api/v1/assets/upload",
        data={"project_id": project_id, "asset_type": "video" if mime.startswith("video/") else "reference"},
        files={"file": (filename, content, mime)},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _assemble(client: AsyncClient, project_id: str, clips: list[dict]) -> str:
    response = await client.post(
        f"/api/v1/projects/{project_id}/timeline/assemble",
        json={"name": "Cut", "custom_clips": clips},
    )
    assert response.status_code == 201, response.text
    return response.json()["sequence_id"]


# --- /api/v1/assets/{id}/download ----------------------------------------------------------


@pytest.mark.asyncio
async def test_download_serves_the_stored_file(client: AsyncClient) -> None:
    project_id = await _project(client)
    asset = await _upload(client, project_id, "ref.png", PNG_BYTES, "image/png")

    response = await client.get(f"/api/v1/assets/{asset['id']}/download")

    assert response.status_code == 200
    assert response.content == PNG_BYTES
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "ref.png" in response.headers["content-disposition"]


@pytest.mark.asyncio
async def test_download_unknown_asset_is_404(client: AsyncClient) -> None:
    response = await client.get("/api/v1/assets/ast_does_not_exist/download")

    assert response.status_code == 404
    assert response.json() == {"detail": "Asset không tồn tại."}


@pytest.mark.asyncio
async def test_download_with_missing_file_is_404_not_500(client: AsyncClient) -> None:
    project_id = await _project(client)
    asset = await _upload(client, project_id, "gone.png", PNG_BYTES, "image/png")
    for path in Path(settings.media_root).rglob(f"{asset['sha256'][:12]}_*"):
        path.unlink()

    response = await client.get(f"/api/v1/assets/{asset['id']}/download")

    assert response.status_code == 404
    assert response.json() == {"detail": "File của asset không còn trên máy chủ."}


# --- export failures are reported, never faked ---------------------------------------------


@pytest.mark.asyncio
async def test_export_of_undecodable_clip_fails_and_creates_no_output(client: AsyncClient) -> None:
    project_id = await _project(client)
    video = await _upload(client, project_id, "clip.mp4", MP4_PLACEHOLDER, "video/mp4")
    await _assemble(client, project_id, [{"asset_id": video["id"], "order_index": 1, "source_duration_seconds": 3.0}])

    response = await client.post(f"/api/v1/projects/{project_id}/timeline/exports", json={})

    # 422 when FFmpeg rejects the placeholder, 503 on a machine without FFmpeg; never 201.
    assert response.status_code in (422, 503), response.text
    detail = response.json()["detail"]
    assert detail["state"] == "failed"
    assert detail["export_id"].startswith("exp_")
    exports_dir = Path(settings.media_root) / project_id / "exports"
    assert not list(exports_dir.glob("*.mp4"))


@pytest.mark.asyncio
async def test_export_rejects_a_clip_that_has_no_video(client: AsyncClient) -> None:
    project_id = await _project(client)
    await _assemble(client, project_id, [{"order_index": 1, "source_duration_seconds": 3.0}])

    response = await client.post(f"/api/v1/projects/{project_id}/timeline/exports", json={})

    assert response.status_code == 422
    assert "clip #1" in response.json()["detail"]


@pytest.mark.asyncio
async def test_export_rejects_a_clip_using_another_projects_asset(client: AsyncClient) -> None:
    mine = await _project(client, "Mine")
    other = await _project(client, "Other")
    foreign = await _upload(client, other, "secret.mp4", MP4_PLACEHOLDER, "video/mp4")
    await _assemble(client, mine, [{"asset_id": foreign["id"], "order_index": 1, "source_duration_seconds": 3.0}])

    response = await client.post(f"/api/v1/projects/{mine}/timeline/exports", json={})

    assert response.status_code == 422
    assert "clip #1" in response.json()["detail"]


@pytest.mark.asyncio
async def test_export_that_fails_verification_is_not_stored_as_an_asset(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = await _project(client)
    await _assemble(client, project_id, [])

    def render_with_bad_probe(*, output_dir: str, export_id: str, fps: int, resolution: str, clips: list):
        probe = {"valid": False, "problems": ["Output has no audio stream."]}
        return os.path.join(output_dir, f"{export_id}.mp4"), 10, "a" * 64, ["ffmpeg"], probe

    monkeypatch.setattr(TimelineValidationService, "render_and_probe_mp4", render_with_bad_probe)

    response = await client.post(f"/api/v1/projects/{project_id}/timeline/exports", json={})

    assert response.status_code == 422
    assert response.json()["detail"]["state"] == "failed"


@pytest.mark.asyncio
async def test_export_error_status_code_is_passed_through(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = await _project(client)
    await _assemble(client, project_id, [])

    def render_unavailable(**_kwargs):
        raise ExportError("FFmpeg chưa được cài.", status_code=503)

    monkeypatch.setattr(TimelineValidationService, "render_and_probe_mp4", render_unavailable)

    response = await client.post(f"/api/v1/projects/{project_id}/timeline/exports", json={})

    assert response.status_code == 503
    assert response.json()["detail"]["message"] == "FFmpeg chưa được cài."
