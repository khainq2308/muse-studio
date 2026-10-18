---
name: safe-ffmpeg
description: Rules and snippets for calling ffmpeg/ffprobe safely from Python in this repo - argv lists, async execution, timeouts, return-code and stderr handling, filtergraph escaping, output verification with ffprobe. Use whenever code spawns ffmpeg or ffprobe, builds a filter_complex, extracts frames, probes media, or verifies exported/downloaded video.
---

# Safe FFmpeg/FFprobe usage

## Hard rules
1. **argv list, never a shell string.** No `shell=True`, no f-string commands. Paths and values are separate list items.
2. **Timeout on every call** and handle `TimeoutExpired`/`asyncio.TimeoutError` by killing the process and returning a typed failure.
3. **Check the return code** and surface a redacted, truncated stderr tail (`SecretRedactor.redact_text(stderr[-2000:])`).
4. **Don't block the event loop.** In FastAPI/ARQ code use `asyncio.create_subprocess_exec` or `await asyncio.to_thread(subprocess.run, ...)`.
5. **Use `-nostdin -hide_banner -y`** (or `-n` when overwriting would be a bug) and write to a temp file in the same directory, then `os.replace` into the final path after verification.
6. **Verify output with ffprobe** before declaring success: video stream present, width/height > 0, duration within tolerance of the expected value, expected codec (`h264`, `aac` for exports), file size > 0. Measure frame counts (`-count_frames`/`nb_read_frames`), never assume.
7. **Hostile text in filters.** Any user text inside `drawtext`/`subtitles`/`textfile` must be escaped (`\`, `:`, `'`, `%`) or, better, passed through a file (`textfile=`/`.srt`) written by us. Never interpolate raw user strings into `-filter_complex`.
8. **Inputs are untrusted media.** Probe first; reject unexpected stream types; limit duration/resolution; pass `-protocol_whitelist file` when handling local files only.

## Snippets
```python
import asyncio, json
from muse_orchestrator.core.config import SecretRedactor

class MediaCommandError(RuntimeError): ...

async def run_ffmpeg(args: list[str], timeout: float = 300.0) -> str:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-nostdin", "-hide_banner", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill(); await proc.wait()
        raise MediaCommandError(f"ffmpeg timed out after {timeout}s")
    if proc.returncode != 0:
        tail = SecretRedactor.redact_text(err.decode("utf-8", "replace")[-2000:])
        raise MediaCommandError(f"ffmpeg failed ({proc.returncode}): {tail}")
    return err.decode("utf-8", "replace")

async def ffprobe_json(path: str, timeout: float = 30.0) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout)
    if proc.returncode != 0:
        raise MediaCommandError("ffprobe failed: " + err.decode("utf-8", "replace")[-500:])
    return json.loads(out)
```

## Testing
Unit-test command construction by asserting the argv list; mock `subprocess`/`create_subprocess_exec` (see `apps/media-engine/tests`). Add at least one real-binary integration test using `-f lavfi -i testsrc=...` generated into `tmp_path` (see `orchestrator-testing`).

## Existing code to reuse
`apps/media-engine/muse_media/probe.py` (`probe_video`, exact decoded-frame count) and `boundary_frames.py` (`extract_boundary_frames`). Extend those instead of writing new ad-hoc wrappers; `services/timeline_service.py` uses synchronous `subprocess.run` (the export runs off the event loop per docs/ARCHITECTURE.md) — keep it off the loop, and keep timeouts and verification when you edit it.
