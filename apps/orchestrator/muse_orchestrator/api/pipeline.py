from __future__ import annotations
import csv
import io
import json
from typing import Any, Literal
from typing_extensions import TypeAliasType
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from muse_orchestrator.api.dependencies import get_studio_repository
from muse_orchestrator.application.pipeline_service import PipelineService
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.models.domain import ShotRecord, ShotApprovalState, ShotGenerationState
from muse_orchestrator.services.script_parser import MAX_SCRIPT_CHARS, MAX_SHOT_DURATION_SECONDS

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["M2 Script-to-Shot Pipeline"])


JsonValue = TypeAliasType(
    "JsonValue",
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"],
)

MAX_REFERENCE_ASSETS = 32
MAX_MUTABLE_ATTRIBUTES_CHARS = 32_768


def get_pipeline_service(
    repository: StudioRepository = Depends(get_studio_repository),
) -> PipelineService:
    return PipelineService(repository)


def _check_asset_ids(value: list[str] | None) -> list[str] | None:
    if value is not None and any(not item or len(item) > 64 for item in value):
        raise ValueError("asset ids must be 1-64 characters")
    return value


class ScriptImportIn(BaseModel):
    raw_content: str = Field(..., min_length=5, max_length=MAX_SCRIPT_CHARS)
    source_format: str = Field(default="museflow", min_length=1, max_length=32)
    auto_apply_proposal: bool = Field(default=False)
    # Required to re-apply a script over shots that already have generated takes.
    replace_existing: bool = Field(default=False)


class StyleMutableAttributesIn(BaseModel):
    """Typed contract for the style entry attributes consumed by the prompt compiler."""

    model_config = ConfigDict(extra="allow")

    color_palette: str | None = Field(default=None, max_length=255)
    negative_constraints: list[str] | None = Field(default=None, max_length=64)
    forbidden_terms: list[str] | None = Field(default=None, max_length=64)

    @field_validator("negative_constraints", "forbidden_terms")
    @classmethod
    def _bounded_terms(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(len(item) > 255 for item in value):
            raise ValueError("each term must be at most 255 characters")
        return value


class BibleEntryCreateIn(BaseModel):
    entry_type: str = Field(..., pattern=r"^(character|location|style|prop)$")
    code_name: str = Field(..., min_length=2, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=255)
    immutable_traits: str = Field(default="", max_length=8_000)
    mutable_attributes: dict[str, JsonValue] = Field(default_factory=dict)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=MAX_REFERENCE_ASSETS)
    requires_reference_lock: bool = Field(default=False)

    @field_validator("reference_asset_ids")
    @classmethod
    def _bounded_ids(cls, value: list[str]) -> list[str]:
        return _check_asset_ids(value) or []

    @model_validator(mode="after")
    def _validate_mutable_attributes(self) -> "BibleEntryCreateIn":
        if len(json.dumps(self.mutable_attributes, ensure_ascii=False)) > MAX_MUTABLE_ATTRIBUTES_CHARS:
            raise ValueError(f"mutable_attributes exceeds {MAX_MUTABLE_ATTRIBUTES_CHARS} characters")
        if self.entry_type == "style":
            # A wrongly typed style entry would otherwise be committed and break every
            # later recompile; reject it with 422 instead.
            StyleMutableAttributesIn.model_validate(self.mutable_attributes)
        return self


