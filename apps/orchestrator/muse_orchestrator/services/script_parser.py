from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedScriptProposal:
    characters: list[dict[str, Any]] = field(default_factory=list)
    locations: list[dict[str, Any]] = field(default_factory=list)
    style: dict[str, Any] = field(default_factory=dict)
    scenes: list[dict[str, Any]] = field(default_factory=list)
    shots: list[dict[str, Any]] = field(default_factory=list)
    diff_summary: str = ""


_SCENE_HEADER_RE = re.compile(r"^(?:#+\s*)?(?:SCENE|CẢNH)\s*(\d+)\s*[:\-–—]?\s*(.*)$", re.IGNORECASE)
_SHOT_HEADER_RE = re.compile(r"^(?:#+\s*|\*\*)?(?:SHOT|NODE|CÚ MÁY)\s*(\d+)\s*(?:\*\*)?[:\-–—]?\s*(.*)$", re.IGNORECASE)
_KV_LINE_RE = re.compile(r"^\s*[-*]?\s*\**([A-Za-zÀ-ỹ_ ]+)\**\s*:\s*(.+)$")


def _slugify(prefix: str, text: str) -> str:
    # Vietnamese "đ" does not decompose under NFKD, so transliterate it first.
    normalized = text.strip().lower().replace("đ", "d")
    ascii_text = unicodedata.normalize("NFKD", normalized).encode("ascii", "ignore").decode("ascii")
    clean = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")
    return f"{prefix}_{clean[:24]}" if clean else f"{prefix}_default"


