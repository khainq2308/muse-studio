from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEVELOPMENT_ENCRYPTION_KEY = "dev-only-32-byte-secret-key-change-me!!"
_DEV_ENVIRONMENTS = {"development", "dev", "local", "test", "testing"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "MuseStudio Orchestrator API"
    environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./musestudio_dev.db"
    redis_url: str = "redis://localhost:6379/0"
    media_root: str = "./storage/media"
    secret_encryption_key: str = _DEVELOPMENT_ENCRYPTION_KEY
    studio_admin_token: str | None = None

    # Payload profiles are source-contract compatibility choices, not live verification.
    video_payload_profile: Literal["compat", "www222fff", "czg86389-hub"] = Field(
        default="compat", validation_alias="MUSE2API_VIDEO_PAYLOAD_PROFILE"
    )
    # The www222fff "size" value is intentionally deployment-supplied: its accepted
    # representation must be confirmed against the actual deployed gateway.
    video_payload_size: str | None = Field(
        default=None, validation_alias="MUSE2API_VIDEO_SIZE"
    )

    # M7: orphan media cleanup (ARQ cron) and project backups.
    orphan_cleanup_enabled: bool = True
    orphan_cleanup_retention_hours: float = 24.0
    backup_retention_count: int = 10

    @model_validator(mode="after")
    def require_production_secrets(self) -> "Settings":
        if self.video_payload_profile == "www222fff" and not (self.video_payload_size or "").strip():
            raise ValueError(
                "MUSE2API_VIDEO_SIZE is required when MUSE2API_VIDEO_PAYLOAD_PROFILE=www222fff; "
                "configure the value accepted by the deployed muse2api version."
            )

        # Fail closed: only explicit dev/test environments may use the public default key.
        if self.environment.strip().lower() not in _DEV_ENVIRONMENTS:
            if not self.studio_admin_token or not self.studio_admin_token.strip():
                raise ValueError("STUDIO_ADMIN_TOKEN is required outside development.")
            if (
                self.secret_encryption_key == _DEVELOPMENT_ENCRYPTION_KEY
                or len(self.secret_encryption_key.strip()) < 32
            ):
                raise ValueError(
                    "SECRET_ENCRYPTION_KEY must be a stable, non-default secret of at least 32 characters."
                )
        return self


settings = Settings()

_SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(authorization\s*[:=]\s*['\"]?(?:bearer|basic)\s+)[^\s,'\"}]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:x-admin-key|api[-_]?key|access[-_]?token|secret)\s*[:=]\s*['\"]?)[^\s,'\"}&]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:set-)?cookie\s*[:=]\s*['\"]?)[^\r\n\"']+"), r"\1[REDACTED_COOKIE]"),
    (re.compile(r"((?:http|https|socks5|socks5h)://)([^:/\s]+:[^@/\s]+)@"), r"\1[REDACTED_PROXY_CRED]@"),
]


class SecretRedactor:
    @staticmethod
    def redact_text(text: str, explicit_secrets: list[str] | None = None) -> str:
        if not text:
            return text
        sanitized = text
        if explicit_secrets:
            for secret in explicit_secrets:
                if secret and len(secret) >= 4:
                    sanitized = sanitized.replace(secret, "[REDACTED]")
        for pattern, replacement in _SENSITIVE_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized

    @classmethod
    def redact_dict(cls, data: dict[str, Any], explicit_secrets: list[str] | None = None) -> dict[str, Any]:
        sensitive_keys = {
            "api_key",
            "admin_api_key",
            "cookie",
            "cookies",
            "password",
            "proxy_url",
            "authorization",
            "secret",
        }
        result: dict[str, Any] = {}
        for key, value in data.items():
            if key.lower() in sensitive_keys and isinstance(value, str):
                result[key] = cls.redact_text(value, explicit_secrets) if "://" in value else "[REDACTED]"
            elif isinstance(value, dict):
                result[key] = cls.redact_dict(value, explicit_secrets)
            elif isinstance(value, str):
                result[key] = cls.redact_text(value, explicit_secrets)
            else:
                result[key] = value
        return result
