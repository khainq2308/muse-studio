from __future__ import annotations
import hashlib
import os
import tempfile
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# Expression statements (not assignments) so Ruff E402 stays quiet for the
# imports below, matching the pattern used by the other API test modules.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp(prefix="muse_m6_test_"))

from muse_orchestrator.main import app
from muse_orchestrator.core.database import get_db
from muse_orchestrator.models.domain import Base
from muse_orchestrator.services.timeline_service import TimelineValidationService


@pytest.fixture()
async def client():
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    # Export jobs are rendered by the ARQ worker; point it at the test database
    # and give the API a fake pool so launches work without Redis (M4/M5 pattern).
    from muse_orchestrator.workers import queue_worker

    class _FakeArqPool:
        def __init__(self) -> None:
            self.jobs: list[tuple[str, tuple, dict]] = []

        async def enqueue_job(self, function: str, *args, **kwargs):
            self.jobs.append((function, args, kwargs))
            return object()

    previous_session_local = queue_worker.AsyncSessionLocal
    queue_worker.AsyncSessionLocal = TestSessionLocal
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


async def _drive_export(export_id: str):
    """Run the export worker inline (tests have no real ARQ worker process)."""
    from muse_orchestrator.workers.queue_worker import process_export_job

    return await process_export_job({}, export_id)


TIMELINE_SCRIPT = """
SCENE 1: Final Showdown
SHOT 1: Detective confronts culprit
- Character: Conan
- Action: Points finger and reveals the truth
- Duration: 4.0

SHOT 2: Culprit drops the key
- Character: Culprit
- Action: Key falls onto marble floor in slow motion
- Duration: 6.0
"""


