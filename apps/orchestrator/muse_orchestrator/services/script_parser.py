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
    warnings: list[str] = field(default_factory=list)


class ScriptParseError(ValueError):
    """The script cannot be turned into a safe structured proposal."""


# Bounds shared with the API layer.
MAX_SCRIPT_CHARS = 1_000_000
MAX_SCRIPT_LINE_CHARS = 10_000
MAX_SCENE_INDEX = 10_000
MAX_SHOT_DURATION_SECONDS = 600.0
DEFAULT_SHOT_DURATION_SECONDS = 5.0


_SCENE_HEADER_RE = re.compile(r"^(?:#+\s*)?(?:SCENE|CẢNH)\s*(\d+)\s*[:\-–—]?\s*(.*)$", re.IGNORECASE)
_SHOT_HEADER_RE = re.compile(r"^(?:#+\s*|\*\*)?(?:SHOT|NODE|CÚ MÁY)\s*(\d+)\s*(?:\*\*)?[:\-–—]?\s*(.*)$", re.IGNORECASE)
# Keys are validated with a single character class (no nested/overlapping repetition);
# the line itself is split with str methods so parsing is linear in the line length.
_KV_KEY_RE = re.compile(r"[A-Za-zÀ-ỹ_ ]+")
_DURATION_RE = re.compile(r"\d{1,7}(?:\.\d{1,6})?")


def _parse_kv_line(line: str) -> tuple[str, str] | None:
    """Parse ``- **Key**: value`` style lines; return None for anything else."""
    text = line.strip()
    if text[:1] in ("-", "*"):
        text = text[1:].lstrip()
    if ":" not in text:
        return None
    raw_key, raw_value = text.split(":", 1)
    key = raw_key.strip().strip("*").strip()
    value = raw_value.strip()
    # Bold markers may wrap the colon ("**Key:** value").
    if value.startswith("**"):
        value = value[2:].strip()
    if not key or not value or not _KV_KEY_RE.fullmatch(key):
        return None
    return key, value


def _slugify(prefix: str, text: str) -> str:
    # Vietnamese "đ" does not decompose under NFKD, so transliterate it first.
    normalized = text.strip().lower().replace("đ", "d")
    ascii_text = unicodedata.normalize("NFKD", normalized).encode("ascii", "ignore").decode("ascii")
    clean = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")
    return f"{prefix}_{clean[:24]}" if clean else f"{prefix}_default"


class _CodeRegistry:
    """Deterministic display-name -> code mapping with collision disambiguation.

    The same (case-insensitive) name always maps to the same code; two different names
    whose slugs collide (punctuation-only differences, 24-character truncation) get
    ``_2``, ``_3`` ... suffixes in first-seen order instead of silently merging.
    """

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self._by_name: dict[str, str] = {}
        self._taken: set[str] = set()

    @staticmethod
    def _name_key(name: str) -> str:
        return " ".join(name.casefold().split())

    def reserve(self, name: str, code: str) -> str:
        self._by_name.setdefault(self._name_key(name), code)
        self._taken.add(code)
        return self._by_name[self._name_key(name)]

    def code_for(self, name: str) -> tuple[str, bool]:
        """Return (code, is_new)."""
        key = self._name_key(name)
        if key in self._by_name:
            return self._by_name[key], False
        base = _slugify(self.prefix, name)
        code = base
        suffix = 2
        while code in self._taken:
            code = f"{base}_{suffix}"
            suffix += 1
        self._by_name[key] = code
        self._taken.add(code)
        return code, True


