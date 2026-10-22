"""Frame-chain continuity capability: detection on connection test and batch warnings."""
from __future__ import annotations

import os
import tempfile

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_ffcap_test_")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["MEDIA_ROOT"] = _TEMP_MEDIA_DIR

from muse_orchestrator.core.database import get_db  # noqa: E402 -- env must be set before import
from muse_orchestrator.main import app  # noqa: E402
from muse_orchestrator.models.domain import Base, ProviderConnection  # noqa: E402
from muse_orchestrator.providers.base import ProviderHealth  # noqa: E402
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter  # noqa: E402

STATE: dict[str, object] = {}
SCRIPT = """
SCENE 1: Chase
SHOT 1: Hero leaps
- Action: Leaps across neon gap
SHOT 2: Hero lands
- Action: Rolls onto concrete roof
"""


class _Pool:
    async def enqueue_job(self, *_args, **_kwargs):
        return object()

    async def aclose(self) -> None:
        return None


@pytest.fixture()
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    STATE["maker"] = maker

    async def override_get_db():
        async with maker() as session:
            yield session

    previous_pool = getattr(app.state, "arq_pool", None)
    app.state.arq_pool = _Pool()
    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()
        app.state.arq_pool = previous_pool
        await engine.dispose()


def _readyz_adapter(body: dict | None, status: int = 200) -> Muse2ApiAdapter:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body or {})

    return Muse2ApiAdapter("http://gw.test", "k", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("body", "status", "expected"),
    [
        ({"ready": True, "driver": {"driver": "browser", "capabilities": {"video_first_frame": True}}}, 200, True),
        ({"ready": True, "driver": {"driver": "mock", "capabilities": {"video_first_frame": False}}}, 200, False),
        # Upstream muse2api 949f326 does not advertise the capability (and ignores the frame).
        ({"ready": True, "driver": {"driver": "browser", "ok": True}}, 200, None),
        ({"ready": True, "driver": {"capabilities": {"video_first_frame": "yes"}}}, 200, None),
        ({"ready": False}, 503, None),
    ],
)
async def test_adapter_reads_first_frame_capability_from_readyz(body, status, expected) -> None:
    assert await _readyz_adapter(body, status).first_frame_capability() is expected


async def _connection(client: AsyncClient, provider_type: str = "muse2api") -> str:
    return (await client.post("/api/v1/connections", json={
        "name": f"gw-{provider_type}", "provider_type": provider_type,
        "base_url": "http://localhost:18610", "api_key": "k"})).json()["id"]


@pytest.mark.parametrize(("advertised", "expected"), [(True, "supported"), (False, "unsupported"), (None, "unknown")])
async def test_connection_test_records_first_frame_status(client, monkeypatch, advertised, expected) -> None:
    async def health(_self):
        return ProviderHealth(healthy=True, ready=True, status_state="healthy", latency_ms=1.0)

    async def list_models(_self):
        return []

    async def capability(_self):
        return advertised

    monkeypatch.setattr(Muse2ApiAdapter, "health", health)
    monkeypatch.setattr(Muse2ApiAdapter, "list_models", list_models)
    monkeypatch.setattr(Muse2ApiAdapter, "first_frame_capability", capability)
    cid = await _connection(client)
    tested = (await client.post(f"/api/v1/connections/{cid}/test")).json()
    assert tested["capabilities_snapshot"]["video_first_frame"] == expected


async def test_mock_connection_never_claims_first_frame_support(client) -> None:
    cid = await _connection(client, "mock")
    tested = (await client.post(f"/api/v1/connections/{cid}/test")).json()
    assert tested["capabilities_snapshot"]["video_first_frame"] == "unsupported"


async def _project_with_chain(client: AsyncClient, chained: bool) -> tuple[str, list[dict]]:
    pid = (await client.post("/api/v1/projects", json={"name": "ffcap"})).json()["id"]
    await client.post(f"/api/v1/projects/{pid}/scripts/import", json={"raw_content": SCRIPT, "auto_apply_proposal": True})
    shots = (await client.get(f"/api/v1/projects/{pid}/shots")).json()
    if chained:
        await client.patch(f"/api/v1/projects/{pid}/shots/{shots[1]['id']}", json={"continuity_mode": "frame_chain"})
    return pid, shots


async def _set_status(cid: str, value: str | None) -> None:
    async with STATE["maker"]() as db:
        conn = await db.get(ProviderConnection, cid)
        snapshot = dict(conn.capabilities_snapshot or {})
        if value is None:
            snapshot.pop("video_first_frame", None)
        else:
            snapshot["video_first_frame"] = value
        conn.capabilities_snapshot = snapshot
        await db.commit()


@pytest.mark.parametrize("status_value", ["unknown", "unsupported", None])
async def test_batch_warns_when_frame_chain_continuity_is_not_guaranteed(client, status_value) -> None:
    cid = await _connection(client, "mock")
    await _set_status(cid, status_value)
    pid, shots = await _project_with_chain(client, chained=True)
    launch = (await client.post(f"/api/v1/projects/{pid}/batch/launch", json={"connection_id": cid})).json()
    assert [w["code"] for w in launch["warnings"]] == ["frame_chain_continuity_not_guaranteed"]
    assert launch["warnings"][0]["shot_ids"] == [shots[1]["id"]]
    assert (status_value or "unknown") in launch["warnings"][0]["message"]


async def test_batch_does_not_warn_for_supported_gateway_or_standard_shots(client) -> None:
    cid = await _connection(client, "mock")
    await _set_status(cid, "supported")
    pid, _ = await _project_with_chain(client, chained=True)
    assert (await client.post(f"/api/v1/projects/{pid}/batch/launch", json={"connection_id": cid})).json()["warnings"] == []

    other = await _connection(client, "muse2api")
    pid2, _ = await _project_with_chain(client, chained=False)
    assert (await client.post(f"/api/v1/projects/{pid2}/batch/launch", json={"connection_id": other})).json()["warnings"] == []