class ShotUpdateIn(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    # String lengths match the shots table columns (String(64) / Text).
    shot_size: str | None = Field(default=None, min_length=1, max_length=64)
    camera_motion: str | None = Field(default=None, min_length=1, max_length=64)
    composition: str | None = Field(default=None, min_length=1, max_length=2000)
    action: str | None = Field(default=None, min_length=1, max_length=4000)
    duration_seconds: float | None = Field(
        default=None, gt=0, le=MAX_SHOT_DURATION_SECONDS, allow_inf_nan=False
    )
    continuity_mode: Literal["standard", "frame_chain"] | None = None
    previous_shot_id: str | None = Field(default=None, min_length=1, max_length=64)
    first_frame_asset_id: str | None = Field(default=None, min_length=1, max_length=64)
    requires_reference_lock: bool | None = None
    reference_asset_ids: list[str] | None = Field(default=None, max_length=MAX_REFERENCE_ASSETS)
    approval_state: ShotApprovalState | None = None

    @field_validator("shot_size", "camera_motion", "composition", "action")
    @classmethod
    def _reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("reference_asset_ids")
    @classmethod
    def _bounded_ids(cls, value: list[str] | None) -> list[str] | None:
        return _check_asset_ids(value)


class PipelineLintIssueOut(BaseModel):
    code: str
    severity: Literal["blocker", "warning", "info"]
    message: str
    field: str | None = None


class ScriptImportOut(BaseModel):
    script_revision_id: str
    version_number: int
    raw_content_preserved: bool
    diff_summary: str
    detected_characters: int
    detected_locations: int
    detected_scenes: int
    generated_shots: int
    warnings: list[str] = Field(default_factory=list)


class StoryBibleEntryOut(BaseModel):
    id: str
    entry_type: Literal["character", "location", "style", "prop"]
    code_name: str
    display_name: str
    immutable_traits: str
    mutable_attributes: dict[str, JsonValue]
    reference_asset_ids: list[str]
    requires_reference_lock: bool


class StoryBibleUpsertOut(BaseModel):
    id: str
    code_name: str
    status: Literal["recompiled_downstream_shots"]


class ProjectShotOut(BaseModel):
    id: str
    scene_id: str
    sequence_index: int
    title: str
    subject_codes: list[str]
    location_code: str
    shot_size: str
    camera_motion: str
    composition: str
    action: str
    dialogue: str
    duration_seconds: float
    requires_reference_lock: bool
    reference_asset_ids: list[str]
    prompt_final: str
    negative_prompt: str
    prompt_hash: str
    lint_report: list[PipelineLintIssueOut]
    continuity_mode: str
    previous_shot_id: str | None
    next_shot_id: str | None
    first_frame_asset_id: str | None
    last_frame_asset_id: str | None
    selected_take_id: str | None
    approval_state: ShotApprovalState
    generation_state: ShotGenerationState


class ShotReviewOut(BaseModel):
    id: str
    prompt_final: str
    prompt_hash: str
    lint_report: list[PipelineLintIssueOut]
    approval_state: ShotApprovalState
    generation_state: ShotGenerationState


class ProjectShotsExportOut(BaseModel):
    project_id: str
    shots: list[ProjectShotOut]


async def _recompile_project_shots(project_id: str, repository: StudioRepository) -> list[ShotRecord]:
    """Recompile and commit prompts/lint state (kept for callers such as batch launch).

    Read-only list/export handlers must use ``StudioRepository.list_project_shots``
    directly; calling this helper writes to the database.
    """
    return await PipelineService(repository).recompile_project_shots(project_id)


async def _recompile_project_shots_with_prompt_hashes(
    project_id: str, repository: StudioRepository
) -> tuple[list[ShotRecord], dict[str, frozenset[str]]]:
    """Like ``_recompile_project_shots`` and also return the prompt hashes per shot id
    that a selected take may carry and still be current (new + computed legacy hash)."""
    return await PipelineService(repository).recompile_project_shots_with_prompt_hashes(project_id)


@router.post("/scripts/import", response_model=ScriptImportOut, status_code=status.HTTP_201_CREATED)
async def import_and_analyze_script(
    project_id: str,
    payload: ScriptImportIn,
    service: PipelineService = Depends(get_pipeline_service),
    actor: str = Depends(verify_single_admin),
) -> ScriptImportOut:
    result = await service.import_script(
        project_id,
        raw_content=payload.raw_content,
        source_format=payload.source_format,
        auto_apply_proposal=payload.auto_apply_proposal,
        replace_existing=payload.replace_existing,
    )
    return ScriptImportOut(
        script_revision_id=result.revision.id,
        version_number=result.revision.version_number,
        raw_content_preserved=True,
        diff_summary=result.revision.diff_summary,
        detected_characters=len(result.proposal.characters),
        detected_locations=len(result.proposal.locations),
        detected_scenes=len(result.proposal.scenes),
        generated_shots=result.generated_shots,
        warnings=result.proposal.warnings,
    )


@router.get("/story-bible", response_model=list[StoryBibleEntryOut])
async def get_story_bible(project_id: str, repository: StudioRepository = Depends(get_studio_repository)) -> list[StoryBibleEntryOut]:
    if not await repository.get_project(project_id):
        raise HTTPException(status_code=404, detail="Project không tồn tại.")
    rows = await repository.list_story_bible_entries(project_id)
    return [
        {
            "id": r.id,
            "entry_type": r.entry_type,
            "code_name": r.code_name,
            "display_name": r.display_name,
            "immutable_traits": r.immutable_traits,
            "mutable_attributes": r.mutable_attributes,
            "reference_asset_ids": r.reference_asset_ids,
            "requires_reference_lock": r.requires_reference_lock,
        }
        for r in rows
    ]


@router.post("/story-bible", response_model=StoryBibleUpsertOut, status_code=status.HTTP_201_CREATED)
async def upsert_story_bible_entry(
    project_id: str,
    payload: BibleEntryCreateIn,
    service: PipelineService = Depends(get_pipeline_service),
    actor: str = Depends(verify_single_admin),
) -> StoryBibleUpsertOut:
    entry = await service.upsert_bible_entry(project_id, payload.model_dump())
    return {"id": entry.id, "code_name": entry.code_name, "status": "recompiled_downstream_shots"}


def _serialize_project_shot(s: ShotRecord) -> dict[str, Any]:
    return {
        "id": s.id,
        "scene_id": s.scene_id,
        "sequence_index": s.sequence_index,
        "title": s.title,
        "subject_codes": s.subject_codes,
        "location_code": s.location_code,
        "shot_size": s.shot_size,
        "camera_motion": s.camera_motion,
        "composition": s.composition,
        "action": s.action,
        "dialogue": s.dialogue,
        "duration_seconds": s.duration_seconds,
        "requires_reference_lock": s.requires_reference_lock,
        "reference_asset_ids": s.reference_asset_ids,
        "prompt_final": s.prompt_final,
        "negative_prompt": s.negative_prompt,
        "prompt_hash": s.prompt_hash,
        "lint_report": s.lint_report_json,
        "continuity_mode": s.continuity_mode,
        "previous_shot_id": s.previous_shot_id,
        "next_shot_id": s.next_shot_id,
        "first_frame_asset_id": s.first_frame_asset_id,
        "last_frame_asset_id": s.last_frame_asset_id,
        "selected_take_id": s.selected_take_id,
        "approval_state": s.approval_state,
        "generation_state": s.generation_state,
    }


@router.get("/shots", response_model=list[ProjectShotOut])
async def list_project_shots(
    project_id: str,
    repository: StudioRepository = Depends(get_studio_repository),
) -> list[ProjectShotOut]:
    """Return stored prompt/lint state without recompiling or committing any writes."""
    shots = await repository.list_project_shots(project_id)
    if shots is None:
        raise HTTPException(status_code=404, detail="Project không tồn tại.")
    return [_serialize_project_shot(shot) for shot in shots]


@router.patch("/shots/{shot_id}", response_model=ShotReviewOut)
async def update_and_review_shot(
    project_id: str,
    shot_id: str,
    payload: ShotUpdateIn,
    service: PipelineService = Depends(get_pipeline_service),
    actor: str = Depends(verify_single_admin),
) -> ShotReviewOut:
    shot = await service.update_shot(project_id, shot_id, payload.model_dump(exclude_unset=True))
    return {
        "id": shot.id,
        "prompt_final": shot.prompt_final,
        "prompt_hash": shot.prompt_hash,
        "lint_report": shot.lint_report_json,
        "approval_state": shot.approval_state,
        "generation_state": shot.generation_state,
    }


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: Any) -> Any:
    """Neutralise spreadsheet formula injection (CWE-1236) in exported text cells."""
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