@pytest.mark.asyncio
async def test_m6_timeline_assemble_trim_subtitles_and_mp4_export(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    # 1. Tạo Project, Connection Mock, Import Script (2 shots)
    p_res = await client.post("/api/v1/projects", json={"name": "M6 Final Cut Movie", "aspect_ratio": "16:9", "fps": 24})
    proj_id = p_res.json()["id"]

    c_res = await client.post(
        "/api/v1/connections",
        json={"name": "mock-m6-conn", "provider_type": "mock", "base_url": "http://localhost:8080", "max_concurrency": 4},
    )
    conn_id = c_res.json()["id"]

    await client.post(f"/api/v1/projects/{proj_id}/scripts/import", json={"raw_content": TIMELINE_SCRIPT, "auto_apply_proposal": True})

    # 2. Thử dựng Timeline với allow_missing_shots=False khi chưa render -> Phải chặn 422
    blocked_tl = await client.post(
        f"/api/v1/projects/{proj_id}/timeline/assemble",
        json={"name": "Premature Edit", "allow_missing_shots": False},
    )
    assert blocked_tl.status_code == 422

    # 3. Chạy Batch Render cho cả 2 shots đến khi hoàn tất (có Selected Takes)
    await client.post(f"/api/v1/projects/{proj_id}/batch/launch", json={"connection_id": conn_id})
    for _ in range(5):
        await client.post(f"/api/v1/projects/{proj_id}/batch/tick")

    shots = (await client.get(f"/api/v1/projects/{proj_id}/shots")).json()
    assert all(s["selected_take_id"] is not None for s in shots)

    # 4. Dựng Timeline có Title Card (2.0s), Cross Dissolve (0.5s), Audio Track và Subtitle SRT
    srt_text = "1\n00:00:00,000 --> 00:00:04,000\nHung thủ chính là ngươi!\n"
    tl_res = await client.post(
        f"/api/v1/projects/{proj_id}/timeline/assemble",
        json={
            "name": "Director Cut v1",
            "allow_missing_shots": False,
            "default_transition": "cross_dissolve",
            "transition_duration_seconds": 0.5,
            "title_card": {"title": "THÁM TỬ CONAN", "duration_seconds": 2.0},
            "subtitle_format": "srt",
            "subtitle_content": srt_text,
            "audio_tracks": [
                {"track_type": "music", "name": "Suspense OST", "volume": 0.7, "fade_in_seconds": 1.0, "fade_out_seconds": 1.5}
            ],
        },
    )
    assert tl_res.status_code == 201
    tl = tl_res.json()
    # Thời lượng = Shot 1 (4.0s) + Shot 2 (6.0s) - Cross Dissolve (0.5s) + Title Card (2.0s) = 11.5s
    assert tl["total_duration_seconds"] == 11.5
    assert tl["clip_count"] == 2
    assert tl["audio_track_count"] == 1
    assert tl["has_subtitles"] is True
    assert len(tl["warnings"]) == 0
    manifest = tl["manifest"]
    assert manifest["sequence_id"] == tl["sequence_id"]
    assert manifest["video_clips"][0]["effective_duration_seconds"] == 4.0
    assert manifest["audio_tracks"][0]["name"] == "Suspense OST"

    latest_tl_res = await client.get(f"/api/v1/projects/{proj_id}/timeline")
    assert latest_tl_res.status_code == 200, latest_tl_res.text
    latest_tl = latest_tl_res.json()
    assert latest_tl["sequence_id"] == tl["sequence_id"]
    assert latest_tl["manifest"]["video_clips"][1]["transition_type"] == "cross_dissolve"

    # 5. Kiểm tra phát hiện TIMELINE_GAP và Trim Clip khi chỉnh sửa custom_clips
    custom_tl = (
        await client.post(
            f"/api/v1/projects/{proj_id}/timeline/assemble",
            json={
                "name": "Trimmed & Gapped Edit",
                "custom_clips": [
                    {
                        "shot_id": shots[0]["id"],
                        "take_id": shots[0]["selected_take_id"],
                        "order_index": 1,
                        "start_time_seconds": 0.0,
                        "source_duration_seconds": 4.0,
                        "trim_start_seconds": 0.5,
                        "trim_end_seconds": 0.5,
                        "transition_type": "hard_cut",
                    },
                    {
                        "shot_id": shots[1]["id"],
                        "take_id": shots[1]["selected_take_id"],
                        "order_index": 2,
                        "start_time_seconds": 5.0,  # Tạo khoảng hở 2.0s sau clip 1 (kết thúc ở 3.0s)
                        "source_duration_seconds": 6.0,
                        "trim_start_seconds": 1.0,
                        "trim_end_seconds": 0.0,
                        "transition_type": "hard_cut",
                    },
                ],
            },
        )
    ).json()
    warning_codes = {w["code"] for w in custom_tl["warnings"]}
    assert "TIMELINE_GAP" in warning_codes
    assert custom_tl["total_duration_seconds"] == 10.0  # kết thúc ở 5.0 + (6.0 - 1.0) = 10.0s

    # 6. Export: the mock provider's "videos" are placeholder bytes that FFmpeg cannot decode, so the
    # render itself is stubbed here. What this test pins down is that the exporter is handed the
    # real clips of the sequence (resolved to files inside the media root) and that the result is
    # stored as a downloadable asset. Real FFmpeg behaviour is covered in test_timeline_export.py.
    render_calls: list[dict] = []

    def fake_render(*, output_dir: str, export_id: str, fps: int, resolution: str, clips: list):
        render_calls.append({"fps": fps, "resolution": resolution, "clips": clips})
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"{export_id}.mp4")
        data = b"\x00\x00\x00\x18ftypmp42" + b"exported-bytes"
        with open(out_path, "wb") as handle:
            handle.write(data)
        width, height = (int(v) for v in resolution.split("x"))
        probe = {"valid": True, "probed_by": "unit_test", "codec_name": "h264", "audio_codec": "aac",
                 "width": width, "height": height, "duration_seconds": 9.5}
        return out_path, len(data), hashlib.sha256(data).hexdigest(), ["ffmpeg", "-y"], probe

    monkeypatch.setattr(TimelineValidationService, "render_and_probe_mp4", fake_render)

    exp_res = await client.post(
        f"/api/v1/projects/{proj_id}/timeline/exports",
        json={"sequence_id": tl["sequence_id"], "aspect_ratio": "9:16", "fps": 30},
    )
    # The render runs in the ARQ worker: launch returns 202 + a QUEUED job.
    assert exp_res.status_code == 202, exp_res.text
    launched = exp_res.json()
    assert launched["state"] == "queued"
    assert launched["sequence_id"] == tl["sequence_id"]
    assert launched["aspect_ratio"] == "9:16"
    assert launched["fps"] == 30
    assert launched["resolution"] == "1080x1920"
    assert launched["unsupported_features"] == ["clip_audio", "title_card", "subtitles", "audio_tracks"]
    export_id = launched["export_id"]

    worker_result = await _drive_export(export_id)
    assert worker_result["state"] == "succeeded"

    status_res = await client.get(f"/api/v1/projects/{proj_id}/timeline/exports/{export_id}")
    assert status_res.status_code == 200, status_res.text
    exp = status_res.json()
    assert exp["state"] == "succeeded"
    assert exp["export_id"] == export_id
    assert exp["sequence_id"] == tl["sequence_id"]
    assert exp["output_asset_id"]
    assert exp["download_url"] == f"/api/v1/assets/{exp['output_asset_id']}/download"
    assert "message" not in exp
    assert exp["video_codec"] == "h264"
    assert exp["audio_codec"] == "aac"
    assert exp["resolution"] == "1080x1920"
    assert exp["ffprobe_verification"]["valid"] is True
    assert exp["ffprobe_verification"]["width"] == 1080
    assert exp["ffprobe_verification"]["height"] == 1920
    assert isinstance(exp["ffmpeg_args"], list) and exp["ffmpeg_args"][0] == "ffmpeg"

    # The exporter received both real clips, in order, as files that exist on disk.
    assert len(render_calls) == 1
    clips = render_calls[0]["clips"]
    assert [round(c.effective_duration, 3) for c in clips] == [4.0, 6.0]
    assert all(os.path.isfile(c.path) for c in clips)
    assert clips[1].transition_type == "cross_dissolve"

    # Title card, subtitles and audio tracks are not rendered yet, and the response says so.
    assert exp["unsupported_features"] == ["clip_audio", "title_card", "subtitles", "audio_tracks"]

    # download_url points at a route that really serves the exported file.
    download = await client.get(exp["download_url"])
    assert download.status_code == 200
    assert download.content == b"\x00\x00\x00\x18ftypmp42" + b"exported-bytes"
    assert download.headers["content-type"] == "video/mp4"



