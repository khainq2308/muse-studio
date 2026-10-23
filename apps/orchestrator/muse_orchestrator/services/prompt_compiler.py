from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class CharacterLock:
    character_id: str
    name: str
    immutable_traits: str
    current_outfit: str
    required_ref_asset_id: str | None = None


@dataclass
class LocationLock:
    location_id: str
    name: str
    spatial_anchors: str
    lighting_rule: str
    time_of_day: str


@dataclass
class StyleBibleLock:
    medium: str
    art_direction: str
    color_palette: str
    negative_constraints: list[str] = field(default_factory=list)
    forbidden_terms: list[str] = field(default_factory=list)


@dataclass
class ShotSpec:
    shot_id: str
    scene_id: str
    sequence_index: int
    subject_ids: list[str]
    location_id: str
    shot_size: str
    camera_angle: str
    camera_motion: str
    composition: str
    action: str
    action_start: str
    action_end: str
    continuity_notes: str
    duration_seconds: float
    aspect_ratio: str = "16:9"
    scene_context_before: str = ""
    requires_reference_lock: bool = False
    reference_asset_ids: list[str] = field(default_factory=list)


@dataclass
class LintIssue:
    code: str
    severity: Literal["blocker", "warning", "info"]
    message: str
    field: str | None = None


@dataclass
class CompiledPromptResult:
    shot_id: str
    prompt_final: str
    negative_prompt: str
    prompt_hash: str
    sections: dict[str, str]
    lint_issues: list[LintIssue]
    # Hash the same shot produced before action_start/action_end entered the prompt.
    # Equal to ``prompt_hash`` when the shot has no informative (non-generic) boundaries.
    # Computed, never persisted: batch ``only_changed`` accepts it so takes rendered
    # before the upgrade stay current.
    legacy_prompt_hash: str

    @property
    def has_blockers(self) -> bool:
        return any(issue.severity == "blocker" for issue in self.lint_issues)


# Camera-movement verbs including inflections (pan/pans/panned/panning, orbit*, dolly/
# dollies, track*, crane*, zoom* in, whip pan*, handheld run*).
_DYNAMIC_CAMERA_KEYWORDS = re.compile(
    r"\b(?:orbit(?:s|ed|ing)?|dolly|dollies|dollied|dollying|pan(?:s|ned|ning)?"
    r"|truck(?:s|ed|ing)?|crane(?:s|d)?|craning|whip[\s_-]*pan(?:s|ned|ning)?"
    r"|tracking[\s_-]*shot|track(?:s|ed|ing)?|handheld[\s_-]*run(?:s|ning)?"
    r"|zoom(?:s|ed|ing)?[\s_-]+in)\b",
    re.IGNORECASE,
)
_STATIC_CAMERA_TOKENS = ("static", "locked", "fixed")


def is_static_camera_motion(camera_motion: str) -> bool:
    """True for static/locked/fixed variants such as "Static shot" or "locked_off"."""
    tokens = [t for t in re.split(r"[^a-z]+", camera_motion.casefold()) if t]
    return any(token.startswith(_STATIC_CAMERA_TOKENS) for token in tokens)


def _contains_term(text: str, term: str) -> bool:
    if not term:
        return False
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.IGNORECASE) is not None


# Conservative inter-shot checks: warnings only unless a future policy explicitly
# upgrades them. These checks never infer that a location change is impossible.
_CONTINUITY_TRANSITION_HINTS = re.compile(
    r"\b(cutaway|establishing shot|transition|location change|new location|cut to|match cut|montage)\b"
    r"|chuyển cảnh|cắt sang|đổi bối cảnh|toàn cảnh mở đầu",
    re.IGNORECASE,
)
_GENERIC_HANDOFF_VALUES = {
    "",
    "in position",
    "neutral pose",
    "pose completed",
    "completes motion",
    "completes the motion",
    "motion completed",
    "same position",
    "same as prior",
    "no specific state",
}


def is_generic_handoff(value: str) -> bool:
    """True for empty or placeholder action boundaries such as "in position".

    Shared by the continuity lint and the prompt compiler: a generic boundary carries no
    information, so it is never placed in the prompt nor in its hash.
    """
    return re.sub(r"[\W_]+", " ", value.casefold()).strip() in _GENERIC_HANDOFF_VALUES


