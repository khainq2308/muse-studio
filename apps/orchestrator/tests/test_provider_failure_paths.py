"""TEST-001: queue failure paths with a non-mock (muse2api) provider.

The mock provider skips ``_verify_downloaded_artifact`` and boundary-frame extraction, so
these tests drive the real ``Muse2ApiAdapter`` through ``httpx.MockTransport`` (no network)
against a ``provider_type="muse2api"`` connection, plus a small stub provider for the
artifacts the adapter itself would already reject. They assert task/shot state, error
classification, lease release, DB rows and files on disk.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp(prefix="muse_provider_fail_test_"))

from muse_orchestrator.core.config import settings  # noqa: E402 -- env must be set before import
from muse_orchestrator.core.database import get_db  # noqa: E402
from muse_orchestrator.core.security import vault  # noqa: E402
from muse_orchestrator.main import app  # noqa: E402
from muse_orchestrator.models.domain import (  # noqa: E402
    Asset,
    Base,
    ConnectionHealthState,
    GenerationDispatchLeaseRecord,
    GenerationTaskRecord,
    ProviderConnection,
    QueueTaskState,
    ShotGenerationState,
    ShotRecord,
    ShotTakeRecord,
)
from muse_orchestrator.providers.base import ProviderTask, ProviderTaskStatus, StoredMedia  # noqa: E402
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter  # noqa: E402
from muse_orchestrator.services import generation_queue  # noqa: E402
from muse_orchestrator.services.generation_queue import GenerationQueueService  # noqa: E402

BASE_URL = "https://muse.example.com"
# Placeholder credential (not a real key); assembled so secret scanners do not flag it.
API_KEY = "-".join(["placeholder", "provider", "token", "0123456789abcdef"])
EXTERNAL_ID = "ext-task-1"
MEDIA_URL = f"{BASE_URL}/v1/media/{EXTERNAL_ID}.mp4"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
# Valid MP4 box header ("ftyp") followed by garbage: passes the adapter's magic-byte check,
# so only the FFprobe decode in _verify_downloaded_artifact can reject it.
UNDECODABLE_MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\xde\xad\xbe\xef" * 256


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    if os.environ.get("REQUIRE_FFMPEG") == "1":
        pytest.fail("ffmpeg/ffprobe required (REQUIRE_FFMPEG=1)")
    pytest.skip("ffmpeg not installed")


def make_tiny_mp4(directory: Path) -> bytes:
    out = directory / "tiny.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=12",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
        ],
        check=True,
        timeout=60,
    )
    return out.read_bytes()


class _FakeArqPool:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple, dict]] = []

    async def enqueue_job(self, function: str, *args, **kwargs):
        self.jobs.append((function, args, kwargs))
        return object()


@dataclass
class ProviderScript:
    """Scripted muse2api upstream for httpx.MockTransport; records every request."""

    dispatch: Callable[[httpx.Request], httpx.Response] | None = None
    poll: Callable[[httpx.Request], httpx.Response] | None = None
    media: Callable[[httpx.Request], httpx.Response] | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        path = request.url.path
        if request.method == "POST" and path == "/v1/videos":
            if self.dispatch is not None:
                return self.dispatch(request)
            return httpx.Response(200, json={"id": EXTERNAL_ID, "status": "queued"})
        if request.method == "GET" and path == f"/v1/videos/{EXTERNAL_ID}":
            if self.poll is not None:
                return self.poll(request)
            return httpx.Response(200, json={"status": "completed", "video_url": MEDIA_URL})
        if request.method == "GET" and path.startswith("/v1/media/"):
            assert self.media is not None, "test did not script a media response"
            return self.media(request)
        return httpx.Response(404, json={"error": "unexpected route"})

    def count(self, method: str, path: str) -> int:
        return sum(1 for call in self.calls if call == (method, path))


@dataclass
class Env:
    client: AsyncClient
    maker: async_sessionmaker
    media_root: Path
    project_id: str
    shot_id: str
    connection_id: str
    script: ProviderScript


@pytest.fixture()
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Same session options as core/database.AsyncSessionLocal.
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    media_root = tmp_path / "media"
    media_root.mkdir()
    monkeypatch.setattr(settings, "media_root", str(media_root))
    monkeypatch.setattr(settings, "video_payload_profile", "compat")
    # Deterministic backoff jitter.
    monkeypatch.setattr(generation_queue.random, "uniform", lambda _a, _b: 0.0)

    script = ProviderScript()

    def build_provider(conn: ProviderConnection, account: Any = None) -> Muse2ApiAdapter:
        assert conn.provider_type == "muse2api"
        return Muse2ApiAdapter(
            base_url=conn.base_url,
            api_key=vault.decrypt(conn.encrypted_api_key),
            timeout=conn.timeout_seconds,
            transport=httpx.MockTransport(script.handler),
        )

    monkeypatch.setattr(generation_queue, "build_provider", build_provider)

    previous_pool = getattr(app.state, "arq_pool", None)
    app.state.arq_pool = _FakeArqPool()
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            project_id = (await client.post("/api/v1/projects", json={"name": "provider-fail"})).json()["id"]
            created = await client.post(
                "/api/v1/connections",
                json={
                    "name": "muse2api-mocked",
                    "provider_type": "muse2api",
                    "base_url": BASE_URL,
                    "api_key": API_KEY,
                    "max_concurrency": 2,
                },
            )
            assert created.status_code in (200, 201), created.text
            await client.post(
                f"/api/v1/projects/{project_id}/scripts/import",
                json={
                    "raw_content": "SCENE 1: Roof\nSHOT 1: Hero looks out\n- Action: Wind\n",
                    "auto_apply_proposal": True,
                },
            )
            shots = (await client.get(f"/api/v1/projects/{project_id}/shots")).json()
            yield Env(
                client=client,
                maker=maker,
                media_root=media_root,
                project_id=project_id,
                shot_id=shots[0]["id"],
                connection_id=created.json()["id"],
                script=script,
            )
    finally:
        app.dependency_overrides.clear()
        if previous_pool is None:
            if hasattr(app.state, "arq_pool"):
                delattr(app.state, "arq_pool")
        else:
            app.state.arq_pool = previous_pool
        await engine.dispose()


async def enqueue(env: Env, task_type: str = "video") -> str:
    response = await env.client.post(
        "/api/v1/queue/tasks",
        json={
            "shot_id": env.shot_id,
            "connection_id": env.connection_id,
            "task_type": task_type,
            "model_id": "muse-video-v1" if task_type == "video" else "muse-image-v1",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def step(env: Env, task_id: str) -> GenerationTaskRecord:
    async with env.maker() as db:
        return await GenerationQueueService.step_task(db, task_id)


async def dispatch_then_poll(env: Env, task_type: str = "video") -> GenerationTaskRecord:
    task_id = await enqueue(env, task_type)
    dispatched = await step(env, task_id)
    assert dispatched.state == QueueTaskState.RUNNING
    assert dispatched.external_task_id == EXTERNAL_ID
    return await step(env, task_id)


@dataclass
class Snapshot:
    task: GenerationTaskRecord
    shot: ShotRecord
    connection: ProviderConnection
    assets: int
    takes: int
    leases: int


async def snapshot(env: Env, task_id: str) -> Snapshot:
    async with env.maker() as db:
        task = await db.get(GenerationTaskRecord, task_id)
        shot = await db.get(ShotRecord, env.shot_id)
        connection = await db.get(ProviderConnection, env.connection_id)
        assets = (
            await db.execute(select(func.count(Asset.id)).where(Asset.project_id == env.project_id))
        ).scalar_one()
        takes = (
            await db.execute(select(func.count(ShotTakeRecord.id)).where(ShotTakeRecord.shot_id == env.shot_id))
        ).scalar_one()
        leases = (
            await db.execute(
                select(func.count()).select_from(GenerationDispatchLeaseRecord).where(
                    GenerationDispatchLeaseRecord.task_id == task_id
                )
            )
        ).scalar_one()
        return Snapshot(task, shot, connection, assets, takes, leases)


def generated_files(env: Env) -> list[Path]:
    root = env.media_root / env.project_id / "generated"
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file()]


def assert_terminal_media_rejection(snap: Snapshot, message_fragment: str) -> None:
    assert snap.task.state == QueueTaskState.FAILED
    assert snap.task.error_category == "invalid_request"
    assert snap.task.retryable is False
    assert message_fragment in (snap.task.error_message_redacted or "")
    assert snap.task.result_asset_id is None and snap.task.take_id is None
    assert snap.shot.generation_state == ShotGenerationState.FAILED
    assert snap.shot.selected_take_id is None
    assert snap.assets == 0 and snap.takes == 0
    assert snap.leases == 0


# --- downloaded media validation (real adapter, MockTransport) -------------------------------


async def test_download_empty_body_fails_terminal_without_asset_or_file(env: Env) -> None:
    env.script.media = lambda _r: httpx.Response(200, content=b"", headers={"content-type": "video/mp4"})
    task = await dispatch_then_poll(env)
    snap = await snapshot(env, task.id)
    assert_terminal_media_rejection(snap, "empty media file")
    assert generated_files(env) == []


async def test_download_mislabeled_media_fails_terminal_without_asset_or_file(env: Env) -> None:
    # Declared video/mp4 but the body is a PNG: attacker-shaped content-type spoof.
    env.script.media = lambda _r: httpx.Response(200, content=PNG_BYTES, headers={"content-type": "video/mp4"})
    task = await dispatch_then_poll(env)
    snap = await snapshot(env, task.id)
    assert_terminal_media_rejection(snap, "does not match its file signature")
    assert generated_files(env) == []


async def test_download_undecodable_mp4_rejected_by_ffprobe_and_unlinked(env: Env) -> None:
    require_ffmpeg()
    env.script.media = lambda _r: httpx.Response(
        200, content=UNDECODABLE_MP4, headers={"content-type": "video/mp4"}
    )
    task = await dispatch_then_poll(env)
    snap = await snapshot(env, task.id)
    assert_terminal_media_rejection(snap, "not decodable by FFprobe")
    # The adapter had already renamed the download into place; the queue must unlink it.
    assert generated_files(env) == []


async def test_download_valid_mp4_succeeds_with_take_and_real_boundary_frames(env: Env, tmp_path: Path) -> None:
    require_ffmpeg()
    mp4 = make_tiny_mp4(tmp_path)
    env.script.media = lambda _r: httpx.Response(200, content=mp4, headers={"content-type": "video/mp4"})
    task = await dispatch_then_poll(env)
    snap = await snapshot(env, task.id)
    assert snap.task.state == QueueTaskState.SUCCEEDED
    assert snap.task.error_category is None
    assert snap.shot.generation_state == ShotGenerationState.SUCCEEDED
    assert snap.shot.selected_take_id == snap.task.take_id
    assert snap.takes == 1
    assert snap.assets == 3  # video + first/last boundary frames
    assert snap.leases == 0
    async with env.maker() as db:
        video = await db.get(Asset, snap.task.result_asset_id)
        take = await db.get(ShotTakeRecord, snap.task.take_id)
        assert video is not None and video.mime_type == "video/mp4"
        assert video.size_bytes == len(mp4)
        assert (env.media_root / video.storage_rel_path).read_bytes() == mp4
        for frame_id in (take.first_frame_asset_id, take.last_frame_asset_id):
            frame = await db.get(Asset, frame_id)
            assert frame is not None and frame.mime_type == "image/png"
            assert (env.media_root / frame.storage_rel_path).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert env.script.count("GET", f"/v1/media/{EXTERNAL_ID}.mp4") == 1


# --- _verify_downloaded_artifact with artifacts the adapter would not produce -----------------


class _StubDownloadProvider:
    """Non-mock provider whose download_result writes arbitrary bytes, bypassing adapter checks."""

    def __init__(self, content: bytes, mime_type: str) -> None:
        self.content = content
        self.mime_type = mime_type
        self.written: list[str] = []

    async def generate_video(self, request) -> ProviderTask:
        return ProviderTask(external_task_id=EXTERNAL_ID, status="queued")

    async def generate_image(self, request) -> ProviderTask:
        return ProviderTask(external_task_id=EXTERNAL_ID, status="queued")

    async def get_task(self, task_id: str) -> ProviderTaskStatus:
        return ProviderTaskStatus(external_task_id=task_id, status="succeeded", result_media_url=MEDIA_URL)

    async def download_result(self, result_ref: str, dest_dir: str) -> StoredMedia:
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, "stub_download.bin")
        with open(path, "wb") as stream:
            stream.write(self.content)
        self.written.append(path)
        return StoredMedia(local_path=path, mime_type=self.mime_type, size_bytes=len(self.content), sha256="0" * 64)


@pytest.mark.parametrize(
    ("task_type", "content", "mime_type", "message"),
    [
        ("video", b"", "video/mp4", "empty media file"),
        ("video", PNG_BYTES, "video/mp4", "MP4 signature validation"),
        ("video", UNDECODABLE_MP4, "image/png", "MP4 signature validation"),
        ("image", JPEG_BYTES, "image/png", "format/signature validation"),
        ("image", PNG_BYTES, "image/gif", "format/signature validation"),
    ],
    ids=["empty", "png-as-mp4", "mp4-labeled-png", "jpeg-as-png", "unsupported-mime"],
)
async def test_verify_downloaded_artifact_rejects_bad_media_for_non_mock_provider(
    env: Env, monkeypatch: pytest.MonkeyPatch, task_type: str, content: bytes, mime_type: str, message: str
) -> None:
    stub = _StubDownloadProvider(content, mime_type)
    monkeypatch.setattr(generation_queue, "build_provider", lambda _conn, _account=None: stub)
    task = await dispatch_then_poll(env, task_type)
    snap = await snapshot(env, task.id)
    assert_terminal_media_rejection(snap, message)
    assert stub.written and not any(os.path.exists(path) for path in stub.written)


# --- poll outcomes ----------------------------------------------------------------------------


async def test_poll_status_failed_is_terminal_and_redacts_provider_error(env: Env) -> None:
    env.script.poll = lambda _r: httpx.Response(
        200, json={"status": "failed", "error": f"content policy (key {API_KEY})"}
    )
    task = await dispatch_then_poll(env)
    snap = await snapshot(env, task.id)
    assert snap.task.state == QueueTaskState.FAILED
    assert snap.task.error_category == "upstream_generation_failed"
    assert snap.task.retryable is False
    assert API_KEY not in (snap.task.error_message_redacted or "")
    assert not any(API_KEY in str(entry) for entry in snap.task.history_log or [])
    assert snap.shot.generation_state == ShotGenerationState.FAILED
    assert snap.leases == 0 and snap.assets == 0 and snap.takes == 0
    assert env.script.count("GET", f"/v1/media/{EXTERNAL_ID}.mp4") == 0


async def test_poll_retryable_failure_keeps_running_with_backoff_and_releases_lease(env: Env) -> None:
    env.script.poll = lambda _r: httpx.Response(503, text="upstream overloaded")
    task = await dispatch_then_poll(env)
    snap = await snapshot(env, task.id)
    assert snap.task.state == QueueTaskState.RUNNING
    assert snap.task.error_category == "upstream_5xx"
    assert snap.task.retryable is True
    assert snap.task.poll_count == 1
    assert snap.task.next_poll_delay_seconds == generation_queue.compute_backoff_seconds(1) == 5.0
    assert snap.task.finished_at is None
    assert snap.shot.generation_state == ShotGenerationState.RUNNING
    assert snap.leases == 0


async def test_poll_retryable_failure_then_success_on_next_poll(env: Env, tmp_path: Path) -> None:
    require_ffmpeg()
    mp4 = make_tiny_mp4(tmp_path)
    responses = iter([httpx.Response(502, text="bad gateway")])
    env.script.poll = lambda _r: next(
        responses, httpx.Response(200, json={"status": "completed", "video_url": MEDIA_URL})
    )
    env.script.media = lambda _r: httpx.Response(200, content=mp4, headers={"content-type": "video/mp4"})
    task = await dispatch_then_poll(env)
    assert task.state == QueueTaskState.RUNNING
    final = await step(env, task.id)
    assert final.state == QueueTaskState.SUCCEEDED
    assert final.poll_count == 2
    assert env.script.count("POST", "/v1/videos") == 1


async def test_poll_terminal_auth_failure_fails_task_and_marks_connection(env: Env) -> None:
    env.script.poll = lambda _r: httpx.Response(401, json={"error": "invalid key"})
    task = await dispatch_then_poll(env)
    snap = await snapshot(env, task.id)
    assert snap.task.state == QueueTaskState.FAILED
    assert snap.task.error_category == "auth_failed"
    assert snap.task.retryable is False
    assert snap.task.finished_at is not None
    assert snap.shot.generation_state == ShotGenerationState.FAILED
    assert snap.connection.health_state == ConnectionHealthState.AUTH_ERROR
    assert snap.leases == 0


async def test_poll_network_error_is_retryable_transient(env: Env) -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    env.script.poll = refused
    task = await dispatch_then_poll(env)
    snap = await snapshot(env, task.id)
    assert snap.task.state == QueueTaskState.RUNNING
    assert snap.task.error_category == "transient_network"
    assert snap.task.retryable is True
    assert snap.leases == 0


# --- take persistence -------------------------------------------------------------------------


async def test_take_number_retry_exhaustion_cleans_up_and_does_not_succeed(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    require_ffmpeg()
    mp4 = make_tiny_mp4(tmp_path)
    env.script.media = lambda _r: httpx.Response(200, content=mp4, headers={"content-type": "video/mp4"})
    # Zero allowed attempts: _insert_take_with_number_retry reports exhaustion immediately,
    # which is the same outcome as N consecutive uniqueness races.
    monkeypatch.setattr(generation_queue, "TAKE_NUMBER_INSERT_MAX_ATTEMPTS", 0)
    task = await dispatch_then_poll(env)
    snap = await snapshot(env, task.id)
    assert snap.task.state == QueueTaskState.RUNNING
    assert snap.task.error_category == "transient_network"
    assert snap.task.retryable is True
    assert snap.task.result_asset_id is None and snap.task.take_id is None
    assert snap.assets == 0 and snap.takes == 0
    assert snap.leases == 0
    assert snap.shot.selected_take_id is None
    # Downloaded video and extracted boundary frames are removed.
    assert generated_files(env) == []


# --- dispatch outcome -------------------------------------------------------------------------


async def test_dispatch_read_timeout_after_post_is_outcome_unknown_and_never_resent(env: Env) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    env.script.dispatch = timeout
    task_id = await enqueue(env)
    await step(env, task_id)
    snap = await snapshot(env, task_id)
    assert snap.task.state == QueueTaskState.FAILED
    assert snap.task.error_category == "dispatch_outcome_unknown"
    assert snap.task.retryable is False
    assert snap.task.external_task_id is None
    assert snap.shot.generation_state == ShotGenerationState.FAILED
    assert snap.leases == 0
    # A further step (recovery cron, manual tick) must not re-send the paid request.
    again = await step(env, task_id)
    assert again.state == QueueTaskState.FAILED
    assert env.script.count("POST", "/v1/videos") == 1


async def test_dispatch_connect_error_before_send_is_retryable_and_requeued(env: Env) -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    env.script.dispatch = refused
    task_id = await enqueue(env)
    await step(env, task_id)
    snap = await snapshot(env, task_id)
    assert snap.task.state == QueueTaskState.QUEUED
    assert snap.task.error_category == "transient_network"
    assert snap.task.retryable is True
    assert snap.shot.generation_state == ShotGenerationState.QUEUED
    assert snap.leases == 0
