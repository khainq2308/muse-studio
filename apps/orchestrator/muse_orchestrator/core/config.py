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

# Every pattern below is anchored on a literal keyword and uses only non-overlapping
# quantifiers, so each match attempt is linear in the length of the scanned token.
_SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization / Proxy-Authorization headers, plain or as quoted JSON/dict keys.
    (
        re.compile(
            # The whole header value up to the end of the line or field is the credential,
            # whatever the scheme (Bearer, ApiKey, none, ...): never guess where it ends.
            # Quotes may be escaped (JSON embedded in a JSON string: \"authorization\": \"x\").
            r"(?i)(\bauthorization(?:\\?[\"'])?[ \t]*[:=][ \t]*(?:\\?[\"'])?)(?:[^\r\n\"',}\\]|\\(?![\"']))+"
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
    # Cookie headers carry name=value pairs; "Fortune cookie: yes" is not a header.
    (
        re.compile(r"(?i)(\b(?:set-)?cookie\s*[:=]\s*['\"]?)(?=[^\r\n\"'=]{0,256}=)[^\r\n\"']+"),
        r"\1[REDACTED_COOKIE]",
    ),
]

# Any key: value / key=value pair in free text. The key is judged by the same rule as
# mapping keys (SecretRedactor.is_sensitive_key), so text and structured redaction agree
# on aliases such as passphrase, pwd or secret_pin. Keys start with a letter and are at most
# 64 characters, so a failed match backtracks over at most 64 characters (linear overall).
_KEY_VALUE_PATTERN = re.compile(
    # The key may be quoted, also with escaped quotes (JSON embedded in a JSON string).
    # At most one line break between separator and value ("password:\n  hunter2").
    r"(\\?[\"']?)\b([A-Za-z][A-Za-z0-9_.-]{0,63})\1[ \t]*([:=])[ \t]*(?:\r?\n[ \t]*)?"
    # Value: an escaped-quote string (\"...\"), a quoted string (backslash escapes allowed),
    # a bare token (stops at "=" and ":" so a nested "key=value" starts a new match), or an
    # unterminated quote (e.g. cut by the input bound), redacted up to the end of the line.
    r"(\\\"(?:[^\"\\\r\n]|\\[^\"\r\n])*\\\"|\"(?:[^\"\\\r\n]|\\.)*\""
    # A bare value may start with "=" or ":" ("password==abc", "token=:abc").
    r"|'(?:[^'\\\r\n]|\\.)*'|[^\s,'\"}&;][^\s,'\"}&;=:]*|\\?[\"'][^\r\n]*)"
)
_REDACTION_MARKERS = frozenset({"[REDACTED]", "[REDACTED_COOKIE]", "[REDACTED_PROXY_CRED]"})
# End of an unquoted value under a sensitive key: the next field delimiter. An apostrophe
# does not end it ("password=it's my secret" is hidden whole); a double quote does, so
# a value embedded in a JSON string keeps the closing quote.
_FIELD_END_PATTERN = re.compile(r"[,;&}\"\r\n]")

# Proxy/provider URLs with userinfo. The scheme is located with a fixed alternation and
# the rest of the whitespace-delimited token is parsed with str methods, so the work is
# linear even for inputs such as "http://" * 300_000.
_URL_SCHEME_PATTERN = re.compile(r"(?i)\b(?:https?|socks4a?|socks5h?|ftp)://")
_URL_TOKEN_END_PATTERN = re.compile(r"[\s<>]")
# A proxy URL kept for diagnostics ends at the first field delimiter too, so the next
# field of compact JSON or a comma list is still scanned.
_PROXY_URL_END_PATTERN = re.compile(r"[\s<>\"',;}&]")


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
    "passphrase",
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
        return SecretRedactor._redact_key_values(sanitized)

    @staticmethod
    def _redact_key_values(text: str) -> str:
        """Apply _KEY_VALUE_PATTERN left to right; a sensitive bare value may extend the match.

        Linear: every search resumes at or after the end of the previous match, and the
        extension of a sensitive bare value is one scan for the next field delimiter over
        text that is then consumed (never scanned again).
        """
        parts: list[str] = []
        cursor = 0
        while (match := _KEY_VALUE_PATTERN.search(text, cursor)) is not None:
            replacement, end = SecretRedactor._redact_key_value_match(match, text)
            parts.append(text[cursor : match.start()])
            parts.append(replacement)
            cursor = end
        parts.append(text[cursor:])
        return "".join(parts)

    @staticmethod
    def _redact_key_value_match(match: re.Match[str], text: str) -> tuple[str, int]:
        """Return the replacement for ``match`` and the index where scanning resumes."""
        key, value = match.group(2), match.group(4)
        whole = match.group(0)
        quote = value[:2] if value[:2] in ("\\\"", "\\'") else value[:1] if value[:1] in ("\"", "'") else ""
        if not SecretRedactor.is_sensitive_key(key):
            # Keep the pair but scan inside its value: "Auth failed: passphrase=x" or
            # {"error": "bad pwd=x"} hide a sensitive pair in an ordinary value.
            prefix = whole[: match.start(4) - match.start(0)]
            if not quote:
                # A bare value cannot contain field delimiters: resume at its start.
                return prefix, match.start(4)
            # A quoted value is scanned on its own, so a pair inside it can never reach
            # past the closing quote into the next field. Each nesting level uses another
            # (or escaped) quote, so the recursion depth is small and the work linear.
            closing = value[-len(quote):] if len(value) >= 2 * len(quote) and value.endswith(quote) else ""
            inner = value[len(quote) : len(value) - len(closing)]
            return prefix + quote + SecretRedactor._redact_key_values(inner) + closing, match.end()
        if SecretRedactor._is_proxy_key(key) and _URL_SCHEME_PATTERN.match(text, match.start(4) + len(quote)):
            # A proxy URL keeps its host for diagnostics; _redact_url_userinfo already
            # stripped its credentials. Skip the whole URL token.
            url_end = _PROXY_URL_END_PATTERN.search(text, match.start(4) + len(quote))
            end = url_end.start() if url_end is not None else len(text)
            return text[match.start() : end], end
        prefix = whole[: match.start(4) - match.start(0)]
        if not quote:
            # A bare value runs to the next field delimiter, spaces included, so
            # "password=my secret value, user=bob" never leaks "secret value".
            field_end = _FIELD_END_PATTERN.search(text, match.end())
            end = field_end.start() if field_end is not None else len(text)
            if value in _REDACTION_MARKERS:
                return whole, end
            return prefix + "[REDACTED]", end
        if value.strip("\"'\\") in _REDACTION_MARKERS:
            # Fully redacted by an earlier pattern. A partial "[REDACTED] rest" (an earlier
            # pattern stopped at a delimiter) is redacted again as a whole.
            return whole, match.end()
        return prefix + f"{quote}[REDACTED]{quote}", match.end()

    @staticmethod
    def redact_credentials(text: str, explicit_secrets: list[str] | None = None) -> str:
        """Remove credentials by value shape only (known secrets, URL userinfo, Authorization,
        bearer tokens, credential query parameters, cookies), not by key names.

        For text that must stay usable as-is, such as the options of a provider question
        that are validated and sent back: key-name heuristics would turn "Key: C major"
        into an option the provider no longer recognises.
        """
        if not text:
            return text
        sanitized = text
        for secret in explicit_secrets or []:
            if secret and len(secret) >= 4:
                sanitized = sanitized.replace(secret, "[REDACTED]")
        sanitized = _redact_url_userinfo(_bound_redaction_input(sanitized))
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
            "passphrase",
            "pwd",
            "proxy_pass",
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
        lowered = cls._key_text(key).strip().lower()
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
        if isinstance(value, (list, tuple, set, frozenset)):
            items = [cls._redact_value(item, explicit_secrets, sensitive, key=key) for item in value]
            return tuple(items) if isinstance(value, tuple) else items
        if isinstance(value, (bytes, bytearray)) and not sensitive:
            return cls.redact_text(bytes(value).decode("utf-8", errors="replace"), explicit_secrets)
        if sensitive and value is not None:
            # Numbers, bytes, booleans ... under a secret key are secrets too.
            return "[REDACTED]"
        return value

    # Keys whose value is a proxy URL: its host is kept for diagnostics (credentials stripped).
    _PROXY_URL_KEYS = frozenset(
        {"proxy", "proxies", "proxyurl", "proxyuri", "httpproxy", "httpsproxy", "allproxy"}
    )

    @classmethod
    def _is_proxy_key(cls, key: object) -> bool:
        return re.sub(r"[-_\s.]", "", cls._key_text(key).lower()) in cls._PROXY_URL_KEYS

    @staticmethod
    def _key_text(key: object) -> str:
        if isinstance(key, (bytes, bytearray)):
            return bytes(key).decode("utf-8", errors="replace")
        return str(key)
