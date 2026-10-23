"""Script-to-shot pipeline use cases (M2): script import, Story Bible, shot review.

All mutations follow one rule: stage changes, flush, recompile prompts/lint in the SAME
transaction, then commit once. A failure anywhere leaves the database untouched.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from muse_orchestrator.application.services import ApplicationError
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.models.domain import (
    GenerationDispatchLeaseRecord,
    GenerationTaskRecord,
    Project,
    ACTIVE_GENERATION_TASK_STATES,
    SceneRecord,
    ScriptRevision,
    ShotApprovalState,
    ShotGenerationState,
    ShotRecord,
    ShotTakeRecord,
    StoryBibleEntry,
)
from muse_orchestrator.services.prompt_compiler import (
    CharacterLock,
    LocationLock,
    PromptCompiler,
    ShotSpec,
    StyleBibleLock,
)
from muse_orchestrator.services.generation_guards import prompt_version_of
from muse_orchestrator.services.script_parser import (
    ParsedScriptProposal,
    ScriptAnalyzerService,
    ScriptParseError,
)

# Tasks in these states still own the shot: recompiles must not rewrite its state and
# destructive re-imports must not delete it.
ACTIVE_TASK_STATES = frozenset(ACTIVE_GENERATION_TASK_STATES)
_DEFAULT_NEGATIVE_CONSTRAINTS = ["3d cgi"]


def _string_list(value: Any, default: list[str]) -> list[str]:
    """Coerce legacy/invalid bible data so one bad row can never 500 every recompile."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return list(default)


def _string_value(value: Any, default: str) -> str:
    return value if isinstance(value, str) and value.strip() else default


@dataclass(frozen=True)
class ShotPromptHashes:
    """The current prompt hash of a shot and its computed (never persisted) legacy hash.

    ``legacy`` is the hash the shot had before informative action_start/action_end entered
    the prompt; it equals ``current`` when the shot has no informative boundaries.
    """

    current: str
    legacy: str

    def take_is_current(self, take_prompt_hash: str, task: GenerationTaskRecord | None) -> bool:
        """Whether a selected take still matches this shot's prompt.

        The legacy hash is accepted only for a take whose task predates the prompt
        version marker (enqueued before the upgrade). A take rendered afterwards on a shot
        with empty/generic boundaries also carries the legacy-equal hash; accepting it
        would hide a later boundary edit forever.
        """
        if take_prompt_hash == self.current:
            return True
        return (
            take_prompt_hash == self.legacy
            and task is not None
            and prompt_version_of(task) is None
        )


@dataclass
class ScriptImportResult:
    revision: ScriptRevision
    proposal: ParsedScriptProposal
    generated_shots: int


