from __future__ import annotations

from muse_orchestrator.services.prompt_compiler import (
    PromptCompiler,
    ShotSpec,
    StyleBibleLock,
)


def make_shot(
    *,
    shot_id: str = "shot-2",
    scene_id: str = "scene-1",
    sequence_index: int = 2,
    location_id: str = "cafe",
    action_start: str = "in position",
    action_end: str = "completes motion",
    continuity_notes: str = "inherit lighting and screen direction from scene",
) -> ShotSpec:
    return ShotSpec(
        shot_id=shot_id,
        scene_id=scene_id,
        sequence_index=sequence_index,
        subject_ids=[],
        location_id=location_id,
        shot_size="medium",
        camera_angle="eye level",
        camera_motion="static",
        composition="balanced framing",
        action="Character remains at the table",
        action_start=action_start,
        action_end=action_end,
        continuity_notes=continuity_notes,
        duration_seconds=4.0,
    )


def compile_shot(shot: ShotSpec, previous_shot: ShotSpec | None = None):
    return PromptCompiler.compile_and_lint(
        shot=shot,
        style=StyleBibleLock(medium="2D anime", art_direction="cinematic", color_palette="moody"),
        characters={},
        locations={},
        available_assets=set(),
        previous_shot=previous_shot,
    )


def test_linter_warns_when_location_changes_inside_same_scene() -> None:
    previous = make_shot(
        shot_id="shot-1",
        sequence_index=1,
        location_id="cafe",
        action_end="Conan remains beside the counter",
    )
    current = make_shot(
        location_id="street",
        action_start="Conan steps under the awning",
    )

    result = compile_shot(current, previous)
    issue = next(item for item in result.lint_issues if item.code == "LOCATION_CONTINUITY_BREAK")
    assert issue.severity == "warning"
    assert issue.field == "location_id"


def test_linter_warns_when_adjacent_shot_states_conflict() -> None:
    previous = make_shot(
        shot_id="shot-1",
        sequence_index=1,
        action_end="The office door is open",
    )
    current = make_shot(action_start="The office door is closed")

    result = compile_shot(current, previous)
    issue = next(item for item in result.lint_issues if item.code == "ACTION_HANDOFF_CONFLICT")
    assert issue.severity == "warning"
    assert issue.field == "action_start"


def test_linter_warns_for_generic_action_handoff() -> None:
    previous = make_shot(shot_id="shot-1", sequence_index=1, action_end="completes motion")
    current = make_shot(action_start="in position")

    result = compile_shot(current, previous)
    assert any(item.code == "ACTION_HANDOFF_UNSPECIFIED" for item in result.lint_issues)


def test_linter_skips_location_and_state_checks_across_scene_boundaries() -> None:
    previous = make_shot(
        shot_id="shot-1",
        sequence_index=1,
        scene_id="scene-1",
        location_id="cafe",
        action_end="The office door is open",
    )
    current = make_shot(
        scene_id="scene-2",
        location_id="street",
        action_start="The office door is closed",
    )

    result = compile_shot(current, previous)
    continuity_codes = {
        item.code for item in result.lint_issues
        if item.code.startswith("LOCATION_CONTINUITY") or item.code.startswith("ACTION_HANDOFF")
    }
    assert continuity_codes == set()


def test_explicit_location_transition_suppresses_location_drift_warning() -> None:
    previous = make_shot(shot_id="shot-1", sequence_index=1, location_id="cafe")
    current = make_shot(
        location_id="street",
        continuity_notes="Cut to the street for an establishing shot",
    )

    result = compile_shot(current, previous)
    assert not any(item.code == "LOCATION_CONTINUITY_BREAK" for item in result.lint_issues)


# --- action boundaries in the video prompt -------------------------------------------

# Prompt and hash produced by main 668659a (before boundaries entered the prompt) for
# make_shot with any action_start/action_end. Pinned so a shot without boundaries keeps
# exactly this text and hash, and the computed legacy hash keeps matching old takes.
_LEGACY_PROMPT = (
    "Style: 2D anime, cinematic. Subjects: Environment. Environment: cafe "
    "Camera: medium, eye level, static. balanced framing. "
    "Action: Character remains at the table "
    "Continuity: inherit lighting and screen direction from scene "
    "Technical: 4.0s, 16:9."
)
_LEGACY_HASH = "7c8313569a4d78f451f22ec200d7ef94f430bcb9b8d1076ec436c0263b5a34e4"


def test_prompt_includes_action_boundaries_when_set() -> None:
    result = compile_shot(
        make_shot(action_start="Seated, cup in hand.", action_end="Stands up and leaves")
    )

    assert result.sections["ACTION"] == (
        "Action: Character remains at the table. Starts: Seated, cup in hand. "
        "Ends: Stands up and leaves."
    )
    assert result.prompt_final == (
        "Style: 2D anime, cinematic. Subjects: Environment. Environment: cafe "
        "Camera: medium, eye level, static. balanced framing. "
        "Action: Character remains at the table. Starts: Seated, cup in hand. "
        "Ends: Stands up and leaves. "
        "Continuity: inherit lighting and screen direction from scene "
        "Technical: 4.0s, 16:9."
    )


def test_prompt_includes_only_the_boundary_that_is_set() -> None:
    only_end = compile_shot(make_shot(action_start="   ", action_end="Stands up"))

    assert only_end.sections["ACTION"] == "Action: Character remains at the table. Ends: Stands up."
    assert "Starts:" not in only_end.prompt_final


def test_prompt_and_hash_are_byte_identical_to_legacy_without_boundaries() -> None:
    result = compile_shot(make_shot(action_start="", action_end=""))

    assert result.prompt_final == _LEGACY_PROMPT
    assert result.prompt_hash == _LEGACY_HASH
    assert result.legacy_prompt_hash == _LEGACY_HASH
    whitespace = compile_shot(make_shot(action_start="  ", action_end="\n"))
    assert whitespace.prompt_final == _LEGACY_PROMPT
    assert whitespace.prompt_hash == _LEGACY_HASH


def test_legacy_hash_matches_pre_change_hash_of_a_shot_with_boundaries() -> None:
    result = compile_shot(make_shot(action_start="in position", action_end="completes motion"))

    assert result.prompt_hash != _LEGACY_HASH
    assert result.legacy_prompt_hash == _LEGACY_HASH


def test_hash_changes_when_only_action_end_changes() -> None:
    before = compile_shot(make_shot(action_start="Seated", action_end="Stands up"))
    after = compile_shot(make_shot(action_start="Seated", action_end="Walks out"))

    assert after.prompt_hash != before.prompt_hash
    # The computed legacy hash cannot see boundaries (documented limitation).
    assert after.legacy_prompt_hash == before.legacy_prompt_hash == _LEGACY_HASH


def test_dialogue_never_reaches_the_video_prompt() -> None:
    spec = make_shot(action_start="Seated", action_end="Stands up")
    # ShotSpec carries no dialogue: the compiler cannot place dialogue in the prompt.
    assert not hasattr(spec, "dialogue")
    result = compile_shot(spec)
    assert "dialogue" not in result.prompt_final.casefold()
    assert "dialogue" not in {key.casefold() for key in result.sections}
