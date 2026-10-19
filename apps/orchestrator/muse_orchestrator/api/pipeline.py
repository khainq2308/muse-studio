from __future__ import annotations
import csv
import io
import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from muse_orchestrator.api.dependencies import get_studio_repository
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.core.security import verify_single_admin
from muse_orchestrator.models.domain import (
    ScriptRevision, StoryBibleEntry,
    SceneRecord, ShotRecord, ShotApprovalState, ShotGenerationState
)
from muse_orchestrator.services.script_parser import ScriptAnalyzerService
from muse_orchestrator.services.prompt_compiler import (
    PromptCompiler, ShotSpec, StyleBibleLock, CharacterLock, LocationLock
)

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["M2 Script-to-Shot Pipeline"])


class ScriptImportIn(BaseModel):
    raw_content: str = Field(..., min_length=5)
    source_format: str = Field(default="museflow")
    auto_apply_proposal: bool = Field(default=True)


class BibleEntryCreateIn(BaseModel):
    entry_type: str = Field(..., pattern=r"^(character|location|style|prop)$")
    code_name: str = Field(..., min_length=2, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=255)
    immutable_traits: str = Field(default="")
    mutable_attributes: dict[str, Any] = Field(default_factory=dict)
    reference_asset_ids: list[str] = Field(default_factory=list)
    requires_reference_lock: bool = Field(default=False)


class ShotUpdateIn(BaseModel):
    shot_size: str | None = None
    camera_motion: str | None = None
    composition: str | None = None
    action: str | None = None
    duration_seconds: float | None = None
    continuity_mode: str | None = None
    previous_shot_id: str | None = None
    first_frame_asset_id: str | None = None
    requires_reference_lock: bool | None = None
    reference_asset_ids: list[str] | None = None
    approval_state: ShotApprovalState | None = None


async def _recompile_project_shots(project_id: str, repository: StudioRepository) -> list[ShotRecord]:
    """Biên dịch lại Prompt và chạy Continuity Linter cho toàn bộ Shot của Project."""
    proj = await repository.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project không tồn tại.")

    bible_rows = await repository.list_story_bible_entries(project_id)
    asset_rows = await repository.list_project_asset_ids(project_id)
    available_assets = set(asset_rows)

    characters: dict[str, CharacterLock] = {}
    locations: dict[str, LocationLock] = {}
    style_lock = StyleBibleLock(
        medium=proj.style_preset,
        art_direction="Cinematic Anime",
        color_palette="Balanced contrast",
        negative_constraints=["3d cgi", "deformed hands"],
    )

    for b in bible_rows:
        if b.entry_type == "character":
            req_asset = b.reference_asset_ids[0] if (b.requires_reference_lock and b.reference_asset_ids) else (
                "MISSING_REQUIRED_CHAR_REF" if b.requires_reference_lock else None
            )
            characters[b.code_name] = CharacterLock(
                character_id=b.code_name,
                name=b.display_name,
                immutable_traits=b.immutable_traits,
                current_outfit=str(b.mutable_attributes.get("current_outfit", "default outfit")),
                required_ref_asset_id=req_asset,
            )
        elif b.entry_type == "location":
            locations[b.code_name] = LocationLock(
                location_id=b.code_name,
                name=b.display_name,
                spatial_anchors=b.immutable_traits,
                lighting_rule=str(b.mutable_attributes.get("lighting_rule", "natural lighting")),
                time_of_day=str(b.mutable_attributes.get("time_of_day", "day")),
            )
        elif b.entry_type == "style":
            style_lock = StyleBibleLock(
                medium=b.display_name,
                art_direction=b.immutable_traits,
                color_palette=str(b.mutable_attributes.get("color_palette", "cinematic")),
                negative_constraints=list(b.mutable_attributes.get("negative_constraints", ["3d cgi"])),
                forbidden_terms=list(b.mutable_attributes.get("forbidden_terms", [])),
            )

    shots = await repository.list_project_shots(project_id)
    if shots is None:
        raise HTTPException(status_code=404, detail="Project không tồn tại.")

    previous_spec: ShotSpec | None = None
    for s in shots:
        spec = ShotSpec(
            shot_id=s.id,
            scene_id=s.scene_id,
            sequence_index=s.sequence_index,
            subject_ids=list(s.subject_codes or []),
            location_id=s.location_code,
            shot_size=s.shot_size,
            camera_angle=s.camera_angle,
            camera_motion=s.camera_motion,
            composition=s.composition,
            action=s.action,
            action_start=s.action_start,
            action_end=s.action_end,
            continuity_notes=s.continuity_notes,
            duration_seconds=s.duration_seconds,
            aspect_ratio=s.aspect_ratio,
            requires_reference_lock=s.requires_reference_lock,
            reference_asset_ids=list(s.reference_asset_ids or []),
        )
        compiled = PromptCompiler.compile_and_lint(
            shot=spec,
            style=style_lock,
            characters=characters,
            locations=locations,
            available_assets=available_assets,
            previous_shot=previous_spec,
        )
        previous_spec = spec
        s.prompt_final = compiled.prompt_final
        s.negative_prompt = compiled.negative_prompt
        s.prompt_hash = compiled.prompt_hash
        s.lint_report_json = [
            {"code": i.code, "severity": i.severity, "message": i.message, "field": i.field}
            for i in compiled.lint_issues
        ]
        if any(i.code == "BLOCKED_REFERENCE" for i in compiled.lint_issues):
            s.generation_state = ShotGenerationState.BLOCKED_REFERENCE
        elif s.generation_state == ShotGenerationState.BLOCKED_REFERENCE:
            s.generation_state = ShotGenerationState.NOT_STARTED

    await repository.commit_pipeline_changes()
    return list(shots)


