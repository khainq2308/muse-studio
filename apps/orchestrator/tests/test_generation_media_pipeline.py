"""Generation pipeline with a real provider type: validation of what is downloaded, real boundary
frames from FFmpeg, and the first frame sent to the provider for frame-chained shots."""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp(prefix="muse_media_pipeline_test_"))

from muse_orchestrator.core.config import settings
from muse_orchestrator.core.database import get_db
from muse_orchestrator.main import app
from muse_orchestrator.models.domain import Base
from muse_orchestrator.providers.base import (
    ErrorCategory,
    ProviderFailure,
    ProviderTask,
    ProviderTaskStatus,
    StoredMedia,
)
from muse_orchestrator.services import generation_queue

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"png-body"
UNDECODABLE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"this is not a decodable video"
TWO_SHOT_SCRIPT = "SCENE 1: Roof\nSHOT 1: Hero leaps\n- Action: Leaps\nSHOT 2: Hero lands\n- Action: Lands\n"


class FakeProvider:
    """A provider whose jobs finish at once and whose download is scripted by the test."""

    def __init__(self, result_file: Path | None = None, result_mime: str = "video/mp4") -> None:
        self.result_file = result_file
        self.result_mime = result_mime
        self.download_failures: list[ProviderFailure] = []
        self.video_requests: list = []

    async def generate_video(self, request):
        self.video_requests.append(request)
        return ProviderTask(external_task_id=f"ext_{len(self.video_requests)}", status="queued")

    async def generate_image(self, request):
        return ProviderTask(external_task_id="ext_image", status="queued")

    async def get_task(self, task_id: str) -> ProviderTaskStatus:
        return ProviderTaskStatus(
            external_task_id=task_id, status="succeeded", result_media_url="https://cdn.example.com/out"
        )

    async def download_result(self, result_ref: str, dest_dir: str) -> StoredMedia:
        if self.download_failures:
            raise self.download_failures.pop(0)
        assert self.result_file is not None
        os.makedirs(dest_dir, exist_ok=True)
        data = self.result_file.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        target = Path(dest_dir) / f"{digest[:16]}.{'png' if self.result_mime == 'image/png' else 'mp4'}"
        shutil.copyfile(self.result_file, target)
        return StoredMedia(
            local_path=str(target), mime_type=self.result_mime, size_bytes=len(data), sha256=digest
        )


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


def _use_provider(monkeypatch: pytest.MonkeyPatch, provider: FakeProvider) -> None:
    monkeypatch.setattr(generation_queue, "build_provider", lambda _conn: provider)


async def _setup(client: AsyncClient, name: str) -> tuple[str, str, list[dict]]:
    project = await client.post("/api/v1/projects", json={"name": name})
    project_id = project.json()["id"]
    # A real provider type (not "mock"), so real frame extraction is used.
    connection = await client.post(
        "/api/v1/connections",
        json={"name": f"conn-{name}", "provider_type": "muse2api", "base_url": "http://localhost:8080"},
    )
    assert connection.status_code == 201, connection.text
    imported = await client.post(
        f"/api/v1/projects/{project_id}/scripts/import",
        json={"raw_content": TWO_SHOT_SCRIPT, "auto_apply_proposal": True},
    )
    assert imported.status_code == 201, imported.text
    shots = (await client.get(f"/api/v1/projects/{project_id}/shots")).json()
    return project_id, connection.json()["id"], shots


