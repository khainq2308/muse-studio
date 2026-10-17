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

    @property
    def has_blockers(self) -> bool:
        return any(issue.severity == "blocker" for issue in self.lint_issues)


_DYNAMIC_CAMERA_KEYWORDS = re.compile(
    r"\b(orbit|dolly|pan|truck|crane|whip\s*pan|tracking\s*shot|handheld\s*run|zoom\s*in\s*fast)\b",
    re.IGNORECASE,
)


class PromptCompiler:
    @staticmethod
    def compile_and_lint(
        shot: ShotSpec,
        style: StyleBibleLock,
        characters: dict[str, CharacterLock],
        locations: dict[str, LocationLock],
        available_assets: set[str],
    ) -> CompiledPromptResult:
        issues: list[LintIssue] = []

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

        if shot.camera_motion.lower() in ("static", "locked", "fixed"):
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

        sections = {
            "GLOBAL_STYLE_LOCK": f"Style: {style.medium}, {style.art_direction}.",
            "CHARACTER_LOCKS": f"Subjects: {'; '.join(char_descriptions)}." if char_descriptions else "Subjects: Environment.",
            "LOCATION_LOCK": f"Environment: {loc_text}",
            "CAMERA_AND_COMPOSITION": f"Camera: {shot.shot_size}, {shot.camera_angle}, {shot.camera_motion}. {shot.composition}.",
            "ACTION": f"Action: {shot.action}",
            "CONTINUITY": f"Continuity: {shot.continuity_notes}",
            "TECHNICAL_OUTPUT": f"Technical: {shot.duration_seconds:.1f}s, {shot.aspect_ratio}.",
        }
        prompt_final = " ".join(v for v in sections.values() if v)
        negative_prompt = ", ".join(style.negative_constraints)
        prompt_hash = hashlib.sha256(
            json.dumps({"s": sections, "n": negative_prompt}, sort_keys=True).encode()
        ).hexdigest()

        return CompiledPromptResult(
            shot_id=shot.shot_id,
            prompt_final=prompt_final,
            negative_prompt=negative_prompt,
            prompt_hash=prompt_hash,
            sections=sections,
            lint_issues=issues,
        )
