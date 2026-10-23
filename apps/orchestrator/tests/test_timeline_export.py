from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from muse_orchestrator.services import timeline_service
from muse_orchestrator.services.timeline_service import (
    ExportClip,
    ExportError,
    TimelineValidationService,
)

_HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.fixture
def real_ffmpeg() -> None:
    """Real-render tests skip on a dev machine without FFmpeg, but must never skip in CI.

    CI installs FFmpeg and sets REQUIRE_FFMPEG=1; if the binaries are missing there, a silently
    skipped render test would hide a broken exporter, so the test fails instead.
    """
    if _HAVE_FFMPEG:
        return
    if os.environ.get("REQUIRE_FFMPEG") == "1":
        pytest.fail("REQUIRE_FFMPEG=1 but ffmpeg/ffprobe are not installed; CI must install them.")
    pytest.skip("ffmpeg and ffprobe are not installed")


needs_ffmpeg = pytest.mark.usefixtures("real_ffmpeg")


def _clip(
    path: str = "/m/a.mp4",
    duration: float = 3.0,
    trim: float = 0.0,
    kind: str = "hard_cut",
    transition: float = 0.0,
    start: float | None = None,
) -> ExportClip:
    return ExportClip(
        path=path,
        effective_duration=duration,
        trim_start=trim,
        transition_type=kind,
        transition_duration=transition,
        start_time=start,
    )


def _plan(clips: list[ExportClip], resolution: str = "1080x1920", fps: int = 30):
    return TimelineValidationService.build_export_plan(clips, "/o/out.mp4", fps, resolution)


def _graph(plan) -> str:
    return plan.args[plan.args.index("-filter_complex") + 1]


# --- plan building: pure, no ffmpeg needed -------------------------------------------------


def test_plan_hard_cut_trims_and_concatenates_the_real_clips() -> None:
    plan = _plan([_clip("/m/a.mp4", 3.0, trim=0.5), _clip("/m/b.mp4", 4.0, start=3.0)])

    assert plan.args[0] == "ffmpeg"
    assert plan.args[-1] == "/o/out.mp4"
    assert plan.expected_duration == 7.0
    assert "/m/a.mp4" in plan.args and "/m/b.mp4" in plan.args
    graph = _graph(plan)
    assert "trim=start=0.500:end=3.500" in graph
    assert "concat=n=2:v=1:a=0" in graph
    assert "xfade" not in graph
    assert "color=c=black" not in " ".join(plan.args)  # no placeholder black video
    assert plan.args.count("-i") == 3  # two clips + the silent audio source


def test_plan_cross_dissolve_uses_xfade_with_the_right_offset() -> None:
    plan = _plan([_clip("/m/a.mp4", 3.0), _clip("/m/b.mp4", 5.0, kind="cross_dissolve", transition=0.5, start=2.5)])

    assert plan.expected_duration == 7.5
    assert "xfade=transition=fade:duration=0.500:offset=2.500" in _graph(plan)


def test_plan_fills_timeline_gaps_with_black() -> None:
    plan = _plan([_clip("/m/a.mp4", 3.0), _clip("/m/b.mp4", 4.0, start=5.0)])

    assert plan.expected_duration == 9.0  # 3.0 + 2.0 gap + 4.0
    assert "color=c=black:s=1080x1920:r=30" in plan.args
    assert plan.args.count("-i") == 4  # two clips + gap + silent audio


def test_plan_rejects_a_hard_cut_that_overlaps_the_previous_clip() -> None:
    # Timeline ends at 6s; concatenating both whole clips would silently produce 8s.
    with pytest.raises(ExportError) as excinfo:
        _plan([_clip("/m/a.mp4", 4.0, start=0.0), _clip("/m/b.mp4", 4.0, start=2.0)])

    assert excinfo.value.status_code == 422
    assert "Clip #2" in excinfo.value.message
    assert "2.000s" in excinfo.value.message


def test_plan_rejects_a_dissolve_whose_length_differs_from_the_overlap() -> None:
    with pytest.raises(ExportError) as excinfo:
        _plan([
            _clip("/m/a.mp4", 4.0, start=0.0),
            _clip("/m/b.mp4", 4.0, kind="cross_dissolve", transition=0.5, start=2.0),
        ])

    assert "0.500s" in excinfo.value.message


def test_plan_rejects_a_negative_start_on_the_first_clip() -> None:
    with pytest.raises(ExportError) as excinfo:
        _plan([_clip("/m/a.mp4", 3.0, start=-1.0)])

    assert "Clip #1" in excinfo.value.message