async def _enqueue(client: AsyncClient, shot_id: str, connection_id: str, task_type: str = "video") -> str:
    response = await client.post(
        "/api/v1/queue/tasks",
        json={"shot_id": shot_id, "connection_id": connection_id, "task_type": task_type},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _step(client: AsyncClient, task_id: str) -> dict:
    response = await client.post(f"/api/v1/queue/tasks/{task_id}/step")
    assert response.status_code == 200, response.text
    return response.json()


async def _run(client: AsyncClient, task_id: str, max_steps: int = 8) -> dict:
    task: dict = {}
    for _ in range(max_steps):
        task = await _step(client, task_id)
        if task["state"] not in ("queued", "running"):
            break
    return task


def _generated_dir(project_id: str) -> Path:
    return Path(settings.media_root) / project_id / "generated"


def _probe_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    width, height = out.split(",")
    return int(width), int(height)


# --- real frames -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_real_video_gets_real_decoded_boundary_frames(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make_test_video
) -> None:
    video = make_test_video(tmp_path / "result.mp4", size="320x240", rate=24, seconds=2)
    _use_provider(monkeypatch, FakeProvider(video))
    project_id, conn_id, shots = await _setup(client, "real-frames")

    task = await _run(client, await _enqueue(client, shots[0]["id"], conn_id))

    assert task["state"] == "succeeded"
    assert task["error_category"] is None
    take = (await client.get(f"/api/v1/queue/shots/{shots[0]['id']}/takes")).json()[0]
    assert take["first_frame_asset_id"] and take["last_frame_asset_id"]

    frame_bytes = []
    for index, asset_id in enumerate((take["first_frame_asset_id"], take["last_frame_asset_id"])):
        download = await client.get(f"/api/v1/assets/{asset_id}/download")
        assert download.status_code == 200
        assert download.headers["content-type"] == "image/png"
        frame_path = tmp_path / f"frame{index}.png"
        frame_path.write_bytes(download.content)
        assert download.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert _probe_size(frame_path) == (320, 240)  # a decoded picture of the video's size
        frame_bytes.append(download.content)
    assert frame_bytes[0] != frame_bytes[1]
    assert not any(path.name.startswith("mock_") for path in _generated_dir(project_id).iterdir())

    updated = (await client.get(f"/api/v1/projects/{project_id}/shots")).json()[0]
    assert updated["last_frame_asset_id"] == take["last_frame_asset_id"]


@pytest.mark.asyncio
async def test_failed_frame_extraction_keeps_the_take_and_says_so(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_ffmpeg
) -> None:
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(UNDECODABLE_MP4)
    _use_provider(monkeypatch, FakeProvider(broken))
    project_id, conn_id, shots = await _setup(client, "frame-failure")

    task = await _run(client, await _enqueue(client, shots[0]["id"], conn_id))

    assert task["state"] == "succeeded"  # the generated video is not thrown away
    assert task["take_id"] and task["result_asset_id"]
    assert task["error_category"] == "frame_extraction_failed"
    assert task["error_message_redacted"]
    assert any("boundary frames were not extracted" in line for line in task["history_log"])
    take = (await client.get(f"/api/v1/queue/shots/{shots[0]['id']}/takes")).json()[0]
    assert take["first_frame_asset_id"] is None and take["last_frame_asset_id"] is None
    updated = (await client.get(f"/api/v1/projects/{project_id}/shots")).json()[0]
    assert updated["last_frame_asset_id"] is None
    # Only the downloaded video is on disk: no placeholder frames and no working directory.
    assert [path.suffix for path in _generated_dir(project_id).iterdir()] == [".mp4"]


# --- validation of the downloaded result -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_result_of_the_wrong_type_fails_the_task_and_is_deleted(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    video = tmp_path / "result.mp4"
    video.write_bytes(UNDECODABLE_MP4)
    _use_provider(monkeypatch, FakeProvider(video, result_mime="video/mp4"))
    project_id, conn_id, shots = await _setup(client, "wrong-type")

    # An image task must not accept a video.
    task = await _run(client, await _enqueue(client, shots[0]["id"], conn_id, task_type="image"))

    assert task["state"] == "failed"
    assert task["error_category"] == ErrorCategory.INVALID_MEDIA.value
    assert task["retryable"] is False
    assert task["result_asset_id"] is None and task["take_id"] is None
    assert list(_generated_dir(project_id).iterdir()) == []
    shot = (await client.get(f"/api/v1/projects/{project_id}/shots")).json()[0]
    assert shot["generation_state"] == "failed"


