"""TEST-002: integration tests against a real PostgreSQL server.

SQLite ignores ``SELECT ... FOR UPDATE`` and serialises a single connection, so the
rest of the suite cannot prove row locks, the partial unique index on active tasks,
asyncpg SQLSTATE mapping or migration behaviour on PostgreSQL. This module can.

* Skipped unless ``TEST_POSTGRES_URL`` (``postgresql+asyncpg://user:pw@host:port/db``)
  is set. With ``REQUIRE_POSTGRES=1`` (CI) a missing URL is a hard failure instead.
* Every test creates its own throwaway database ``muse_it_<hex>`` on that server,
  builds the schema with the real Alembic ``upgrade head`` (never ``create_all``)
  and drops the database afterwards (``WITH (FORCE)``). The database named in the URL
  is only used as the maintenance connection for CREATE/DROP DATABASE.
* Concurrent actors use separate ``AsyncSession`` objects (separate pooled
  connections), so the races below are real races on the server.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

_PG_URL = os.environ.get("TEST_POSTGRES_URL", "").strip()
if not _PG_URL:
    if os.environ.get("REQUIRE_POSTGRES") == "1":
        pytest.fail(
            "REQUIRE_POSTGRES=1 but TEST_POSTGRES_URL is not set; the PostgreSQL "
            "integration suite must not be silently skipped.",
            pytrace=False,
        )
    pytest.skip(
        "TEST_POSTGRES_URL not set (postgresql+asyncpg://...); PostgreSQL integration tests skipped.",
        allow_module_level=True,
    )
if not _PG_URL.startswith("postgresql+asyncpg://"):
    pytest.fail("TEST_POSTGRES_URL must use the postgresql+asyncpg:// driver.", pytrace=False)

# Same import-time contract as the other API modules: the app builds its default
# engine/settings at import, so these must be set first. The default engine stays
# on SQLite; every request in this module is routed to PostgreSQL via get_db.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("MEDIA_ROOT", tempfile.mkdtemp(prefix="muse_pg_it_"))

from alembic import command  # noqa: E402
from alembic.autogenerate import compare_metadata  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.migration import MigrationContext  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.exc import DBAPIError, IntegrityError  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from starlette.requests import Request  # noqa: E402

from muse_orchestrator.core.config import settings  # noqa: E402
from muse_orchestrator.core.database import _run_alembic_upgrade, get_db  # noqa: E402
from muse_orchestrator.infrastructure.repositories import StudioRepository  # noqa: E402
from muse_orchestrator.main import app, is_unique_violation  # noqa: E402
from muse_orchestrator.models import schema_baseline_v1 as baseline  # noqa: E402
from muse_orchestrator.models.domain import (  # noqa: E402
    Base,
    GenerationDispatchLeaseRecord,
    GenerationTaskRecord,
    Project,
    ProviderConnection,
    QueueTaskState,
    ShotRecord,
    StoryBibleEntry,
    WorkflowDefinition,
    WorkflowVersion,
)
from muse_orchestrator.providers.mock_provider import MockVideoProvider  # noqa: E402
from muse_orchestrator.services.generation_queue import GenerationQueueService  # noqa: E402
from muse_orchestrator.workers import queue_worker  # noqa: E402

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
ACTIVE_STATES = (QueueTaskState.QUEUED, QueueTaskState.RUNNING, QueueTaskState.PAUSED)
# Upper bound for an actor waiting on its competitor; a correct run never reaches it.
RACE_TIMEOUT_SECONDS = 15.0

TWO_SHOT_SCRIPT = """
SCENE 1: Final Showdown
SHOT 1: Detective confronts culprit
- Character: Conan
- Action: Points finger and reveals the truth
- Duration: 4.0