_CONTINUITY_STOP_WORDS = {
    "a", "an", "and", "at", "by", "from", "in", "into", "is", "it", "of", "on",
    "or", "the", "to", "was", "were", "with", "đang", "được", "là", "một",
    "vẫn", "bên", "trong", "ngoài", "của",
}
_STATE_PATTERNS = {
    "open": re.compile(r"\b(?:open|opened|opens|mở)\b", re.IGNORECASE),
    "closed": re.compile(r"\b(?:closed|shut|shuts|đóng|khép)\b", re.IGNORECASE),
    "inside": re.compile(r"\b(?:inside|indoors|bên\s+trong|trong\s+phòng)\b", re.IGNORECASE),
    "outside": re.compile(r"\b(?:outside|outdoors|bên\s+ngoài|ngoài\s+phòng)\b", re.IGNORECASE),
    "standing": re.compile(r"\b(?:standing|stands|stand|đứng)\b", re.IGNORECASE),
    "sitting": re.compile(r"\b(?:sitting|sits|sit|ngồi)\b", re.IGNORECASE),
    "lying": re.compile(r"\b(?:lying|lies|lay|nằm)\b", re.IGNORECASE),
}
_INCOMPATIBLE_STATES = (
    ("open", "closed"),
    ("inside", "outside"),
    ("standing", "sitting"),
    ("standing", "lying"),
    ("sitting", "lying"),
)


def _meaningful_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE)
        if len(token) > 1 and token not in _CONTINUITY_STOP_WORDS
    }


def _has_incompatible_handoff_state(previous_end: str, current_start: str) -> bool:
    previous_states = {name for name, pattern in _STATE_PATTERNS.items() if pattern.search(previous_end)}
    current_states = {name for name, pattern in _STATE_PATTERNS.items() if pattern.search(current_start)}
    incompatible = any(
        left in previous_states and right in current_states
        for left, right in _INCOMPATIBLE_STATES
    )
    if not incompatible:
        return False

    # Require a shared meaningful noun/entity token to avoid flagging unrelated
    # states that happen to appear in the two descriptions.
    return bool(_meaningful_tokens(previous_end) & _meaningful_tokens(current_start))


def _lint_inter_shot_continuity(
    shot: ShotSpec,
    previous_shot: ShotSpec | None,
    issues: list[LintIssue],
) -> None:
    if previous_shot is None:
        return
    if shot.scene_id != previous_shot.scene_id:
        # A scene boundary may intentionally reset location and physical state.
        return
    if shot.sequence_index != previous_shot.sequence_index + 1:
        # Do not compare across gaps in the shot sequence.
        return

    if shot.location_id != previous_shot.location_id:
        if not _CONTINUITY_TRANSITION_HINTS.search(shot.continuity_notes):
            issues.append(
                LintIssue(
                    "LOCATION_CONTINUITY_BREAK",
                    "warning",
                    (
                        f"Location changes from '{previous_shot.location_id}' to "
                        f"'{shot.location_id}' inside the same scene; confirm this is intentional "
                        "or split the scene."
                    ),
                    "location_id",
                )
            )
        # Handoff checks are meaningful only when adjacent shots share a location.
        return

    previous_end = previous_shot.action_end.strip()
    current_start = shot.action_start.strip()
    if _has_incompatible_handoff_state(previous_end, current_start):
        issues.append(
            LintIssue(
                "ACTION_HANDOFF_CONFLICT",
                "warning",
                (
                    "The previous shot's ending state conflicts with this shot's starting state; "
                    "review the transition or describe the intervening action."
                ),
                "action_start",
            )
        )
    elif (
        is_generic_handoff(previous_end) or is_generic_handoff(current_start)
    ):
        issues.append(
            LintIssue(
                "ACTION_HANDOFF_UNSPECIFIED",
                "warning",
                (
                    "The adjacent shots use a generic or empty action handoff; specify the exact "
                    "visual state at the end of the previous shot and start of this shot."
                ),
                "action_start",
            )
        )


# Version of the compiled prompt format. 2 = informative action_start/action_end are part
# of the ACTION section and the hash. Recorded in each new task's request snapshot so batch
# ``only_changed`` can tell pre-upgrade takes (no marker) from current ones.
PROMPT_COMPILER_VERSION = 2

_SENTENCE_END = (".", "!", "?", "…")


def _boundary_text(value: str) -> str:
    """Normalise one action boundary; empty and generic placeholders become ""."""
    if is_generic_handoff(value):
        return ""
    return value.strip().rstrip("".join(_SENTENCE_END)).strip()


def _action_section(action: str, action_start: str, action_end: str) -> str:
    """Build the ACTION section; boundaries are appended only when informative.

    Empty and generic boundaries (``is_generic_handoff``, e.g. the script parser's
    "in position" / "completes motion" defaults) are left out of the prompt and hash.
    Dialogue is deliberately never part of the video prompt: video models do not speak
    and tend to render quoted dialogue as on-screen text.
    """
    start = _boundary_text(action_start)
    end = _boundary_text(action_end)
    if not start and not end:
        # Byte-identical to the pre-boundary prompt so existing hashes stay valid.
        return f"Action: {action}"
    parts = ["Action:"]
    action_text = action.strip()
    if action_text:
        parts.append(action_text if action_text.endswith(_SENTENCE_END) else f"{action_text}.")
    if start:
        parts.append(f"Starts: {start}.")
    if end:
        parts.append(f"Ends: {end}.")
    return " ".join(parts)


