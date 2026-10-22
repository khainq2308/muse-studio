from __future__ import annotations
import os
import tempfile
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_m5_test_")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["MEDIA_ROOT"] = _TEMP_MEDIA_DIR

from muse_orchestrator.main import app  # noqa: E402 -- environment must be configured before app/database import
from muse_orchestrator.core.database import get_db  # noqa: E402 -- environment must be configured before app/database import
from muse_orchestrator.models.domain import Base  # noqa: E402 -- environment must be configured before app/database import


class _FakeArqPool:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple, dict]] = []
        self._job_ids: set[str] = set()

    async def enqueue_job(self, function: str, *args, **kwargs):
        job_id = kwargs.get("_job_id")
        if job_id and job_id in self._job_ids:
            return None
        if job_id:
            self._job_ids.add(job_id)
        self.jobs.append((function, args, kwargs))
        return object()

    async def aclose(self) -> None:
        return None


@pytest.fixture()
async def client():
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    previous_pool = getattr(app.state, "arq_pool", None)
    app.state.arq_pool = _FakeArqPool()
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
        if previous_pool is None:
            if hasattr(app.state, "arq_pool"):
                delattr(app.state, "arq_pool")
        else:
            app.state.arq_pool = previous_pool


BATCH_SCRIPT_4_SHOTS = """
SCENE 1: Neo-Tokyo Detective Agency
SHOT 1: Detective opens case file
- Character: Kaito
- Action: Opens leather folder on desk
- Duration: 4.0

SHOT 2: Close up on photograph
- Character: Kaito
- Action: Picks up mysterious photo
- Duration: 4.0

SHOT 3: Corrupted transmission shot
- Character: Kaito
- Action: [SIMULATE_401] Static screen flickers
- Duration: 4.0

SHOT 4: Looking out the rainy window
- Character: Kaito
- Action: Walks to the window and watches neon lights
- Duration: 5.0
"""


