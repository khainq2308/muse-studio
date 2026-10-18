---
name: orchestrator-testing
description: How to write reliable pytest tests for the MuseStudio orchestrator - async API client fixture, in-memory SQLite, temp media root, mock provider, httpx.MockTransport, FFmpeg-backed export tests, security negative tests, and consolidating the duplicated client fixture into conftest.py. Use whenever tests are added, fixed, flaky, or missing for backend code, even if the user just says "add tests" or "why does this test fail".
---

# Testing the orchestrator

## Environment facts
- `apps/orchestrator/pyproject.toml`: `asyncio_mode = "auto"` — write `async def test_...` without markers.
- Tests set env vars **before importing the app**:
  ```python
  os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
  os.environ["MEDIA_ROOT"] = tempfile.mkdtemp(prefix="muse_test_")
  from muse_orchestrator.main import app
  ```
  The app module builds the engine/settings at import time; setting env vars afterwards has no effect.
- Per-test DB: in-memory SQLite + `StaticPool` + `app.dependency_overrides[get_db]` (see `references/conftest-template.md`).
- Auth: `settings` is read at import. To test the token path, patch the object: `monkeypatch.setattr(settings, "studio_admin_token", "t0ken")` and send `X-Studio-Admin-Token`.
- SQLite ignores `with_for_update()`: it cannot prove worker mutual exclusion.

## Consolidate fixtures
There is no `tests/conftest.py`; the `client` fixture is duplicated across files. Create it from `references/conftest-template.md` and delete the copies when you touch those files. Keep the env-var lines at the very top of conftest, before any `muse_orchestrator` import.

## Patterns
- **API test**: arrange via API calls (create project → upload asset) rather than inserting rows, so tests cover real flows.
- **Provider**: use `MockVideoProvider` (connection `provider_type="mock"`), or `httpx.MockTransport` to simulate muse2api responses, redirects, 401/429/5xx, and malicious result URLs.
- **Queue/backoff**: call `GenerationQueueService.step_task` directly; assert state transitions and `next_poll_delay_seconds`; patch `random.uniform` for determinism.
- **Export/FFmpeg**: generate input with `ffmpeg -f lavfi -i testsrc=duration=1:size=320x240:rate=24 -c:v libx264 -pix_fmt yuv420p out.mp4` into `tmp_path`. Never commit binary media fixtures. Gate with `REQUIRE_FFMPEG`: `pytest.skip` locally if missing and the env var is not `1`; **fail** when it is `1`.
  ```python
  def require_ffmpeg():
      if shutil.which("ffmpeg") and shutil.which("ffprobe"):
          return
      if os.environ.get("REQUIRE_FFMPEG") == "1":
          pytest.fail("ffmpeg/ffprobe required (REQUIRE_FFMPEG=1)")
      pytest.skip("ffmpeg not installed")
  ```
- **Security negatives** (always for: redaction, download origin/redirects, asset upload, backup restore, admin token): craft attacker-shaped input — `../../etc/passwd` ids, scheme-relative URLs, a 4th redirect, wrong magic bytes, zip with `../evil` member, secrets inside error text — and assert rejection **and** that no secret appears in the response.
- **Regression**: every bug fix ships a test that fails without the fix.

## Running
```bash
scripts/test.sh orchestrator -k "queue and not export" -x     # focused
scripts/test.sh orchestrator                                  # full, REQUIRE_FFMPEG=1
```
Report actual counts. If dependencies are missing run `scripts/doctor.sh` and say what is missing rather than claiming tests pass.

## Anti-patterns
`time.sleep`, real network, assertions on log text, sharing DB state across tests, weakening an assertion to get green, skipping a failing test without a reason string.