@router.post("/scripts/import", status_code=status.HTTP_201_CREATED)
async def import_and_analyze_script(
    project_id: str,
    payload: ScriptImportIn,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    proj = await repository.get_project(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="Project không tồn tại.")

    existing_revs = await repository.list_script_revisions(project_id)
    next_ver = len(existing_revs) + 1

    proposal = ScriptAnalyzerService.analyze_script(payload.raw_content, default_style=proj.style_preset)
    proposal_dict = {
        "characters": proposal.characters,
        "locations": proposal.locations,
        "style": proposal.style,
        "scenes": proposal.scenes,
        "shots": proposal.shots,
    }

    script_rev = ScriptRevision(
        id=f"scr_{uuid.uuid4().hex[:10]}",
        project_id=project_id,
        version_number=next_ver,
        source_format=payload.source_format,
        raw_content=payload.raw_content,  # Giữ nguyên vẹn 100% kịch bản gốc
        structured_proposal_json=proposal_dict,
        diff_summary=proposal.diff_summary,
        is_approved=payload.auto_apply_proposal,
    )
    repository.stage_pipeline_entity(script_rev)

    if payload.auto_apply_proposal:
        await repository.delete_project_pipeline_records(project_id)

        # 1. Lưu Style, Characters, Locations vào Story Bible
        style_data = proposal.style
        repository.stage_pipeline_entity(StoryBibleEntry(
            id=f"bib_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            entry_type="style",
            code_name=style_data["code_name"],
            display_name=style_data["display_name"],
            immutable_traits=style_data["immutable_traits"],
            mutable_attributes=style_data["mutable_attributes"],
        ))
        for ch in proposal.characters:
            repository.stage_pipeline_entity(StoryBibleEntry(
                id=f"bib_{uuid.uuid4().hex[:8]}",
                project_id=project_id,
                entry_type="character",
                code_name=ch["code_name"],
                display_name=ch["display_name"],
                immutable_traits=ch["immutable_traits"],
                mutable_attributes=ch["mutable_attributes"],
            ))
        for loc in proposal.locations:
            repository.stage_pipeline_entity(StoryBibleEntry(
                id=f"bib_{uuid.uuid4().hex[:8]}",
                project_id=project_id,
                entry_type="location",
                code_name=loc["code_name"],
                display_name=loc["display_name"],
                immutable_traits=loc["immutable_traits"],
                mutable_attributes=loc["mutable_attributes"],
            ))

        # 2. Lưu Scenes & Shots
        scene_id_by_idx: dict[int, str] = {}
        for sc in proposal.scenes:
            sc_id = f"scn_{uuid.uuid4().hex[:8]}"
            scene_id_by_idx[sc["scene_index"]] = sc_id
            repository.stage_pipeline_entity(SceneRecord(
                id=sc_id,
                project_id=project_id,
                scene_index=sc["scene_index"],
                title=sc["title"],
                location_code=sc["location_code"],
                time_of_day=sc["time_of_day"],
                lighting_mood=sc["lighting_mood"],
                summary=sc["summary"],
            ))

        created_shot_records: list[ShotRecord] = []
        for sh in proposal.shots:
            sc_id = scene_id_by_idx.get(sh["scene_index"]) or next(iter(scene_id_by_idx.values()))
            prev_id = created_shot_records[-1].id if (created_shot_records and created_shot_records[-1].scene_id == sc_id) else None
            new_shot = ShotRecord(
                id=f"sht_{uuid.uuid4().hex[:8]}",
                project_id=project_id,
                scene_id=sc_id,
                previous_shot_id=prev_id,
                sequence_index=sh["sequence_index"],
                title=sh["title"],
                story_beat=sh["story_beat"],
                subject_codes=sh["subject_codes"],
                location_code=sh["location_code"],
                shot_size=sh["shot_size"],
                camera_angle=sh["camera_angle"],
                camera_motion=sh["camera_motion"],
                composition=sh["composition"],
                action=sh["action"],
                action_start=sh["action_start"],
                action_end=sh["action_end"],
                dialogue=sh["dialogue"],
                continuity_notes=sh["continuity_notes"],
                duration_seconds=sh["duration_seconds"],
                aspect_ratio=proj.aspect_ratio,
            )
            if created_shot_records and created_shot_records[-1].scene_id == sc_id:
                created_shot_records[-1].next_shot_id = new_shot.id
            created_shot_records.append(new_shot)
            repository.stage_pipeline_entity(new_shot)

        await repository.commit_pipeline_changes()
        shots = await _recompile_project_shots(project_id, repository)
    else:
        await repository.commit_pipeline_changes()
        shots = []

    return {
        "script_revision_id": script_rev.id,
        "version_number": script_rev.version_number,
        "raw_content_preserved": True,
        "diff_summary": script_rev.diff_summary,
        "detected_characters": len(proposal.characters),
        "detected_locations": len(proposal.locations),
        "detected_scenes": len(proposal.scenes),
        "generated_shots": len(shots),
    }


@router.get("/story-bible")
async def get_story_bible(project_id: str, repository: StudioRepository = Depends(get_studio_repository)) -> list[dict[str, Any]]:
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


@router.post("/story-bible", status_code=status.HTTP_201_CREATED)
async def upsert_story_bible_entry(
    project_id: str,
    payload: BibleEntryCreateIn,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    existing = await repository.get_story_bible_entry(project_id, payload.code_name)

    if existing:
        existing.display_name = payload.display_name
        existing.immutable_traits = payload.immutable_traits
        existing.mutable_attributes = payload.mutable_attributes
        existing.reference_asset_ids = payload.reference_asset_ids
        existing.requires_reference_lock = payload.requires_reference_lock
        entry = existing
    else:
        entry = StoryBibleEntry(
            id=f"bib_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            entry_type=payload.entry_type,
            code_name=payload.code_name,
            display_name=payload.display_name,
            immutable_traits=payload.immutable_traits,
            mutable_attributes=payload.mutable_attributes,
            reference_asset_ids=payload.reference_asset_ids,
            requires_reference_lock=payload.requires_reference_lock,
        )
        repository.stage_pipeline_entity(entry)

    await repository.commit_pipeline_changes()
    await _recompile_project_shots(project_id, repository)
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


@router.get("/shots")
async def list_project_shots(
    project_id: str,
    repository: StudioRepository = Depends(get_studio_repository),
) -> list[dict[str, Any]]:
    """Return stored prompt/lint state without recompiling or committing any writes."""
    shots = await repository.list_project_shots(project_id)
    if shots is None:
        raise HTTPException(status_code=404, detail="Project không tồn tại.")
    return [_serialize_project_shot(shot) for shot in shots]


@router.patch("/shots/{shot_id}")
async def update_and_review_shot(
    project_id: str,
    shot_id: str,
    payload: ShotUpdateIn,
    repository: StudioRepository = Depends(get_studio_repository),
    actor: str = Depends(verify_single_admin),
) -> dict[str, Any]:
    shot = await repository.get_shot(shot_id)
    if not shot or shot.project_id != project_id:
        raise HTTPException(status_code=404, detail="Không tìm thấy Shot trong dự án.")

    for field_name, val in payload.model_dump(exclude_unset=True).items():
        setattr(shot, field_name, val)

    await repository.commit_pipeline_changes()
    await _recompile_project_shots(project_id, repository)
    await repository.refresh_pipeline_entity(shot)

    # Chặn duyệt (approve) nếu Shot vẫn còn lỗi Blocker trong Prompt Linter
    if shot.approval_state == ShotApprovalState.APPROVED:
        blockers = [i for i in (shot.lint_report_json or []) if i.get("severity") == "blocker"]
        if blockers:
            shot.approval_state = ShotApprovalState.NEEDS_REVIEW
            await repository.commit_pipeline_changes()
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Không thể Approve Shot khi còn lỗi Blocker: {blockers[0]['code']} - {blockers[0]['message']}",
            )

    return {
        "id": shot.id,
        "prompt_final": shot.prompt_final,
        "prompt_hash": shot.prompt_hash,
        "lint_report": shot.lint_report_json,
        "approval_state": shot.approval_state,
        "generation_state": shot.generation_state,
    }


@router.get("/shots/export")
async def export_shot_list(
    project_id: str,
    format: str = "json",
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
            writer.writerow([
                s["sequence_index"], s["id"], s["title"], s["location_code"],
                "|".join(s["subject_codes"]), s["shot_size"], s["camera_motion"],
                s["duration_seconds"], s["approval_state"].value if hasattr(s["approval_state"], "value") else s["approval_state"],
                s["generation_state"].value if hasattr(s["generation_state"], "value") else s["generation_state"],
                s["prompt_hash"], s["prompt_final"]
            ])
        return Response(
            content=buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{project_id}_shots.csv"'},
        )
    import json
    return Response(
        content=json.dumps({"project_id": project_id, "shots": shots}, ensure_ascii=False, indent=2),
        media_type="application/json",
    )