@router.get(
    "/shots/export",
    responses={
        200: {
            "model": ProjectShotsExportOut,
            "description": "Download project shots as JSON or CSV.",
            "content": {"text/csv": {"schema": {"type": "string"}}},
        }
    },
)
async def export_shot_list(
    project_id: str,
    format: Literal["json", "csv"] = "json",
    repository: StudioRepository = Depends(get_studio_repository),
) -> Response:
    shot_records = await repository.list_project_shots(project_id)
    if shot_records is None:
        raise HTTPException(status_code=404, detail="Project không tồn tại.")
    shots = [_serialize_project_shot(shot) for shot in shot_records]
    if format.lower() == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "sequence_index", "shot_id", "title", "location_code", "subject_codes",
            "shot_size", "camera_motion", "duration_seconds", "approval_state",
            "generation_state", "prompt_hash", "prompt_final"
        ])
        for s in shots:
            writer.writerow([_csv_safe(cell) for cell in [
                s["sequence_index"], s["id"], s["title"], s["location_code"],
                "|".join(s["subject_codes"]), s["shot_size"], s["camera_motion"],
                s["duration_seconds"], s["approval_state"].value if hasattr(s["approval_state"], "value") else s["approval_state"],
                s["generation_state"].value if hasattr(s["generation_state"], "value") else s["generation_state"],
                s["prompt_hash"], s["prompt_final"]
            ]])
        return Response(
            content=buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{project_id}_shots.csv"'},
        )
    return Response(
        content=json.dumps({"project_id": project_id, "shots": shots}, ensure_ascii=False, indent=2),
        media_type="application/json",
    )