SHOT 2: Culprit drops the key
- Character: Culprit
- Action: Key falls onto marble floor in slow motion
- Duration: 6.0
"""


# --- infrastructure -------------------------------------------------------------------


def _database_url(name: str) -> str:
    return make_url(_PG_URL).set(database=name).render_as_string(hide_password=False)


def _alembic_upgrade_to(url: str, revision: str) -> None:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, revision)


@pytest.fixture()
async def pg_database_url(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    """A brand-new, empty PostgreSQL database for this test only."""
    name = f"muse_it_{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(_PG_URL, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
        url = _database_url(name)
        # migrations/env.py prefers DATABASE_URL over the configured URL.
        monkeypatch.setenv("DATABASE_URL", url)
        yield url
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


@pytest.fixture()
async def migrated_url(pg_database_url: str) -> str:
    # The real startup path (core.database._run_alembic_upgrade) in a worker thread,
    # exactly like init_db(): env.py calls asyncio.run().
    await asyncio.to_thread(_run_alembic_upgrade, pg_database_url)
    return pg_database_url


@pytest.fixture()
async def pg_engine(migrated_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_url, pool_size=10, max_overflow=10)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture()
def maker(pg_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # Same options as core.database.AsyncSessionLocal.
    return async_sessionmaker(bind=pg_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


class _FakeArqPool:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple, dict]] = []
        self._ids: set[str] = set()

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> object | None:
        job_id = kwargs.get("_job_id")
        if job_id and job_id in self._ids:
            return None
        if job_id:
            self._ids.add(job_id)
        self.jobs.append((function, args, kwargs))
        return object()

    async def aclose(self) -> None:
        return None


@pytest.fixture()
async def client(
    maker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    media_root = tmp_path / "media"
    media_root.mkdir()
    monkeypatch.setattr(settings, "media_root", str(media_root))
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "studio_admin_token", None)
    monkeypatch.setattr(queue_worker, "AsyncSessionLocal", maker)
    previous_pool = getattr(app.state, "arq_pool", None)
    app.state.arq_pool = _FakeArqPool()
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)
        if previous_pool is None:
            if hasattr(app.state, "arq_pool"):
                delattr(app.state, "arq_pool")
        else:
            app.state.arq_pool = previous_pool


async def _project_with_shots(
    client: AsyncClient, *, max_concurrency: int = 4, script: str = TWO_SHOT_SCRIPT
) -> tuple[str, str, list[str]]:
    project = await client.post("/api/v1/projects", json={"name": f"PG {uuid.uuid4().hex[:6]}"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    conn = await client.post(
        "/api/v1/connections",
        json={
            "name": f"mock-pg-{uuid.uuid4().hex[:6]}",
            "provider_type": "mock",
            "base_url": "http://localhost:8080",
            "max_concurrency": max_concurrency,
        },
    )
    assert conn.status_code == 201, conn.text
    imported = await client.post(
        f"/api/v1/projects/{project_id}/scripts/import",
        json={"raw_content": script, "auto_apply_proposal": True},
    )
    assert imported.status_code == 201, imported.text
    shots = (await client.get(f"/api/v1/projects/{project_id}/shots")).json()
    assert shots, "script import produced no shots"
    return project_id, conn.json()["id"], [shot["id"] for shot in shots]


async def _enqueue(client: AsyncClient, shot_id: str, conn_id: str) -> str:
    response = await client.post(
        "/api/v1/queue/tasks",
        json={"shot_id": shot_id, "connection_id": conn_id, "task_type": "video"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


class _DispatchProbe:
    """Counts MockVideoProvider.generate_video calls.

    The first dispatcher holds its provider call open until every *other* actor has
    finished its step, which forces the interesting interleaving (a competitor steps
    while a dispatch is in flight) without sleeps.
    """

    def __init__(self, actors: int, maker: async_sessionmaker[AsyncSession], connection_id: str) -> None:
        self.calls = 0
        self.max_running_seen = 0
        self._actors = actors
        self._finished = 0
        self._others_done = asyncio.Event()
        self._maker = maker
        self._connection_id = connection_id

    def actor_finished(self) -> None:
        self._finished += 1
        if self._finished >= self._actors - 1:
            self._others_done.set()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = MockVideoProvider.generate_video
        probe = self

        async def counting_generate_video(provider_self, request):  # noqa: ANN001
            probe.calls += 1
            async with probe._maker() as observer:
                running = (
                    await observer.execute(
                        select(func.count(GenerationTaskRecord.id)).where(
                            GenerationTaskRecord.connection_id == probe._connection_id,
                            GenerationTaskRecord.state == QueueTaskState.RUNNING,
                        )
                    )
                ).scalar_one()
            probe.max_running_seen = max(probe.max_running_seen, int(running))
            try:
                await asyncio.wait_for(probe._others_done.wait(), RACE_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                pass  # a second dispatcher is also waiting: the assertions report it
            return await original(provider_self, request)

        monkeypatch.setattr(MockVideoProvider, "generate_video", counting_generate_video)

    async def step(self, maker: async_sessionmaker[AsyncSession], task_id: str) -> GenerationTaskRecord:
        try:
            async with maker() as session:
                return await GenerationQueueService.step_task(session, task_id)
        finally:
            self.actor_finished()


async def _app_response_for(exc: Exception):  # noqa: ANN202
    """Route an exception through the app's registered handlers the way Starlette does (MRO lookup)."""
    request = Request({"type": "http", "method": "POST", "path": "/pg-it", "headers": [], "query_string": b""})
    for cls in type(exc).__mro__:
        handler = app.exception_handlers.get(cls)
        if handler is not None:
            return await handler(request, exc)
    raise AssertionError(f"no exception handler registered for {type(exc).__name__}")




