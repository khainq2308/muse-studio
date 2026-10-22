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

    # DNS-rebinding guard: while running with insecure development defaults (no admin
    # token), only these Host header names are accepted. Comma-separated.
    dev_allowed_hosts: str = Field(
        default="localhost,127.0.0.1,::1,testserver,orchestrator-api",
        validation_alias="DEV_ALLOWED_HOSTS",
    )

    @property
    def dev_allowed_host_set(self) -> frozenset[str]:
        return frozenset(
            item.strip().lower().strip("[]") for item in self.dev_allowed_hosts.split(",") if item.strip()
        )

    @model_validator(mode="after")
    def require_production_secrets(self) -> "Settings":
        if self.video_payload_profile == "www222fff" and not (self.video_payload_size or "").strip():
            raise ValueError(
                "MUSE2API_VIDEO_SIZE is required when MUSE2API_VIDEO_PAYLOAD_PROFILE=www222fff; "
                "configure the value accepted by the deployed muse2api version."
            )

        # "compat" duplicates first-frame bytes into two JSON fields and sends size/aspect
        # aliases without knowing which schema the deployed gateway actually accepts.
        # Keep it for development, but require production to choose a provider-specific
        # contract so large data URIs are not silently duplicated.
        if (
            self.environment.strip().lower() not in _DEV_ENVIRONMENTS
            and self.video_payload_profile == "compat"
        ):
            raise ValueError(
                "MUSE2API_VIDEO_PAYLOAD_PROFILE must select a provider-specific schema "
                "outside development; choose a profile verified against the deployed gateway."
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

    @property
    def insecure_dev_defaults(self) -> bool:
        """True when the process runs with development defaults that are unsafe to expose.

        In a development environment the API is unauthenticated while no admin
        token is set, and provider secrets are encrypted with a publicly known
        key. Both are convenient on a developer machine and dangerous anywhere
        else, so the startup path warns loudly whenever they are in effect.
        """
        if self.environment.strip().lower() not in _DEV_ENVIRONMENTS:
            return False
        return (
            not (self.studio_admin_token or "").strip()
            or self.secret_encryption_key == _DEVELOPMENT_ENCRYPTION_KEY
        )


settings = Settings()

_SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(authorization\s*[:=]\s*['\"]?(?:bearer|basic)\s+)[^\s,'\"}]+"), r"\1[REDACTED]"),
    (
        re.compile(
            r"(?i)(\b(?:x-admin-key|x-api-key|x-studio-admin-token|api[-_]?key|access[-_]?token"
            r"|refresh[-_]?token|client[-_]?secret|private[-_]?key|password|passwd|secret)"
            r"\s*[:=]\s*['\"]?)[^\s,'\"}&]+"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)((?:set-)?cookie\s*[:=]\s*['\"]?)[^\r\n\"']+"), r"\1[REDACTED_COOKIE]"),
]

# Proxy/provider URLs with userinfo. The userinfo part is matched greedily up to the
# LAST "@" of the whitespace-delimited token so passwords containing "/" or "@" are
# still fully hidden. Scheme matching is case-insensitive and covers SOCKS4/5.
_URL_USERINFO_PATTERN = re.compile(
    r"(?i)\b((?:https?|socks4a?|socks5h?)://)([^\s'\"<>]+)@"
)


def _redact_url_userinfo(match: re.Match[str]) -> str:
    userinfo = match.group(2)
    # "user:pass@" is always a credential; a bare "token@host" (no path before the "@")
    # is treated as one too. An "@" that only appears inside a path/query is left alone.
    if ":" in userinfo or "/" not in userinfo:
        return f"{match.group(1)}[REDACTED_PROXY_CRED]@"
    return match.group(0)


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
        sanitized = _URL_USERINFO_PATTERN.sub(_redact_url_userinfo, sanitized)
        for pattern, replacement in _SENSITIVE_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized

    @classmethod
    def redact_dict(cls, data: dict[str, Any], explicit_secrets: list[str] | None = None) -> dict[str, Any]:
        """Deeply redact sensitive values in mappings, sequences, and nested structures."""
        return cls._redact_mapping(data, explicit_secrets, inherited=False)

    @classmethod
    def redact_value(cls, value: Any, explicit_secrets: list[str] | None = None) -> Any:
        """Redact arbitrary JSON-like values, including root lists and tuples."""
        return cls._redact_value(value, explicit_secrets, sensitive=False)

    _SENSITIVE_KEYS = frozenset(
        {
            "api_key",
            "admin_api_key",
            "cookie",
            "cookies",
            "password",
            "proxy_url",
            "authorization",
            "secret",
            "token",
            "access_token",
            "refresh_token",
            "id_token",
            "apikey",
            "api-key",
            "x-api-key",
            "x_api_key",
            "x-admin-key",
            "x-studio-admin-token",
            "studio_admin_token",
            "client_secret",
            "private_key",
            "encrypted_api_key",
            "secret_encryption_key",
            "proxy_password",
            "passwd",
            "set-cookie",
        }
    )

    @classmethod
    def _redact_mapping(
        cls, data: dict[str, Any], explicit_secrets: list[str] | None, inherited: bool
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in data.items():
            sensitive = inherited or str(key).lower() in cls._SENSITIVE_KEYS
            result[key] = cls._redact_value(value, explicit_secrets, sensitive)
        return result

    @classmethod
    def _redact_value(cls, value: Any, explicit_secrets: list[str] | None, sensitive: bool) -> Any:
        if isinstance(value, str):
            if sensitive:
                # Preserve a proxy URL’s diagnostic host, but never its credentials.
                return cls.redact_text(value, explicit_secrets) if "://" in value else "[REDACTED]"
            return cls.redact_text(value, explicit_secrets)
        if isinstance(value, dict):
            return cls._redact_mapping(value, explicit_secrets, sensitive)
        if isinstance(value, (list, tuple)):
            items = [cls._redact_value(item, explicit_secrets, sensitive) for item in value]
            return tuple(items) if isinstance(value, tuple) else items
        return value
