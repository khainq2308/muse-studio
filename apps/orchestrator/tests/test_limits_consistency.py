"""ARCH-005: invariants between limits that live in different modules/config files.

These limits used to be coupled only by comments. Each assertion below names the
operational failure it prevents.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from muse_orchestrator.core.config import Settings, settings
from muse_orchestrator.services import asset_storage, backup_service, generation_queue, timeline_service
from muse_orchestrator.workers import queue_worker

REPO_ROOT = Path(__file__).resolve().parents[3]
NGINX_PROD_CONF = REPO_ROOT / "apps" / "studio-web" / "nginx.prod.conf"
# A multipart body carries boundaries and part headers on top of the file bytes.
MULTIPART_OVERHEAD_BYTES = 1024 * 1024

_NGINX_SIZE_RE = re.compile(r"^(\d+)([kKmMgG]?)$")
_NGINX_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


def _nginx_size_to_bytes(value: str) -> float:
    match = _NGINX_SIZE_RE.match(value.strip())
    assert match, f"Unparseable nginx size: {value!r}"
    number = int(match.group(1))
    if number == 0:
        return float("inf")  # nginx: 0 disables the body-size check
    return float(number * _NGINX_UNITS[match.group(2).lower()])


def _location_body_limit(conf: str, location: str) -> float:
    """client_max_body_size effective for an exact-match location (falls back to server level)."""
    block = re.search(
        r"location\s+=\s+" + re.escape(location) + r"\s*\{(?P<body>[^{}]*)\}", conf
    )
    assert block, f"nginx.prod.conf has no exact location for {location}"
    inner = re.search(r"client_max_body_size\s+(\S+);", block.group("body"))
    if inner:
        return _nginx_size_to_bytes(inner.group(1))
    outer = re.search(r"client_max_body_size\s+(\S+);", conf)
    assert outer, "nginx.prod.conf sets no client_max_body_size"
    return _nginx_size_to_bytes(outer.group(1))


def test_module_constants_derive_from_settings() -> None:
    assert queue_worker.EXPORT_JOB_TIMEOUT_SECONDS == settings.export_job_timeout_seconds
    assert timeline_service._FFMPEG_TIMEOUT_SECONDS == settings.export_ffmpeg_timeout_seconds
    assert timeline_service._FFPROBE_TIMEOUT_SECONDS == settings.export_ffprobe_timeout_seconds
    assert timeline_service.MAX_EXPORT_DURATION_SECONDS == settings.export_max_duration_seconds
    assert timeline_service.MAX_EXPORT_CLIPS == settings.export_max_clips
    assert asset_storage.MAX_UPLOAD_SIZE_BYTES == settings.max_upload_size_bytes
    assert timeline_service._EXPORT_PROBE_BUDGET_SECONDS == pytest.approx(
        (
            settings.export_job_timeout_seconds
            - settings.export_ffmpeg_timeout_seconds
            - settings.export_verify_budget_seconds
        )
        / 2
    )


def test_export_job_timeout_covers_render_and_output_verification() -> None:
    # Otherwise ARQ cancels the export while FFmpeg is still inside its own timeout or
    # while verify_output (two FFprobe calls) is checking the result.
    verify_budget = 2 * timeline_service._FFPROBE_TIMEOUT_SECONDS
    assert queue_worker.EXPORT_JOB_TIMEOUT_SECONDS > (
        timeline_service._FFMPEG_TIMEOUT_SECONDS + verify_budget
    )
    assert queue_worker.EXPORT_STALE_RUNNING_AFTER_SECONDS > queue_worker.EXPORT_JOB_TIMEOUT_SECONDS


def test_export_job_timeout_covers_worst_case_source_probing() -> None:
    # build_export_plan probes every source clip, but all probes share one overall deadline
    # (_EXPORT_PROBE_BUDGET_SECONDS), so the worst case is that budget, not
    # MAX_EXPORT_CLIPS x FFPROBE timeout. Otherwise ARQ cancels long timelines mid-plan.
    probe_budget = timeline_service._EXPORT_PROBE_BUDGET_SECONDS
    verify_budget = 2 * timeline_service._FFPROBE_TIMEOUT_SECONDS
    assert probe_budget > 0
    assert queue_worker.EXPORT_JOB_TIMEOUT_SECONDS > (
        timeline_service._FFMPEG_TIMEOUT_SECONDS + probe_budget + verify_budget
    )


def _probeable_clips(tmp_path: Path, count: int) -> list[timeline_service.ExportClip]:
    clips = []
    for index in range(count):
        source = tmp_path / f"clip{index}.mp4"
        source.write_bytes(b"not inspected: subprocess.run is faked")
        clips.append(timeline_service.ExportClip(path=str(source), effective_duration=1.0))
    return clips


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _fake_probe_run(clock: _FakeClock, timeouts: list[float], cost: float):
    probe_stdout = '{"streams": [{"codec_type": "video", "duration": "5.0"}]}'

    def run(cmd, **kwargs):
        timeouts.append(kwargs["timeout"])
        clock.now += min(cost, kwargs["timeout"])
        if cost >= kwargs["timeout"]:
            raise timeline_service.subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        class _Result:
            returncode = 0
            stdout = probe_stdout
            stderr = ""

        return _Result()

    return run


@pytest.fixture
def fake_probe_env(monkeypatch: pytest.MonkeyPatch):
    clock = _FakeClock()
    monkeypatch.setattr(timeline_service.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(timeline_service.shutil, "which", lambda *_a, **_k: "/usr/bin/ffprobe")
    return clock


def test_build_export_plan_probe_timeout_is_remaining_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_probe_env: _FakeClock
) -> None:
    timeouts: list[float] = []
    monkeypatch.setattr(timeline_service.subprocess, "run", _fake_probe_run(fake_probe_env, timeouts, cost=3.0))
    plan = timeline_service.TimelineValidationService.build_export_plan(
        _probeable_clips(tmp_path, 3), str(tmp_path / "out.mp4"), 24, "320x240", probe_budget_seconds=10.0
    )
    assert plan.expected_duration == pytest.approx(3.0)
    # Each probe gets min(per-probe timeout, remaining overall budget).
    assert timeouts == [pytest.approx(10.0), pytest.approx(7.0), pytest.approx(4.0)]


def test_build_export_plan_probe_budget_exhausted_raises_504(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_probe_env: _FakeClock
) -> None:
    timeouts: list[float] = []
    monkeypatch.setattr(timeline_service.subprocess, "run", _fake_probe_run(fake_probe_env, timeouts, cost=4.0))
    with pytest.raises(timeline_service.ExportError) as excinfo:
        timeline_service.TimelineValidationService.build_export_plan(
            _probeable_clips(tmp_path, 5), str(tmp_path / "out.mp4"), 24, "320x240", probe_budget_seconds=10.0
        )
    assert excinfo.value.status_code == 504
    assert "budget" in excinfo.value.message
    # Third probe was cut short by the remaining 2 s; no fourth probe was started.
    assert len(timeouts) == 3


def test_build_export_plan_default_budget_bounds_total_probe_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_probe_env: _FakeClock
) -> None:
    # Every probe hangs until its timeout: total probe time must stay within the module budget.
    timeouts: list[float] = []
    monkeypatch.setattr(
        timeline_service.subprocess, "run", _fake_probe_run(fake_probe_env, timeouts, cost=float("inf"))
    )
    started = fake_probe_env.now
    with pytest.raises(timeline_service.ExportError) as excinfo:
        timeline_service.TimelineValidationService.build_export_plan(
            _probeable_clips(tmp_path, 20), str(tmp_path / "out.mp4"), 24, "320x240"
        )
    assert excinfo.value.status_code == 504
    assert fake_probe_env.now - started <= timeline_service._EXPORT_PROBE_BUDGET_SECONDS + 1e-6


def test_nginx_upload_location_accepts_max_upload() -> None:
    conf = NGINX_PROD_CONF.read_text(encoding="utf-8")
    limit = _location_body_limit(conf, "/api/v1/assets/upload")
    assert limit >= asset_storage.MAX_UPLOAD_SIZE_BYTES + MULTIPART_OVERHEAD_BYTES


def test_nginx_restore_location_accepts_max_backup() -> None:
    conf = NGINX_PROD_CONF.read_text(encoding="utf-8")
    limit = _location_body_limit(conf, "/api/v1/backups/restore")
    assert limit >= backup_service.BACKUP_MAX_BYTES + MULTIPART_OVERHEAD_BYTES


def test_generation_step_job_timeout_covers_max_lease() -> None:
    # A step job that ARQ kills before its lease expires leaves the lease held with no worker.
    assert generation_queue.GENERATION_STEP_JOB_TIMEOUT_SECONDS >= generation_queue.GENERATION_LEASE_MAX_SECONDS
    assert queue_worker.WorkerSettings.job_timeout >= generation_queue.GENERATION_LEASE_MAX_SECONDS


def test_nginx_size_parser() -> None:
    assert _nginx_size_to_bytes("160m") == 160 * 1024**2
    assert _nginx_size_to_bytes("1G") == 1024**3
    assert _nginx_size_to_bytes("512k") == 512 * 1024
    assert _nginx_size_to_bytes("2048") == 2048
    assert _nginx_size_to_bytes("0") == float("inf")


def test_settings_env_overrides_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_SIZE_BYTES", str(10 * 1024 * 1024))
    monkeypatch.setenv("EXPORT_FFMPEG_TIMEOUT_SECONDS", "600")
    monkeypatch.setenv("EXPORT_FFPROBE_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("EXPORT_JOB_TIMEOUT_SECONDS", "700")
    monkeypatch.setenv("EXPORT_MAX_CLIPS", "100")
    monkeypatch.setenv("EXPORT_MAX_DURATION_SECONDS", "3600")
    configured = Settings()
    assert configured.max_upload_size_bytes == 10 * 1024 * 1024
    assert configured.export_ffmpeg_timeout_seconds == 600
    assert configured.export_ffprobe_timeout_seconds == 30
    assert configured.export_job_timeout_seconds == 700
    assert configured.export_max_clips == 100
    assert configured.export_max_duration_seconds == 3600


def test_settings_reject_export_job_timeout_not_covering_render(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPORT_FFMPEG_TIMEOUT_SECONDS", "1800")
    monkeypatch.setenv("EXPORT_FFPROBE_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("EXPORT_JOB_TIMEOUT_SECONDS", "1900")  # < 1800 + 2 x 60
    with pytest.raises(ValidationError, match="EXPORT_JOB_TIMEOUT_SECONDS"):
        Settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAX_UPLOAD_SIZE_BYTES", "0"),
        ("EXPORT_MAX_CLIPS", "0"),
        ("EXPORT_FFPROBE_TIMEOUT_SECONDS", "-1"),
    ],
)
def test_settings_reject_out_of_range_limits(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings()
