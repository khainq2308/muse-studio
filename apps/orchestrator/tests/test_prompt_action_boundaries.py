"""Shot action boundaries (action_start/action_end) in the compiled video prompt.

Informative boundaries entered the prompt and its hash; generic placeholders such as the
script parser's "in position" / "completes motion" and dialogue never do. Takes rendered before
that change carry the legacy hash, which batch ``only_changed`` still accepts.
"""
# The `client` fixture is imported from test_queue_review2; test parameters reuse its name.
# ruff: noqa: F811
from __future__ import annotations

import hashlib

from httpx import AsyncClient

from muse_orchestrator.application.pipeline_service import PipelineService
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.models.domain import GenerationTaskRecord, ShotRecord, ShotTakeRecord
from tests.test_queue_review2 import (  # noqa: F401 - `client` is a pytest fixture
    STATE,
    _setup,
    _shots,
    _tasks_for_shot,
    _ticks,
    client,
)

BOUNDARY_SCRIPT = """
SCENE 1: Rooftop
SHOT 1: Hero waits
- Action: Hero watches the street below
- Action_start: Hero crouches at the ledge
- Action_end: Hero stands and turns away
- Dialogue: We move at midnight.
"""


async def _render_first_shot(client: AsyncClient) -> tuple[str, str, str]:
    pid, cid, s1, _ = await _setup(client)
    # The imported defaults are generic (not in the prompt); give the shot real boundaries.
    async with STATE["maker"]() as db:
        shot = await db.get(ShotRecord, s1["id"])
        shot.action_start = "Hero crouches at the gap edge"
        shot.action_end = "Hero lands on the far roof"
        await db.commit()
    await client.post(f"/api/v1/projects/{pid}/batch/launch", json={"connection_id": cid, "shot_ids": [s1["id"]]})
    await _ticks(client, pid)
    assert (await _shots(client, pid))[s1["id"]]["generation_state"] == "succeeded"
    return pid, cid, s1["id"]


async def _legacy_hash(pid: str, shot_id: str) -> str:
    async with STATE["maker"]() as db:
        _shots_list, hashes = await PipelineService(StudioRepository(db)).recompile_project_shots_with_prompt_hashes(
            pid, commit=False
        )
        shot = await db.get(ShotRecord, shot_id)
        legacy = hashes[shot_id] - {shot.prompt_hash}
        await db.rollback()
    # The shot has real boundaries, so the new and legacy hashes differ.
    assert len(legacy) == 1
    return next(iter(legacy))


async def _make_selected_take_legacy(pid: str, shot_id: str) -> str:
    """Simulate a take (and its task) rendered before boundaries entered the prompt.

    The task's default idempotency key embeds the prompt hash, so it is rewritten with
    the legacy hash too; otherwise the queue's idempotent replay would mask the batch rule.
    """
    legacy = await _legacy_hash(pid, shot_id)
    async with STATE["maker"]() as db:
        shot = await db.get(ShotRecord, shot_id)
        take = await db.get(ShotTakeRecord, shot.selected_take_id)
        task = await db.get(GenerationTaskRecord, take.task_id)
        take.prompt_hash = legacy
        task.prompt_hash = legacy
        task.idempotency_key = hashlib.sha256(
            f"{shot.id}:{task.task_type}:{task.model_id}:{task.connection_id}:{legacy}:no_first_frame".encode()
        ).hexdigest()[:32]
        await db.commit()
    return legacy


async def _set_action_end(shot_id: str, value: str) -> None:
    # The shot PATCH API does not expose action_end; edit the row like a script re-import would.
    async with STATE["maker"]() as db:
        shot = await db.get(ShotRecord, shot_id)
        shot.action_end = value
        await db.commit()


