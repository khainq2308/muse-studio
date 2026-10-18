---
name: safe-ffmpeg
description: Safe FFmpeg/FFprobe invocation, timeout handling, redacted errors and media output verification.
---

# Safe FFmpeg/FFprobe usage

## Hard rules
1. Use argv lists; never shell=True or build a shell command string.
2. Every process needs a timeout. On timeout, kill the child, drain pipes and reap it.
3. Check exit codes. Redact and bound stderr before including it in errors.
4. Never block the async event loop; use asyncio subprocess APIs or asyncio.to_thread.
5. Write to a temporary file and atomically replace the destination only after verifying output.
6. Verify expected streams, dimensions, duration tolerance, codec and non-empty size with FFprobe. Measure frame counts.
7. Treat user-provided filter text as hostile; escape it or use controlled text/subtitle files.
8. Probe untrusted input, limit size/duration/resolution and restrict protocols where appropriate.

## Reference snippet
The helper below is illustrative; adapt it to the repository's typed errors and process policy.

    async def communicate_with_timeout(proc, timeout, label):
        try:
            return await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            try:
                await asyncio.wait_for(proc.communicate(), 5.0)
            except asyncio.TimeoutError:
                await proc.wait()
            raise MediaCommandError(f"{label} timed out after {timeout}s") from exc

For non-zero return codes, redact stderr through SecretRedactor before exposing it. Apply timeout handling to both FFmpeg and FFprobe, including process creation and cleanup failures.

## Tests
Unit-test argv construction and timeout behavior with mocked subprocesses. Add bounded real-FFmpeg integration tests using a generated lavfi clip. Verify output with FFprobe; never commit binary fixtures.

Reuse apps/media-engine/muse_media/probe.py and boundary_frames.py rather than creating ad-hoc wrappers.
