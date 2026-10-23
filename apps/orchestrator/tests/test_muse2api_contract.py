"""Contract tests between Muse2ApiAdapter and the www222fff/muse2api gateway.

Offline tests replay response shapes recorded from muse2api 0.2.0 (mock driver) and
always run. Live tests run against a real gateway only when MUSE2API_CONTRACT_URL and
MUSE2API_CONTRACT_KEY are set (CI starts muse2api with its mock driver); set
MUSE2API_CONTRACT_REQUIRE_MP4=1 when the gateway uses a real driver, so the downloaded
video must also be a decodable MP4.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess

import httpx
import pytest

from muse_orchestrator.core.config import settings
from muse_orchestrator.providers import muse2api_adapter
from muse_orchestrator.providers.base import ImageGenerationRequest, VideoGenerationRequest
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter

BASE = "http://muse2api.test"
PNG_DATA_URI = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()

# Shapes recorded from muse2api 0.2.0 (src/muse2api/api/routes/videos.py, images.py).
VIDEO_TASK_QUEUED = {"id": "task_v1", "object": "video", "status": "queued", "progress": 0,
                     "created_at": 1, "model": "muse-video", "result": None, "error": None}
VIDEO_TASK_DONE = {**VIDEO_TASK_QUEUED, "status": "succeeded", "progress": 100,
                   "result": {"url": f"{BASE}/v1/media/vid_1.mp4", "mime": "video/mp4",
                              "width": None, "height": None}}
IMAGE_TASK_QUEUED = {"id": "task_i1", "object": "image.task", "status": "queued", "progress": 0,
                     "created_at": 1, "model": "muse-image", "result": None, "error": None}
IMAGE_TASK_DONE = {**IMAGE_TASK_QUEUED, "status": "succeeded",
                   "result": {"created": 1, "data": [{"url": f"{BASE}/v1/media/img_1.png"}]}}


@pytest.fixture()
def www222fff(monkeypatch):
    monkeypatch.setattr(settings, "video_payload_profile", "www222fff")
    monkeypatch.setattr(settings, "video_payload_size", "16:9")


def _adapter(handler, seen: list[httpx.Request]) -> Muse2ApiAdapter:
    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return Muse2ApiAdapter(BASE, "contract-key", transport=httpx.MockTransport(record))


async def test_video_payload_matches_www222fff_schema(www222fff) -> None:
    seen: list[httpx.Request] = []
    adapter = _adapter(lambda _r: httpx.Response(200, json=VIDEO_TASK_QUEUED), seen)
    task = await adapter.generate_video(VideoGenerationRequest(
        shot_id="sht", prompt="fox", model_id="muse-video", duration_seconds=4.5,
        first_frame_url=PNG_DATA_URI, idempotency_key="k1",
    ))
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/videos"
    # VideoCreateRequest.duration is `int` in [1, 60]: 4.5 would be rejected with 422.
    assert body["duration"] == 4 and isinstance(body["duration"], int)
    assert body["size"] == "16:9" and body["image"] == PNG_DATA_URI
    assert "first_frame_image" not in body and "aspect_ratio" not in body
    assert task.external_task_id == "task_v1"


async def test_video_duration_is_clamped_to_gateway_range(www222fff) -> None:
    for seconds, expected in ((0.2, 1), (5.0, 5), (59.6, 60), (600.0, 60)):
        seen: list[httpx.Request] = []
        adapter = _adapter(lambda _r: httpx.Response(200, json=VIDEO_TASK_QUEUED), seen)
        await adapter.generate_video(VideoGenerationRequest(shot_id="s", prompt="p", duration_seconds=seconds))
        assert json.loads(seen[0].content)["duration"] == expected


async def test_video_result_url_is_read_from_nested_result(www222fff) -> None:
    adapter = _adapter(lambda _r: httpx.Response(200, json=VIDEO_TASK_DONE), [])
    status = await adapter.get_task("task_v1")
    assert status.status == "succeeded"
    assert status.result_media_url == f"{BASE}/v1/media/vid_1.mp4"


async def test_legacy_top_level_url_is_still_supported() -> None:
    legacy = {"id": "t", "status": "completed", "video_url": f"{BASE}/v1/media/old.mp4"}
    adapter = _adapter(lambda _r: httpx.Response(200, json=legacy), [])
    assert (await adapter.get_task("t")).result_media_url == f"{BASE}/v1/media/old.mp4"


async def test_image_task_uses_image_routes_and_payload(www222fff) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=IMAGE_TASK_QUEUED)
        return httpx.Response(200, json=IMAGE_TASK_DONE)

    adapter = _adapter(handler, seen)
    task = await adapter.generate_image(ImageGenerationRequest(
        shot_id="sht", prompt="poster", model_id="muse-image", aspect_ratio="9:16",
        reference_image_urls=[PNG_DATA_URI],
    ))
    body = json.loads(seen[0].content)
    assert body["async"] is True and body["size"] == "9:16" and body["image"] == [PNG_DATA_URI]
    assert "aspect_ratio" not in body and "reference_images" not in body
    assert task.external_task_id == "image:task_i1"

    status = await adapter.get_task(task.external_task_id)
    # Image tasks live under /v1/images/generations; /v1/videos/{id} answers 404 for them.
    assert seen[1].url.path == "/v1/images/generations/task_i1"
    assert status.status == "succeeded"
    assert status.result_media_url == f"{BASE}/v1/media/img_1.png"


async def test_too_long_image_task_id_is_dispatch_outcome_unknown(www222fff) -> None:
    long_id = {**IMAGE_TASK_QUEUED, "id": "x" * muse2api_adapter._MAX_TASK_ID_CHARS}
    adapter = _adapter(lambda _r: httpx.Response(200, json=long_id), [])
    with pytest.raises(Exception) as excinfo:
        await adapter.generate_image(ImageGenerationRequest(shot_id="s", prompt="p"))
    assert getattr(excinfo.value, "category", None).value == "dispatch_outcome_unknown"


# --- operator questions (needs_input) ------------------------------------------------------

QUESTION = {"kind": "question", "text": "Which style do you prefer?",
            "options": ["Cinematic", "Anime"], "asked_at": 1761200000.5}
VIDEO_TASK_NEEDS_INPUT = {**VIDEO_TASK_QUEUED, "status": "needs_input", "question": QUESTION,
                          "interactions": []}
VIDEO_TASK_RUNNING_AFTER_REPLY = {
    **VIDEO_TASK_QUEUED, "status": "running", "question": None,
    "interactions": [{**QUESTION, "answer": {"choice": "Anime"}, "answered_at": 1761200010.0}],
}
VIDEO_TASK_INPUT_TIMEOUT = {
    **VIDEO_TASK_QUEUED, "status": "failed", "question": None,
    "error": {"message": "No operator answer within 30 minutes.", "code": "input_timeout"},
}
CONFLICT = {"error": {"message": "Task is not waiting for input.", "code": "conflict"}}
INVALID = {"error": {"message": "choice must be one of the options", "code": "invalid_request"}}


async def test_needs_input_maps_status_and_bounded_question() -> None:
    adapter = _adapter(lambda _r: httpx.Response(200, json=VIDEO_TASK_NEEDS_INPUT), [])
    status = await adapter.get_task("task_v1")
    assert status.status == "needs_input"
    assert status.question == QUESTION


async def test_question_is_ignored_unless_status_is_needs_input() -> None:
    running = {**VIDEO_TASK_QUEUED, "status": "running", "question": QUESTION}
    adapter = _adapter(lambda _r: httpx.Response(200, json=running), [])
    status = await adapter.get_task("task_v1")
    assert status.status == "running" and status.question is None


async def test_needs_input_without_question_keeps_status_and_null_question() -> None:
    adapter = _adapter(lambda _r: httpx.Response(200, json={**VIDEO_TASK_NEEDS_INPUT, "question": None}), [])
    status = await adapter.get_task("task_v1")
    assert status.status == "needs_input" and status.question is None


@pytest.mark.parametrize("bad_question", [
    {**QUESTION, "kind": "payment"},
    {**QUESTION, "text": "x" * 4001},
    {**QUESTION, "text": 42},
    {**QUESTION, "options": [f"o{i}" for i in range(11)]},
    {**QUESTION, "options": ["y" * 201]},
    {**QUESTION, "options": [1, 2]},
    "not-an-object",
])
async def test_out_of_contract_question_is_non_retryable_invalid_request(bad_question) -> None:
    view = {**VIDEO_TASK_NEEDS_INPUT, "question": bad_question}
    adapter = _adapter(lambda _r: httpx.Response(200, json=view), [])
    with pytest.raises(Exception) as excinfo:
        await adapter.get_task("task_v1")
    assert excinfo.value.category.value == "invalid_request"
    assert excinfo.value.retryable is False


async def test_stalled_question_with_keep_waiting_options_is_accepted() -> None:
    stalled = {"kind": "stalled", "text": "Generation seems stuck.", "options": ["Keep waiting", "Give up"],
               "asked_at": 1761200100}
    adapter = _adapter(lambda _r: httpx.Response(200, json={**VIDEO_TASK_NEEDS_INPUT, "question": stalled}), [])
    status = await adapter.get_task("task_v1")
    assert status.question == {**stalled, "asked_at": 1761200100.0}


async def test_input_timeout_failure_exposes_error_code() -> None:
    adapter = _adapter(lambda _r: httpx.Response(200, json=VIDEO_TASK_INPUT_TIMEOUT), [])
    status = await adapter.get_task("task_v1")
    assert status.status == "failed"
    assert status.error_code == "input_timeout"
    assert status.error_message == "No operator answer within 30 minutes."


async def test_reply_posts_exact_payload_to_video_route() -> None:
    seen: list[httpx.Request] = []
    adapter = _adapter(lambda _r: httpx.Response(200, json=VIDEO_TASK_RUNNING_AFTER_REPLY), seen)
    status = await adapter.reply("task_v1", {"choice": "Anime"})
    assert seen[0].method == "POST" and seen[0].url.path == "/v1/videos/task_v1/reply"
    assert json.loads(seen[0].content) == {"choice": "Anime"}
    assert seen[0].headers["Authorization"] == "Bearer contract-key"
    assert status.status == "running" and status.question is None


async def test_reply_routes_image_tasks_to_image_reply_route() -> None:
    seen: list[httpx.Request] = []
    view = {**IMAGE_TASK_QUEUED, "status": "running", "question": None}
    adapter = _adapter(lambda _r: httpx.Response(200, json=view), seen)
    await adapter.reply("image:task_i1", {"text": "Use a blue background"})
    assert seen[0].url.path == "/v1/images/generations/task_i1/reply"
    assert json.loads(seen[0].content) == {"text": "Use a blue background"}


async def test_reply_conflict_is_input_not_pending_and_not_retryable() -> None:
    adapter = _adapter(lambda _r: httpx.Response(409, json=CONFLICT), [])
    with pytest.raises(Exception) as excinfo:
        await adapter.reply("task_v1", {"text": "hi"})
    assert excinfo.value.category.value == "input_not_pending"
    assert excinfo.value.retryable is False and excinfo.value.http_status == 409


@pytest.mark.parametrize("status_code", [400, 404])
async def test_reply_bad_request_and_unknown_task_are_invalid_request(status_code) -> None:
    adapter = _adapter(lambda _r: httpx.Response(status_code, json=INVALID), [])
    with pytest.raises(Exception) as excinfo:
        await adapter.reply("task_v1", {"choice": "Nope"})
    assert excinfo.value.category.value == "invalid_request"
    assert excinfo.value.retryable is False


@pytest.mark.parametrize("answer", [{}, {"text": "a", "choice": "b"}, {"text": ""}, {"text": "x" * 4001},
                                    {"choice": "c" * 201}, {"other": "x"}])
async def test_reply_rejects_malformed_answers_before_sending(answer) -> None:
    seen: list[httpx.Request] = []
    adapter = _adapter(lambda _r: httpx.Response(200, json=VIDEO_TASK_RUNNING_AFTER_REPLY), seen)
    with pytest.raises(Exception) as excinfo:
        await adapter.reply("task_v1", answer)
    assert excinfo.value.category.value == "invalid_request"
    assert seen == []


# --- live gateway -------------------------------------------------------------------------

LIVE_URL = os.environ.get("MUSE2API_CONTRACT_URL", "").rstrip("/")
LIVE_KEY = os.environ.get("MUSE2API_CONTRACT_KEY", "")
REQUIRE_MP4 = os.environ.get("MUSE2API_CONTRACT_REQUIRE_MP4") == "1"
if os.environ.get("MUSE2API_CONTRACT_REQUIRE") == "1" and not (LIVE_URL and LIVE_KEY):
    # CI sets this so a missing gateway fails loudly instead of skipping to green.
    raise RuntimeError("MUSE2API_CONTRACT_REQUIRE=1 but MUSE2API_CONTRACT_URL/KEY are not set")
live = pytest.mark.skipif(not (LIVE_URL and LIVE_KEY), reason="MUSE2API_CONTRACT_URL/KEY not set")


def _decodable_first_frame(tmp_path) -> str:
    """A real 1280x720 PNG; a real gateway may stall on an undecodable placeholder."""
    if not shutil.which("ffmpeg"):
        return PNG_DATA_URI
    frame = tmp_path / "first_frame.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=1",
         "-frames:v", "1", str(frame)],
        check=True, timeout=60,
    )
    return "data:image/png;base64," + base64.b64encode(frame.read_bytes()).decode()


async def _poll(adapter: Muse2ApiAdapter, task_id: str, timeout: float):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        status = await adapter.get_task(task_id)
        if status.status in ("succeeded", "failed") or asyncio.get_running_loop().time() > deadline:
            return status
        await asyncio.sleep(1.0)


@live
async def test_live_health_and_models() -> None:
    adapter = Muse2ApiAdapter(LIVE_URL, LIVE_KEY)
    health = await adapter.health()
    assert health.healthy and health.ready, health
    modalities = {m.modality for m in await adapter.list_models()}
    assert {"video", "image"} <= modalities
    # The pinned gateway advertises first-frame support (False for its mock driver).
    assert isinstance(await adapter.first_frame_capability(), bool)


@live
async def test_live_video_round_trip(www222fff, tmp_path) -> None:
    adapter = Muse2ApiAdapter(LIVE_URL, LIVE_KEY)
    task = await adapter.generate_video(VideoGenerationRequest(
        shot_id="contract", prompt="A red fox runs through snow", model_id="muse-video",
        duration_seconds=4.5, first_frame_url=_decodable_first_frame(tmp_path),
        idempotency_key="contract-video",
    ))
    status = await _poll(adapter, task.external_task_id, timeout=float(os.environ.get("MUSE2API_CONTRACT_TIMEOUT", "900")))
    assert status.status == "succeeded", status.error_message
    assert status.result_media_url
    media = await adapter.download_result(status.result_media_url, str(tmp_path))
    assert media.mime_type == "video/mp4" and media.size_bytes > 0
    if REQUIRE_MP4:
        assert shutil.which("ffprobe"), "ffprobe is required to verify real gateway output"
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", media.local_path],
                               capture_output=True, text=True, timeout=60)
        assert probe.returncode == 0, probe.stderr[:500]


@live
async def test_live_image_round_trip(www222fff, tmp_path) -> None:
    adapter = Muse2ApiAdapter(LIVE_URL, LIVE_KEY)
    task = await adapter.generate_image(ImageGenerationRequest(
        shot_id="contract", prompt="A lighthouse at dusk", model_id="muse-image", aspect_ratio="16:9",
    ))
    assert task.external_task_id.startswith("image:")
    status = await _poll(adapter, task.external_task_id, timeout=float(os.environ.get("MUSE2API_CONTRACT_TIMEOUT", "900")))
    assert status.status == "succeeded", status.error_message
    media = await adapter.download_result(status.result_media_url, str(tmp_path))
    assert media.mime_type.startswith("image/") and media.size_bytes > 0