class ScriptAnalyzerService:
    """
    Phân tích kịch bản đầu vào (Markdown / MuseFlow Node Blocks / Plaintext) thành đề xuất
    Story Bible + Scenes + Shots mà KHÔNG ghi đè kịch bản gốc (Mục 3.2 & 3.3 PRD).
    """

    @staticmethod
    def analyze_script(raw_text: str, default_style: str = "2D Anime Detective Cinema") -> ParsedScriptProposal:
        """Parse a script deterministically. CPU-bound: call via ``asyncio.to_thread``.

        Raises ScriptParseError for inputs that cannot be represented safely (oversized
        script, out-of-range SCENE numbers).
        """
        if len(raw_text) > MAX_SCRIPT_CHARS:
            raise ScriptParseError(f"Script exceeds {MAX_SCRIPT_CHARS} characters.")
        warnings: list[str] = []
        lines: list[str] = []
        for line_no, line in enumerate(raw_text.splitlines(), start=1):
            if len(line) > MAX_SCRIPT_LINE_CHARS:
                warnings.append(
                    f"Line {line_no} exceeds {MAX_SCRIPT_LINE_CHARS} characters and was truncated for analysis."
                )
                line = line[:MAX_SCRIPT_LINE_CHARS]
            lines.append(line.rstrip())
        characters_map: dict[str, dict[str, Any]] = {}
        locations_map: dict[str, dict[str, Any]] = {}
        character_codes = _CodeRegistry("char")
        location_codes = _CodeRegistry("loc")
        scenes: list[dict[str, Any]] = []
        shots: list[dict[str, Any]] = []
        used_scene_indexes: set[int] = set()

        current_scene: dict[str, Any] | None = None
        current_shot: dict[str, Any] | None = None
        shot_seq = 0

        def ensure_location(name: str, defaults: dict[str, Any]) -> str:
            code, is_new = location_codes.code_for(name)
            if is_new:
                locations_map[code] = {"code_name": code, **defaults}
            return code

        def ensure_default_scene() -> dict[str, Any]:
            nonlocal current_scene
            if current_scene is None:
                loc_code = location_codes.reserve("Main Stage", "loc_main_stage")
                locations_map.setdefault(loc_code, {
                    "code_name": loc_code,
                    "display_name": "Main Stage",
                    "immutable_traits": "indoor cinematic room with window light",
                    "mutable_attributes": {"time_of_day": "night", "lighting_rule": "moody contrast"},
                })
                scene_index = 1
                while scene_index in used_scene_indexes:
                    scene_index += 1
                used_scene_indexes.add(scene_index)
                current_scene = {
                    "scene_index": scene_index,
                    "title": f"Scene {scene_index}",
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
                digits = scene_match.group(1)
                if len(digits) > len(str(MAX_SCENE_INDEX)) or int(digits) > MAX_SCENE_INDEX:
                    raise ScriptParseError(
                        f"SCENE number '{digits[:12]}' is out of range (1-{MAX_SCENE_INDEX})."
                    )
                idx = int(digits)
                title = scene_match.group(2).strip() or f"Scene {idx}"
                if idx in used_scene_indexes:
                    # Duplicate SCENE numbers: keep document order and renumber the later
                    # scene after the highest index so shots never attach to the wrong scene.
                    new_idx = max(used_scene_indexes) + 1
                    warnings.append(f"Duplicate SCENE {idx} renumbered to SCENE {new_idx}.")
                    idx = new_idx
                used_scene_indexes.add(idx)
                location_name = title.split("-")[0].strip() or f"Location {idx}"
                loc_code = ensure_location(location_name, {
                    "display_name": location_name,
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
                    "duration_seconds": DEFAULT_SHOT_DURATION_SECONDS,
                }
                shots.append(current_shot)
                continue

            kv = _parse_kv_line(stripped)
            if kv is not None and current_shot is not None:
                key = kv[0].strip().lower()
                val = kv[1]
                if key in ("character", "characters", "nhân vật", "subject"):
                    for name in [n.strip() for n in val.split(",") if n.strip()]:
                        c_code, is_new = character_codes.code_for(name)
                        if is_new:
                            characters_map[c_code] = {
                                "code_name": c_code,
                                "display_name": name,
                                "immutable_traits": f"consistent 2D anime character design for {name}",
                                "mutable_attributes": {"current_outfit": "signature outfit"},
                            }
                        if c_code not in current_shot["subject_codes"]:
                            current_shot["subject_codes"].append(c_code)
                elif key in ("location", "bối cảnh"):
                    l_code = ensure_location(val, {
                        "display_name": val,
                        "immutable_traits": f"architectural layout of {val}",
                        "mutable_attributes": {"time_of_day": "night", "lighting_rule": "dramatic"},
                    })
                    current_shot["location_code"] = l_code
                elif key in ("camera", "camera_motion", "chuyển động máy"):
                    current_shot["camera_motion"] = val.lower()[:64]
                elif key in ("shot_size", "cỡ cảnh"):
                    current_shot["shot_size"] = val.lower()[:64]
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
                    num_match = _DURATION_RE.search(val)
                    if num_match:
                        duration = float(num_match.group(0))
                        if duration <= 0:
                            warnings.append(
                                f"SHOT {current_shot['sequence_index']}: non-positive Duration replaced "
                                f"by {DEFAULT_SHOT_DURATION_SECONDS:g}s."
                            )
                            duration = DEFAULT_SHOT_DURATION_SECONDS
                        elif duration > MAX_SHOT_DURATION_SECONDS:
                            warnings.append(
                                f"SHOT {current_shot['sequence_index']}: Duration {duration:g}s clamped "
                                f"to {MAX_SHOT_DURATION_SECONDS:g}s."
                            )
                            duration = MAX_SHOT_DURATION_SECONDS
                        current_shot["duration_seconds"] = duration
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
            warnings=warnings,
        )