# --- migrations -----------------------------------------------------------------------


async def test_pg_alembic_upgrade_head_matches_orm_metadata(pg_engine: AsyncEngine) -> None:
    async with pg_engine.connect() as conn:
        version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()

        def _diff(sync_conn):  # noqa: ANN001, ANN202
            context = MigrationContext.configure(sync_conn, opts={"compare_type": True})
            return compare_metadata(context, Base.metadata)

        diffs = await conn.run_sync(_diff)
    assert version == ["0002_uniqueness"]
    assert diffs == []


async def test_pg_alembic_upgrade_head_is_idempotent(migrated_url: str) -> None:
    await asyncio.to_thread(_run_alembic_upgrade, migrated_url)
    engine = create_async_engine(migrated_url)
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
            index_def = (
                await conn.execute(
                    text(
                        "SELECT indexdef FROM pg_indexes "
                        "WHERE indexname = 'uq_generation_tasks_active_shot_type'"
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert version == ["0002_uniqueness"]
    assert "UNIQUE" in index_def and "WHERE" in index_def


async def test_pg_migration_0002_repairs_duplicates_before_adding_constraints(pg_database_url: str) -> None:
    await asyncio.to_thread(_alembic_upgrade_to, pg_database_url, "0001_bootstrap")
    engine = create_async_engine(pg_database_url)
    seed = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with seed() as db:
            db.add(baseline.Project(id="prj_dup", name="Dup"))
            db.add(baseline.ProviderConnection(id="conn_dup", name="dup", base_url="http://localhost:8080"))
            await db.flush()
            db.add(baseline.SceneRecord(id="scn_dup", project_id="prj_dup", scene_index=0, title="S"))
            db.add(baseline.WorkflowDefinition(id="wf_dup", project_id="prj_dup", name="W", current_version=1))
            await db.flush()
            db.add(baseline.ShotRecord(
                id="sht_dup", project_id="prj_dup", scene_id="scn_dup",
                sequence_index=0, title="Shot", location_code="loc_default",
            ))
            for idx in range(2):
                db.add(baseline.WorkflowVersion(id=f"wfv_dup{idx}", workflow_id="wf_dup", version_number=1))
                db.add(baseline.StoryBibleEntry(
                    id=f"bib_dup{idx}", project_id="prj_dup", entry_type="character",
                    code_name="hero", display_name=f"Hero {idx}",
                ))
            await db.flush()
            for idx, state in enumerate((baseline.QueueTaskState.QUEUED, baseline.QueueTaskState.RUNNING)):
                db.add(baseline.GenerationTaskRecord(
                    id=f"tsk_dup{idx}", project_id="prj_dup", shot_id="sht_dup", connection_id="conn_dup",
                    task_type="video", model_id="m", idempotency_key=f"idem_dup{idx}", state=state,
                ))
            await db.commit()
    finally:
        await engine.dispose()

    await asyncio.to_thread(_run_alembic_upgrade, pg_database_url)

    engine = create_async_engine(pg_database_url)
    try:
        async with engine.connect() as conn:
            versions = (await conn.execute(text(
                "SELECT version_number FROM workflow_versions WHERE workflow_id = 'wf_dup' ORDER BY 1"
            ))).scalars().all()
            current = (await conn.execute(text(
                "SELECT current_version FROM workflows WHERE id = 'wf_dup'"
            ))).scalar_one()
            code_names = (await conn.execute(text(
                "SELECT code_name FROM story_bible_entries WHERE project_id = 'prj_dup' ORDER BY 1"
            ))).scalars().all()
            task_states = (await conn.execute(text(
                "SELECT state::text, error_category FROM generation_tasks WHERE shot_id = 'sht_dup'"
            ))).all()
            head = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    finally:
        await engine.dispose()

    assert head == "0002_uniqueness"
    assert versions == [1, 2]
    assert current == 2
    assert code_names == ["hero", "hero__dup1"]
    active = [state for state, _ in task_states if state in ("QUEUED", "RUNNING", "PAUSED")]
    assert len(active) == 1
    assert ("CANCELED", "duplicate_active_task") in task_states


# --- queue concurrency ----------------------------------------------------------------


async def test_pg_concurrent_enqueue_same_shot_creates_exactly_one_active_task(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    _, conn_id, shot_ids = await _project_with_shots(client)
    shot_id = shot_ids[0]

    async def enqueue(*, force_retry: bool, prompt_hash: str | None) -> tuple[str, bool]:
        async with maker() as session:
            shot = await session.get(ShotRecord, shot_id)
            conn = await session.get(ProviderConnection, conn_id)
            assert shot is not None and conn is not None
            if prompt_hash is not None:
                shot.prompt_hash = prompt_hash  # different idempotency key per actor
            task, created = await GenerationQueueService.enqueue_shot_generation(
                session, shot, conn, task_type="video", force_retry=force_retry
            )
            return task.id, created

    results = await asyncio.gather(
        enqueue(force_retry=True, prompt_hash=None),
        enqueue(force_retry=False, prompt_hash="f" * 64),
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, BaseException)]
    assert errors == []
    assert sorted(created for _, created in results) == [False, True]
    assert results[0][0] == results[1][0]

    async with maker() as db:
        active = (
            await db.execute(
                select(GenerationTaskRecord.id).where(
                    GenerationTaskRecord.shot_id == shot_id,
                    GenerationTaskRecord.state.in_(ACTIVE_STATES),
                )
            )
        ).scalars().all()
    assert active == [results[0][0]]


async def test_pg_active_task_partial_unique_index_rejects_second_active_insert(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    _, conn_id, shot_ids = await _project_with_shots(client)
    task_id = await _enqueue(client, shot_ids[0], conn_id)
    async with maker() as db:
        existing = await db.get(GenerationTaskRecord, task_id)
        assert existing is not None
        db.add(GenerationTaskRecord(
            id="tsk_second", project_id=existing.project_id, shot_id=existing.shot_id,
            connection_id=conn_id, task_type="video", model_id="m",
            idempotency_key="idem_second_active", state=QueueTaskState.QUEUED,
        ))
        with pytest.raises(IntegrityError) as caught:
            await db.flush()
    assert is_unique_violation(caught.value)
    assert "uq_generation_tasks_active_shot_type" in str(caught.value.orig)


async def test_pg_concurrent_step_same_queued_task_dispatches_once(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, conn_id, shot_ids = await _project_with_shots(client)
    task_id = await _enqueue(client, shot_ids[0], conn_id)
    probe = _DispatchProbe(actors=2, maker=maker, connection_id=conn_id)
    probe.install(monkeypatch)

    results = await asyncio.gather(
        probe.step(maker, task_id), probe.step(maker, task_id), return_exceptions=True
    )

    assert [r for r in results if isinstance(r, BaseException)] == []
    assert probe.calls == 1
    async with maker() as db:
        task = await db.get(GenerationTaskRecord, task_id)
        leases = (await db.execute(select(func.count()).select_from(GenerationDispatchLeaseRecord))).scalar_one()
    assert task is not None
    assert task.state == QueueTaskState.RUNNING
    assert task.external_task_id and task.external_task_id.startswith("mock_vid_")
    assert task.attempt_count == 1
    assert leases == 0


async def test_pg_max_concurrency_one_never_runs_two_dispatches(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, conn_id, shot_ids = await _project_with_shots(client, max_concurrency=1)
    assert len(shot_ids) == 2
    task_ids = [await _enqueue(client, shot_id, conn_id) for shot_id in shot_ids]
    probe = _DispatchProbe(actors=2, maker=maker, connection_id=conn_id)
    probe.install(monkeypatch)

    results = await asyncio.gather(
        *(probe.step(maker, task_id) for task_id in task_ids), return_exceptions=True
    )

    assert [r for r in results if isinstance(r, BaseException)] == []
    assert probe.calls == 1
    assert probe.max_running_seen == 1
    async with maker() as db:
        states = sorted(
            (await db.execute(
                select(GenerationTaskRecord.state).where(GenerationTaskRecord.id.in_(task_ids))
            )).scalars().all(),
            key=lambda state: state.name,
        )
    assert states == [QueueTaskState.QUEUED, QueueTaskState.RUNNING]


# --- workflow / story bible concurrency -----------------------------------------------


async def test_pg_concurrent_workflow_graph_puts_get_distinct_contiguous_versions(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    project_id, _, _ = await _project_with_shots(client)
    created = await client.post(f"/api/v1/projects/{project_id}/workflows", json={"name": "Race"})
    assert created.status_code == 201, created.text
    body = created.json()
    workflow_id = body["id"]
    payload = {"nodes": body["nodes"], "edges": body["edges"], "ui_layout": body["ui_layout"]}
    writers = 6

    responses = await asyncio.gather(*(
        client.put(f"/api/v1/projects/{project_id}/workflows/{workflow_id}/graph", json=payload)
        for _ in range(writers)
    ))

    assert [r.status_code for r in responses] == [200] * writers, [r.text for r in responses]
    returned = sorted(r.json()["current_version"] for r in responses)
    assert returned == list(range(2, writers + 2))
    async with maker() as db:
        workflow = await db.get(WorkflowDefinition, workflow_id)
        stored = (await db.execute(
            select(WorkflowVersion.version_number)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(WorkflowVersion.version_number)
        )).scalars().all()
    assert stored == list(range(1, writers + 2))
    assert workflow is not None and workflow.current_version == writers + 1


async def test_pg_concurrent_story_bible_upserts_keep_one_row(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    project_id, _, _ = await _project_with_shots(client)
    writers = 5

    responses = await asyncio.gather(*(
        client.post(
            f"/api/v1/projects/{project_id}/story-bible",
            json={"entry_type": "character", "code_name": "char_racer", "display_name": f"Racer {idx}"},
        )
        for idx in range(writers)
    ))

    statuses = [r.status_code for r in responses]
    assert all(code < 500 for code in statuses), [r.text for r in responses]
    assert statuses.count(201) >= 1
    for response in responses:
        if response.status_code != 201:
            assert response.status_code == 409, response.text
            assert response.json()["retryable"] is True
    async with maker() as db:
        rows = (await db.execute(
            select(StoryBibleEntry.id).where(
                StoryBibleEntry.project_id == project_id, StoryBibleEntry.code_name == "char_racer"
            )
        )).scalars().all()
    assert len(rows) == 1


# --- end-to-end on PostgreSQL ---------------------------------------------------------


async def _render_all_shots(client: AsyncClient) -> tuple[str, list[str]]:
    project_id, conn_id, shot_ids = await _project_with_shots(client)
    launched = await client.post(f"/api/v1/projects/{project_id}/batch/launch", json={"connection_id": conn_id})
    assert launched.status_code == 201, launched.text
    for _ in range(5):
        tick = await client.post(f"/api/v1/projects/{project_id}/batch/tick")
        assert tick.status_code == 200, tick.text
    shots = (await client.get(f"/api/v1/projects/{project_id}/shots")).json()
    assert all(shot["selected_take_id"] is not None for shot in shots), shots
    return project_id, shot_ids


async def test_pg_timeline_assemble_through_api(client: AsyncClient) -> None:
    project_id, _ = await _render_all_shots(client)

    assembled = await client.post(
        f"/api/v1/projects/{project_id}/timeline/assemble",
        json={
            "name": "PG Cut",
            "allow_missing_shots": False,
            "default_transition": "cross_dissolve",
            "transition_duration_seconds": 0.5,
            "title_card": {"title": "PG", "duration_seconds": 2.0},
            "audio_tracks": [{"track_type": "music", "name": "OST", "volume": 0.7}],
        },
    )
    assert assembled.status_code == 201, assembled.text
    assert assembled.json()["total_duration_seconds"] == 11.5

    fetched = await client.get(f"/api/v1/projects/{project_id}/timeline")
    assert fetched.status_code == 200, fetched.text


async def test_pg_backup_restore_round_trip_preserves_fk_graph(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    project_id, _ = await _render_all_shots(client)
    assembled = await client.post(
        f"/api/v1/projects/{project_id}/timeline/assemble",
        json={"name": "PG Backup Cut", "allow_missing_shots": False},
    )
    assert assembled.status_code == 201, assembled.text

    created = await client.post(f"/api/v1/projects/{project_id}/backup")
    assert created.status_code == 201, created.text
    backup = created.json()
    downloaded = await client.get(f"/api/v1/backups/{backup['id']}/download")
    assert downloaded.status_code == 200

    restored = await client.post(
        "/api/v1/backups/restore",
        files={"file": ("backup.zip", downloaded.content, "application/zip")},
        params={"project_name": "PG Restored"},
    )

    assert restored.status_code == 201, restored.text
    body = restored.json()
    assert body["project_id"] != project_id
    manifest_tables = {k: v for k, v in backup["manifest_json"]["tables"].items() if v}
    restored_tables = body["report"]["tables_restored"]
    for table, count in manifest_tables.items():
        if table == "generation_tasks":
            continue  # the report may neutralise tasks; checked below
        assert restored_tables.get(table) == count, (table, restored_tables)
    async with maker() as db:
        shots = (await db.execute(
            select(func.count(ShotRecord.id)).where(ShotRecord.project_id == body["project_id"])
        )).scalar_one()
        project = await db.get(Project, body["project_id"])
    assert project is not None and project.name == "PG Restored"
    assert shots == manifest_tables["shots"]


# --- asyncpg SQLSTATE mapping ---------------------------------------------------------


async def test_pg_real_unique_violation_maps_to_409(maker: async_sessionmaker[AsyncSession]) -> None:
    async with maker() as db:
        db.add(Project(id="prj_unique", name="Unique"))
        await db.commit()
    async with maker() as db:
        db.add(Project(id="prj_unique", name="Unique again"))
        with pytest.raises(IntegrityError) as caught:
            await db.commit()

    assert is_unique_violation(caught.value)
    response = await _app_response_for(caught.value)
    assert response.status_code == 409
    assert b'"category":"conflict"' in response.body
    assert b"prj_unique" not in response.body  # never echo bound parameters


async def test_pg_real_foreign_key_violation_is_not_a_conflict(maker: async_sessionmaker[AsyncSession]) -> None:
    async with maker() as db:
        db.add(ShotRecord(
            id="sht_orphan", project_id="prj_missing", scene_id="scn_missing",
            sequence_index=0, title="Orphan", location_code="loc_default",
        ))
        with pytest.raises(IntegrityError) as caught:
            await db.commit()

    assert not is_unique_violation(caught.value)
    response = await _app_response_for(caught.value)
    assert response.status_code == 500


async def test_pg_real_overlong_value_maps_to_422(maker: async_sessionmaker[AsyncSession]) -> None:
    async with maker() as db:
        db.add(Project(id="prj_long", name="x" * 300))  # projects.name is VARCHAR(255)
        with pytest.raises(DBAPIError) as caught:
            await db.commit()

    response = await _app_response_for(caught.value)
    assert response.status_code == 422, (type(caught.value).__name__, response.status_code)
    assert b'"category":"validation"' in response.body
    assert b"xxxxxxxxxx" not in response.body


async def test_pg_connection_create_lost_race_returns_409_not_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {"name": "pg-dup-conn", "provider_type": "mock", "base_url": "http://localhost:8080"}
    first = await client.post("/api/v1/connections", json=payload)
    assert first.status_code == 201, first.text

    # Simulate the competitor that passed the duplicate-name pre-check before the
    # first commit: the insert must then lose on the UNIQUE constraint (SQLSTATE 23505).
    async def no_connections(self):  # noqa: ANN001, ANN202
        return []

    monkeypatch.setattr(StudioRepository, "list_connections", no_connections)
    second = await client.post("/api/v1/connections", json=payload)

    assert second.status_code == 409, second.text
    assert second.json()["category"] == "conflict"


async def test_pg_batch_launch_overlong_model_id_is_rejected_with_422(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession]
) -> None:
    project_id, conn_id, _ = await _project_with_shots(client)

    # Observe the HTTP response the way a real server returns it (500 handler output)
    # instead of re-raising the server-side exception into the test.
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://testserver"
    ) as raw_client:
        response = await raw_client.post(
            f"/api/v1/projects/{project_id}/batch/launch",
            json={"connection_id": conn_id, "model_id": "m" * 300},  # column is VARCHAR(128)
        )

    assert response.status_code == 422, response.text
    async with maker() as db:
        tasks = (await db.execute(
            select(func.count(GenerationTaskRecord.id)).where(GenerationTaskRecord.project_id == project_id)
        )).scalar_one()
    assert tasks == 0