class ScriptAnalyzerService:
    """
    Phân tích kịch bản đầu vào (Markdown / MuseFlow Node Blocks / Plaintext) thành đề xuất
    Story Bible + Scenes + Shots mà KHÔNG ghi đè kịch bản gốc (Mục 3.2 & 3.3 PRD).
    """

    @staticmethod
    def analyze_script(raw_text: str, default_style: str = "2D Anime Detective Cinema") -> ParsedScriptProposal:
        lines = [line.rstrip() for line in raw_text.splitlines()]
        characters_map: dict[str, dict[str, Any]] = {}
        locations_map: dict[str, dict[str, Any]] = {}
        scenes: list[dict[str, Any]] = []
        shots: list[dict[str, Any]] = []

        current_scene: dict[str, Any] | None = None
        current_shot: dict[str, Any] | None = None
        shot_seq = 0

        def ensure_default_scene() -> dict[str, Any]:
            nonlocal current_scene
            if current_scene is None:
                loc_code = "loc_main_stage"
                locations_map.setdefault(loc_code, {
                    "code_name": loc_code,
                    "display_name": "Main Stage",
                    "immutable_traits": "indoor cinematic room with window light",
                    "mutable_attributes": {"time_of_day": "night", "lighting_rule": "moody contrast"},
                })
                current_scene = {
                    "scene_index": 1,
                    "title": "Scene 1",
                    "location_code": loc_code,
                    "time_of_day": "night",
                    "lighting_mood": "moody contrast",
                    "summary": "Auto-detected opening scene",
                }
                scenes.append(current_scene)
            return current_scene

        for raw_line in lines:
            stripped = raw_line.strip()
            if not stripped:
                continue

            scene_match = _SCENE_HEADER_RE.match(stripped)
            if scene_match:
                idx = int(scene_match.group(1))
                title = scene_match.group(2).strip() or f"Scene {idx}"
                loc_code = _slugify("loc", title.split("-")[0])
                locations_map.setdefault(loc_code, {
                    "code_name": loc_code,
                    "display_name": title.split("-")[0].strip() or f"Location {idx}",
                    "immutable_traits": f" key environment for {title}",
                    "mutable_attributes": {"time_of_day": "rainy night", "lighting_rule": "cinematic rim light"},
                })
                current_scene = {
                    "scene_index": idx,
                    "title": title,
                    "location_code": loc_code,
                    "time_of_day": "rainy night",
                    "lighting_mood": "cinematic rim light",
                    "summary": title,
                }
                scenes.append(current_scene)
                current_shot = None
                continue

            shot_match = _SHOT_HEADER_RE.match(stripped)
            if shot_match:
                sc = ensure_default_scene()
                shot_seq += 1
                shot_title = shot_match.group(2).strip() or f"Shot {shot_seq}"
                current_shot = {
                    "sequence_index": shot_seq,
                    "scene_index": sc["scene_index"],
                    "title": shot_title,
                    "story_beat": shot_title,
                    "subject_codes": [],
                    "location_code": sc["location_code"],  # Kế thừa tự động từ Scene (Mục 3.3 PRD)
                    "shot_size": "medium_shot",
                    "camera_angle": "eye_level",
                    "camera_motion": "static",
                    "composition": "balanced rule of thirds",
                    "action": shot_title,
                    "action_start": "in position",
                    "action_end": "completes motion",
                    "dialogue": "",
                    "continuity_notes": "inherit lighting and screen direction from scene",
                    "duration_seconds": 5.0,
                }
                shots.append(current_shot)
                continue

            kv_match = _KV_LINE_RE.match(stripped)
            if kv_match and current_shot is not None:
                key = kv_match.group(1).strip().lower()
                val = kv_match.group(2).strip()
                if key in ("character", "characters", "nhân vật", "subject"):
                    for name in [n.strip() for n in val.split(",") if n.strip()]:
                        c_code = _slugify("char", name)
                        characters_map.setdefault(c_code, {
                            "code_name": c_code,
                            "display_name": name,
                            "immutable_traits": f"consistent 2D anime character design for {name}",
                            "mutable_attributes": {"current_outfit": "signature outfit"},
                        })
                        if c_code not in current_shot["subject_codes"]:
                            current_shot["subject_codes"].append(c_code)
                elif key in ("location", "bối cảnh"):
                    l_code = _slugify("loc", val)
                    locations_map.setdefault(l_code, {
                        "code_name": l_code,
                        "display_name": val,
                        "immutable_traits": f"architectural layout of {val}",
                        "mutable_attributes": {"time_of_day": "night", "lighting_rule": "dramatic"},
                    })
                    current_shot["location_code"] = l_code
                elif key in ("camera", "camera_motion", "chuyển động máy"):
                    current_shot["camera_motion"] = val.lower()
                elif key in ("shot_size", "cỡ cảnh"):
                    current_shot["shot_size"] = val.lower()
                elif key in ("composition", "bố cục"):
                    current_shot["composition"] = val
                elif key in ("action", "hành động"):
                    current_shot["action"] = val
                elif key in ("action_start", "bắt đầu"):
                    current_shot["action_start"] = val
                elif key in ("action_end", "kết thúc"):
                    current_shot["action_end"] = val
                elif key in ("dialogue", "thoại"):
                    current_shot["dialogue"] = val
                elif key in ("duration", "thời lượng"):
                    num_match = re.search(r"(\d+(?:\.\d+)?)", val)
                    if num_match:
                        current_shot["duration_seconds"] = float(num_match.group(1))
                continue

        # Fallback: Nếu kịch bản là văn xuôi thuần chưa có thẻ SHOT, tách mỗi đoạn văn thành 1 Shot
        if not shots:
            sc = ensure_default_scene()
            paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw_text) if p.strip()]
            for idx, para in enumerate(paragraphs, start=1):
                shots.append({
                    "sequence_index": idx,
                    "scene_index": sc["scene_index"],
                    "title": f"Shot {idx}",
                    "story_beat": para[:120],
                    "subject_codes": [],
                    "location_code": sc["location_code"],
                    "shot_size": "medium_shot",
                    "camera_angle": "eye_level",
                    "camera_motion": "static",
                    "composition": "cinematic framing",
                    "action": para,
                    "action_start": "neutral pose",
                    "action_end": "pose completed",
                    "dialogue": "",
                    "continuity_notes": "maintain scene lighting",
                    "duration_seconds": 5.0,
                })

        reconstructed_lines = [f"# Structured Breakdown ({len(scenes)} scenes, {len(shots)} shots)"]
        for s in shots:
            reconstructed_lines.append(
                f"SHOT {s['sequence_index']} [{s['location_code']}] ({s['camera_motion']}): {s['action']}"
            )
        diff_text = "\n".join(
            difflib.unified_diff(
                raw_text.splitlines(),
                reconstructed_lines,
                fromfile="original_script.md",
                tofile="ai_structured_proposal.md",
                lineterm="",
            )
        )

        return ParsedScriptProposal(
            characters=list(characters_map.values()),
            locations=list(locations_map.values()),
            style={
                "code_name": "style_global",
                "display_name": default_style,
                "immutable_traits": "sharp lineart, cinematic lighting, consistent anime proportions",
                "mutable_attributes": {
                    "color_palette": "moody teal and warm amber",
                    "negative_constraints": ["3d cgi", "photorealistic", "deformed fingers", "extra limbs"],
                    "forbidden_terms": ["live action", "tokusatsu"],
                },
            },
            scenes=scenes,
            shots=shots,
            diff_summary=diff_text,
        )
