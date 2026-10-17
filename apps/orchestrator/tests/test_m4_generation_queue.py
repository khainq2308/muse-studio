from __future__ import annotations
import os
import tempfile
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_m4_test_")
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
async def test_m4_async_video_generation_idempotency_and_asset_persistence(client: AsyncClient):
    # 1. Tạo Project, Connection Mock (max_concurrency=1), và Import Script
    p_res = await client.post("/api/v1/projects", json={"name": "M4 Render Film"})
    proj_id = p_res.json()["id"]

    c_res = await client.post(
        "/api/v1/connections",
        json={"name": "mock-render-pool", "provider_type": "mock", "base_url": "http://localhost:8080", "max_concurrency": 1},
    )
    conn_id = c_res.json()["id"]

    await client.post(
        f"/api/v1/projects/{proj_id}/scripts/import",
        json={"raw_content": "SCENE 1: Rooftop\nSHOT 1: Hero looks at city\n- Action: Wind blowing coat\n", "auto_apply_proposal": True},
    )
    shots = (await client.get(f"/api/v1/projects/{proj_id}/shots")).json()
    shot_id = shots[0]["id"]

    # 2. Enqueue Task tạo Video & Kiểm tra Idempotency (Gửi trùng lần 2 không sinh task mới)
    t1_res = await client.post(
        "/api/v1/queue/tasks",
        json={"shot_id": shot_id, "connection_id": conn_id, "task_type": "video", "idempotency_key": "idem_shot_01_v1"},
    )
    assert t1_res.status_code == 201
    t1 = t1_res.json()
    assert t1["created_new"] is True
    task_id = t1["id"]

    t1_dup = await client.post(
        "/api/v1/queue/tasks",
        json={"shot_id": shot_id, "connection_id": conn_id, "task_type": "video", "idempotency_key": "idem_shot_01_v1"},
    )
    assert t1_dup.json()["created_new"] is False
    assert t1_dup.json()["id"] == task_id

    # 3. Step 1 (Dispatch): Gửi lên provider -> nhận external_task_id, chuyển sang running
    s1 = (await client.post(f"/api/v1/queue/tasks/{task_id}/step")).json()
    assert s1["state"] == "running"
    assert s1["external_task_id"].startswith("mock_vid_")

    # 4. Step 2 (Poll #1): Provider vẫn đang xử lý -> giữ nguyên running và tăng backoff
    s2 = (await client.post(f"/api/v1/queue/tasks/{task_id}/step")).json()
    assert s2["state"] == "running"
    assert s2["poll_count"] == 1
    assert s2["next_poll_delay_seconds"] >= 5.0

    # 5. Step 3 (Poll #2): Hoàn tất -> Tự tải video vào Asset Library & Tạo Take #1
    s3 = (await client.post(f"/api/v1/queue/tasks/{task_id}/step")).json()
    assert s3["state"] == "succeeded"
    assert s3["result_asset_id"] is not None
    assert s3["take_id"] is not None
    assert s3["account_used"] == "mock_pool_acc_01"

    # Kiểm tra Take đã được gắn làm selected_take_id của Shot
    takes = (await client.get(f"/api/v1/queue/shots/{shot_id}/takes")).json()
    assert len(takes) == 1
    assert takes[0]["is_selected"] is True


@pytest.mark.asyncio
async def test_m4_retry_classification_and_poll_timeout_isolation(client: AsyncClient):
    p_res = await client.post("/api/v1/projects", json={"name": "M4 Error Classification"})
    proj_id = p_res.json()["id"]
    c_res = await client.post(
        "/api/v1/connections",
        json={"name": "mock-error-pool", "provider_type": "mock", "base_url": "http://localhost:8080", "max_concurrency": 5},
    )
    conn_id = c_res.json()["id"]

    script = """
SCENE 1: Lab
SHOT 1: Transient 503 Retry Shot
- Action: [SIMULATE_503_ONCE] Scientist types on console
SHOT 2: Auth Error 401 Shot
- Action: [SIMULATE_401] Door opens
SHOT 3: Stuck Poll Timeout Shot
- Action: [SIMULATE_STUCK_POLL] Clock ticks
"""
    await client.post(f"/api/v1/projects/{proj_id}/scripts/import", json={"raw_content": script, "auto_apply_proposal": True})
    shots = (await client.get(f"/api/v1/projects/{proj_id}/shots")).json()
    shot_503, shot_401, shot_timeout = shots[0], shots[1], shots[2]

    # CASE A: Lỗi 503 tạm thời -> Lần 1 giữ ở state=queued (retryable=True), Lần 2 dispatch thành công!
    t_503 = (await client.post("/api/v1/queue/tasks", json={"shot_id": shot_503["id"], "connection_id": conn_id})).json()
    step_fail_once = (await client.post(f"/api/v1/queue/tasks/{t_503['id']}/step")).json()
    assert step_fail_once["state"] == "queued"
    assert step_fail_once["retryable"] is True
    assert step_fail_once["attempt_count"] == 1

    step_recover = (await client.post(f"/api/v1/queue/tasks/{t_503['id']}/step")).json()
    assert step_recover["state"] == "running"
    assert step_recover["external_task_id"] is not None

    # CASE B: Lỗi 401 Auth -> Dừng ngay lập tức (state=failed, retryable=False), KHÔNG retry vô hạn, và đổi Connection sang auth_error
    t_401 = (await client.post("/api/v1/queue/tasks", json={"shot_id": shot_401["id"], "connection_id": conn_id})).json()
    step_auth_err = (await client.post(f"/api/v1/queue/tasks/{t_401['id']}/step")).json()
    assert step_auth_err["state"] == "failed"
    assert step_auth_err["retryable"] is False
    assert step_auth_err["error_category"] == "auth_failed"

    conns = (await client.get("/api/v1/connections")).json()
    target_conn = next(c for c in conns if c["id"] == conn_id)
    assert target_conn["health_state"] == "auth_error"

    # CASE C: Task bị treo quá max_polls=2 -> Đánh dấu poll_timeout (phân biệt rõ với failed)
    t_stuck = (await client.post("/api/v1/queue/tasks", json={"shot_id": shot_timeout["id"], "connection_id": conn_id, "max_polls": 2})).json()
    await client.post(f"/api/v1/queue/tasks/{t_stuck['id']}/step")  # Dispatch
    await client.post(f"/api/v1/queue/tasks/{t_stuck['id']}/step")  # Poll 1
    await client.post(f"/api/v1/queue/tasks/{t_stuck['id']}/step")  # Poll 2
    step_timeout = (await client.post(f"/api/v1/queue/tasks/{t_stuck['id']}/step")).json()  # Poll 3 -> Vượt ngưỡng max_polls=2
    assert step_timeout["state"] == "poll_timeout"
    assert step_timeout["error_category"] == "upstream_poll_timeout"