@pytest.mark.asyncio
async def test_m5_batch_fan_out_fault_isolation_and_selective_rerun(client: AsyncClient):
    # 1. Tạo Project & Connection có max_concurrency = 2
    p_res = await client.post("/api/v1/projects", json={"name": "M5 Batch Detective Film"})
    proj_id = p_res.json()["id"]

    c_res = await client.post(
        "/api/v1/connections",
        json={"name": "mock-batch-conn", "provider_type": "mock", "base_url": "http://localhost:8080", "max_concurrency": 2},
    )
    conn_id = c_res.json()["id"]

    await client.post(
        f"/api/v1/projects/{proj_id}/scripts/import",
        json={"raw_content": BATCH_SCRIPT_4_SHOTS, "auto_apply_proposal": True},
    )
    shots = (await client.get(f"/api/v1/projects/{proj_id}/shots")).json()
    assert len(shots) == 4
    s1, s2, s3_fail, s4 = shots[0], shots[1], shots[2], shots[3]

    # 2. Đặt Shot 4 làm BLOCKED_REFERENCE (thiếu ảnh tham chiếu) để kiểm tra bộ chặn Batch
    await client.patch(
        f"/api/v1/projects/{proj_id}/shots/{s4['id']}",
        json={"requires_reference_lock": True, "reference_asset_ids": []},
    )

    # 3. Launch Batch Render lần 1 -> Phải enqueue 3 shots (s1, s2, s3_fail) và chặn 1 shot (s4)
    b1 = (
        await client.post(
            f"/api/v1/projects/{proj_id}/batch/launch",
            json={"connection_id": conn_id, "only_changed": True},
        )
    ).json()
    assert b1["total_shots"] == 4
    assert b1["enqueued_count"] == 3
    assert b1["blocked_count"] == 1
    assert s4["id"] in b1["blocked_shot_ids"]

    # 4. Tick #1: Vì max_concurrency=2, chỉ có tối đa 2 tasks được chạy RUNNING cùng lúc!
    tick1 = (await client.post(f"/api/v1/projects/{proj_id}/batch/tick")).json()
    assert tick1["queue_summary"].get("running", 0) <= 2

    # Chạy thêm các chu kỳ tick cho đến khi toàn bộ hàng đợi xử lý xong
    for _ in range(5):
        await client.post(f"/api/v1/projects/{proj_id}/batch/tick")

    shots_after = {s["id"]: s for s in (await client.get(f"/api/v1/projects/{proj_id}/shots")).json()}
    # Tiêu chí nghiệm thu #6: Shot 3 lỗi (failed) KHÔNG làm mất thành quả của Shot 1 và Shot 2 (succeeded)!
    assert shots_after[s1["id"]]["generation_state"] == "succeeded"
    assert shots_after[s2["id"]]["generation_state"] == "succeeded"
    assert shots_after[s3_fail["id"]]["generation_state"] == "failed"
    assert shots_after[s4["id"]]["generation_state"] == "blocked_reference"

    # 5. Sửa nội dung Shot 3 (bỏ [SIMULATE_401]) và bấm Retry Failed -> Chỉ chạy lại đúng Shot 3!
    await client.patch(
        f"/api/v1/projects/{proj_id}/shots/{s3_fail['id']}",
        json={"action": "Screen stabilizes showing suspect profile"},
    )
    retry_res = (await client.post(f"/api/v1/projects/{proj_id}/batch/retry-failed")).json()
    assert retry_res["retried_count"] == 1

    for _ in range(4):
        await client.post(f"/api/v1/projects/{proj_id}/batch/tick")

    shots_recovered = {s["id"]: s for s in (await client.get(f"/api/v1/projects/{proj_id}/shots")).json()}
    assert shots_recovered[s3_fail["id"]]["generation_state"] == "succeeded"

    # 6. Tiêu chí nghiệm thu #10: Sửa duy nhất Shot 2, sau đó Launch Batch với only_changed=True
    # -> Shot 1 và Shot 3 KHÔNG đổi phải nằm trong skipped_shot_ids (không bị render lại)!
    await client.patch(
        f"/api/v1/projects/{proj_id}/shots/{s2['id']}",
        json={"action": "Zooms in on the timestamp of the photo"},
    )
    b2 = (
        await client.post(
            f"/api/v1/projects/{proj_id}/batch/launch",
            json={"connection_id": conn_id, "only_changed": True},
        )
    ).json()
    assert b2["enqueued_count"] == 1
    assert b2["skipped_unchanged_count"] == 2
    assert s1["id"] in b2["skipped_shot_ids"]
    assert s3_fail["id"] in b2["skipped_shot_ids"]


