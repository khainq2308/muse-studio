import pytest
from muse_orchestrator.core.security import SecretVault
from muse_orchestrator.services.generation_queue import compute_backoff_seconds


def test_backoff_has_real_jitter_and_stays_bounded():
    values = {compute_backoff_seconds(3) for _ in range(30)}
    assert len(values) > 1  # jitter is random, not a per-process constant
    assert all(0 < v <= 60.0 for v in values)


def test_secret_mask_reveals_little():
    assert SecretVault.mask("sk-abcdefghijklmnop") == "sk...op"
    assert SecretVault.mask("short") == "********"
    assert SecretVault.mask(None) == "[NOT_SET]"


def test_dispatch_lease_model_is_registered_with_metadata():
    from muse_orchestrator.models.domain import Base, GenerationDispatchLeaseRecord

    assert GenerationDispatchLeaseRecord.__tablename__ == "generation_dispatch_leases"
    assert "generation_dispatch_leases" in Base.metadata.tables


@pytest.mark.asyncio
async def test_artifact_verifier_rejects_mislabeled_or_undecodable_media(tmp_path):
    from muse_orchestrator.providers.base import ProviderFailure
    from muse_orchestrator.services.generation_queue import _verify_downloaded_artifact

    bad_image = tmp_path / "bad.png"
    bad_image.write_bytes(b"<html>not an image</html>")
    with pytest.raises(ProviderFailure, match="image"):
        await _verify_downloaded_artifact(str(bad_image), "image", "muse2api", "image/png")

    bad_video = tmp_path / "bad.mp4"
    bad_video.write_bytes(b"\x00\x00\x00\x18ftypmp42not a decodable video")
    with pytest.raises(ProviderFailure):
        await _verify_downloaded_artifact(str(bad_video), "video", "muse2api", "video/mp4")
