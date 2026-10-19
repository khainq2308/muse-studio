from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from muse_orchestrator.core.config import settings
from muse_orchestrator.models.domain import Asset
from muse_orchestrator.providers.base import ProviderFailure, VideoGenerationRequest
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter
from muse_orchestrator.services.generation_queue import (
    GenerationQueueService,
    _create_mock_boundary_frame_png,
)


class FakeAssetSession:
    def __init__(self, assets: dict[str, Asset]) -> None:
        self.assets = assets

    async def get(self, model, asset_id: str):
        assert model is Asset
        return self.assets.get(asset_id)


def _asset_for_frame(frame, project_id: str = "project-1", sha256: str | None = None) -> Asset:
    path = Path(frame.path)
    return Asset(
        id="asset-frame-1",
        project_id=project_id,
        scene_id="scene-1",
        shot_id="shot-1",
        asset_type="reference",
        filename=path.name,
        storage_rel_path=path.name,
        mime_type="image/png",
        size_bytes=path.stat().st_size,
        sha256=sha256 or frame.sha256,
        is_locked_reference=True,
    )


@pytest.mark.asyncio
async def test_frame_chain_handoff_builds_data_uri_from_valid_local_asset(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    frame = _create_mock_boundary_frame_png(str(tmp_path), "shot-1", 1, "last", "test-seed")
    asset = _asset_for_frame(frame)
    db = FakeAssetSession({asset.id: asset})
    shot = SimpleNamespace(
        project_id="project-1",
        first_frame_asset_id=asset.id,
        continuity_mode="frame_chain",
        previous_shot_id="shot-0",
    )

    data_uri = await GenerationQueueService.load_first_frame_data_uri(db, shot)

    assert data_uri is not None
    header, payload = data_uri.split(",", 1)
    assert header == "data:image/png;base64"
    assert base64.b64decode(payload) == Path(frame.path).read_bytes()
    assert asset.id not in data_uri


@pytest.mark.asyncio
async def test_frame_chain_handoff_rejects_corrupted_asset_hash(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    frame = _create_mock_boundary_frame_png(str(tmp_path), "shot-1", 1, "last", "test-seed")
    asset = _asset_for_frame(frame, sha256="0" * 64)
    db = FakeAssetSession({asset.id: asset})
    shot = SimpleNamespace(
        project_id="project-1",
        first_frame_asset_id=asset.id,
        continuity_mode="frame_chain",
        previous_shot_id="shot-0",
    )

    with pytest.raises(ProviderFailure, match="SHA-256"):
        await GenerationQueueService.load_first_frame_data_uri(db, shot)


@pytest.mark.asyncio
async def test_frame_chain_handoff_refuses_missing_upstream_frame():
    shot = SimpleNamespace(
        project_id="project-1",
        first_frame_asset_id=None,
        continuity_mode="frame_chain",
        previous_shot_id="shot-0",
    )

    with pytest.raises(ProviderFailure, match="refusing to dispatch"):
        await GenerationQueueService.load_first_frame_data_uri(FakeAssetSession({}), shot)


@pytest.mark.asyncio
async def test_optional_non_chained_frame_can_be_absent():
    shot = SimpleNamespace(
        project_id="project-1",
        first_frame_asset_id=None,
        continuity_mode="previous_shot",
        previous_shot_id="shot-0",
    )

    assert await GenerationQueueService.load_first_frame_data_uri(FakeAssetSession({}), shot) is None


def test_compat_video_payload_maps_first_frame_to_both_documented_fields(monkeypatch):
    monkeypatch.setattr(settings, "video_payload_profile", "compat")
    frame_data_uri = "data:image/png;base64,aGVsbG8="
    adapter = Muse2ApiAdapter("https://muse.example.com")
    request = VideoGenerationRequest(
        shot_id="shot-2",
        prompt="Continue the motion from the supplied first frame",
        model_id="muse-video-v1",
        duration_seconds=5,
        aspect_ratio="16:9",
        first_frame_url=frame_data_uri,
    )

    body = adapter._build_video_payload(request)

    assert body["image"] == frame_data_uri
    assert body["first_frame_image"] == frame_data_uri
    assert body["size"] == "16:9"
    assert body["aspect_ratio"] == "16:9"


@pytest.mark.parametrize(
    ("profile", "frame_key", "ratio_key"),
    [
        ("www222fff", "image", "size"),
        ("czg86389-hub", "first_frame_image", "aspect_ratio"),
    ],
)
def test_provider_specific_video_payload_avoids_duplicate_data_uri(
    monkeypatch, profile, frame_key, ratio_key
):
    monkeypatch.setattr(settings, "video_payload_profile", profile)
    monkeypatch.setattr(
        settings, "video_payload_size", "720x1280" if profile == "www222fff" else None
    )
    frame_data_uri = "data:image/png;base64,aGVsbG8="
    adapter = Muse2ApiAdapter("https://muse.example.com")
    request = VideoGenerationRequest(
        shot_id="shot-2",
        prompt="Continue the motion from the supplied first frame",
        model_id="muse-video-v1",
        duration_seconds=5,
        aspect_ratio="9:16",
        first_frame_url=frame_data_uri,
    )

    body = adapter._build_video_payload(request)

    assert sum(body.get(key) == frame_data_uri for key in ("image", "first_frame_image")) == 1
    if profile == "www222fff":
        assert body["image"] == frame_data_uri
        assert body["size"] == "720x1280"
        assert "aspect_ratio" not in body
        assert "first_frame_image" not in body
    else:
        assert body["first_frame_image"] == frame_data_uri
        assert body["aspect_ratio"] == "9:16"
        assert "size" not in body
        assert "image" not in body


def test_www222fff_video_payload_requires_explicit_size(monkeypatch):
    monkeypatch.setattr(settings, "video_payload_profile", "www222fff")
    monkeypatch.setattr(settings, "video_payload_size", None)
    adapter = Muse2ApiAdapter("https://muse.example.com")
    request = VideoGenerationRequest(shot_id="shot-1", prompt="A simple test")

    with pytest.raises(ValueError, match="MUSE2API_VIDEO_SIZE"):
        adapter._build_video_payload(request)


def test_video_payload_omits_optional_frame_fields_when_no_frame_is_set():
    adapter = Muse2ApiAdapter("https://muse.example.com")
    request = VideoGenerationRequest(
        shot_id="shot-1",
        prompt="Text-to-video without a first frame",
        first_frame_url=None,
    )

    body = adapter._build_video_payload(request)

    assert "image" not in body
    assert "first_frame_image" not in body


@pytest.mark.asyncio
async def test_non_chained_shot_does_not_use_its_stored_opening_frame(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    frame = _create_mock_boundary_frame_png(str(tmp_path), "shot-1", 1, "first", "test-seed")
    asset = _asset_for_frame(frame)
    db = FakeAssetSession({asset.id: asset})
    shot = SimpleNamespace(
        project_id="project-1",
        first_frame_asset_id=asset.id,
        continuity_mode="previous_shot",
        previous_shot_id="shot-0",
    )

    assert await GenerationQueueService.load_first_frame_data_uri(db, shot) is None
