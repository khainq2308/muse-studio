"""Queue-level behaviour for media a provider hands back, and for frame chaining.

A scripted provider stands in for muse2api; everything else (database, asset rows, files on disk,
FFmpeg frame extraction) is real. The download itself (size cap, signature sniffing, streaming) is
covered in test_provider_download_security.py.
"""

from __future__ import annotations

import base64
import hashlib
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from muse_orchestrator.core.config import settings
from muse_orchestrator.models.domain import (
    Asset,
    Base,
    GenerationTaskRecord,
    Project,
    ProviderConnection,
    QueueTaskState,
    SceneRecord,
    ShotGenerationState,
    ShotRecord,
    ShotTakeRecord,
)
from muse_orchestrator.providers.base import (
    ErrorCategory,
    ProviderFailure,
    ProviderTask,
    ProviderTaskStatus,
    StoredMedia,
)
from muse_orchestrator.services import frame_chain, generation_queue
from muse_orchestrator.services.asset_storage import LocalAssetStorage
from muse_orchestrator.services.frame_chain import (
    FrameInputError,
    FrameToolUnavailableError,
    load_frame_data_uri,
    placeholder_png,
)
from muse_orchestrator.services.generation_queue import GenerationQueueService

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _make_video(path: Path, size: str = "320x240", seconds: int = 2, rate: int = 24) -> bytes:
    # testsrc draws a running counter, so the first and the last frame are visibly different.
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate={rate}",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", str(path),
        ],
        check=True,
    )
    return path.read_bytes()