def _prompt_hash(sections: dict[str, str], negative_prompt: str) -> str:
    return hashlib.sha256(
        json.dumps({"s": sections, "n": negative_prompt}, sort_keys=True).encode()
    ).hexdigest()


class PromptCompiler:
    @staticmethod
    def compile_and_lint(
        shot: ShotSpec,
        style: StyleBibleLock,
        characters: dict[str, CharacterLock],
        locations: dict[str, LocationLock],
        available_assets: set[str],
        previous_shot: ShotSpec | None = None,
    ) -> CompiledPromptResult:
        issues: list[LintIssue] = []
        _lint_inter_shot_continuity(shot, previous_shot, issues)

        if shot.requires_reference_lock and not shot.reference_asset_ids:
            issues.append(
                LintIssue(
                    "BLOCKED_REFERENCE",
                    "blocker",
                    "Thiếu reference_asset_ids bắt buộc.",
                    "reference_asset_ids",
                )
            )

        for asset_id in shot.reference_asset_ids:
            if asset_id not in available_assets:
                issues.append(
                    LintIssue(
                        "BLOCKED_REFERENCE",
                        "blocker",
                        f"Asset '{asset_id}' không tồn tại.",
                        "reference_asset_ids",
                    )
                )

        char_descriptions: list[str] = []
        for cid in shot.subject_ids:
            if cid not in characters:
                issues.append(
                    LintIssue(
                        "UNKNOWN_CHARACTER",
                        "blocker",
                        f"Nhân vật '{cid}' chưa có trong Bible.",
                        "subject_ids",
                    )
                )
                continue
            c = characters[cid]
            if c.required_ref_asset_id and c.required_ref_asset_id not in available_assets:
                issues.append(
                    LintIssue(
                        "BLOCKED_REFERENCE",
                        "blocker",
                        f"Thiếu ref asset '{c.required_ref_asset_id}'.",
                        "subject_ids",
                    )
                )
            char_descriptions.append(f"{c.name} ({c.immutable_traits}, wearing {c.current_outfit})")

        loc = locations.get(shot.location_id)
        loc_text = f"{loc.name}, {loc.time_of_day}. Anchors: {loc.spatial_anchors}." if loc else shot.location_id

        if is_static_camera_motion(shot.camera_motion):
            for text_field, val in [("composition", shot.composition), ("action", shot.action)]:
                if _DYNAMIC_CAMERA_KEYWORDS.search(val):
                    issues.append(
                        LintIssue(
                            "CAMERA_MOTION_CONFLICT",
                            "blocker",
                            f"Camera '{shot.camera_motion}' mâu thuẫn với '{text_field}'.",
                            text_field,
                        )
                    )

        # Style-bible forbidden terms are a review warning: the operator decides whether
        # the wording is intentional (they are never silently stripped from the prompt).
        reported_terms: set[str] = set()
        for term in style.forbidden_terms:
            normalized_term = term.strip()
            if not normalized_term or normalized_term.casefold() in reported_terms:
                continue
            for text_field, val in [
                ("composition", shot.composition),
                ("action", shot.action),
                ("camera_motion", shot.camera_motion),
                ("continuity_notes", shot.continuity_notes),
            ]:
                if _contains_term(val, normalized_term):
                    reported_terms.add(normalized_term.casefold())
                    issues.append(
                        LintIssue(
                            "FORBIDDEN_TERM",
                            "warning",
                            f"'{text_field}' chứa thuật ngữ bị Style Bible cấm: '{normalized_term}'.",
                            text_field,
                        )
                    )
                    break

        sections = {
            "GLOBAL_STYLE_LOCK": f"Style: {style.medium}, {style.art_direction}.",
            "CHARACTER_LOCKS": f"Subjects: {'; '.join(char_descriptions)}." if char_descriptions else "Subjects: Environment.",
            "LOCATION_LOCK": f"Environment: {loc_text}",
            "CAMERA_AND_COMPOSITION": f"Camera: {shot.shot_size}, {shot.camera_angle}, {shot.camera_motion}. {shot.composition}.",
            "ACTION": _action_section(shot.action, shot.action_start, shot.action_end),
            "CONTINUITY": f"Continuity: {shot.continuity_notes}",
            "TECHNICAL_OUTPUT": f"Technical: {shot.duration_seconds:.1f}s, {shot.aspect_ratio}.",
        }
        prompt_final = " ".join(v for v in sections.values() if v)
        negative_prompt = ", ".join(style.negative_constraints)
        prompt_hash = _prompt_hash(sections, negative_prompt)
        legacy_prompt_hash = _prompt_hash(
            {**sections, "ACTION": f"Action: {shot.action}"}, negative_prompt
        )

        return CompiledPromptResult(
            shot_id=shot.shot_id,
            prompt_final=prompt_final,
            negative_prompt=negative_prompt,
            prompt_hash=prompt_hash,
            sections=sections,
            lint_issues=issues,
            legacy_prompt_hash=legacy_prompt_hash,
        )