async def _relaunch(client: AsyncClient, pid: str, cid: str, shot_id: str) -> dict:
    response = await client.post(
        f"/api/v1/projects/{pid}/batch/launch",
        json={"connection_id": cid, "shot_ids": [shot_id], "only_changed": True},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


async def test_imported_prompt_contains_boundaries_but_never_dialogue(client: AsyncClient):
    pid = (await client.post("/api/v1/projects", json={"name": "boundaries"})).json()["id"]
    imported = await client.post(
        f"/api/v1/projects/{pid}/scripts/import",
        json={"raw_content": BOUNDARY_SCRIPT, "auto_apply_proposal": True},
    )
    assert imported.status_code == 201, imported.text
    (shot,) = (await client.get(f"/api/v1/projects/{pid}/shots")).json()

    assert shot["dialogue"] == "We move at midnight."
    assert (
        "Action: Hero watches the street below. Starts: Hero crouches at the ledge. "
        "Ends: Hero stands and turns away."
    ) in shot["prompt_final"]
    assert "midnight" not in shot["prompt_final"]
    assert "Dialogue" not in shot["prompt_final"]


async def test_imported_generic_default_boundaries_keep_the_legacy_prompt(client: AsyncClient):
    pid, _cid, s1, _ = await _setup(client)
    shot = (await _shots(client, pid))[s1["id"]]

    # script_parser defaults; they carry no information and stay out of prompt and hash.
    async with STATE["maker"]() as db:
        row = await db.get(ShotRecord, s1["id"])
        assert (row.action_start, row.action_end) == ("in position", "completes motion")
    assert "Action: Leaps across neon gap Continuity:" in shot["prompt_final"]
    assert "Starts:" not in shot["prompt_final"] and "Ends:" not in shot["prompt_final"]
    async with STATE["maker"]() as db:
        _shots_list, hashes = await PipelineService(StudioRepository(db)).recompile_project_shots_with_prompt_hashes(
            pid, commit=False
        )
        await db.rollback()
    assert hashes[s1["id"]] == frozenset({shot["prompt_hash"]})


async def test_imported_generic_and_real_boundary_shows_only_the_real_one(client: AsyncClient):
    pid = (await client.post("/api/v1/projects", json={"name": "mixed-boundaries"})).json()["id"]
    script = BOUNDARY_SCRIPT.replace("Hero crouches at the ledge", "in position")
    imported = await client.post(
        f"/api/v1/projects/{pid}/scripts/import", json={"raw_content": script, "auto_apply_proposal": True}
    )
    assert imported.status_code == 201, imported.text
    (shot,) = (await client.get(f"/api/v1/projects/{pid}/shots")).json()

    assert "Action: Hero watches the street below. Ends: Hero stands and turns away." in shot["prompt_final"]
    assert "Starts:" not in shot["prompt_final"]
    assert "in position" not in shot["prompt_final"]


async def test_only_changed_skips_legacy_take_of_unchanged_shot(client: AsyncClient):
    pid, cid, shot_id = await _render_first_shot(client)
    legacy = await _make_selected_take_legacy(pid, shot_id)
    assert (await _shots(client, pid))[shot_id]["prompt_hash"] != legacy
    tasks_before = len(await _tasks_for_shot(shot_id))

    again = await _relaunch(client, pid, cid, shot_id)

    assert again["enqueued_count"] == 0
    assert again["skipped_shot_ids"] == [shot_id]
    assert len(await _tasks_for_shot(shot_id)) == tasks_before


async def test_only_changed_rerenders_legacy_take_after_action_edit(client: AsyncClient):
    pid, cid, shot_id = await _render_first_shot(client)
    await _make_selected_take_legacy(pid, shot_id)
    patched = await client.patch(
        f"/api/v1/projects/{pid}/shots/{shot_id}", json={"action": "Leaps across a wider neon gap"}
    )
    assert patched.status_code == 200, patched.text

    again = await _relaunch(client, pid, cid, shot_id)

    assert again["enqueued_count"] == 1
    assert again["skipped_shot_ids"] == []


async def test_only_changed_rerenders_current_take_after_action_end_edit(client: AsyncClient):
    pid, cid, shot_id = await _render_first_shot(client)
    await _set_action_end(shot_id, "Lands in a crouch on the far roof")

    again = await _relaunch(client, pid, cid, shot_id)

    assert again["enqueued_count"] == 1
    assert again["skipped_shot_ids"] == []


async def test_legacy_take_does_not_detect_boundary_only_edit(client: AsyncClient):
    """Documented limitation: the legacy hash cannot see action_start/action_end."""
    pid, cid, shot_id = await _render_first_shot(client)
    await _make_selected_take_legacy(pid, shot_id)
    await _set_action_end(shot_id, "Lands in a crouch on the far roof")
    tasks_before = len(await _tasks_for_shot(shot_id))

    again = await _relaunch(client, pid, cid, shot_id)

    assert again["enqueued_count"] == 0
    assert again["skipped_shot_ids"] == [shot_id]
    assert len(await _tasks_for_shot(shot_id)) == tasks_before
