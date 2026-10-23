from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEVELOPMENT_ENCRYPTION_KEY = "dev-only-32-byte-secret-key-change-me!!"
_DEV_ENVIRONMENTS = {"development", "dev", "local", "test", "testing"}
MIN_PRODUCTION_ADMIN_TOKEN_LENGTH = 32


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

    # ARCH-005: coupled media/export limits. Module constants (asset_storage,
    # timeline_service, queue_worker) derive from these; invariants between them are
    # validated below and in tests/test_limits_consistency.py (which also checks the
    # nginx client_max_body_size for the upload location).
    # Raising MAX_UPLOAD_SIZE_BYTES also requires raising nginx client_max_body_size.
    max_upload_size_bytes: int = Field(default=150 * 1024 * 1024, ge=1024, le=4 * 1024**3)
    # One FFmpeg render subprocess (seconds).
    export_ffmpeg_timeout_seconds: float = Field(default=1800.0, gt=0, le=24 * 3600)
    # One FFprobe call: per source clip while planning, and twice to verify the output.
    export_ffprobe_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    export_max_duration_seconds: float = Field(default=4 * 3600.0, gt=0, le=24 * 3600)
    export_max_clips: int = Field(default=500, ge=1, le=10_000)
    # ARQ job timeout for one export; must exceed render + output verification.
    export_job_timeout_seconds: int = Field(default=2100, ge=60, le=48 * 3600)

    # DNS-rebinding guard: while running with insecure development defaults (no admin
    # token), only these Host header names are accepted. Comma-separated.
    dev_allowed_hosts: str = Field(
        default="localhost,127.0.0.1,::1,testserver,orchestrator-api",
        validation_alias="DEV_ALLOWED_HOSTS",
    )

    # CSRF guard for the unauthenticated dev server: state-changing requests whose Origin
    # is not one of these (or which a browser marks Sec-Fetch-Site: cross-site) get 403.
    dev_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Provider base URLs must use https outside development unless explicitly allowed
    # (e.g. a gateway on a private network segment).
    allow_insecure_provider_http: bool = False

    # Process logging (stdout). Format "keyvalue" or "json"; messages are redacted.
    log_level: str = "INFO"
    log_format: Literal["keyvalue", "json"] = "keyvalue"

    @property
    def is_development(self) -> bool:
        return self.environment.strip().lower() in _DEV_ENVIRONMENTS

    @property
    def dev_allowed_origin_set(self) -> frozenset[str]:
        return frozenset(
            item.strip().lower().rstrip("/") for item in self.dev_allowed_origins.split(",") if item.strip()
        )

    @property
    def dev_allowed_host_set(self) -> frozenset[str]:
        return frozenset(
            item.strip().lower().strip("[]") for item in self.dev_allowed_hosts.split(",") if item.strip()
        )

    @property
    def export_verify_budget_seconds(self) -> float:
        """Worst-case time verify_output spends probing the rendered file (two FFprobe calls)."""
        return 2 * self.export_ffprobe_timeout_seconds

    @model_validator(mode="after")
    def require_consistent_export_limits(self) -> "Settings":
        minimum = self.export_ffmpeg_timeout_seconds + self.export_verify_budget_seconds
        if self.export_job_timeout_seconds <= minimum:
            raise ValueError(
                "EXPORT_JOB_TIMEOUT_SECONDS must exceed EXPORT_FFMPEG_TIMEOUT_SECONDS plus the "
                f"output verification budget (2 x EXPORT_FFPROBE_TIMEOUT_SECONDS = {minimum:.0f}s); "
                "otherwise ARQ cancels exports while FFmpeg/FFprobe may still legitimately run."
            )
        return self

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
            if len(self.studio_admin_token.strip()) < MIN_PRODUCTION_ADMIN_TOKEN_LENGTH:
                raise ValueError(
                    f"STUDIO_ADMIN_TOKEN must be at least {MIN_PRODUCTION_ADMIN_TOKEN_LENGTH} "
                    "characters outside development."
                )
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

# Redaction runs on provider error bodies, tracebacks and log payloads. Bound the input
# first so no pattern can be driven into super-linear work by a huge hostile string.
MAX_REDACTION_INPUT_CHARS = 16_384
_TRUNCATION_BACKOFF_CHARS = 2_048

_SECRET_KEY_NAMES = (
    r"x-admin-key|x-api-key|x-goog-api-key|x-studio-admin-token|api[-_]?key|access[-_]?token"
    r"|refresh[-_]?token|id[-_]?token|auth[-_]?token|client[-_]?secret|private[-_]?key"
    r"|secret[-_]?key|password|passwd|secret|token"
)