@pytest.mark.asyncio
async def test_failed_export_status_omits_success_only_fields(client: AsyncClient) -> None:
    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "Export Failure Contract"},
    )
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    sequence_response = await client.post(
        f"/api/v1/projects/{project_id}/timeline/assemble",
        json={"name": "Empty Timeline"},
    )
    assert sequence_response.status_code == 201
    sequence_id = sequence_response.json()["sequence_id"]
    assert sequence_response.json()["manifest"]["video_clips"] == []

    launch_response = await client.post(
        f"/api/v1/projects/{project_id}/timeline/exports",
        json={"sequence_id": sequence_id},
    )
    assert launch_response.status_code == 202, launch_response.text
    export_id = launch_response.json()["export_id"]

    worker_result = await _drive_export(export_id)
    assert worker_result["state"] == "failed"

    status_response = await client.get(
        f"/api/v1/projects/{project_id}/timeline/exports/{export_id}"
    )
    assert status_response.status_code == 200, status_response.text
    export_status = status_response.json()
    assert export_status["state"] == "failed"
    assert export_status["message"]
    assert "download_url" not in export_status
    assert "output_asset_id" not in export_status
    assert "ffmpeg_args" not in export_status
    assert "ffprobe_verification" not in export_status


@pytest.mark.asyncio
async def test_application_errors_preserve_http_status_and_detail(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    from muse_orchestrator.core import security

    monkeypatch.setattr(security.settings, "studio_admin_token", "expected-admin-token")
    headers = {"X-Studio-Admin-Token": "expected-admin-token"}

    blank_name = await client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": "   "},
    )
    assert blank_name.status_code == 422
    assert blank_name.json() == {"detail": "Project name cannot be blank."}

    missing_project = await client.post(
        "/api/v1/projects/project-that-does-not-exist/duplicate",
        headers=headers,
    )
    assert missing_project.status_code == 404
    assert missing_project.json() == {"detail": "Original project not found."}


@pytest.mark.asyncio
async def test_timeline_export_resolution_defaults_and_custom_override(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "Export Resolution Regression", "aspect_ratio": "16:9", "fps": 24},
    )
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    sequence_response = await client.post(
        f"/api/v1/projects/{project_id}/timeline/assemble",
        json={"name": "Resolution Test Sequence"},
    )
    assert sequence_response.status_code == 201
    sequence_id = sequence_response.json()["sequence_id"]

    render_calls: list[str] = []

    def fake_render_and_probe_mp4(
        *,
        output_dir: str,
        export_id: str,
        fps: int,
        resolution: str,
        clips: list,
    ):
        render_calls.append(resolution)
        width, height = (int(value) for value in resolution.split("x"))
        probe = {
            "valid": True,
            "probed_by": "unit_test",
            "codec_name": "h264",
            "audio_codec": "aac",
            "width": width,
            "height": height,
            "duration_seconds": 1.0,
        }
        return (
            os.path.join(output_dir, f"{export_id}.mp4"),
            128,
            "a" * 64,
            ["ffmpeg", "-y"],
            probe,
        )

    monkeypatch.setattr(TimelineValidationService, "render_and_probe_mp4", fake_render_and_probe_mp4)

    cases = [
        ({"sequence_id": sequence_id, "aspect_ratio": "9:16"}, "1080x1920", 1080, 1920),
        ({"sequence_id": sequence_id, "aspect_ratio": "1:1"}, "1080x1080", 1080, 1080),
        (
            {"sequence_id": sequence_id, "aspect_ratio": "9:16", "resolution": "720x1280"},
            "720x1280",
            720,
            1280,
        ),
    ]

    for payload, expected_resolution, expected_width, expected_height in cases:
        response = await client.post(
            f"/api/v1/projects/{project_id}/timeline/exports",
            json=payload,
        )
        # The render runs in the ARQ worker: launch returns 202 + a QUEUED job.
        assert response.status_code == 202, response.text
        export_id = response.json()["export_id"]

        worker_result = await _drive_export(export_id)
        assert worker_result["state"] == "succeeded"

        status_response = await client.get(
            f"/api/v1/projects/{project_id}/timeline/exports/{export_id}"
        )
        assert status_response.status_code == 200, status_response.text
        export = status_response.json()
        assert export["state"] == "succeeded"
        assert export["resolution"] == expected_resolution
        assert export["ffprobe_verification"]["width"] == expected_width
        assert export["ffprobe_verification"]["height"] == expected_height

    assert render_calls == ["1080x1920", "1080x1080", "720x1280"]