@pytest.mark.asyncio
async def test_m5_frame_chaining_ab_comparison_and_targeted_regeneration(client: AsyncClient):
    p_res = await client.post("/api/v1/projects", json={"name": "M5 Frame Chain & Takes QA"})
    proj_id = p_res.json()["id"]
    c_res = await client.post(
        "/api/v1/connections",
        json={"name": "mock-chain-conn", "provider_type": "mock", "base_url": "http://localhost:8080", "max_concurrency": 2},
    )
    conn_id = c_res.json()["id"]

    script = """
SCENE 1: Chase Scene
SHOT 1: Hero leaps from roof
- Action: Leaps across neon gap
SHOT 2: Hero lands on opposite roof
- Action: Rolls onto concrete roof
"""
    await client.post(f"/api/v1/projects/{proj_id}/scripts/import", json={"raw_content": script, "auto_apply_proposal": True})
    shots = (await client.get(f"/api/v1/projects/{proj_id}/shots")).json()
    shot1, shot2 = shots[0], shots[1]
    assert shot2["previous_shot_id"] == shot1["id"]

    # 1. Bật chế độ Frame Chaining cho Shot 2 -> Shot 2 phải chờ frame cuối của Shot 1
    await client.patch(
        f"/api/v1/projects/{proj_id}/shots/{shot2['id']}",
        json={"continuity_mode": "frame_chain"},
    )

    # Launch Batch: Shot 1 được enqueue ngay, còn Shot 2 bị tạm hoãn (blocked_upstream_frame)
    launch = (await client.post(f"/api/v1/projects/{proj_id}/batch/launch", json={"connection_id": conn_id})).json()
    assert launch["enqueued_count"] == 1
    assert shot2["id"] in launch["blocked_shot_ids"]

    # Chạy tick cho đến khi Shot 1 xong -> Hệ thống tự tách last_frame của Shot 1,
    # gán vào first_frame của Shot 2, tự động enqueue Shot 2 và chạy hoàn tất cả 2 shots!
    for _ in range(7):
        await client.post(f"/api/v1/projects/{proj_id}/batch/tick")

    updated_shots = {s["id"]: s for s in (await client.get(f"/api/v1/projects/{proj_id}/shots")).json()}
    s1_done = updated_shots[shot1["id"]]
    s2_done = updated_shots[shot2["id"]]
    assert s1_done["generation_state"] == "succeeded"
    assert s2_done["generation_state"] == "succeeded"
    assert s1_done["last_frame_asset_id"] is not None
    assert s2_done["first_frame_asset_id"] == s1_done["last_frame_asset_id"]

    # 2. Đánh dấu lỗi Continuity trên Take #1 của Shot 1 và bấm Regenerate tạo Take #2
    takes_s1 = (await client.get(f"/api/v1/queue/shots/{shot1['id']}/takes")).json()
    take1_id = takes_s1[0]["id"]

    flag_res = (
        await client.post(
            f"/api/v1/projects/{proj_id}/shots/{shot1['id']}/takes/{take1_id}/flag-continuity",
            json={
                "continuity_flagged": True,
                "director_notes": "Áo khoác bị lệch màu ở giây cuối",
                "regenerate_now": True,
                "connection_id": conn_id,
            },
        )
    ).json()
    assert flag_res["continuity_flagged"] is True
    assert flag_res["regenerated_task_id"] is not None

    for _ in range(4):
        await client.post(f"/api/v1/projects/{proj_id}/batch/tick")

    takes_s1_after = (await client.get(f"/api/v1/queue/shots/{shot1['id']}/takes")).json()
    assert len(takes_s1_after) == 2
    take2_id = takes_s1_after[1]["id"]

    # 3. So sánh A/B giữa Take #1 và Take #2
    ab_res = (
        await client.get(
            f"/api/v1/projects/{proj_id}/shots/{shot1['id']}/compare-takes?take_a_id={take1_id}&take_b_id={take2_id}"
        )
    ).json()
    assert ab_res["take_a"]["continuity_flagged"] is True
    assert ab_res["take_b"]["continuity_flagged"] is False

    # 4. Chọn Take #2 làm Selected Take cho Shot 1 -> Frame đầu của Shot 2 tự động cập nhật sang frame cuối của Take #2!
    sel_res = (await client.post(f"/api/v1/queue/shots/{shot1['id']}/takes/{take2_id}/select")).json()
    assert shot2["id"] in sel_res["propagated_downstream_shots"]

    final_shots = {s["id"]: s for s in (await client.get(f"/api/v1/projects/{proj_id}/shots")).json()}
    assert final_shots[shot2["id"]]["first_frame_asset_id"] == takes_s1_after[1]["last_frame_asset_id"]