class PipelineService:
    def __init__(self, repository: StudioRepository) -> None:
        self.repository = repository

    # --- shared helpers -------------------------------------------------------------

    async def require_project(self, project_id: str) -> Project:
        project = await self.repository.get_project(project_id)
        if project is None:
            raise ApplicationError(404, "Project không tồn tại.")
        return project

    async def _lock_project(self, project_id: str) -> None:
        """Serialise allocators on the parent row (no-op on SQLite, row lock on PostgreSQL)."""
        await self.repository.session.execute(
            select(Project.id).where(Project.id == project_id).with_for_update()
        )

    async def active_task_shot_ids(self, project_id: str) -> set[str]:
        tasks = await self.repository.list_project_generation_tasks(project_id)
        return {task.shot_id for task in tasks if task.state in ACTIVE_TASK_STATES}

    async def count_project_takes(self, project_id: str) -> int:
        result = await self.repository.session.execute(
            select(func.count(ShotTakeRecord.id)).where(ShotTakeRecord.project_id == project_id)
        )
        return int(result.scalar_one())

    async def validate_project_asset_references(
        self, project_id: str, asset_ids: list[str] | None
    ) -> None:
        requested_ids = set(asset_ids or [])
        if not requested_ids:
            return
        project_asset_ids = set(await self.repository.list_project_asset_ids(project_id))
        if not requested_ids.issubset(project_asset_ids):
            raise ApplicationError(422, "Mọi reference_asset_ids phải thuộc project đang thao tác.")

    # --- prompt compilation ---------------------------------------------------------

    async def recompile_project_shots(self, project_id: str, *, commit: bool = True) -> list[ShotRecord]:
        """Recompile prompts/lint state and (optionally) commit the current transaction."""
        shots, _hashes = await self.recompile_project_shots_with_prompt_hashes(project_id, commit=commit)
        return shots

    async def recompile_project_shots_with_prompt_hashes(
        self, project_id: str, *, commit: bool = True
    ) -> tuple[list[ShotRecord], dict[str, ShotPromptHashes]]:
        """Recompile like ``recompile_project_shots`` and return, per shot id, the current
        and computed legacy prompt hash (see ``ShotPromptHashes.take_is_current``), so
        takes rendered before boundaries entered the prompt are not all invalidated.
        """
        result = await self._compile(project_id)
        assert isinstance(result, tuple)
        if commit:
            await self.repository.commit_pipeline_changes()
        return result

    async def dry_run_shot_lint(
        self, project_id: str, shot_id: str, overrides: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Compile the would-be state of one shot without mutating anything."""
        result = await self._compile(project_id, dry_run_shot_id=shot_id, dry_run_overrides=overrides)
        assert isinstance(result, list)
        return result

    async def _compile(
        self,
        project_id: str,
        *,
        dry_run_shot_id: str | None = None,
        dry_run_overrides: dict[str, Any] | None = None,
    ) -> tuple[list[ShotRecord], dict[str, ShotPromptHashes]] | list[dict[str, Any]]:
        proj = await self.require_project(project_id)
        bible_rows = await self.repository.list_story_bible_entries(project_id)
        available_assets = set(await self.repository.list_project_asset_ids(project_id))

        characters: dict[str, CharacterLock] = {}
        locations: dict[str, LocationLock] = {}
        style_lock = StyleBibleLock(
            medium=proj.style_preset,
            art_direction="Cinematic Anime",
            color_palette="Balanced contrast",
            negative_constraints=["3d cgi", "deformed hands"],
        )
        for b in sorted(bible_rows, key=lambda row: (row.entry_type, row.code_name)):
            attrs = b.mutable_attributes if isinstance(b.mutable_attributes, dict) else {}
            if b.entry_type == "character":
                req_asset = b.reference_asset_ids[0] if (b.requires_reference_lock and b.reference_asset_ids) else (
                    "MISSING_REQUIRED_CHAR_REF" if b.requires_reference_lock else None
                )
                characters[b.code_name] = CharacterLock(
                    character_id=b.code_name,
                    name=b.display_name,
                    immutable_traits=b.immutable_traits,
                    current_outfit=str(attrs.get("current_outfit", "default outfit")),
                    required_ref_asset_id=req_asset,
                )
            elif b.entry_type == "location":
                locations[b.code_name] = LocationLock(
                    location_id=b.code_name,
                    name=b.display_name,
                    spatial_anchors=b.immutable_traits,
                    lighting_rule=str(attrs.get("lighting_rule", "natural lighting")),
                    time_of_day=str(attrs.get("time_of_day", "day")),
                )
            elif b.entry_type == "style":
                style_lock = StyleBibleLock(
                    medium=b.display_name,
                    art_direction=b.immutable_traits,
                    color_palette=_string_value(attrs.get("color_palette"), "cinematic"),
                    negative_constraints=_string_list(
                        attrs.get("negative_constraints", _DEFAULT_NEGATIVE_CONSTRAINTS),
                        _DEFAULT_NEGATIVE_CONSTRAINTS,
                    ),
                    forbidden_terms=_string_list(attrs.get("forbidden_terms", []), []),
                )

        shots = await self.repository.list_project_shots(project_id)
        if shots is None:
            raise ApplicationError(404, "Project không tồn tại.")
        busy_shot_ids = set() if dry_run_overrides is not None else await self.active_task_shot_ids(project_id)

        previous_spec: ShotSpec | None = None
        dry_run_lint: list[dict[str, Any]] = []
        prompt_hashes: dict[str, ShotPromptHashes] = {}
        for s in shots:
            values: dict[str, Any] = {
                "subject_codes": s.subject_codes,
                "location_code": s.location_code,
                "shot_size": s.shot_size,
                "camera_angle": s.camera_angle,
                "camera_motion": s.camera_motion,
                "composition": s.composition,
                "action": s.action,
                "action_start": s.action_start,
                "action_end": s.action_end,
                "continuity_notes": s.continuity_notes,
                "duration_seconds": s.duration_seconds,
                "aspect_ratio": s.aspect_ratio,
                "requires_reference_lock": s.requires_reference_lock,
                "reference_asset_ids": s.reference_asset_ids,
            }
            if dry_run_overrides is not None and s.id == dry_run_shot_id:
                values.update({k: v for k, v in dry_run_overrides.items() if k in values})
            spec = ShotSpec(
                shot_id=s.id,
                scene_id=s.scene_id,
                sequence_index=s.sequence_index,
                subject_ids=list(values["subject_codes"] or []),
                location_id=values["location_code"],
                shot_size=values["shot_size"],
                camera_angle=values["camera_angle"],
                camera_motion=values["camera_motion"],
                composition=values["composition"],
                action=values["action"],
                action_start=values["action_start"],
                action_end=values["action_end"],
                continuity_notes=values["continuity_notes"],
                duration_seconds=values["duration_seconds"],
                aspect_ratio=values["aspect_ratio"],
                requires_reference_lock=values["requires_reference_lock"],
                reference_asset_ids=list(values["reference_asset_ids"] or []),
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
            lint = [
                {"code": i.code, "severity": i.severity, "message": i.message, "field": i.field}
                for i in compiled.lint_issues
            ]
            if dry_run_overrides is not None:
                if s.id == dry_run_shot_id:
                    dry_run_lint = lint
                continue
            s.prompt_final = compiled.prompt_final
            s.negative_prompt = compiled.negative_prompt
            s.prompt_hash = compiled.prompt_hash
            prompt_hashes[s.id] = ShotPromptHashes(
                current=compiled.prompt_hash, legacy=compiled.legacy_prompt_hash
            )
            s.lint_report_json = lint
            if s.id in busy_shot_ids:
                # A queued/running/paused task owns the generation state; the queue and
                # its guards re-check lint before dispatch.
                continue
            if any(i.code == "BLOCKED_REFERENCE" for i in compiled.lint_issues):
                s.generation_state = ShotGenerationState.BLOCKED_REFERENCE
            elif s.generation_state == ShotGenerationState.BLOCKED_UPSTREAM_FRAME and not (
                s.continuity_mode == "frame_chain" and s.previous_shot_id
            ):
                # The shot no longer waits on an upstream frame (continuity changed).
                s.generation_state = (
                    ShotGenerationState.SUCCEEDED
                    if s.selected_take_id
                    else ShotGenerationState.NOT_STARTED
                )
            elif s.generation_state == ShotGenerationState.BLOCKED_REFERENCE and not compiled.has_blockers:
                # Only unblock once no linter blocker remains; restore SUCCEEDED when the
                # shot still has a selected take instead of discarding finished work.
                s.generation_state = (
                    ShotGenerationState.SUCCEEDED
                    if s.selected_take_id
                    else ShotGenerationState.NOT_STARTED
                )

        if dry_run_overrides is not None:
            return dry_run_lint
        return (list(shots), prompt_hashes)

    # --- script import --------------------------------------------------------------

    async def import_script(
        self,
        project_id: str,
        *,
        raw_content: str,
        source_format: str,
        auto_apply_proposal: bool,
        replace_existing: bool,
    ) -> ScriptImportResult:
        proj = await self.require_project(project_id)
        try:
            # CPU-bound parsing must not block the event loop.
            proposal = await asyncio.to_thread(
                ScriptAnalyzerService.analyze_script, raw_content, default_style=proj.style_preset
            )
        except ScriptParseError as exc:
            raise ApplicationError(422, str(exc)) from exc

        if auto_apply_proposal:
            await self._guard_destructive_reimport(project_id, replace_existing)

        try:
            await self._lock_project(project_id)
            if auto_apply_proposal:
                # Enqueue serialises on the shot row, not the project row: lock every shot
                # of the project, then re-check under those locks so a task created after
                # the early guard above is either seen here or cannot be inserted.
                await self.repository.session.execute(
                    select(ShotRecord.id).where(ShotRecord.project_id == project_id).with_for_update()
                )
                try:
                    await self._guard_destructive_reimport(project_id, replace_existing)
                except ApplicationError:
                    await self.repository.session.rollback()
                    raise
            max_version = (
                await self.repository.session.execute(
                    select(func.max(ScriptRevision.version_number)).where(
                        ScriptRevision.project_id == project_id
                    )
                )
            ).scalar_one()
            script_rev = ScriptRevision(
                id=f"scr_{uuid.uuid4().hex[:10]}",
                project_id=project_id,
                version_number=int(max_version or 0) + 1,
                source_format=source_format,
                raw_content=raw_content,  # Giữ nguyên vẹn 100% kịch bản gốc
                structured_proposal_json={
                    "characters": proposal.characters,
                    "locations": proposal.locations,
                    "style": proposal.style,
                    "scenes": proposal.scenes,
                    "shots": proposal.shots,
                    "warnings": proposal.warnings,
                },
                diff_summary=proposal.diff_summary,
                is_approved=auto_apply_proposal,
            )
            self.repository.stage_pipeline_entity(script_rev)

            generated = 0
            if auto_apply_proposal:
                await self._replace_pipeline_records(project_id, replace_existing)
                generated = await self._stage_proposal(project_id, proj, proposal)
                await self.repository.session.flush()
                await self.recompile_project_shots(project_id, commit=False)
            await self.repository.commit_pipeline_changes()
        except IntegrityError as exc:
            await self.repository.session.rollback()
            raise ApplicationError(
                409,
                "The script was modified concurrently; retry the import.",
                category="conflict",
                retryable=True,
            ) from exc
        return ScriptImportResult(revision=script_rev, proposal=proposal, generated_shots=generated)

    async def _guard_destructive_reimport(self, project_id: str, replace_existing: bool) -> None:
        if await self.active_task_shot_ids(project_id):
            raise ApplicationError(
                409,
                "Project has queued/running/paused generation tasks; cancel or finish them "
                "before re-applying a script.",
            )
        if not replace_existing and await self.count_project_takes(project_id):
            raise ApplicationError(
                409,
                "Re-applying a script would delete every existing shot together with its "
                "generated takes. Retry with replace_existing=true to confirm.",
            )

    async def _replace_pipeline_records(self, project_id: str, replace_existing: bool) -> None:
        session = self.repository.session
        if replace_existing:
            # PostgreSQL cascades these from shots; delete explicitly so every backend
            # ends in the same state. Active tasks were refused above.
            task_ids = select(GenerationTaskRecord.id).where(GenerationTaskRecord.project_id == project_id)
            await session.execute(delete(ShotTakeRecord).where(ShotTakeRecord.project_id == project_id))
            await session.execute(
                delete(GenerationDispatchLeaseRecord).where(GenerationDispatchLeaseRecord.task_id.in_(task_ids))
            )
            await session.execute(delete(GenerationTaskRecord).where(GenerationTaskRecord.project_id == project_id))
        await self.repository.delete_project_pipeline_records(project_id)

    async def _stage_proposal(self, project_id: str, proj: Project, proposal: ParsedScriptProposal) -> int:
        stage = self.repository.stage_pipeline_entity
        style_data = proposal.style
        stage(StoryBibleEntry(
            id=f"bib_{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            entry_type="style",
            code_name=style_data["code_name"],
            display_name=style_data["display_name"],
            immutable_traits=style_data["immutable_traits"],
            mutable_attributes=style_data["mutable_attributes"],
        ))
        for entry_type, rows in (("character", proposal.characters), ("location", proposal.locations)):
            for row in rows:
                stage(StoryBibleEntry(
                    id=f"bib_{uuid.uuid4().hex[:8]}",
                    project_id=project_id,
                    entry_type=entry_type,
                    code_name=row["code_name"],
                    display_name=row["display_name"],
                    immutable_traits=row["immutable_traits"],
                    mutable_attributes=row["mutable_attributes"],
                ))

        scene_id_by_idx: dict[int, str] = {}
        for sc in proposal.scenes:
            sc_id = f"scn_{uuid.uuid4().hex[:8]}"
            scene_id_by_idx[sc["scene_index"]] = sc_id
            stage(SceneRecord(
                id=sc_id,
                project_id=project_id,
                scene_index=sc["scene_index"],
                title=sc["title"][:255],
                location_code=sc["location_code"],
                time_of_day=sc["time_of_day"],
                lighting_mood=sc["lighting_mood"],
                summary=sc["summary"],
            ))

        # No relationship(): insert scenes before the shots that reference them.
        await self.repository.flush_changes()

        created: list[ShotRecord] = []
        for sh in proposal.shots:
            sc_id = scene_id_by_idx[sh["scene_index"]]
            prev = created[-1] if created and created[-1].scene_id == sc_id else None
            new_shot = ShotRecord(
                id=f"sht_{uuid.uuid4().hex[:8]}",
                project_id=project_id,
                scene_id=sc_id,
                previous_shot_id=prev.id if prev else None,
                sequence_index=sh["sequence_index"],
                title=sh["title"][:255],
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
            if prev is not None:
                prev.next_shot_id = new_shot.id
            created.append(new_shot)
            stage(new_shot)
        return len(created)

    # --- story bible ----------------------------------------------------------------

    async def upsert_bible_entry(self, project_id: str, values: dict[str, Any]) -> StoryBibleEntry:
        """Create or update one entry by (project_id, code_name) and recompile atomically.

        A concurrent create of the same code_name loses on the unique constraint; the
        operation is then retried once as an update.
        """
        for attempt in range(2):
            await self.require_project(project_id)
            await self.validate_project_asset_references(project_id, values.get("reference_asset_ids"))
            existing = await self.repository.get_story_bible_entry(project_id, values["code_name"])
            if existing is not None:
                if existing.entry_type != values["entry_type"]:
                    raise ApplicationError(
                        409,
                        f"code_name '{values['code_name']}' already belongs to a "
                        f"'{existing.entry_type}' entry.",
                    )
                existing.display_name = values["display_name"]
                existing.immutable_traits = values["immutable_traits"]
                existing.mutable_attributes = values["mutable_attributes"]
                existing.reference_asset_ids = values["reference_asset_ids"]
                existing.requires_reference_lock = values["requires_reference_lock"]
                entry = existing
            else:
                entry = StoryBibleEntry(
                    id=f"bib_{uuid.uuid4().hex[:8]}",
                    project_id=project_id,
                    entry_type=values["entry_type"],
                    code_name=values["code_name"],
                    display_name=values["display_name"],
                    immutable_traits=values["immutable_traits"],
                    mutable_attributes=values["mutable_attributes"],
                    reference_asset_ids=values["reference_asset_ids"],
                    requires_reference_lock=values["requires_reference_lock"],
                )
                self.repository.stage_pipeline_entity(entry)
            try:
                await self.repository.session.flush()
                await self.recompile_project_shots(project_id, commit=False)
                await self.repository.commit_pipeline_changes()
                return entry
            except IntegrityError as exc:
                await self.repository.session.rollback()
                if attempt == 1:
                    raise ApplicationError(
                        409,
                        "Story Bible entry was modified concurrently; retry.",
                        category="conflict",
                        retryable=True,
                    ) from exc
        raise AssertionError("unreachable")  # pragma: no cover

    # --- shot review ----------------------------------------------------------------

    async def update_shot(self, project_id: str, shot_id: str, updates: dict[str, Any]) -> ShotRecord:
        repo = self.repository
        shot = await repo.get_shot(shot_id)
        if not shot or shot.project_id != project_id:
            raise ApplicationError(404, "Không tìm thấy Shot trong dự án.")

        nullable_fields = {"previous_shot_id", "first_frame_asset_id"}
        invalid_null_fields = [k for k, v in updates.items() if v is None and k not in nullable_fields]
        if invalid_null_fields:
            raise ApplicationError(422, f"Trường '{invalid_null_fields[0]}' không được phép là null.")

        new_prev: ShotRecord | None = None
        old_prev_id = shot.previous_shot_id
        relink = "previous_shot_id" in updates and updates["previous_shot_id"] != old_prev_id
        if relink and updates["previous_shot_id"] is not None:
            new_prev = await self._validate_new_predecessor(shot, updates["previous_shot_id"])
        if updates.get("reference_asset_ids") is not None:
            await self.validate_project_asset_references(project_id, updates["reference_asset_ids"])
        if updates.get("first_frame_asset_id") is not None:
            ff_asset = await repo.get_asset(updates["first_frame_asset_id"])
            if not ff_asset or ff_asset.project_id != project_id:
                raise ApplicationError(422, "first_frame_asset_id không thuộc dự án này.")

        # C1: first_frame_asset_id is only a conditioning input. Changing the continuity
        # source invalidates it unless the operator sets a new one in the same request.
        continuity_changed = relink or (
            "continuity_mode" in updates and updates["continuity_mode"] != shot.continuity_mode
        )
        if continuity_changed and "first_frame_asset_id" not in updates:
            updates = {**updates, "first_frame_asset_id": None}

        # Reject approval on a dry-run compile BEFORE any field is assigned or committed.
        would_be_state = updates.get("approval_state", shot.approval_state)
        if would_be_state == ShotApprovalState.APPROVED:
            lint = await self.dry_run_shot_lint(project_id, shot_id, updates)
            blockers = [i for i in lint if i.get("severity") == "blocker"]
            if blockers:
                raise ApplicationError(
                    422,
                    f"Không thể Approve Shot khi còn lỗi Blocker: {blockers[0]['code']} - {blockers[0]['message']}",
                )

        if relink:
            if old_prev_id is not None:
                old_prev = await repo.get_shot(old_prev_id)
                if old_prev is not None and old_prev.next_shot_id == shot.id:
                    old_prev.next_shot_id = None
            if new_prev is not None:
                new_prev.next_shot_id = shot.id

        for field_name, val in updates.items():
            setattr(shot, field_name, val)

        await repo.session.flush()
        await self.recompile_project_shots(project_id, commit=False)
        await repo.commit_pipeline_changes()
        await repo.refresh_pipeline_entity(shot)
        return shot

    async def _validate_new_predecessor(self, shot: ShotRecord, prev_id: str) -> ShotRecord:
        repo = self.repository
        if prev_id == shot.id:
            raise ApplicationError(422, "Shot không thể trỏ previous_shot_id vào chính nó.")
        prev_shot = await repo.get_shot(prev_id)
        if not prev_shot or prev_shot.project_id != shot.project_id:
            raise ApplicationError(422, "previous_shot_id không thuộc dự án này.")
        # Walk the predecessor chain: linking to any shot that (transitively) points back
        # to this shot would create a continuity cycle.
        visited: set[str] = set()
        cursor: ShotRecord | None = prev_shot
        while cursor is not None and cursor.previous_shot_id is not None:
            if cursor.previous_shot_id == shot.id:
                raise ApplicationError(422, "previous_shot_id tạo thành vòng lặp continuity giữa các Shot.")
            if cursor.previous_shot_id in visited:
                break
            visited.add(cursor.previous_shot_id)
            cursor = await repo.get_shot(cursor.previous_shot_id)
        if prev_shot.scene_id != shot.scene_id:
            raise ApplicationError(422, "previous_shot_id phải thuộc cùng Scene với Shot này.")
        if prev_shot.next_shot_id is not None and prev_shot.next_shot_id != shot.id:
            successor = await repo.get_shot(prev_shot.next_shot_id)
            # A stale next_shot_id (successor gone or relinked) may be overwritten.
            if successor is not None and successor.previous_shot_id == prev_shot.id:
                raise ApplicationError(
                    409,
                    f"Shot '{prev_shot.id}' đã có shot kế tiếp '{successor.id}'; gỡ liên kết đó trước.",
                )
        return prev_shot
