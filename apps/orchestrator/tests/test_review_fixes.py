import pytest
from muse_orchestrator.core.security import SecretVault
from muse_orchestrator.services.generation_queue import compute_backoff_seconds


def test_backoff_has_real_jitter_and_stays_bounded():
    values = {compute_backoff_seconds(3) for _ in range(30)}
    assert len(values) > 1  # jitter is random, not a per-process constant
    assert all(0 < v <= 60.0 for v in values)


def test_secret_mask_reveals_little():
    assert SecretVault.mask("sk-abcdefghijklmnop") == "********"
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

def test_secret_redactor_descends_into_nested_mappings_lists_and_tuples():
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
    for secret in (
        "sk-live-abcdef123456",
        "hunter2",
        "sk-nested-key",
        "topsecret",
        "adm-777777",
        "sk-first-key",
        "pw99",
    ):
        assert secret not in flat

    assert isinstance(redacted["events"][2], tuple)
    assert redacted["api_key"][0] == "[REDACTED]"
    assert redacted["count"] == 3
    # Redaction must not mutate caller-owned request data.
    assert data["events"][0].endswith("sk-live-abcdef123456")
    assert data["api_key"][0] == "sk-first-key"


def test_secret_redactor_inherits_sensitive_state_for_nested_containers():
    from muse_orchestrator.core.config import SecretRedactor

    cases = [
        ({"api_key": {"raw": "sk-secret-value"}}, "sk-secret-value"),
        ({"password": [{"nested": "secret-value"}]}, "secret-value"),
        ({"secret": ({"a": ["deep-secret-1"]}, "deep-secret-2")}, "deep-secret-1"),
        ({"authorization": {"scheme": {"token": "tok-abc-123"}}}, "tok-abc-123"),
        ({"outer": {"cookie": {"sid": "sid-value-9"}}}, "sid-value-9"),
    ]
    for data, secret in cases:
        assert secret not in repr(SecretRedactor.redact_dict(data))


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["socks5", "socks5h"])
async def test_installed_httpx_can_construct_accepted_socks_proxies(scheme):
    """Accepted SOCKS URLs must work with the actual dependency installation."""
    import httpx

    async with httpx.AsyncClient(proxy=f"{scheme}://user:pw@127.0.0.1:1080"):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("proxy", ["http://user:pw@proxy.example.com:8080", None])
async def test_every_provider_api_request_passes_the_account_proxy(monkeypatch, proxy):
    import httpx
    from muse_orchestrator.providers import muse2api_adapter
    from muse_orchestrator.providers.base import ImageGenerationRequest, VideoGenerationRequest

    created: list[dict] = []

    class RecordingClient:
        def __init__(self, **kwargs):
            created.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get(self, url, **kwargs):
            if url.endswith("/v1/models"):
                return httpx.Response(200, json={"data": []})
            if "/v1/videos/" in url:
                return httpx.Response(200, json={"status": "running"})
            return httpx.Response(200, json={})

        async def post(self, url, **kwargs):
            return httpx.Response(200, json={"id": "task_1", "status": "queued"})

    monkeypatch.setattr(muse2api_adapter.httpx, "AsyncClient", RecordingClient)
    adapter = muse2api_adapter.Muse2ApiAdapter(
        "http://provider.test", api_key="key-1234", proxy=proxy
    )

    await adapter.health()
    await adapter.list_models()
    await adapter.generate_image(ImageGenerationRequest(shot_id="shot_1", prompt="a rooftop"))
    await adapter.generate_video(VideoGenerationRequest(shot_id="shot_1", prompt="a rooftop"))
    await adapter.get_task("task_1")

    assert len(created) == 5
    assert all("proxy" in kwargs and kwargs["proxy"] == proxy for kwargs in created)