@pytest.mark.asyncio
async def test_frame_chain_resolves_only_successful_upstream_config_and_allows_new_connection():
    from types import SimpleNamespace
    from muse_orchestrator.api.batch import _resolve_frame_chain_generation_config
    from muse_orchestrator.models.domain import QueueTaskState

    upstream = SimpleNamespace(
        id="task-success",
        project_id="project-a",
        shot_id="shot-upstream",
        state=QueueTaskState.SUCCEEDED,
        connection_id="conn-upstream",
        model_id="model-from-selected-take",
    )
    unrelated_success = SimpleNamespace(
        id="task-other",
        project_id="project-a",
        shot_id="another-shot",
        state=QueueTaskState.SUCCEEDED,
        connection_id="conn-unrelated",
        model_id="unrelated-success-model",
    )
    failed_newer = SimpleNamespace(
        id="task-failed",
        project_id="project-a",
        shot_id="shot-upstream",
        state=QueueTaskState.FAILED,
        connection_id="conn-failed",
        model_id="failed-model-must-not-be-used",
    )
    upstream_shot = SimpleNamespace(
        id="shot-upstream",
        project_id="project-a",
        selected_take_id=None,
    )

    class FakeRepository:
        async def get_shot(self, shot_id: str):
            assert shot_id == "shot-upstream"
            return upstream_shot

        async def get_latest_successful_generation_task_for_shot(self, shot_id: str):
            assert shot_id == "shot-upstream"
            return upstream

        async def list_project_generation_tasks(self, project_id: str):
            assert project_id == "project-a"
            # Includes the failed regenerate to ensure it cannot be a config source.
            return [upstream, unrelated_success, failed_newer]

        async def get_shot_take(self, take_id: str):
            assert take_id == "take-selected"
            return SimpleNamespace(
                project_id="project-a",
                shot_id="shot-upstream",
                task_id="task-selected",
            )

        async def get_generation_task(self, task_id: str):
            assert task_id == "task-selected"
            return SimpleNamespace(
                id="task-selected",
                project_id="project-a",
                shot_id="shot-upstream",
                state=QueueTaskState.SUCCEEDED,
                connection_id="conn-selected",
                model_id="selected-take-model",
            )

    repo = FakeRepository()
    shot = SimpleNamespace(previous_shot_id="shot-upstream")

    assert await _resolve_frame_chain_generation_config(repo, "project-a", shot) == (
        "conn-upstream",
        "model-from-selected-take",
    )
    # The selected take owns the propagated frame, so it overrides a newer successful task.
    upstream_shot.selected_take_id = "take-selected"
    assert await _resolve_frame_chain_generation_config(repo, "project-a", shot) == (
        "conn-selected",
        "selected-take-model",
    )
    upstream_shot.selected_take_id = None
    assert await _resolve_frame_chain_generation_config(
        repo, "project-a", shot, requested_connection_id="conn-unrelated"
    ) == ("conn-unrelated", "unrelated-success-model")
    assert await _resolve_frame_chain_generation_config(
        repo, "project-a", shot, requested_connection_id="conn-new"
    ) == ("conn-new", "model-from-selected-take")
    assert await _resolve_frame_chain_generation_config(
        repo, "project-a", shot, requested_connection_id="conn-failed"
    ) == ("conn-failed", "model-from-selected-take")


@pytest.mark.asyncio
async def test_batch_launch_rejects_unknown_or_foreign_scene_before_creating_job(client: AsyncClient):
    project_a = (
        await client.post("/api/v1/projects", json={"name": "Batch scene owner"})
    ).json()["id"]
    project_b = (
        await client.post("/api/v1/projects", json={"name": "Foreign scene project"})
    ).json()["id"]

    for project_id in (project_a, project_b):
        response = await client.post(
            f"/api/v1/projects/{project_id}/scripts/import",
            json={"raw_content": BATCH_SCRIPT_4_SHOTS, "auto_apply_proposal": True},
        )
        assert response.status_code == 201

    scene_b_id = (await client.get(f"/api/v1/projects/{project_b}/shots")).json()[0]["scene_id"]
    connection = await client.post(
        "/api/v1/connections",
        json={
            "name": "batch-scene-validation-connection",
            "provider_type": "mock",
            "base_url": "http://localhost:8080",
        },
    )
    assert connection.status_code == 201
    connection_id = connection.json()["id"]

    foreign_scene = await client.post(
        f"/api/v1/projects/{project_a}/batch/launch",
        json={"connection_id": connection_id, "scene_id": scene_b_id},
    )
    assert foreign_scene.status_code == 422
    assert "Scene không thuộc project" in foreign_scene.json()["detail"]

    unknown_scene = await client.post(
        f"/api/v1/projects/{project_a}/batch/launch",
        json={"connection_id": connection_id, "scene_id": "scene-that-does-not-exist"},
    )
    assert unknown_scene.status_code == 404
    assert "Scene không tồn tại" in unknown_scene.json()["detail"]
