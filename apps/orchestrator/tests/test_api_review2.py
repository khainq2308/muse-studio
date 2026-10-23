"""Regression tests for deep-review-2 findings (API, pipeline, workflow, security)."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import tempfile
import time
import zipfile
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp(prefix="muse_review2_api_"))

from muse_orchestrator.application.services import ProjectService  # noqa: E402 -- env first
from muse_orchestrator.core.config import SecretRedactor, Settings, settings  # noqa: E402
from muse_orchestrator.core.database import get_db  # noqa: E402
from muse_orchestrator.main import app  # noqa: E402
from muse_orchestrator.models import domain as d  # noqa: E402
from muse_orchestrator.providers.base import ErrorCategory, ProviderFailure  # noqa: E402
from muse_orchestrator.services import script_parser as sp  # noqa: E402
from muse_orchestrator.services.prompt_compiler import (  # noqa: E402
    PromptCompiler,
    ShotSpec,
    StyleBibleLock,
    is_static_camera_motion,
)

SCRIPT = """
SCENE 1: Cafe
SHOT 1: =HYPERLINK("http://evil.example","x")
- Character: Conan
- Action: Conan lifts cup
SHOT 2: Ran looks
- Character: Conan
- Action: Ran puts cup down
SHOT 3: Conan waits
- Character: Conan
- Action: Conan waits by the door
SCENE 2: Street
SHOT 4: Outside
- Character: Conan
- Action: Conan walks
"""
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64

STATE: dict[str, Any] = {}


@pytest.fixture()
async def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Any):
    # Emulate production: PostgreSQL enforces FK cascades and sessions use autoflush=False.
    # A file database gives every session its own connection, so concurrency tests see
    # real transaction isolation instead of one shared in-memory connection.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'review2.db'}", future=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(d.Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)

    async def override() -> Any:
        async with maker() as session:
            yield session

    monkeypatch.setattr(settings, "studio_admin_token", None)
    app.dependency_overrides[get_db] = override
    STATE["maker"] = maker
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://testserver"
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
    await engine.dispose()


async def _project(client: AsyncClient) -> str:
    r = await client.post("/api/v1/projects", json={"name": "Review2"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _import(client: AsyncClient, pid: str, script: str = SCRIPT, **extra: Any) -> Any:
    return await client.post(
        f"/api/v1/projects/{pid}/scripts/import",
        json={"raw_content": script, "auto_apply_proposal": True, **extra},
    )


async def _shots(client: AsyncClient, pid: str) -> list[dict[str, Any]]:
    r = await client.get(f"/api/v1/projects/{pid}/shots")
    assert r.status_code == 200
    return r.json()


async def _upload(client: AsyncClient, pid: str, asset_type: str = "reference") -> str:
    r = await client.post(
        "/api/v1/assets/upload",
        data={"project_id": pid, "asset_type": asset_type},
        files={"file": ("a.png", PNG, "image/png")},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _add_task_and_take(
    pid: str, sid: str, *, state: d.QueueTaskState, asset_id: str | None = None, suffix: str = "1"
) -> None:
    async with STATE["maker"]() as s:
        if await s.get(d.ProviderConnection, "conn_r2") is None:
            s.add(d.ProviderConnection(id="conn_r2", name="r2", base_url="http://x"))
            await s.flush()
        if asset_id is None:
            asset_id = f"ast_v{suffix}"
            s.add(d.Asset(
                id=asset_id, project_id=pid, asset_type="video", filename="v.mp4",
                storage_rel_path=f"x/v{suffix}.mp4", mime_type="video/mp4", size_bytes=1, sha256="0" * 64,
            ))
            await s.flush()
        s.add(d.GenerationTaskRecord(
            id=f"tsk_{suffix}", project_id=pid, shot_id=sid, connection_id="conn_r2", model_id="m",
            idempotency_key=f"k{suffix}", state=state, take_id=f"tk_{suffix}", result_asset_id=asset_id,
        ))
        await s.flush()
        if state == d.QueueTaskState.SUCCEEDED:
            s.add(d.ShotTakeRecord(
                id=f"tk_{suffix}", project_id=pid, shot_id=sid, take_number=1, task_id=f"tsk_{suffix}",
                asset_id=asset_id, model_id="m", prompt_hash="h", is_selected=True,
            ))
            shot = await s.get(d.ShotRecord, sid)
            shot.selected_take_id = f"tk_{suffix}"
            shot.generation_state = d.ShotGenerationState.SUCCEEDED
        await s.commit()


async def _count(model: Any) -> int:
    async with STATE["maker"]() as s:
        return int((await s.execute(select(func.count()).select_from(model))).scalar_one())


# --- DATA-001: destructive re-import -------------------------------------------------------


async def test_reimport_refuses_to_cascade_delete_takes_without_replace_flag(client: AsyncClient) -> None:
    pid = await _project(client)
    assert (await _import(client, pid)).status_code == 201
    sid = (await _shots(client, pid))[0]["id"]
    await _add_task_and_take(pid, sid, state=d.QueueTaskState.SUCCEEDED)

    r = await _import(client, pid)
    assert r.status_code == 409, r.text
    assert "replace_existing" in r.json()["detail"]
    assert await _count(d.ShotTakeRecord) == 1
    assert await _count(d.GenerationTaskRecord) == 1
    assert sid in {s["id"] for s in await _shots(client, pid)}

    r = await _import(client, pid, replace_existing=True)
    assert r.status_code == 201, r.text
    assert await _count(d.ShotTakeRecord) == 0
    assert sid not in {s["id"] for s in await _shots(client, pid)}


async def test_reimport_refuses_when_project_has_existing_pipeline_without_replace_flag(
    client: AsyncClient,
) -> None:
    pid = await _project(client)
    # First import creates pipeline data (bible entries, scenes, shots) with no takes
    assert (await _import(client, pid)).status_code == 201
    original_shots = await _shots(client, pid)
    assert len(original_shots) > 0
    assert await _count(d.ShotTakeRecord) == 0

    # Second import with auto_apply_proposal=True and replace_existing=False must fail with 409
    r = await _import(client, pid, replace_existing=False)
    assert r.status_code == 409, r.text
    assert "replace_existing" in r.json()["detail"]
    # Existing shots must remain untouched
    assert [s["id"] for s in await _shots(client, pid)] == [s["id"] for s in original_shots]

    # Re-import with replace_existing=True succeeds and replaces pipeline
    r = await _import(client, pid, replace_existing=True)
    assert r.status_code == 201, r.text


async def test_reimport_refuses_while_tasks_are_active_even_with_replace(client: AsyncClient) -> None:
    pid = await _project(client)
    await _import(client, pid)
    sid = (await _shots(client, pid))[0]["id"]
    await _add_task_and_take(pid, sid, state=d.QueueTaskState.RUNNING)

    r = await _import(client, pid, replace_existing=True)
    assert r.status_code == 409, r.text
    assert await _count(d.GenerationTaskRecord) == 1
    # A non-applying import (proposal only) is still allowed and versions are max+1.
    r = await client.post(f"/api/v1/projects/{pid}/scripts/import", json={"raw_content": SCRIPT})
    assert r.status_code == 201
    assert r.json()["version_number"] == 2


# --- DATA-003: recompile must not clobber generation state ---------------------------------


async def test_recompile_keeps_running_state_and_restores_succeeded_on_unblock(client: AsyncClient) -> None:
    pid = await _project(client)
    await _import(client, pid)
    shots = await _shots(client, pid)
    running, done = shots[1]["id"], shots[2]["id"]
    async with STATE["maker"]() as s:
        shot = await s.get(d.ShotRecord, running)
        shot.generation_state = d.ShotGenerationState.RUNNING
        await s.commit()
    await _add_task_and_take(pid, running, state=d.QueueTaskState.RUNNING, suffix="run")
    await _add_task_and_take(pid, done, state=d.QueueTaskState.SUCCEEDED, suffix="ok")

    lock = {"entry_type": "character", "code_name": "char_conan", "display_name": "Conan", "requires_reference_lock": True}
    assert (await client.post(f"/api/v1/projects/{pid}/story-bible", json=lock)).status_code == 201
    by_id = {s["id"]: s for s in await _shots(client, pid)}
    assert by_id[running]["generation_state"] == "running"
    assert by_id[done]["generation_state"] == "blocked_reference"

    unlock = {**lock, "requires_reference_lock": False}
    assert (await client.post(f"/api/v1/projects/{pid}/story-bible", json=unlock)).status_code == 201
    by_id = {s["id"]: s for s in await _shots(client, pid)}
    assert by_id[done]["generation_state"] == "succeeded"
    assert by_id[running]["generation_state"] == "running"


# --- DATA-004: asset deletion with references ---------------------------------------------


async def test_delete_referenced_asset_requires_force_and_force_detaches_and_recompiles(client: AsyncClient) -> None:
    pid = await _project(client)
    await _import(client, pid)
    sid = (await _shots(client, pid))[1]["id"]
    aid = await _upload(client, pid)
    r = await client.patch(
        f"/api/v1/projects/{pid}/shots/{sid}",
        json={"reference_asset_ids": [aid], "requires_reference_lock": True},
    )
    assert r.status_code == 200, r.text

    r = await client.delete(f"/api/v1/assets/{aid}")
    assert r.status_code == 409
    assert sid in r.json()["detail"]
    async with STATE["maker"]() as s:
        assert await s.get(d.Asset, aid) is not None

    assert (await client.delete(f"/api/v1/assets/{aid}?force=true")).status_code == 204
    shot = next(s for s in await _shots(client, pid) if s["id"] == sid)
    assert shot["reference_asset_ids"] == []
    assert "BLOCKED_REFERENCE" in {i["code"] for i in shot["lint_report"] if i["severity"] == "blocker"}
    assert shot["generation_state"] == "blocked_reference"


async def test_force_delete_of_take_asset_clears_selected_take(client: AsyncClient) -> None:
    pid = await _project(client)
    await _import(client, pid)
    sid = (await _shots(client, pid))[0]["id"]
    aid = await _upload(client, pid, asset_type="generated")
    await _add_task_and_take(pid, sid, state=d.QueueTaskState.SUCCEEDED, asset_id=aid, suffix="2")

    assert (await client.delete(f"/api/v1/assets/{aid}")).status_code == 409
    assert (await client.delete(f"/api/v1/assets/{aid}?force=true")).status_code == 204
    async with STATE["maker"]() as s:
        shot = await s.get(d.ShotRecord, sid)
        assert shot.selected_take_id is None
        assert shot.generation_state != d.ShotGenerationState.SUCCEEDED
        assert await s.get(d.ShotTakeRecord, "tk_2") is None


# --- DATA-005: typed style attributes and atomic recompile --------------------------------


async def test_bad_style_entry_is_rejected_and_never_poisons_recompiles(client: AsyncClient) -> None:
    pid = await _project(client)
    await _import(client, pid)
    sid = (await _shots(client, pid))[0]["id"]
    for bad in ({"negative_constraints": 5}, {"negative_constraints": [1, 2]}, {"forbidden_terms": "x"}, {"color_palette": 3}):
        r = await client.post(
            f"/api/v1/projects/{pid}/story-bible",
            json={"entry_type": "style", "code_name": "style_global", "display_name": "S", "mutable_attributes": bad},
        )
        assert r.status_code == 422, (bad, r.text)
    bible = (await client.get(f"/api/v1/projects/{pid}/story-bible")).json()
    style = next(b for b in bible if b["code_name"] == "style_global")
    assert isinstance(style["mutable_attributes"]["negative_constraints"], list)
    assert (await client.patch(f"/api/v1/projects/{pid}/shots/{sid}", json={"action": "new action"})).status_code == 200


async def test_forbidden_terms_produce_lint_warning(client: AsyncClient) -> None:
    pid = await _project(client)
    await _import(client, pid)
    sid = (await _shots(client, pid))[0]["id"]
    r = await client.patch(f"/api/v1/projects/{pid}/shots/{sid}", json={"action": "A live action brawl"})
    assert r.status_code == 200
    codes = {(i["code"], i["severity"]) for i in r.json()["lint_report"]}
    assert ("FORBIDDEN_TERM", "warning") in codes


# --- DATA-006: safe allocation ---------------------------------------------------------------


async def test_concurrent_graph_saves_allocate_distinct_versions(client: AsyncClient) -> None:
    pid = await _project(client)
    wf = (await client.post(f"/api/v1/projects/{pid}/workflows", json={})).json()
    body = {"nodes": wf["nodes"], "edges": wf["edges"]}
    results = await asyncio.gather(
        *[client.put(f"/api/v1/projects/{pid}/workflows/{wf['id']}/graph", json=body) for _ in range(4)]
    )
    ok = [r.json()["current_version"] for r in results if r.status_code == 200]
    assert all(r.status_code in (200, 409) for r in results)
    assert len(ok) == len(set(ok))
    async with STATE["maker"]() as s:
        versions = (
            await s.execute(select(d.WorkflowVersion.version_number).where(d.WorkflowVersion.workflow_id == wf["id"]))
        ).scalars().all()
    assert sorted(versions) == list(range(1, len(versions) + 1))
    run = await client.post(f"/api/v1/projects/{pid}/workflows/{wf['id']}/runs", json={"run_mode": "dry_run"})
    assert run.status_code == 201


async def test_concurrent_bible_upserts_never_duplicate_code_name(client: AsyncClient) -> None:
    pid = await _project(client)
    body = {"entry_type": "character", "code_name": "char_x", "display_name": "X"}
    results = await asyncio.gather(*[client.post(f"/api/v1/projects/{pid}/story-bible", json=body) for _ in range(4)])
    assert all(r.status_code in (201, 409) for r in results)
    later = await client.post(f"/api/v1/projects/{pid}/story-bible", json=body)
    assert later.status_code == 201
    bible = (await client.get(f"/api/v1/projects/{pid}/story-bible")).json()
    assert [b["code_name"] for b in bible].count("char_x") == 1


async def test_bible_upsert_rejects_entry_type_change(client: AsyncClient) -> None:
    pid = await _project(client)
    body = {"entry_type": "character", "code_name": "char_x", "display_name": "X"}
    assert (await client.post(f"/api/v1/projects/{pid}/story-bible", json=body)).status_code == 201
    r = await client.post(f"/api/v1/projects/{pid}/story-bible", json={**body, "entry_type": "location"})
    assert r.status_code == 409


# --- DATA-007: bounded inputs -----------------------------------------------------------------


def test_owned_input_max_lengths_match_column_lengths() -> None:
    from muse_orchestrator.api.assets import upload_asset
    from muse_orchestrator.api.connections import ConnectionCreateIn
    from muse_orchestrator.api.pipeline import BibleEntryCreateIn, ScriptImportIn, ShotUpdateIn
    from muse_orchestrator.api.projects import ProjectCreateIn
    from muse_orchestrator.api.system import LogCreateIn
    from muse_orchestrator.api.workflows import WorkflowCreateIn

    def max_len(model: Any, field: str) -> int | None:
        for meta in model.model_fields[field].metadata:
            if getattr(meta, "max_length", None) is not None:
                return meta.max_length
        return None

    pairs = [
        (ShotUpdateIn, "shot_size", d.ShotRecord, "shot_size"),
        (ShotUpdateIn, "camera_motion", d.ShotRecord, "camera_motion"),
        (ShotUpdateIn, "previous_shot_id", d.ShotRecord, "previous_shot_id"),
        (ShotUpdateIn, "first_frame_asset_id", d.ShotRecord, "first_frame_asset_id"),
        (ScriptImportIn, "source_format", d.ScriptRevision, "source_format"),
        (BibleEntryCreateIn, "code_name", d.StoryBibleEntry, "code_name"),
        (BibleEntryCreateIn, "display_name", d.StoryBibleEntry, "display_name"),
        (WorkflowCreateIn, "name", d.WorkflowDefinition, "name"),
        (ProjectCreateIn, "name", d.Project, "name"),
        (ProjectCreateIn, "style_preset", d.Project, "style_preset"),
        (ProjectCreateIn, "language", d.Project, "language"),
        (ProjectCreateIn, "output_profile", d.Project, "output_profile"),
        (LogCreateIn, "correlation_id", d.StructuredLogRecord, "correlation_id"),
        (LogCreateIn, "event", d.StructuredLogRecord, "event"),
        (LogCreateIn, "project_id", d.StructuredLogRecord, "project_id"),
        (LogCreateIn, "task_id", d.StructuredLogRecord, "task_id"),
        (ConnectionCreateIn, "name", d.ProviderConnection, "name"),
        (ConnectionCreateIn, "base_url", d.ProviderConnection, "base_url"),
        (ConnectionCreateIn, "proxy_display_host", d.ProviderConnection, "proxy_display_host"),
    ]
    for model, field, table, column in pairs:
        column_length = table.__table__.c[column].type.length
        assert max_len(model, field) is not None, f"{model.__name__}.{field} is unbounded"
        assert max_len(model, field) <= column_length, f"{model.__name__}.{field} > {table.__name__}.{column}"

    form_params = {p.name: p.default for p in __import__("inspect").signature(upload_asset).parameters.values()}
    asset_type_meta = [m for m in form_params["asset_type"].metadata if getattr(m, "max_length", None)]
    assert asset_type_meta and asset_type_meta[0].max_length <= d.Asset.__table__.c["asset_type"].type.length


async def test_shot_size_longer_than_column_is_422(client: AsyncClient) -> None:
    pid = await _project(client)
    await _import(client, pid)
    sid = (await _shots(client, pid))[0]["id"]
    r = await client.patch(f"/api/v1/projects/{pid}/shots/{sid}", json={"shot_size": "x" * 65})
    assert r.status_code == 422


async def test_data_error_maps_to_422(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(self: ProjectService) -> list:
        raise DataError("INSERT", {}, Exception("value too long for type character varying(64)"))

    monkeypatch.setattr(ProjectService, "list_projects", boom)
    r = await client.get("/api/v1/projects")
    assert r.status_code == 422
    assert r.json()["category"] == "validation"


# --- WF-001: partial runs and cache keys -------------------------------------------------------


async def test_partial_run_requires_upstream_manual_approval(client: AsyncClient) -> None:
    pid = await _project(client)
    nodes = [
        {"node_id": "p", "node_type": "PromptRefine", "config": {}},
        {"node_id": "gate", "node_type": "ManualApproval", "config": {}},
        {"node_id": "vid", "node_type": "VideoGenerate", "config": {}},
    ]
    edges = [
        {"edge_id": "e1", "source_node_id": "p", "source_port": "prompts", "target_node_id": "gate", "target_port": "in"},
        {"edge_id": "e2", "source_node_id": "gate", "source_port": "out", "target_node_id": "vid", "target_port": "prompt"},
    ]
    wf = (await client.post(f"/api/v1/projects/{pid}/workflows", json={"use_default_m3_template": False, "nodes": nodes, "edges": edges})).json()
    runs = f"/api/v1/projects/{pid}/workflows/{wf['id']}/runs"
    r = await client.post(runs, json={"run_mode": "from_here", "start_node_id": "vid"})
    assert r.status_code == 409, r.text
    assert "gate" in r.json()["detail"]

    full = (await client.post(runs, json={"run_mode": "full"})).json()
    assert full["status"] == "waiting_approval"
    assert (await client.post(f"{runs}/{full['run_id']}/nodes/gate/approve")).status_code == 200
    r = await client.post(runs, json={"run_mode": "from_here", "start_node_id": "vid"})
    assert r.status_code == 201, r.text
    assert r.json()["execution_plan_order"] == ["vid"]


async def test_cache_keys_include_node_identity_and_external_inputs(client: AsyncClient) -> None:
    pid = await _project(client)
    nodes = [
        {"node_id": "note_a", "node_type": "Note/Group", "config": {}},
        {"node_id": "note_b", "node_type": "Note/Group", "config": {}},
        {"node_id": "img_a", "node_type": "ImageGenerate", "config": {}},
        {"node_id": "img_b", "node_type": "ImageGenerate", "config": {}},
    ]
    wf = (await client.post(f"/api/v1/projects/{pid}/workflows", json={"use_default_m3_template": False, "nodes": nodes, "edges": []})).json()
    assert wf["cache_keys"]["img_a"] != wf["cache_keys"]["img_b"]
    for _ in range(2):  # identical deterministic nodes must not collide on the cache PK
        r = await client.post(f"/api/v1/projects/{pid}/workflows/{wf['id']}/runs", json={"run_mode": "full"})
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "succeeded"

    default = (await client.post(f"/api/v1/projects/{pid}/workflows", json={})).json()
    dry = f"/api/v1/projects/{pid}/workflows/{default['id']}/runs"
    before = (await client.post(dry, json={"run_mode": "dry_run"})).json()["cache_keys"]
    await client.post(f"/api/v1/projects/{pid}/scripts/import", json={"raw_content": SCRIPT})
    after = (await client.post(dry, json={"run_mode": "dry_run"})).json()["cache_keys"]
    assert before["n_script_in"] != after["n_script_in"]
    assert before["n_bible"] != after["n_bible"]


async def test_workflow_graph_size_is_bounded(client: AsyncClient) -> None:
    pid = await _project(client)
    nodes = [{"node_id": f"n{i}", "node_type": "Note/Group"} for i in range(201)]
    r = await client.post(f"/api/v1/projects/{pid}/workflows", json={"use_default_m3_template": False, "nodes": nodes})
    assert r.status_code == 422
    r = await client.post(f"/api/v1/projects/{pid}/workflows", json={"ui_layout": {"x": "y" * 300_000}})
    assert r.status_code == 422


# --- SEC-003: linear redaction and bypasses ---------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    ["http://" * 300_000, "a@" * 300_000, "password" + " " * 400_000, "authorization:" + " " * 400_000],
)
def test_redaction_is_fast_on_hostile_input(hostile: str) -> None:
    started = time.perf_counter()
    SecretRedactor.redact_text(hostile)
    assert time.perf_counter() - started < 0.5


def test_redaction_truncates_with_marker() -> None:
    out = SecretRedactor.redact_text("x " * 20_000)
    assert "[TRUNCATED" in out and len(out) < 17_000


@pytest.mark.parametrize(
    "raw, secret",
    [
        ('{"api_key": "sk-live-AAAA1111"}', "sk-live-AAAA1111"),
        ("{'password': 'hunter2pass'}", "hunter2pass"),
        ('"Authorization": "Bearer sk-live-BBBB"', "sk-live-BBBB"),
        ("Authorization: Token sk-live-CCCC", "sk-live-CCCC"),
        ("Bearer sk-live-DDDD", "sk-live-DDDD"),
        ("GET /v1?token=sk-live-EEEE&x=1", "sk-live-EEEE"),
        ("https://h/v1?key=sk-live-FFFF", "sk-live-FFFF"),
        ("https://h/v1?sig=sk-live-SIGN", "sk-live-SIGN"),
        ('"apiKey":"sk-live-JJJJ"', "sk-live-JJJJ"),
        ("socks5://user:pa'ss@proxy.example:1080", "pa'ss"),
        ("x-goog-api-key: sk-live-GGGG", "sk-live-GGGG"),
    ],
)
def test_redactor_closes_known_bypasses(raw: str, secret: str) -> None:
    assert secret not in SecretRedactor.redact_text(raw)


def test_redact_dict_matches_secret_key_variants_without_over_redacting() -> None:
    out = SecretRedactor.redact_dict(
        {
            "headers": {"Authorization": "Bearer zzz"},
            "apiKey": "sk-camel",
            "token_value": "t0k3n",
            "x": [{"key": "kkk"}],
            "api_key_masked": "********",
            "cache_key": "abc",
            "max_tokens": 5,
            "note": "bearer of bad news",
        }
    )
    assert "zzz" not in json.dumps(out) and "sk-camel" not in json.dumps(out)
    assert out["token_value"] == "[REDACTED]" and out["x"][0]["key"] == "[REDACTED]"
    assert out["api_key_masked"] == "********"
    assert out["cache_key"] == "abc" and out["max_tokens"] == 5
    assert out["note"] == "bearer of bad news"


# --- SEC-004: script parser ---------------------------------------------------------------------


def test_script_kv_parser_is_linear() -> None:
    for line in ("-" + " " * 50_000 + "x", "a" + " " * 50_000 + "b", "a " * 50_000 + "!"):
        started = time.perf_counter()
        sp._parse_kv_line(line)
        assert time.perf_counter() - started < 0.2
    assert sp._parse_kv_line("- **Action**: run") == ("Action", "run")
    assert sp._parse_kv_line("**Camera:** pan") == ("Camera", "pan")


def test_parser_edge_cases_are_deterministic() -> None:
    script = (
        "SCENE 1: A\nSHOT 1: a\n- Character: Ran!\n- Duration: 99999\n"
        "SCENE 1: B\nSHOT 2: b\n- Character: Ran?\n- Duration: 0\n"
    )
    proposal = sp.ScriptAnalyzerService.analyze_script(script)
    assert [s["scene_index"] for s in proposal.scenes] == [1, 2]
    assert [s["scene_index"] for s in proposal.shots] == [1, 2]
    assert proposal.shots[0]["duration_seconds"] == sp.MAX_SHOT_DURATION_SECONDS
    assert proposal.shots[1]["duration_seconds"] == sp.DEFAULT_SHOT_DURATION_SECONDS
    assert {c["code_name"] for c in proposal.characters} == {"char_ran", "char_ran_2"}
    assert len(proposal.warnings) == 3
    with pytest.raises(sp.ScriptParseError):
        sp.ScriptAnalyzerService.analyze_script("SCENE 99999999: x\nSHOT 1: y\n")
    with pytest.raises(sp.ScriptParseError):
        sp.ScriptAnalyzerService.analyze_script("SCENE " + "9" * 5000 + ": x\n")


async def test_script_import_bounds_and_warnings(client: AsyncClient) -> None:
    pid = await _project(client)
    r = await client.post(f"/api/v1/projects/{pid}/scripts/import", json={"raw_content": "x" * 1_000_001})
    assert r.status_code == 422
    r = await client.post(f"/api/v1/projects/{pid}/scripts/import", json={"raw_content": "SCENE 99999999: x\nSHOT 1: y"})
    assert r.status_code == 422
    r = await _import(client, pid, "SHOT 1: a\n- Action: walk\n- Duration: 99999\n")
    assert r.status_code == 201
    assert r.json()["warnings"]
    assert (await _shots(client, pid))[0]["duration_seconds"] == 600.0


# --- SEC-005: dev CSRF guard ---------------------------------------------------------------------


def _backup_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps({"format_version": "m7-backup-1", "project_id": "p1"}))
        z.writestr("data/projects.json", json.dumps([{"id": "p1", "name": "planted-by-evil-page"}]))
    return buf.getvalue()


async def test_dev_mode_rejects_cross_site_state_changes(client: AsyncClient) -> None:
    assert settings.insecure_dev_defaults
    evil = {"Origin": "https://evil.example", "Host": "localhost:8000"}
    r = await client.post("/api/v1/backups/restore", headers=evil, files={"file": ("b.zip", _backup_zip(), "application/zip")})
    assert r.status_code == 403
    assert r.json()["category"] == "forbidden"
    assert await _count(d.Project) == 0

    r = await client.post("/api/v1/projects", headers={"Sec-Fetch-Site": "cross-site"}, json={"name": "x"})
    assert r.status_code == 403
    r = await client.post("/api/v1/projects", headers={"Origin": "null"}, json={"name": "x"})
    assert r.status_code == 403
    # Allowed dev origin, non-browser clients and safe methods still work.
    r = await client.post("/api/v1/projects", headers={"Origin": "http://localhost:5173"}, json={"name": "ok"})
    assert r.status_code == 201
    assert (await client.post("/api/v1/projects", json={"name": "cli"})).status_code == 201
    assert (await client.get("/api/v1/projects", headers=evil)).status_code == 200


# --- P3 auth -----------------------------------------------------------------------------------------


async def test_non_ascii_admin_token_is_401_not_500(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "studio_admin_token", "real-token-123")
    r = await client.get("/api/v1/projects", headers={"X-Studio-Admin-Token": "ÿ".encode("latin-1")})
    assert r.status_code == 401
    r = await client.get("/api/v1/projects", headers={"X-Studio-Admin-Token": "real-token-123"})
    assert r.status_code == 200


def test_production_requires_long_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSE2API_VIDEO_PAYLOAD_PROFILE", "czg86389-hub")
    key = "replace-this-with-a-stable-production-secret-32"
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(environment="production", studio_admin_token="short-token", secret_encryption_key=key)
    assert Settings(environment="production", studio_admin_token="t" * 32, secret_encryption_key=key)


# --- P3 URL validation ------------------------------------------------------------------------------


async def test_connection_base_url_validation(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def create(base_url: str, name: str) -> Any:
        return await client.post("/api/v1/connections", json={"name": name, "provider_type": "mock", "base_url": base_url})

    for i, bad in enumerate(["http://user:s3cr3t@h.example", "https://h.example:99999", "https://h.example:abc", "ftp://h", "https://[::1"]):
        r = await create(bad, f"bad{i}")
        assert r.status_code == 422, bad
        assert "s3cr3t" not in r.text
    assert (await create("http://localhost:8080", "dev-http")).status_code == 201

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "studio_admin_token", None)
    from muse_orchestrator.application.services import ApplicationError, validate_provider_base_url

    with pytest.raises(ApplicationError):
        validate_provider_base_url("http://gateway.internal")
    monkeypatch.setattr(settings, "allow_insecure_provider_http", True)
    assert validate_provider_base_url("http://gateway.internal/v1") == "http://gateway.internal"
    assert validate_provider_base_url("https://gw.example") == "https://gw.example"


async def test_proxy_url_with_bad_port_is_422_without_echo(client: AsyncClient) -> None:
    conn = (await client.post("/api/v1/connections", json={"name": "c", "provider_type": "mock", "base_url": "http://localhost:1"})).json()
    for proxy in ("http://user:s3cr3tpw@proxy.example:99999", "socks5://user:s3cr3tpw@proxy.example:abc", "http://user:s3cr3tpw@[::1"):
        r = await client.post(f"/api/v1/connections/{conn['id']}/accounts", json={"label": proxy[-5:], "api_key": "k", "proxy_url": proxy})
        assert r.status_code == 422, proxy
        assert "s3cr3tpw" not in r.text


# --- P3 error mapping ----------------------------------------------------------------------------------


async def test_integrity_error_only_maps_unique_violations_to_409(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    message = {"value": "UNIQUE constraint failed: x.y"}

    async def boom(self: ProjectService) -> list:
        raise IntegrityError("INSERT", {}, Exception(message["value"]))

    monkeypatch.setattr(ProjectService, "list_projects", boom)
    assert (await client.get("/api/v1/projects")).status_code == 409
    message["value"] = "FOREIGN KEY constraint failed"
    r = await client.get("/api/v1/projects")
    assert r.status_code == 500
    assert r.json()["category"] == "internal_error"


@pytest.mark.parametrize(
    "category, expected",
    [
        (ErrorCategory.INVALID_REQUEST, 422),
        (ErrorCategory.RATE_LIMITED, 429),
        (ErrorCategory.UPSTREAM_POLL_TIMEOUT, 504),
        (ErrorCategory.AUTH_FAILED, 502),
        (ErrorCategory.UPSTREAM_5XX, 502),
    ],
)
async def test_provider_failure_status_mapping(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, category: ErrorCategory, expected: int
) -> None:
    async def boom(self: ProjectService) -> list:
        raise ProviderFailure(category=category, message="upstream said no api_key=sk-zzz", retryable=False)

    monkeypatch.setattr(ProjectService, "list_projects", boom)
    r = await client.get("/api/v1/projects")
    assert r.status_code == expected
    body = r.json()
    assert body["category"] == category.value
    assert "HTTP None" not in body["detail"] and "sk-zzz" not in body["detail"]


# --- P3 continuity links (C1 + #14) --------------------------------------------------------------------


async def test_previous_shot_relink_maintains_next_pointers_and_scene(client: AsyncClient) -> None:
    pid = await _project(client)
    await _import(client, pid)
    s1, s2, s3, s4 = [s["id"] for s in await _shots(client, pid)]
    url = f"/api/v1/projects/{pid}/shots"

    assert (await client.patch(f"{url}/{s4}", json={"previous_shot_id": s3})).status_code == 422  # other scene
    assert (await client.patch(f"{url}/{s3}", json={"previous_shot_id": s1})).status_code == 409  # s1 -> s2 taken

    assert (await client.patch(f"{url}/{s3}", json={"previous_shot_id": None})).status_code == 200
    by_id = {s["id"]: s for s in await _shots(client, pid)}
    assert by_id[s2]["next_shot_id"] is None

    assert (await client.patch(f"{url}/{s2}", json={"previous_shot_id": None})).status_code == 200
    assert (await client.patch(f"{url}/{s3}", json={"previous_shot_id": s1})).status_code == 200
    by_id = {s["id"]: s for s in await _shots(client, pid)}
    assert by_id[s1]["next_shot_id"] == s3 and by_id[s3]["previous_shot_id"] == s1


async def test_continuity_change_clears_conditioning_first_frame(client: AsyncClient) -> None:
    pid = await _project(client)
    await _import(client, pid)
    shots = await _shots(client, pid)
    sid = shots[1]["id"]
    aid = await _upload(client, pid)
    url = f"/api/v1/projects/{pid}/shots/{sid}"
    assert (await client.patch(url, json={"first_frame_asset_id": aid})).status_code == 200
    assert (await client.patch(url, json={"continuity_mode": "frame_chain"})).status_code == 200
    assert next(s for s in await _shots(client, pid) if s["id"] == sid)["first_frame_asset_id"] is None

    assert (await client.patch(url, json={"continuity_mode": "standard", "first_frame_asset_id": aid})).status_code == 200
    assert next(s for s in await _shots(client, pid) if s["id"] == sid)["first_frame_asset_id"] == aid
    assert (await client.patch(url, json={"previous_shot_id": None})).status_code == 200
    assert next(s for s in await _shots(client, pid) if s["id"] == sid)["first_frame_asset_id"] is None


# --- P3 camera linter -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action", ["The camera pans left", "panning across", "orbiting the hero", "dollies in", "tracks the car",
               "zooms in on the clock", "whip-panned to Ran", "handheld running shot", "craning up"],
)
def test_inflected_camera_verbs_conflict_with_static(action: str) -> None:
    spec = ShotSpec(
        shot_id="s", scene_id="sc", sequence_index=1, subject_ids=[], location_id="l", shot_size="ms",
        camera_angle="eye", camera_motion="Static shot", composition="center", action=action,
        action_start="a", action_end="b", continuity_notes="", duration_seconds=5.0,
    )
    result = PromptCompiler.compile_and_lint(spec, StyleBibleLock("m", "a", "c"), {}, {}, set())
    assert "CAMERA_MOTION_CONFLICT" in {i.code for i in result.lint_issues}


def test_static_camera_normalisation() -> None:
    assert is_static_camera_motion("locked_off")
    assert is_static_camera_motion("Fixed camera")
    assert not is_static_camera_motion("slow_pan")


# --- P3 CSV formula injection ----------------------------------------------------------------------------


async def test_csv_export_neutralises_formulas(client: AsyncClient) -> None:
    pid = await _project(client)
    await _import(client, pid)
    r = await client.get(f"/api/v1/projects/{pid}/shots/export?format=csv")
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[1][2].startswith("'=HYPERLINK")
    assert all(not cell.startswith(("=", "+", "-", "@")) for row in rows[1:] for cell in row)


# --- OPS-001 / OPS-002 -----------------------------------------------------------------------------------


class _FakePool:
    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy

    async def ping(self) -> bool:
        if not self.healthy:
            raise ConnectionError("redis://:pw@redis down")
        return True


async def test_readyz_reports_dependency_state(client: AsyncClient) -> None:
    app.state.arq_pool = _FakePool(True)
    try:
        r = await client.get("/readyz")
        assert r.status_code == 200
        assert r.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}
        app.state.arq_pool = _FakePool(False)
        r = await client.get("/readyz")
        assert r.status_code == 503
        assert r.json()["detail"]["checks"]["redis"] == "unavailable"
        assert "pw" not in r.text
    finally:
        del app.state.arq_pool
    assert (await client.get("/healthz")).status_code == 200


async def test_request_id_is_echoed_or_generated(client: AsyncClient) -> None:
    r = await client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"
    r = await client.get("/healthz", headers={"X-Request-ID": "bad id\nwith newline"})
    assert r.headers["X-Request-ID"] != "bad id\nwith newline" and len(r.headers["X-Request-ID"]) == 32
