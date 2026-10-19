from __future__ import annotations

import pytest
from pydantic import ValidationError

from muse_orchestrator.core.config import Settings


def _set_production_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STUDIO_ADMIN_TOKEN", "production-admin-token")
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", "k" * 48)
    monkeypatch.delenv("MUSE2API_VIDEO_SIZE", raising=False)


def test_production_requires_provider_specific_payload_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_production_secrets(monkeypatch)
    monkeypatch.setenv("MUSE2API_VIDEO_PAYLOAD_PROFILE", "compat")

    with pytest.raises(ValidationError, match="provider-specific schema"):
        Settings()


def test_production_accepts_explicit_provider_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_production_secrets(monkeypatch)
    monkeypatch.setenv("MUSE2API_VIDEO_PAYLOAD_PROFILE", "czg86389-hub")

    assert Settings().video_payload_profile == "czg86389-hub"


def test_production_www222fff_profile_requires_confirmed_size(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_production_secrets(monkeypatch)
    monkeypatch.setenv("MUSE2API_VIDEO_PAYLOAD_PROFILE", "www222fff")

    with pytest.raises(ValidationError, match="MUSE2API_VIDEO_SIZE"):
        Settings()


def test_development_keeps_compat_profile_for_local_testing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MUSE2API_VIDEO_PAYLOAD_PROFILE", "compat")
    monkeypatch.delenv("MUSE2API_VIDEO_SIZE", raising=False)

    assert Settings().video_payload_profile == "compat"
