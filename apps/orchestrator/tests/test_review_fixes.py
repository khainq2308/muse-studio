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


def test_redact_dict_descends_into_lists_and_tuples():
    from muse_orchestrator.core.config import SecretRedactor

    data = {
        "events": [
            "retry with Authorization: Bearer sk-live-abcdef123456",
            {"proxy_url": "http://user:hunter2@10.0.0.1:8080", "api_key": "sk-nested-key"},
            ("Cookie: sid=topsecret", ["x-admin-key: adm-777777"]),
        ],
        "api_key": ["sk-first-key", "http://u:pw99@proxy.local:3128"],
        "count": 3,
    }

    redacted = SecretRedactor.redact_dict(data)

    flat = repr(redacted)
    for leaked in (
        "sk-live-abcdef123456",
        "hunter2",
        "sk-nested-key",
        "topsecret",
        "adm-777777",
        "sk-first-key",
        "pw99",
    ):
        assert leaked not in flat
    assert isinstance(redacted["events"][2], tuple)
    assert redacted["api_key"][0] == "[REDACTED]"
    assert redacted["count"] == 3
    # The input structure must not be mutated.
    assert data["events"][0].endswith("sk-live-abcdef123456")
    assert data["api_key"][0] == "sk-first-key"
