from __future__ import annotations

import re
from typing import Any
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "MuseStudio Orchestrator API"
    environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./musestudio_dev.db"
    redis_url: str = "redis://localhost:6379/0"
    media_root: str = "./storage/media"
    secret_encryption_key: str = "dev-only-32-byte-secret-key-change-me!!"
    studio_admin_token: str | None = None


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
        for k, v in data.items():
            if k.lower() in sensitive_keys and isinstance(v, str):
                result[k] = cls.redact_text(v, explicit_secrets) if "://" in v else "[REDACTED]"
            elif isinstance(v, dict):
                result[k] = cls.redact_dict(v, explicit_secrets)
            elif isinstance(v, str):
                result[k] = cls.redact_text(v, explicit_secrets)
            else:
                result[k] = v
        return result