# Every pattern below is anchored on a literal keyword and uses only non-overlapping
# quantifiers, so each match attempt is linear in the length of the scanned token.
_SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization / Proxy-Authorization headers, plain or as quoted JSON/dict keys.
    (
        re.compile(
            # Any scheme word (Bearer, Basic, ApiKey, ...) or none: the credential is redacted.
            r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?(?:[A-Za-z][A-Za-z0-9_-]*\s+)?)[^\s,'\"}]+"
        ),
        r"\1[REDACTED]",
    ),
    # A bare "Bearer <token>" outside a header line. Require a token-like value so prose
    # such as "bearer of bad news" is not rewritten.
    (
        re.compile(r"(?i)(\bbearer\s+)(?=[A-Za-z0-9\-._~+/]{8})[A-Za-z0-9\-._~+/]+=*"),
        r"\1[REDACTED]",
    ),
    # Credential-bearing query parameters (?token=, &key=, ?sig= ...).
    (
        re.compile(
            r"(?i)([?&](?:token|access[-_]?token|api[-_]?key|key|sig|signature|x-amz-signature"
            r"|auth|password|secret)=)[^&\s#'\"]+"
        ),
        r"\1[REDACTED]",
    ),
    # key: value / key=value, including quoted keys such as "api_key": "x" or 'password': 'x'.
    (
        re.compile(
            r"(?i)([\"']?\b(?:" + _SECRET_KEY_NAMES + r")[\"']?\s*[:=]\s*[\"']?)[^\s,'\"}&]+"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)((?:set-)?cookie\s*[:=]\s*['\"]?)[^\r\n\"']+"), r"\1[REDACTED_COOKIE]"),
]

# Proxy/provider URLs with userinfo. The scheme is located with a fixed alternation and
# the rest of the whitespace-delimited token is parsed with str methods, so the work is
# linear even for inputs such as "http://" * 300_000.
_URL_SCHEME_PATTERN = re.compile(r"(?i)\b(?:https?|socks4a?|socks5h?|ftp)://")
_URL_TOKEN_END_PATTERN = re.compile(r"[\s<>]")


def _redact_url_userinfo(text: str) -> str:
    """Hide URL userinfo, matching up to the LAST "@" of the token.

    Passwords containing "/" or "@" are therefore still fully hidden. "user:pass@" is
    always a credential; a bare "token@host" (no path before the "@") is treated as one
    too. An "@" that only appears inside a path/query is left alone.
    """
    parts: list[str] = []
    cursor = 0
    while True:
        scheme = _URL_SCHEME_PATTERN.search(text, cursor)
        if scheme is None:
            break
        authority_start = scheme.end()
        token_end_match = _URL_TOKEN_END_PATTERN.search(text, authority_start)
        token_end = token_end_match.start() if token_end_match else len(text)
        token = text[authority_start:token_end]
        parts.append(text[cursor:authority_start])
        at = token.rfind("@")
        userinfo = token[:at] if at > 0 else ""
        if userinfo and (":" in userinfo or "/" not in userinfo):
            parts.append("[REDACTED_PROXY_CRED]" + token[at:])
        else:
            parts.append(token)
        # Continue AFTER the token: nested schemes inside it were already covered.
        cursor = token_end
    parts.append(text[cursor:])
    return "".join(parts)


def _bound_redaction_input(text: str) -> str:
    if len(text) <= MAX_REDACTION_INPUT_CHARS:
        return text
    cut = text[:MAX_REDACTION_INPUT_CHARS]
    # Back off to a token boundary so half of a credential is never left un-redactable.
    boundary = max(cut.rfind(" "), cut.rfind("\n"), cut.rfind("\t"))
    if boundary >= MAX_REDACTION_INPUT_CHARS - _TRUNCATION_BACKOFF_CHARS:
        cut = cut[:boundary]
    else:
        cut = cut[: MAX_REDACTION_INPUT_CHARS - _TRUNCATION_BACKOFF_CHARS]
    return f"{cut} ...[TRUNCATED {len(text) - len(cut)} chars]"


_NON_SECRET_KEY_NAMES = frozenset(
    {
        # Token counts of LLM usage/limits, not credentials.
        "maxtokens",
        "maxnewtokens",
        "tokencount",
        "totaltokens",
        "prompttokens",
        "completiontokens",
        "inputtokens",
        "outputtokens",
        "cachekey",
        "idempotencykey",
        "sortkey",
        "primarykey",
        "foreignkey",
        "partitionkey",
        "objectkey",
    }
)
_SENSITIVE_KEY_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "cookie",
    "apikey",
    "privatekey",
    "credential",
    "proxyurl",
    "signature",
)


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
        sanitized = _bound_redaction_input(sanitized)
        sanitized = _redact_url_userinfo(sanitized)
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
            "key",
        }
    )

    @classmethod
    def is_sensitive_key(cls, key: object) -> bool:
        """Match secret-looking mapping keys (camelCase, kebab-case and snake_case).

        Display-only derivatives such as ``api_key_masked`` and well-known non-secret
        identifiers such as ``cache_key`` / ``idempotency_key`` are not treated as secrets.
        """
        lowered = str(key).strip().lower()
        if lowered in cls._SENSITIVE_KEYS:
            return True
        normalized = re.sub(r"[-_\s.]", "", lowered)
        if normalized.endswith("masked") or normalized in _NON_SECRET_KEY_NAMES:
            return False
        if any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS):
            return True
        return normalized.endswith("key") or normalized.endswith("keys")

    @classmethod
    def _redact_mapping(
        cls, data: dict[str, Any], explicit_secrets: list[str] | None, inherited: bool
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in data.items():
            sensitive = inherited or cls.is_sensitive_key(key)
            result[key] = cls._redact_value(value, explicit_secrets, sensitive, key=key)
        return result

    @classmethod
    def _redact_value(
        cls, value: Any, explicit_secrets: list[str] | None, sensitive: bool, key: object = None
    ) -> Any:
        if isinstance(value, str):
            if sensitive:
                if cls._is_proxy_key(key) and "://" in value:
                    # Preserve a proxy URL's diagnostic host, but never its credentials.
                    return cls.redact_text(value, explicit_secrets)
                return "[REDACTED]"
            return cls.redact_text(value, explicit_secrets)
        if isinstance(value, dict):
            return cls._redact_mapping(value, explicit_secrets, sensitive)
        if isinstance(value, (list, tuple)):
            items = [cls._redact_value(item, explicit_secrets, sensitive, key=key) for item in value]
            return tuple(items) if isinstance(value, tuple) else items
        if sensitive and value is not None:
            # Numbers, bytes, booleans ... under a secret key are secrets too.
            return "[REDACTED]"
        return value

    @staticmethod
    def _is_proxy_key(key: object) -> bool:
        return "proxy" in str(key or "").lower() and "auth" not in str(key or "").lower()
