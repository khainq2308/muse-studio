from __future__ import annotations
import os
import tempfile
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_m3_test_")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["MEDIA_ROOT"] = _TEMP_MEDIA_DIR

from muse_orchestrator.main import app
from muse_orchestrator.core.database import get_db
from muse_orchestrator.models.domain import Base


@pytest.fixture()
async def client():
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_m3_workflow_canvas_approval_gate_and_selective_cache_invalidation(client: AsyncClient):
    # 1. Tạo Project & Khởi tạo Workflow mẫu 7 bước của M3
    p_res = await client.post("/api/v1/projects", json={"name": "M3 Workflow Film Project"})
    proj_id = p_res.json()["id"]

    wf_res = await client.post(
        f"/api/v1/projects/{proj_id}/workflows",
        json={"name": "Main Production DAG", "use_default_m3_template": True},
    )
    assert wf_res.status_code == 201
    wf_data = wf_res.json()
    wf_id = wf_data["id"]
    assert len(wf_data["execution_order"]) == 7
    assert wf_data["nodes"][0]["config"]["source"] == "project_latest_script"
    assert wf_data["edges"][0]["edge_id"] == "e1"

    # Dry-run phải trả contract riêng và không thực thi node.
    dry_run_res = await client.post(
        f"/api/v1/projects/{proj_id}/workflows/{wf_id}/runs",
        json={"run_mode": "dry_run"},
    )
    assert dry_run_res.status_code == 201
    dry_run = dry_run_res.json()
    assert dry_run["run_mode"] == "dry_run"
    assert dry_run["execution_mode"] == "planning_only"
    assert len(dry_run["execution_plan_order"]) == 7
    assert len(dry_run["cache_keys"]) == 7

    # 2. Chạy Workflow lần 1 -> Phải thực thi 6 bước đầu và dừng tại bước 7 (ManualApproval)
    run1_res = await client.post(
        f"/api/v1/projects/{proj_id}/workflows/{wf_id}/runs",
        json={"run_mode": "full"},
    )
    assert run1_res.status_code == 201
    run1 = run1_res.json()
    assert run1["status"] == "waiting_approval"
    states_by_id = {n["node_id"]: n["state"] for n in run1["node_executions"]}
    assert states_by_id["n_prompt"] == "succeeded"
    assert states_by_id["n_approval"] == "waiting_approval"
    assert run1["execution_mode"] == "simulation"
    prompt_execution = next(n for n in run1["node_executions"] if n["node_id"] == "n_prompt")
    assert prompt_execution["output_refs"]["status"] == "simulated"
    assert prompt_execution["output_refs"]["placeholder"] is True

    # 3. Đạo diễn bấm Approve tại cổng n_approval -> Run chuyển sang succeeded
    app_res = await client.post(
        f"/api/v1/projects/{proj_id}/workflows/{wf_id}/runs/{run1['run_id']}/nodes/n_approval/approve"
    )
    assert app_res.status_code == 200
    assert app_res.json()["status"] == "succeeded"
    assert app_res.json()["execution_mode"] == "simulation"
    assert len(app_res.json()["node_executions"]) == 7

    # 4. Kéo thả vị trí UI Layout trên Canvas -> Không được làm mất Cache!
    layout_res = await client.patch(
        f"/api/v1/projects/{proj_id}/workflows/{wf_id}/layout",
        json={"ui_layout": {"n_script_in": {"x": 500, "y": 300}}},
    )
    assert layout_res.status_code == 200
    assert layout_res.json()["ui_layout_saved"] is True

    # 5. Chạy lại Workflow lần 2 (không sửa logic) -> Cả 7 node đều phải Cache Hit (state = skipped)
    run2_res = await client.post(
        f"/api/v1/projects/{proj_id}/workflows/{wf_id}/runs",
        json={"run_mode": "full"},
    )
    run2 = run2_res.json()
    assert run2["status"] == "succeeded"
    assert all(n["cache_hit"] is True and n["state"] == "skipped" for n in run2["node_executions"])

    # 6. Sửa cấu hình của node thứ 5 (n_shots: đổi default_duration từ 5.0 sang 8.0)
    # -> 4 node đầu tiên (n_script_in, n_analyze, n_bible, n_scenes) PHẢI giữ nguyên Cache Hit (skipped)
    # -> Chỉ có n_shots, n_prompt, n_approval bị đổi Merkle Hash và phải chạy mới!
    modified_nodes = wf_data["nodes"]
    for nd in modified_nodes:
        if nd["node_id"] == "n_shots":
            nd["config"] = {"default_duration": 8.0}

    upd_res = await client.put(
        f"/api/v1/projects/{proj_id}/workflows/{wf_id}/graph",
        json={"nodes": modified_nodes, "edges": wf_data["edges"], "ui_layout": {}},
    )
    assert upd_res.status_code == 200
    assert upd_res.json()["current_version"] == 2

    run3_res = await client.post(
        f"/api/v1/projects/{proj_id}/workflows/{wf_id}/runs",
        json={"run_mode": "full"},
    )
    run3 = run3_res.json()
    exec_map = {n["node_id"]: n for n in run3["node_executions"]}

    # 4 node upstream dùng lại cache
    for upstream_id in ["n_script_in", "n_analyze", "n_bible", "n_scenes"]:
        assert exec_map[upstream_id]["cache_hit"] is True
        assert exec_map[upstream_id]["state"] == "skipped"

    # Node bị sửa và các node downstream của nó chạy mới (cache_hit = False)
    assert exec_map["n_shots"]["cache_hit"] is False
    assert exec_map["n_shots"]["state"] == "succeeded"
    assert exec_map["n_prompt"]["cache_hit"] is False
    assert exec_map["n_prompt"]["state"] == "succeeded"
    assert exec_map["n_approval"]["cache_hit"] is False
    assert exec_map["n_approval"]["state"] == "waiting_approval"