@pytest.mark.asyncio
async def test_rejecting_a_result_never_deletes_a_file_that_an_asset_already_uses(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    video = tmp_path / "result.mp4"
    video.write_bytes(UNDECODABLE_MP4)
    _use_provider(monkeypatch, FakeProvider(video, result_mime="video/mp4"))
    project_id, conn_id, shots = await _setup(client, "shared-file")

    done = await _run(client, await _enqueue(client, shots[0]["id"], conn_id))
    assert done["state"] == "succeeded"
    stored = [path for path in _generated_dir(project_id).iterdir() if path.suffix == ".mp4"]
    assert len(stored) == 1

    # An image task for the other shot gets byte-identical video content back. Files are stored
    # under their content hash, so it lands on the very file the first asset points at.
    rejected = await _run(client, await _enqueue(client, shots[1]["id"], conn_id, task_type="image"))

    assert rejected["state"] == "failed"
    assert rejected["error_category"] == ErrorCategory.INVALID_MEDIA.value
    assert stored[0].is_file()
    download = await client.get(f"/api/v1/assets/{done['result_asset_id']}/download")
    assert download.status_code == 200
    assert download.content == UNDECODABLE_MP4


@pytest.mark.asyncio
async def test_a_non_retryable_download_failure_fails_the_task(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider()
    provider.download_failures = [ProviderFailure(ErrorCategory.INVALID_MEDIA, "Result is not a supported image or video.")]
    _use_provider(monkeypatch, provider)
    _project_id, conn_id, shots = await _setup(client, "non-retryable")

    task = await _run(client, await _enqueue(client, shots[0]["id"], conn_id))

    assert task["state"] == "failed"
    assert task["error_category"] == "invalid_media"
    assert task["retryable"] is False


@pytest.mark.asyncio
async def test_a_retryable_download_failure_keeps_polling_and_then_recovers(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    video = tmp_path / "result.mp4"
    video.write_bytes(UNDECODABLE_MP4)
    provider = FakeProvider(video)
    provider.download_failures = [ProviderFailure(ErrorCategory.TRANSIENT_NETWORK, "connection reset", retryable=True)]
    _use_provider(monkeypatch, provider)
    project_id, conn_id, shots = await _setup(client, "retryable")
    task_id = await _enqueue(client, shots[0]["id"], conn_id)

    assert (await _step(client, task_id))["state"] == "running"  # dispatched
    after_failure = await _step(client, task_id)  # first poll: the download fails
    assert after_failure["state"] == "running"
    assert after_failure["retryable"] is True
    assert after_failure["error_category"] == "transient_network"

    done = await _step(client, task_id)  # next poll: the download works
    assert done["state"] == "succeeded"
    assert done["retryable"] is False
    assert done["error_message_redacted"] is None or done["error_category"] == "frame_extraction_failed"
    assert done["error_category"] in (None, "frame_extraction_failed")  # never the stale network error


# --- first frame sent to the provider --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_chained_shot_sends_the_real_first_frame_image_not_an_asset_id(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make_test_video
) -> None:
    video = make_test_video(tmp_path / "result.mp4")
    provider = FakeProvider(video)
    _use_provider(monkeypatch, provider)
    project_id, conn_id, shots = await _setup(client, "chain-data-uri")
    first, second = shots[0], shots[1]
    patched = await client.patch(
        f"/api/v1/projects/{project_id}/shots/{second['id']}", json={"continuity_mode": "frame_chain"}
    )
    assert patched.status_code == 200, patched.text

    assert (await _run(client, await _enqueue(client, first["id"], conn_id)))["state"] == "succeeded"
    listed = {s["id"]: s for s in (await client.get(f"/api/v1/projects/{project_id}/shots")).json()}
    last_frame_id = listed[first["id"]]["last_frame_asset_id"]
    assert last_frame_id and listed[second["id"]]["first_frame_asset_id"] == last_frame_id

    await _step(client, await _enqueue(client, second["id"], conn_id))  # dispatch the chained shot

    first_request, chained_request = provider.video_requests
    assert first_request.first_frame_url is None  # a shot that is not chained sends no frame
    uri = chained_request.first_frame_url
    assert uri.startswith("data:image/png;base64,")
    assert uri != last_frame_id
    frame_file = (await client.get(f"/api/v1/assets/{last_frame_id}/download")).content
    assert base64.b64decode(uri.split(",", 1)[1]) == frame_file


@pytest.mark.asyncio
async def test_a_chained_shot_whose_first_frame_file_is_gone_fails_with_a_clear_reason(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make_test_video
) -> None:
    video = make_test_video(tmp_path / "result.mp4")
    provider = FakeProvider(video)
    _use_provider(monkeypatch, provider)
    project_id, conn_id, shots = await _setup(client, "chain-missing-frame")
    first, second = shots[0], shots[1]
    await client.patch(
        f"/api/v1/projects/{project_id}/shots/{second['id']}", json={"continuity_mode": "frame_chain"}
    )
    assert (await _run(client, await _enqueue(client, first["id"], conn_id)))["state"] == "succeeded"
    for frame in _generated_dir(project_id).glob("*_last_*.png"):
        frame.unlink()

    task = await _step(client, await _enqueue(client, second["id"], conn_id))

    assert task["state"] == "failed"
    assert task["error_category"] == "invalid_request"
    assert "first-frame" in task["error_message_redacted"]
    assert len(provider.video_requests) == 1  # nothing was sent for the chained shot
