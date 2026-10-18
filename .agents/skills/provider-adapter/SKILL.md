---
name: provider-adapter
description: Guide for adding or changing video/image generation providers and the muse2api adapter - the VideoProvider protocol, ProviderFailure error taxonomy, capability probing, secure result download, per-account keys/proxies, and the capability matrix. Use whenever work touches providers/, muse2api endpoints, polling, retries/backoff, connection accounts, or a new generation backend.
---

# Provider adapters

## Files
- `providers/base.py` — `VideoProvider` protocol, request/response dataclasses, `ErrorCategory`, `ProviderFailure(category, message, http_status, retryable)`, `classify_http_error`.
- `providers/muse2api_adapter.py` — real HTTP adapter (httpx); `download_result` has the security-critical origin/redirect logic.
- `providers/mock_provider.py` — deterministic test double.
- `services/account_pool.py` — least-recently-used account selection, `build_provider_for_account` (per-account API key + proxy).
- `services/generation_queue.py` — consumes providers; backoff/polling/leases.
- `docs/MUSE2API_CAPABILITY_MATRIX.md` — what is actually verified.

## Adding or changing a provider call
1. Implement against the `VideoProvider` protocol; keep one method per upstream capability and return typed results, not raw dicts.
2. **Classify every failure** into an `ErrorCategory` with the right `retryable`: network/5xx/429 → retryable; auth/quota/invalid request → not. Use `classify_http_error`; add a test for each mapping you touch.
3. Timeouts on every request (connect and read), bounded retries are owned by the queue, not the adapter.
4. Credentials: obtained from the decrypted connection/account at call time, sent only in the `Authorization`/key header to the provider's configured origin. Never log or embed them in exceptions (wrap messages with `SecretRedactor`). Proxy URLs contain credentials — redact.
5. **Result download rules (do not weaken):** key only to the provider origin; other hosts must be `https` and never receive the key; redirects followed manually (≤3) and re-validated each hop; reject non-http(s), scheme-relative URLs, traversal in media ids; verify the file (non-empty, magic bytes, ffprobe) before the task may succeed.
6. Register the provider type: connection schema pattern in `api/connections.py` (`^(muse2api|mock)$`), builders in `application/services.py`, `services/account_pool.py`, `services/generation_queue.py::build_provider`. Prefer replacing the repeated `if provider_type == "mock"` branches with one factory when you touch them.
7. Tests with `httpx.MockTransport` — success, 401, 429 with retry-after, 5xx, timeout, malformed JSON, malicious result URL/redirect chain. No real network.

## Capability matrix discipline
Statuses: `verified` (observed against a real instance, with date), `unverified`, `mock-only`, `unsupported`. A `verified` row requires a committed `docs/evidence/` artifact containing ISO probe date, instance/version, request/response outcome and limitations; `scripts/check_docs_sync.py` checks for the date and that file path. Capture probe output with `ops/m0_probe_muse2api.py`, then curate it into the evidence artifact. Never promote from reading code, dependency presence or mock tests. UI must show `unsupported` honestly (no quota lookup, no per-account proxy on a single instance).

## Smells to fix when seen
Hard-coded model ids (`muse-video-mock-v1` as default in API schemas), provider branching scattered across modules, adapter methods returning untyped dicts, retries inside adapters.