def test_plan_gap_before_a_cross_dissolve_is_exactly_the_timeline_gap() -> None:
    # A ends at 3s, B starts at 5s: the gap is 2.0s, not 2.5s, and there is nothing to dissolve across.
    plan = _plan([
        _clip("/m/a.mp4", 3.0, start=0.0),
        _clip("/m/b.mp4", 3.0, kind="cross_dissolve", transition=0.5, start=5.0),
    ])

    assert plan.expected_duration == 8.0  # B ends at 5.0 + 3.0
    assert "xfade" not in _graph(plan)
    assert "cross_dissolve_dropped_across_gap" in plan.notes
    assert "2.000" in plan.args  # the black filler is 2.0s long
    assert "2.500" not in plan.args


def test_plan_cross_dissolve_that_only_touches_the_previous_clip_becomes_a_hard_cut() -> None:
    # B starts exactly where A ends, so no gap may be inserted and nothing overlaps to dissolve.
    plan = _plan([
        _clip("/m/a.mp4", 3.0, start=0.0),
        _clip("/m/b.mp4", 3.0, kind="cross_dissolve", transition=0.5, start=3.0),
    ])

    assert plan.expected_duration == 6.0
    assert "xfade" not in _graph(plan)
    assert "color=c=black" not in " ".join(plan.args)
    assert "cross_dissolve_ignored_without_overlap" in plan.notes


def test_plan_expected_duration_matches_the_timeline_end_for_every_supported_layout() -> None:
    layouts = [
        ([_clip("/m/a.mp4", 3.0, start=0.0), _clip("/m/b.mp4", 3.0, start=3.0)], 6.0),
        ([_clip("/m/a.mp4", 3.0, start=1.0)], 4.0),  # leading gap
        ([_clip("/m/a.mp4", 3.0, start=0.0), _clip("/m/b.mp4", 3.0, start=5.0)], 8.0),
        (
            [
                _clip("/m/a.mp4", 3.0, start=0.0),
                _clip("/m/b.mp4", 3.0, kind="cross_dissolve", transition=0.5, start=2.5),
            ],
            5.5,
        ),
    ]
    for clips, timeline_end in layouts:
        assert _plan(clips).expected_duration == timeline_end


def test_plan_clamps_a_dissolve_longer_than_its_clips() -> None:
    plan = _plan([_clip("/m/a.mp4", 1.0), _clip("/m/b.mp4", 1.0, kind="cross_dissolve", transition=3.0)])

    assert "duration=0.950" in _graph(plan)
    assert "cross_dissolve_clamped_to_clip_length" in plan.notes
    assert plan.expected_duration == 1.05


def test_plan_without_clips_is_an_error_not_a_blank_video() -> None:
    with pytest.raises(ExportError) as excinfo:
        _plan([])
    assert excinfo.value.status_code == 422


def test_plan_keeps_hostile_paths_and_resolutions_out_of_the_filter_graph() -> None:
    hostile = "/m/a b;drawtext=text='x'.mp4"
    plan = _plan([_clip(hostile)], resolution="1920x1080; rm -rf /")

    assert hostile in plan.args  # one argv element, never interpolated into a shell string
    assert hostile not in _graph(plan)
    assert all(isinstance(arg, str) for arg in plan.args)
    assert (plan.width, plan.height) == (1920, 1080)  # malformed resolution falls back to the default


def test_render_without_ffmpeg_reports_unavailable_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(timeline_service.shutil, "which", lambda *_args, **_kwargs: None)
    out_dir = tmp_path / "out"

    with pytest.raises(ExportError) as excinfo:
        TimelineValidationService.render_and_probe_mp4(
            output_dir=str(out_dir), export_id="exp_none", fps=24, resolution="1280x720", clips=[_clip()]
        )

    assert excinfo.value.status_code == 503
    assert not out_dir.exists() or not list(out_dir.glob("*"))


# --- real FFmpeg: skipped when the binaries are not installed ------------------------------


def _make_video(path: Path, size: str, rate: int, seconds: int) -> None:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate={rate}",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", str(path),
        ],
        check=True,
    )