def _png_dimensions(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    width, height = out.split("x")
    return int(width), int(height)


class ScriptedProvider:
    """Succeeds immediately; what download_result does is decided by the test."""

    def __init__(
        self,
        body: bytes | None = None,
        mime_type: str = "video/mp4",
        download_error: ProviderFailure | None = None,
    ) -> None:
        self.body = body
        self.mime_type = mime_type
        self.download_error = download_error
        self.video_requests: list = []

    async def generate_video(self, request):
        self.video_requests.append(request)
        return ProviderTask(external_task_id="ext_1", status="queued")

    async def generate_image(self, request):
        return ProviderTask(external_task_id="ext_img", status="queued")

    async def get_task(self, task_id: str) -> ProviderTaskStatus:
        return ProviderTaskStatus(
            external_task_id=task_id, status="succeeded",
            result_media_url="https://cdn.example.com/result", account_used="acc_1",
        )

    async def download_result(self, result_ref: str, dest_dir: str) -> StoredMedia:
        if self.download_error is not None:
            raise self.download_error
        assert self.body is not None
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(self.body).hexdigest()
        extension = "mp4" if self.mime_type.startswith("video/") else "png"
        path = Path(dest_dir) / f"{digest[:16]}.{extension}"
        path.write_bytes(self.body)
        return StoredMedia(str(path), self.mime_type, len(self.body), digest)


@pytest.fixture()
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture()
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr(settings, "media_root", str(root))
    return root


async def _seed(db: AsyncSession, provider_type: str = "muse2api") -> tuple[ShotRecord, ProviderConnection]:
    db.add(Project(id="proj_1", name="Pipeline"))
    db.add(SceneRecord(id="scn_1", project_id="proj_1", scene_index=1, title="Scene 1"))
    shot = ShotRecord(
        id="sht_1", project_id="proj_1", scene_id="scn_1", sequence_index=1,
        title="Shot 1", location_code="loc_1", duration_seconds=2.0,
    )
    conn = ProviderConnection(
        id="conn_1", name="pool", provider_type=provider_type, base_url="https://muse.example.com",
    )
    db.add_all([shot, conn])
    await db.commit()
    return shot, conn


async def _run(
    db: AsyncSession,
    shot: ShotRecord,
    conn: ProviderConnection,
    provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    task_type: str = "video",
) -> GenerationTaskRecord:
    monkeypatch.setattr(generation_queue, "build_provider", lambda _conn: provider)
    task, _created = await GenerationQueueService.enqueue_shot_generation(
        db, shot, conn, task_type=task_type, model_id="model-1"
    )
    for _ in range(3):  # dispatch, then poll + finish
        task = await GenerationQueueService.step_task(db, task.id)
        if task.state not in (QueueTaskState.QUEUED, QueueTaskState.RUNNING):
            break
    return task


async def _count(db: AsyncSession, model) -> int:
    return len((await db.execute(select(model))).scalars().all())


def _generated_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


# --- boundary frames come from the video, never from fabricated bytes -----------------------


@pytest.mark.asyncio
async def test_a_real_video_yields_real_decodable_boundary_frames(
    db, media_root, tmp_path, real_media_engine, monkeypatch
):
    shot, conn = await _seed(db)
    video = _make_video(tmp_path / "result.mp4")

    task = await _run(db, shot, conn, ScriptedProvider(video), monkeypatch)

    assert task.state == QueueTaskState.SUCCEEDED
    take = (await db.execute(select(ShotTakeRecord))).scalars().one()
    frames = {
        label: await db.get(Asset, asset_id)
        for label, asset_id in (("first", take.first_frame_asset_id), ("last", take.last_frame_asset_id))
    }
    for asset in frames.values():
        path = media_root / asset.storage_rel_path
        data = path.read_bytes()
        assert data.startswith(PNG_SIGNATURE)
        assert asset.mime_type == "image/png"
        assert asset.size_bytes == len(data)
        assert asset.sha256 == hashlib.sha256(data).hexdigest()
        assert _png_dimensions(path) == (320, 240)  # decodes, and has the video's own dimensions
    first_bytes = (media_root / frames["first"].storage_rel_path).read_bytes()
    last_bytes = (media_root / frames["last"].storage_rel_path).read_bytes()
    assert first_bytes != last_bytes  # two different frames of the video, not one placeholder twice
    assert b"BOUNDARY_FRAME" not in first_bytes + last_bytes

    await db.refresh(shot)
    assert shot.last_frame_asset_id == take.last_frame_asset_id
    # first_frame_asset_id is an input to generation; a finished take must not overwrite it.
    assert shot.first_frame_asset_id is None


@pytest.mark.asyncio
async def test_a_video_that_cannot_be_decoded_is_rejected_and_leaves_nothing_behind(
    db, media_root, real_media_engine, monkeypatch
):
    shot, conn = await _seed(db)
    not_a_video = b"\x00\x00\x00\x18ftypmp42" + b"looks like mp4 but is not decodable"

    task = await _run(db, shot, conn, ScriptedProvider(not_a_video), monkeypatch)

    assert task.state == QueueTaskState.FAILED
    assert task.retryable is False
    assert task.error_category == ErrorCategory.INVALID_UPSTREAM_MEDIA.value
    await db.refresh(shot)
    assert shot.generation_state == ShotGenerationState.FAILED
    assert await _count(db, Asset) == 0
    assert await _count(db, ShotTakeRecord) == 0
    assert _generated_files(media_root) == []


@pytest.mark.asyncio
async def test_missing_ffmpeg_keeps_the_video_but_blocks_frame_chaining_with_a_warning(
    db, media_root, monkeypatch
):
    shot, conn = await _seed(db)

    async def ffmpeg_missing(_video_path: str, _output_dir: str):
        raise FrameToolUnavailableError("ffprobe executable was not found.")

    monkeypatch.setattr(generation_queue, "extract_frames", ffmpeg_missing)
    video = b"\x00\x00\x00\x18ftypmp42" + b"paid-for result"

    task = await _run(db, shot, conn, ScriptedProvider(video), monkeypatch)

    assert task.state == QueueTaskState.SUCCEEDED  # the generated video is not thrown away
    assert task.error_category == "frame_extraction_unavailable"
    assert "frame chaining" in task.history_log[-1]
    take = (await db.execute(select(ShotTakeRecord))).scalars().one()
    assert take.first_frame_asset_id is None and take.last_frame_asset_id is None
    await db.refresh(shot)
    assert shot.last_frame_asset_id is None  # nothing was invented for downstream shots to chain from
    assert [a.asset_type for a in (await db.execute(select(Asset))).scalars()] == ["video"]


@pytest.mark.asyncio
async def test_the_mock_provider_gets_valid_placeholder_frames_not_real_extraction(
    db, media_root, real_ffmpeg, monkeypatch
):
    shot, conn = await _seed(db, provider_type="mock")

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("the mock provider's videos are not decodable; FFmpeg must not be used")

    monkeypatch.setattr(generation_queue, "extract_frames", must_not_run)
    video = b"\x00\x00\x00\x18ftypmp42" + b"mock"

    task = await _run(db, shot, conn, ScriptedProvider(video), monkeypatch)

    assert task.state == QueueTaskState.SUCCEEDED
    take = (await db.execute(select(ShotTakeRecord))).scalars().one()
    for asset_id in (take.first_frame_asset_id, take.last_frame_asset_id):
        asset = await db.get(Asset, asset_id)
        assert _png_dimensions(media_root / asset.storage_rel_path) == (1, 1)  # a real, decodable PNG


# --- what the queue does with a bad download -----------------------------------------------


@pytest.mark.asyncio
async def test_a_result_of_the_wrong_kind_is_rejected_and_its_file_removed(db, media_root, monkeypatch):
    shot, conn = await _seed(db)
    png = placeholder_png("wrong-kind")

    task = await _run(db, shot, conn, ScriptedProvider(png, mime_type="image/png"), monkeypatch)

    assert task.state == QueueTaskState.FAILED
    assert task.error_category == ErrorCategory.INVALID_UPSTREAM_MEDIA.value
    assert "image/png for a video task" in task.error_message_redacted
    assert await _count(db, Asset) == 0
    assert _generated_files(media_root) == []


@pytest.mark.asyncio
async def test_a_permanent_download_failure_fails_the_task_instead_of_polling_forever(
    db, media_root, monkeypatch
):
    shot, conn = await _seed(db)
    refused = ProviderFailure(ErrorCategory.INVALID_UPSTREAM_MEDIA, "Result is not a supported image or video file.")

    task = await _run(db, shot, conn, ScriptedProvider(download_error=refused), monkeypatch)

    assert task.state == QueueTaskState.FAILED
    assert task.retryable is False
    assert task.finished_at is not None
    await db.refresh(shot)
    assert shot.generation_state == ShotGenerationState.FAILED


@pytest.mark.asyncio
async def test_a_transient_download_failure_stays_running_and_retries(db, media_root, monkeypatch):
    shot, conn = await _seed(db)
    flaky = ProviderFailure(ErrorCategory.TRANSIENT_NETWORK, "connection reset", retryable=True)

    task = await _run(db, shot, conn, ScriptedProvider(download_error=flaky), monkeypatch)

    assert task.state == QueueTaskState.RUNNING
    assert any("will retry" in line for line in task.history_log)
    assert await _count(db, Asset) == 0


@pytest.mark.asyncio
async def test_a_failed_download_does_not_delete_a_file_another_asset_already_uses(
    db, media_root, monkeypatch
):
    shot, conn = await _seed(db)
    png = placeholder_png("shared")
    digest = hashlib.sha256(png).hexdigest()
    shared = media_root / "proj_1" / "generated" / f"{digest[:16]}.png"
    shared.parent.mkdir(parents=True)
    shared.write_bytes(png)
    db.add(Asset(
        id="ast_existing", project_id="proj_1", asset_type="image", filename=shared.name,
        storage_rel_path=str(shared.relative_to(media_root)), mime_type="image/png",
        size_bytes=len(png), sha256=digest,
    ))
    await db.commit()

    await _run(db, shot, conn, ScriptedProvider(png, mime_type="image/png"), monkeypatch)  # wrong kind

    assert shared.is_file()  # the already-registered asset keeps its file


# --- the first frame sent to the provider is the frame's pixels, not an asset id -----------


async def _store_input_frame(db: AsyncSession, media_root: Path, data: bytes) -> Asset:
    saved = LocalAssetStorage(str(media_root)).save_bytes("proj_1", "prev_last.png", data, "image/png")
    asset = Asset(
        id="ast_prev_last", project_id="proj_1", asset_type="reference", filename=saved.safe_filename,
        storage_rel_path=saved.storage_rel_path, mime_type="image/png",
        size_bytes=saved.size_bytes, sha256=saved.sha256,
    )
    db.add(asset)
    await db.commit()
    return asset


@pytest.mark.asyncio
async def test_the_first_frame_is_sent_as_a_data_uri_of_the_stored_image(
    db, media_root, real_media_engine, tmp_path, monkeypatch
):
    shot, conn = await _seed(db)
    frame = placeholder_png("previous shot, last frame")
    asset = await _store_input_frame(db, media_root, frame)
    shot.first_frame_asset_id = asset.id
    await db.commit()
    provider = ScriptedProvider(_make_video(tmp_path / "result.mp4"))

    task = await _run(db, shot, conn, provider, monkeypatch)

    assert task.state == QueueTaskState.SUCCEEDED
    sent = provider.video_requests[0].first_frame_url
    assert sent == "data:image/png;base64," + base64.b64encode(frame).decode("ascii")
    assert asset.id not in sent
    await db.refresh(shot)
    assert shot.first_frame_asset_id == asset.id  # still the input, not replaced by this take's frame


@pytest.mark.asyncio
async def test_no_first_frame_means_none_is_sent(db, media_root, real_media_engine, tmp_path, monkeypatch):
    shot, conn = await _seed(db)
    provider = ScriptedProvider(_make_video(tmp_path / "result.mp4"))

    await _run(db, shot, conn, provider, monkeypatch)

    assert provider.video_requests[0].first_frame_url is None


@pytest.mark.asyncio
async def test_a_first_frame_that_cannot_be_loaded_fails_the_task_without_calling_the_provider(
    db, media_root, monkeypatch
):
    shot, conn = await _seed(db)
    shot.first_frame_asset_id = "ast_does_not_exist"
    await db.commit()
    provider = ScriptedProvider(b"unused")

    task = await _run(db, shot, conn, provider, monkeypatch)

    assert task.state == QueueTaskState.FAILED
    assert task.error_category == ErrorCategory.INVALID_REQUEST.value
    assert provider.video_requests == []


def test_load_frame_data_uri_refuses_oversize_and_non_image_files(tmp_path, monkeypatch):
    storage = LocalAssetStorage(str(tmp_path / "media"))
    png = storage.save_bytes("p", "ok.png", placeholder_png("ok"), "image/png")
    video = storage.save_bytes("p", "clip.mp4", b"\x00\x00\x00\x18ftypmp42" + b"x" * 20, "video/mp4")

    assert load_frame_data_uri(storage, png.storage_rel_path).startswith("data:image/png;base64,")
    with pytest.raises(FrameInputError, match="not a PNG, JPEG or WebP"):
        load_frame_data_uri(storage, video.storage_rel_path)
    monkeypatch.setattr(frame_chain, "MAX_INLINE_FRAME_BYTES", 8)
    with pytest.raises(FrameInputError, match="inline limit"):
        load_frame_data_uri(storage, png.storage_rel_path)
    with pytest.raises(FrameInputError, match="not available"):
        load_frame_data_uri(storage, "../../etc/passwd")
