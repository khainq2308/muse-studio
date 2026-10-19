from __future__ import annotations
import os
import tempfile
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_m2_test_")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["MEDIA_ROOT"] = _TEMP_MEDIA_DIR

from muse_orchestrator.main import app  # noqa: E402 -- test environment must be set before app import
from muse_orchestrator.core.database import get_db  # noqa: E402 -- test environment must be set before app import
from muse_orchestrator.models.domain import Base  # noqa: E402 -- test environment must be set before app import


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


SAMPLE_MUSEFLOW_SCRIPT = """
SCENE 1: Quán Cà Phê Poirot Đêm Mưa
SHOT 1: Conan suy luận bên tách cà phê
- Character: Conan Edogawa, Ran Mouri
- Location: Quán Cà Phê Poirot
- Shot_Size: medium_close_up
- Camera: static
- Composition: rule of thirds, rain droplets on glass
- Action: Conan nâng tách cà phê nóng, ánh mắt sắc lạnh nhìn qua kính
- Duration: 4.5

SHOT 2: Ran nhìn ra cửa sổ
- Character: Ran Mouri
- Camera: slow_pan
- Action: Ran khẽ đặt cốc nước xuống bàn gỗ
- Duration: 5.0
"""


@pytest.mark.asyncio
async def test_m2_script_import_bible_extraction_and_continuity(client: AsyncClient):
    # 1. Tạo Project
    p_res = await client.post("/api/v1/projects", json={"name": "Conan Detective Case 02"})
    assert p_res.status_code == 201
    proj_id = p_res.json()["id"]

    # 2. Import kịch bản MuseFlow
    imp_res = await client.post(
        f"/api/v1/projects/{proj_id}/scripts/import",
        json={"raw_content": SAMPLE_MUSEFLOW_SCRIPT, "source_format": "museflow", "auto_apply_proposal": True},
    )
    assert imp_res.status_code == 201
    summary = imp_res.json()
    assert summary["raw_content_preserved"] is True
    assert summary["detected_characters"] == 2
    assert summary["generated_shots"] == 2

    # 3. Kiểm tra Story Bible đã trích xuất đúng Conan & Ran + Poirot
    bible = (await client.get(f"/api/v1/projects/{proj_id}/story-bible")).json()
    code_names = {b["code_name"] for b in bible}
    assert "char_conan_edogawa" in code_names
    assert "char_ran_mouri" in code_names
    assert "loc_quan_ca_phe_poirot" in code_names

    # 4. Kiểm tra Shot 2 tự động kế thừa Location của Scene 1 và có Prompt Hash hợp lệ
    shots = (await client.get(f"/api/v1/projects/{proj_id}/shots")).json()
    assert len(shots) == 2
    shot1, shot2 = shots[0], shots[1]
    assert shot1["prompt_hash"] != ""
    assert "Conan Edogawa" in shot1["prompt_final"]
    assert shot2["approval_state"] == "needs_review"

    # 5. Bật requires_reference_lock cho Shot 1 khi chưa có Asset -> Phải chuyển sang blocked_reference
    patch_block = await client.patch(
        f"/api/v1/projects/{proj_id}/shots/{shot1['id']}",
        json={"requires_reference_lock": True, "reference_asset_ids": []},
    )
    assert patch_block.status_code == 200
    assert patch_block.json()["generation_state"] == "blocked_reference"

    # 6. Cố tình Approve Shot đang bị Blocker -> Phải bị chặn 422 Unprocessable Entity
    approve_fail = await client.patch(
        f"/api/v1/projects/{proj_id}/shots/{shot1['id']}",
        json={"approval_state": "approved"},
    )
    assert approve_fail.status_code == 422

    # 7. Upload ảnh Reference thật và gán vào Shot 1 -> Mở khoá và Approve thành công
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    ast_res = await client.post(
        "/api/v1/assets/upload",
        data={"project_id": proj_id, "asset_type": "reference", "is_locked_reference": "true"},
        files={"file": ("conan_ref.png", png_bytes, "image/png")},
    )
    asset_id = ast_res.json()["id"]

    approve_ok = await client.patch(
        f"/api/v1/projects/{proj_id}/shots/{shot1['id']}",
        json={
            "requires_reference_lock": True,
            "reference_asset_ids": [asset_id],
            "approval_state": "approved",
        },
    )
    assert approve_ok.status_code == 200
    assert approve_ok.json()["generation_state"] == "not_started"
    assert approve_ok.json()["approval_state"] == "approved"

    # 8. Xuất Shot List ra CSV và JSON
    csv_res = await client.get(f"/api/v1/projects/{proj_id}/shots/export?format=csv")
    assert csv_res.status_code == 200
    assert "sequence_index,shot_id,title" in csv_res.text
    assert "Conan Edogawa" in csv_res.text



@pytest.mark.asyncio
async def test_shot_reads_and_exports_do_not_recompile_or_write(client: AsyncClient, monkeypatch):
    project_response = await client.post("/api/v1/projects", json={"name": "Read-only shot list"})
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    import_response = await client.post(
        f"/api/v1/projects/{project_id}/scripts/import",
        json={"raw_content": SAMPLE_MUSEFLOW_SCRIPT, "source_format": "museflow", "auto_apply_proposal": True},
    )
    assert import_response.status_code == 201

    import muse_orchestrator.api.pipeline as pipeline_module

    async def unexpected_recompile(*_args, **_kwargs):
        raise AssertionError("GET shot routes must not recompile prompts or commit writes")

    monkeypatch.setattr(pipeline_module, "_recompile_project_shots", unexpected_recompile)

    shots_response = await client.get(f"/api/v1/projects/{project_id}/shots")
    assert shots_response.status_code == 200
    shots = shots_response.json()
    assert len(shots) == 2
    assert shots[0]["prompt_hash"]

    csv_response = await client.get(f"/api/v1/projects/{project_id}/shots/export?format=csv")
    assert csv_response.status_code == 200
    assert "shot_id,title" in csv_response.text

    json_response = await client.get(f"/api/v1/projects/{project_id}/shots/export?format=json")
    assert json_response.status_code == 200
    assert json_response.json()["shots"][0]["prompt_hash"] == shots[0]["prompt_hash"]