@needs_ffmpeg
def test_render_assembles_real_clips_and_verifies_the_output(tmp_path: Path) -> None:
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _make_video(first, "640x360", 24, 4)
    _make_video(second, "320x240", 30, 6)  # different size and frame rate on purpose

    out_path, size, sha256_hex, args, meta = TimelineValidationService.render_and_probe_mp4(
        output_dir=str(tmp_path / "out"),
        export_id="exp_real",
        fps=30,
        resolution="1080x1920",
        clips=[
            _clip(str(first), 3.0, trim=0.5),
            _clip(str(second), 5.0, kind="cross_dissolve", transition=0.5, start=2.5),
        ],
    )

    assert args[0] == "ffmpeg"
    assert meta["valid"] is True
    assert meta["probed_by"] == "ffprobe"
    assert (meta["codec_name"], meta["audio_codec"]) == ("h264", "aac")
    assert (meta["width"], meta["height"]) == (1080, 1920)
    assert abs(meta["duration_seconds"] - 7.5) < 0.2  # 3.0 + 5.0 - 0.5 dissolve
    data = Path(out_path).read_bytes()
    assert size == len(data)
    assert sha256_hex == hashlib.sha256(data).hexdigest()


@needs_ffmpeg
def test_render_gap_before_a_cross_dissolve_matches_the_timeline_duration(tmp_path: Path) -> None:
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _make_video(first, "320x240", 24, 4)
    _make_video(second, "320x240", 24, 4)

    _out, _size, _sha, _args, meta = TimelineValidationService.render_and_probe_mp4(
        output_dir=str(tmp_path / "out"),
        export_id="exp_gap_dissolve",
        fps=24,
        resolution="320x240",
        clips=[
            _clip(str(first), 3.0, start=0.0),
            _clip(str(second), 3.0, kind="cross_dissolve", transition=0.5, start=5.0),
        ],
    )

    assert meta["valid"] is True
    # Compared with the timeline itself (B ends at 5.0 + 3.0), not with the exporter's own plan.
    assert abs(meta["duration_seconds"] - 8.0) < 0.2


@needs_ffmpeg
@pytest.mark.parametrize(
    ("second_start", "third_start", "expected_duration"),
    [
        # A, B hard cut, then C dissolves over B: xfade's first input is a concat output.
        (3.0, 6.0, 10.0),
        # A, black gap, B, then C dissolves over B: the concat also joins the black filler.
        (6.0, 9.0, 13.0),
    ],
)
def test_render_dissolve_after_a_concat_segment(
    tmp_path: Path, second_start: float, third_start: float, expected_duration: float
) -> None:
    paths = [tmp_path / f"{name}.mp4" for name in ("a", "b", "c")]
    for path in paths:
        _make_video(path, "320x240", 24, 4)

    _out, _size, _sha, _args, meta = TimelineValidationService.render_and_probe_mp4(
        output_dir=str(tmp_path / "out"),
        export_id="exp_concat_dissolve",
        fps=24,
        resolution="320x240",
        clips=[
            _clip(str(paths[0]), 3.0, start=0.0),
            _clip(str(paths[1]), 4.0, start=second_start),
            _clip(str(paths[2]), 4.0, kind="cross_dissolve", transition=1.0, start=third_start),
        ],
    )

    assert meta["valid"] is True
    assert abs(meta["duration_seconds"] - expected_duration) < 0.2


@needs_ffmpeg
def test_render_refuses_an_overlap_before_running_ffmpeg(tmp_path: Path) -> None:
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _make_video(first, "320x240", 24, 4)
    _make_video(second, "320x240", 24, 4)
    out_dir = tmp_path / "out"

    with pytest.raises(ExportError) as excinfo:
        TimelineValidationService.render_and_probe_mp4(
            output_dir=str(out_dir),
            export_id="exp_overlap",
            fps=24,
            resolution="320x240",
            clips=[_clip(str(first), 4.0, start=0.0), _clip(str(second), 4.0, start=2.0)],
        )

    assert excinfo.value.status_code == 422
    assert not (out_dir / "exp_overlap.mp4").exists()


@needs_ffmpeg
def test_render_rejects_undecodable_input_instead_of_faking_success(tmp_path: Path) -> None:
    # This is exactly the kind of placeholder file the mock provider produces.
    fake = tmp_path / "placeholder.mp4"
    fake.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"not a real video")
    out_dir = tmp_path / "out"

    with pytest.raises(ExportError) as excinfo:
        TimelineValidationService.render_and_probe_mp4(
            output_dir=str(out_dir), export_id="exp_bad", fps=24, resolution="1280x720", clips=[_clip(str(fake), 2.0)]
        )

    assert excinfo.value.status_code == 422
    assert excinfo.value.ffmpeg_args and excinfo.value.ffmpeg_args[0] == "ffmpeg"
    assert not (out_dir / "exp_bad.mp4").exists()
