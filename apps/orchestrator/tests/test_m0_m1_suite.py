from __future__ import annotations

import os
import tempfile
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_test_")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["MEDIA_ROOT"] = _TEMP_MEDIA_DIR

from muse_orchestrator.main import app  # noqa: E402 -- environment must be configured before app/database import
from muse_orchestrator.core.config import SecretRedactor  # noqa: E402 -- environment must be configured before app/database import
from muse_orchestrator.core.database import get_db  # noqa: E402 -- environment must be configured before app/database import
from muse_orchestrator.models.domain import Base  # noqa: E402 -- environment must be configured before app/database import
from muse_orchestrator.providers.base import classify_http_error  # noqa: E402 -- environment must be configured before app/database import
from muse_orchestrator.services.prompt_compiler import (  # noqa: E402 -- environment must be configured before app/database import
    PromptCompiler,
    ShotSpec,
    StyleBibleLock,
    CharacterLock,
    LocationLock,
)
from muse_orchestrator.services.workflow_engine import (  # noqa: E402 -- environment must be configured before app/database import
    WorkflowEngine,
    WorkflowNodeDef,
    WorkflowEdgeDef,
    PortDef,
    WorkflowValidationError,
)


@pytest.fixture()
async def client():
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
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
    await test_engine.dispose()


def test_m0_secret_redactor_and_error_classifier():
    raw = "Proxy http://user:Pass123@1.2.3.4:8080 Authorization: Bearer sk-muse-secret999 Cookie: sid=abc"
    redacted = SecretRedactor.redact_text(raw)
    assert "Pass123" not in redacted
    assert "sk-muse-secret999" not in redacted
    assert "sid=abc" not in redacted

    assert classify_http_error(401, "Unauthorized").retryable is False
    assert classify_http_error(429, "Quota exceeded").retryable is False
    assert classify_http_error(503, "Gateway timeout").retryable is True


def test_m0_prompt_compiler_and_dag_validator():
    style = StyleBibleLock("2D Anime", "Noir", "Blue/Amber", ["3d cgi"])
    chars = {"c1": CharacterLock("c1", "Conan", "glasses", "blue suit", "ast_missing")}
    locs = {"l1": LocationLock("l1", "Cafe", "window", "warm lamp", "night")}
    shot = ShotSpec(
        shot_id="s1",
        scene_id="sc1",
        sequence_index=1,
        subject_ids=["c1"],
        location_id="l1",
        shot_size="close-up",
        camera_angle="eye",
        camera_motion="static",
        composition="rapid orbit around subject",
        action="sips tea",
        action_start="hold",
        action_end="drink",
        continuity_notes="ok",
        duration_seconds=4.0,
        requires_reference_lock=True,
    )
    compiled = PromptCompiler.compile_and_lint(shot, style, chars, locs, available_assets=set())
    assert compiled.has_blockers is True
    codes = {i.code for i in compiled.lint_issues}
    assert "BLOCKED_REFERENCE" in codes
    assert "CAMERA_MOTION_CONFLICT" in codes

    n1 = WorkflowNodeDef("n1", "ScriptInput", outputs={"out": PortDef("out", "Script")})
    n2 = WorkflowNodeDef(
        "n2",
        "StoryBibleBuild",
        inputs={"in": PortDef("in", "Script")},
        outputs={"out": PortDef("out", "Script")},
    )
    with pytest.raises(WorkflowValidationError):
        WorkflowEngine.validate_and_plan(
            [n1, n2],
            [WorkflowEdgeDef("e1", "n1", "out", "n2", "in"), WorkflowEdgeDef("e2", "n2", "out", "n1", "out")],
        )


@pytest.mark.asyncio
async def test_m1_end_to_end_foundation(client: AsyncClient):
    # 1. Create Project & Check Revision #1
    p_res = await client.post("/api/v1/projects", json={"name": "Conan Ep 1", "aspect_ratio": "16:9", "fps": 24})
    assert p_res.status_code == 201
    proj_id = p_res.json()["id"]

    revs = (await client.get(f"/api/v1/projects/{proj_id}/revisions")).json()
    assert len(revs) == 1 and revs[0]["revision_number"] == 1

    # 2. Create Connection with Secrets & Probe Mock
    c_res = await client.post(
        "/api/v1/connections",
        json={
            "name": "mock-conn",
            "provider_type": "mock",
            "base_url": "http://localhost:8080",
            "api_key": "sk-muse-super-secret-key",
            "proxy_display_host": "http://u:SecretPass@10.0.0.1:8080",
        },
    )
    assert c_res.status_code == 201
    assert "sk-muse-super-secret-key" not in c_res.text
    assert "SecretPass" not in c_res.text
    conn_id = c_res.json()["id"]

    probe = (await client.post(f"/api/v1/connections/{conn_id}/test")).json()
    assert probe["health_state"] == "healthy"
    assert len(probe["capabilities_snapshot"]["models"]) == 3

    # 3. Upload Asset & Protect Locked Reference
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    a_res = await client.post(
        "/api/v1/assets/upload",
        data={"project_id": proj_id, "asset_type": "reference", "is_locked_reference": "true"},
        files={"file": ("ref.png", png_bytes, "image/png")},
    )
    assert a_res.status_code == 201
    asset_id = a_res.json()["id"]

    assert (await client.delete(f"/api/v1/assets/{asset_id}")).status_code == 409
    assert (await client.delete(f"/api/v1/assets/{asset_id}?force=true")).status_code == 204
