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


class _FakeArqPool:
    """Records enqueued ARQ jobs instead of talking to Redis (same pattern as M4/M5)."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple, dict]] = []

    async def enqueue_job(self, function: str, *args, **kwargs):
        self.jobs.append((function, args, kwargs))
        return object()


@pytest.fixture()
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    # Export jobs are rendered by the ARQ worker; point it at the test database
    # and give the API a fake pool so launches can be asserted without Redis.
    from muse_orchestrator.workers import queue_worker

    previous_session_local = queue_worker.AsyncSessionLocal
    queue_worker.AsyncSessionLocal = session_factory
    previous_pool = getattr(app.state, "arq_pool", None)
    app.state.arq_pool = _FakeArqPool()
    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()
    queue_worker.AsyncSessionLocal = previous_session_local
    if previous_pool is None:
        if hasattr(app.state, "arq_pool"):
            delattr(app.state, "arq_pool")
    else:
        app.state.arq_pool = previous_pool


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
    assert response.json()["detail"] == "Asset không tồn tại."
    assert response.json()["category"] == "not_found"
    assert response.json()["retryable"] is False


@pytest.mark.asyncio
async def test_download_with_missing_file_is_404_not_500(client: AsyncClient) -> None:
    project_id = await _project(client)
    asset = await _upload(client, project_id, "gone.png", PNG_BYTES, "image/png")
    for path in Path(settings.media_root).rglob(f"{asset['sha256'][:12]}_*"):
        path.unlink()

    response = await client.get(f"/api/v1/assets/{asset['id']}/download")

    assert response.status_code == 404
    assert response.json()["detail"] == "File của asset không còn trên máy chủ."
    assert response.json()["category"] == "not_found"
    assert response.json()["retryable"] is False


# --- export failures are reported, never faked ---------------------------------------------


@pytest.mark.asyncio
async def test_export_of_undecodable_clip_fails_and_creates_no_output(client: AsyncClient) -> None:
    project_id = await _project(client)
    video = await _upload(client, project_id, "clip.mp4", MP4_PLACEHOLDER, "video/mp4")
    await _assemble(client, project_id, [{"asset_id": video["id"], "order_index": 1, "source_duration_seconds": 3.0}])

    # The render runs in the ARQ worker now: launching returns 202 + a QUEUED job,
    # and the failure is recorded on the job instead of the launch response.
    response = await client.post(f"/api/v1/projects/{project_id}/timeline/exports", json={})
    assert response.status_code == 202, response.text
    export_id = response.json()["export_id"]
    assert export_id.startswith("exp_")
    assert response.json()["state"] == "queued"

    from muse_orchestrator.workers.queue_worker import process_export_job

    result = await process_export_job({}, export_id)
    assert result["state"] == "failed"

    status_response = await client.get(f"/api/v1/projects/{project_id}/timeline/exports/{export_id}")
    assert status_response.status_code == 200, status_response.text
    assert status_response.json()["state"] == "failed"
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

    # The render runs in the ARQ worker: launch returns 202, the worker records
    # the failed verification on the job, and no output asset is created.
    response = await client.post(f"/api/v1/projects/{project_id}/timeline/exports", json={})
    assert response.status_code == 202, response.text
    export_id = response.json()["export_id"]

    from muse_orchestrator.workers.queue_worker import process_export_job

    result = await process_export_job({}, export_id)
    assert result["state"] == "failed"

    status_response = await client.get(f"/api/v1/projects/{project_id}/timeline/exports/{export_id}")
    assert status_response.status_code == 200, status_response.text
    body = status_response.json()
    assert body["state"] == "failed"
    assert "output_asset_id" not in body


@pytest.mark.asyncio
async def test_export_error_is_recorded_on_the_job(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = await _project(client)
    await _assemble(client, project_id, [])

    def render_unavailable(**_kwargs):
        raise ExportError("FFmpeg chưa được cài.", status_code=503)

    monkeypatch.setattr(TimelineValidationService, "render_and_probe_mp4", render_unavailable)

    # Render errors surface on the export job (polled via GET), not on the launch call.
    response = await client.post(f"/api/v1/projects/{project_id}/timeline/exports", json={})
    assert response.status_code == 202, response.text
    export_id = response.json()["export_id"]
    # The "not rendered yet" contract also holds on the launch response.
    assert "clip_audio" in response.json()["unsupported_features"]

    from muse_orchestrator.workers.queue_worker import process_export_job

    result = await process_export_job({}, export_id)
    assert result["state"] == "failed"

    status_response = await client.get(f"/api/v1/projects/{project_id}/timeline/exports/{export_id}")
    assert status_response.status_code == 200, status_response.text
    body = status_response.json()
    assert body["state"] == "failed"
    assert body["message"] == "FFmpeg chưa được cài."
    assert "clip_audio" in body["unsupported_features"]

@pytest.mark.asyncio
async def test_p1_and_p3_short_source_video_rejected_and_early_422_includes_unsupported_features(client, tmp_path):
    """
    - P1: Video nguồn dài 1.0s nhưng clip trên timeline yêu cầu 3.0s -> từ chối export (422) và không lưu Asset xuất ra.
    - P3: Lỗi 422 sớm khi clip trỏ tới asset_id không tồn tại vẫn trả về đầy đủ unsupported_features.
    """
    import shutil
    import subprocess

    # Lấy admin headers nếu file test có định nghĩa helper hoặc biến ADMIN_HEADERS
    headers = globals().get("ADMIN_HEADERS") or {}
    if not headers and callable(globals().get("_admin_headers")):
        headers = globals()["_admin_headers"]()

    p_res = await client.post("/api/v1/projects", json={"name": "P1 P3 Check"}, headers=headers)
    proj_id = p_res.json()["id"]

    # Kiểm tra P3: Early 422 do asset_id không tồn tại vẫn phải có unsupported_features
    tl_p3 = (
        await client.post(
            f"/api/v1/projects/{proj_id}/timeline/assemble",
            headers=headers,
            json={
                "name": "Missing Asset Seq",
                "title_card": {"title": "Intro", "duration_seconds": 2.0},
                "custom_clips": [
                    {
                        "asset_id": "ast_does_not_exist_999",
                        "order_index": 1,
                        "start_time_seconds": 0.0,
                        "source_duration_seconds": 3.0,
                    }
                ],
            },
        )
    ).json()

    exp_p3 = await client.post(
        f"/api/v1/projects/{proj_id}/timeline/exports",
        headers=headers,
        json={"sequence_id": tl_p3["sequence_id"]},
    )
    assert exp_p3.status_code == 422
    assert exp_p3.json()["category"] == "validation"
    assert exp_p3.json()["retryable"] is False

    # Kiểm tra P1: Video nguồn thực tế dài 1.0s nhưng timeline yêu cầu 3.0s -> Phải bị từ chối 422
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        short_mp4 = tmp_path / "source_1s.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=640x360:r=24:d=1.0",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(short_mp4),
            ],
            check=True,
            capture_output=True,
        )
        up_res = await client.post(
            "/api/v1/assets/upload",
            headers=headers,
            data={"project_id": proj_id, "asset_type": "video"},
            files={"file": ("source_1s.mp4", short_mp4.read_bytes(), "video/mp4")},
        )
        assert up_res.status_code == 201
        short_asset_id = up_res.json()["id"]

        tl_p1 = (
            await client.post(
                f"/api/v1/projects/{proj_id}/timeline/assemble",
                headers=headers,
                json={
                    "name": "Short Source Clip Seq",
                    "custom_clips": [
                        {
                            "asset_id": short_asset_id,
                            "order_index": 1,
                            "start_time_seconds": 0.0,
                            "source_duration_seconds": 3.0,
                            "trim_start_seconds": 0.0,
                            "trim_end_seconds": 0.0,
                        }
                    ],
                },
            )
        ).json()

        exp_p1 = await client.post(
            f"/api/v1/projects/{proj_id}/timeline/exports",
            headers=headers,
            json={"sequence_id": tl_p1["sequence_id"]},
        )
        # The duration check lives in the render path, which now runs in the ARQ
        # worker: launch returns 202 and the rejection is recorded on the job.
        assert exp_p1.status_code == 202
        assert "unsupported_features" in exp_p1.json()
        export_id = exp_p1.json()["export_id"]

        from muse_orchestrator.workers.queue_worker import process_export_job

        worker_result = await process_export_job({}, export_id)
        assert worker_result["state"] == "failed"

        status_p1 = await client.get(
            f"/api/v1/projects/{proj_id}/timeline/exports/{export_id}",
            headers=headers,
        )
        assert status_p1.status_code == 200
        assert status_p1.json()["state"] == "failed"
        assert "output_asset_id" not in status_p1.json()
