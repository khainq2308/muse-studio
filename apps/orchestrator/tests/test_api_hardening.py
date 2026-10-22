"""Regression tests for the deep-review API hardening findings.

Covers DATA-003/006/007, API-001/002/004, BUG-003, WF-001 and SEC-003/004/005/007.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_hardening_test_")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["MEDIA_ROOT"] = _TEMP_MEDIA_DIR

from muse_orchestrator.application.services import ProjectService
from muse_orchestrator.core.config import SecretRedactor, settings
from muse_orchestrator.core.database import get_db
from muse_orchestrator.infrastructure.repositories import StudioRepository
from muse_orchestrator.main import app
from muse_orchestrator.models.domain import Base
from muse_orchestrator.providers.base import ProviderFailure, VideoGenerationRequest
from muse_orchestrator.providers.muse2api_adapter import Muse2ApiAdapter

SCRIPT = """
SCENE 1: Cafe
SHOT 1: Conan thinks
- Character: Conan Edogawa
- Location: Cafe
- Camera: static
- Action: Conan lifts cup
- Duration: 4.5

SHOT 2: Ran looks
- Character: Conan Edogawa
- Camera: slow_pan
- Action: Ran puts cup down
- Duration: 5.0

SHOT 3: Door opens
- Character: Conan Edogawa
- Camera: static
- Action: The door opens
- Duration: 3.0
"""


@pytest.fixture()
async def client():
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    # raise_app_exceptions=False: observe the envelope the catch-all handler sends
    # instead of the re-raised exception Starlette propagates to the server.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()
    await test_engine.dispose()


async def _project(client: AsyncClient, name: str = "P") -> str:
    response = await client.post("/api/v1/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _project_with_shots(client: AsyncClient) -> tuple[str, list[dict]]:
    pid = await _project(client)
    response = await client.post(
        f"/api/v1/projects/{pid}/scripts/import",
        json={"raw_content": SCRIPT, "auto_apply_proposal": True},
    )
    assert response.status_code == 201, response.text
    shots = (await client.get(f"/api/v1/projects/{pid}/shots")).json()
    assert len(shots) >= 3
    return pid, shots


async def _shot(client: AsyncClient, pid: str, sid: str) -> dict:
    return next(s for s in (await client.get(f"/api/v1/projects/{pid}/shots")).json() if s["id"] == sid)


# --- DATA-003 ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejected_approval_leaves_shot_unchanged(client: AsyncClient) -> None:
    pid, shots = await _project_with_shots(client)
    sid = shots[0]["id"]
    before = await _shot(client, pid, sid)

    response = await client.patch(
        f"/api/v1/projects/{pid}/shots/{sid}",
        json={
            "action": "MUTATED",
            "duration_seconds": 9.0,
            "requires_reference_lock": True,
            "approval_state": "approved",
        },
    )

    assert response.status_code == 422, response.text
    assert "Blocker" in response.json()["detail"]
    after = await _shot(client, pid, sid)
    assert after == before


@pytest.mark.asyncio
async def test_approval_without_blockers_still_persists(client: AsyncClient) -> None:
    pid, shots = await _project_with_shots(client)
    sid = shots[0]["id"]
    response = await client.patch(
        f"/api/v1/projects/{pid}/shots/{sid}", json={"action": "Conan sips coffee"}
    )
    assert response.status_code == 200, response.text
    assert (await _shot(client, pid, sid))["action"] == "Conan sips coffee"


# --- API-002 -------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"duration_seconds": -5},
        {"duration_seconds": 0},
        {"duration_seconds": 1e9},
        {"shot_size": ""},
        {"camera_motion": "   "},
        {"action": None},
        {"continuity_mode": "teleport"},
    ],
)
async def test_shot_update_rejects_invalid_values(client: AsyncClient, body: dict) -> None:
    pid, shots = await _project_with_shots(client)
    sid = shots[0]["id"]
    before = await _shot(client, pid, sid)
    response = await client.patch(f"/api/v1/projects/{pid}/shots/{sid}", json=body)
    assert response.status_code == 422, response.text
    assert await _shot(client, pid, sid) == before


@pytest.mark.asyncio
async def test_shot_update_rejects_nan_and_list_stays_readable(client: AsyncClient) -> None:
    pid, shots = await _project_with_shots(client)
    sid = shots[0]["id"]
    for literal in (b"NaN", b"Infinity"):
        response = await client.patch(
            f"/api/v1/projects/{pid}/shots/{sid}",
            content=b'{"duration_seconds": ' + literal + b"}",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422
    listing = await client.get(f"/api/v1/projects/{pid}/shots")
    assert listing.status_code == 200


# --- DATA-007 ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_previous_shot_cycles_are_rejected(client: AsyncClient) -> None:
    pid, shots = await _project_with_shots(client)
    a, b, c = shots[0]["id"], shots[1]["id"], shots[2]["id"]
    # Script import chains shots within a scene: b -> a, c -> b.
    assert shots[1]["previous_shot_id"] == a
    assert shots[2]["previous_shot_id"] == b

    # Two-node cycle: a -> b while b -> a.
    r = await client.patch(f"/api/v1/projects/{pid}/shots/{a}", json={"previous_shot_id": b})
    assert r.status_code == 422, r.text
    # Transitive cycle: a -> c while c -> b -> a.
    r = await client.patch(f"/api/v1/projects/{pid}/shots/{a}", json={"previous_shot_id": c})
    assert r.status_code == 422, r.text
    assert (await _shot(client, pid, a))["previous_shot_id"] is None

    # Breaking the chain makes the same link legal.
    assert (await client.patch(f"/api/v1/projects/{pid}/shots/{b}", json={"previous_shot_id": None})).status_code == 200
    r = await client.patch(f"/api/v1/projects/{pid}/shots/{a}", json={"previous_shot_id": c})
    assert r.status_code == 200, r.text


# --- API-001 -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unhandled_exception_returns_redacted_envelope(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def explode(self: ProjectService) -> list:
        raise RuntimeError("db exploded api_key=sk-live-SECRET123 at http://u:pw@proxy:1")

    monkeypatch.setattr(ProjectService, "list_projects", explode)
    with caplog.at_level(logging.ERROR, logger="muse_orchestrator"):
        response = await client.get("/api/v1/projects")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Internal server error.",
        "category": "internal_error",
        "retryable": False,
    }
    assert "RuntimeError" in caplog.text
    assert "sk-live-SECRET123" not in caplog.text
    assert "pw@" not in caplog.text


@pytest.mark.asyncio
async def test_integrity_error_race_on_connection_create_is_409(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate losing the race: the pre-insert duplicate scan sees nothing.
    async def no_existing(self: StudioRepository) -> list:
        return []

    first = await client.post("/api/v1/connections", json={"name": "Race", "base_url": "http://a"})
    assert first.status_code == 201
    monkeypatch.setattr(StudioRepository, "list_connections", no_existing)

    responses = await asyncio.gather(
        *[client.post("/api/v1/connections", json={"name": "Race", "base_url": "http://a"}) for _ in range(3)]
    )
    for response in responses:
        assert response.status_code == 409, response.text
        body = response.json()
        assert body["category"] == "conflict"
        assert body["retryable"] is False
        assert "INSERT" not in body["detail"]


# --- BUG-003 -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_is_not_cached_or_shared_across_projects(client: AsyncClient) -> None:
    p1 = await _project(client, "P1")
    p2 = await _project(client, "P2")
    w1 = (await client.post(f"/api/v1/projects/{p1}/workflows", json={})).json()["id"]
    run1 = (await client.post(f"/api/v1/projects/{p1}/workflows/{w1}/runs", json={})).json()
    approve = await client.post(
        f"/api/v1/projects/{p1}/workflows/{w1}/runs/{run1['run_id']}/nodes/n_approval/approve"
    )
    assert approve.status_code == 200

    w2 = (await client.post(f"/api/v1/projects/{p2}/workflows", json={})).json()["id"]
    run2 = (await client.post(f"/api/v1/projects/{p2}/workflows/{w2}/runs", json={})).json()
    assert run2["status"] == "waiting_approval"
    # Nothing from P1 is reused by the identical P2 graph.
    assert all(n["cache_hit"] is False for n in run2["node_executions"])
    gate = next(n for n in run2["node_executions"] if n["node_id"] == "n_approval")
    assert gate["state"] == "waiting_approval"

    # Re-running P1 must ask for approval again.
    rerun = (await client.post(f"/api/v1/projects/{p1}/workflows/{w1}/runs", json={})).json()
    assert rerun["status"] == "waiting_approval"


@pytest.mark.asyncio
async def test_approve_via_another_projects_url_is_404(client: AsyncClient) -> None:
    p1 = await _project(client, "P1")
    p2 = await _project(client, "P2")
    w1 = (await client.post(f"/api/v1/projects/{p1}/workflows", json={})).json()["id"]
    run = (await client.post(f"/api/v1/projects/{p1}/workflows/{w1}/runs", json={})).json()

    response = await client.post(
        f"/api/v1/projects/{p2}/workflows/{w1}/runs/{run['run_id']}/nodes/n_approval/approve"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_resume_stops_at_next_manual_approval_gate(client: AsyncClient) -> None:
    pid = await _project(client)
    graph = {
        "use_default_m3_template": False,
        "nodes": [
            {"node_id": "g1", "node_type": "ManualApproval", "config": {"x": 1}},
            {"node_id": "g2", "node_type": "ManualApproval", "config": {"x": 2}},
            {"node_id": "g3", "node_type": "Condition/Filter"},
        ],
        "edges": [
            {"edge_id": "e1", "source_node_id": "g1", "source_port": "out", "target_node_id": "g2", "target_port": "in"},
            {"edge_id": "e2", "source_node_id": "g2", "source_port": "out", "target_node_id": "g3", "target_port": "in"},
        ],
    }
    wf = await client.post(f"/api/v1/projects/{pid}/workflows", json=graph)
    assert wf.status_code == 201, wf.text
    wid = wf.json()["id"]
    run = (await client.post(f"/api/v1/projects/{pid}/workflows/{wid}/runs", json={})).json()
    base = f"/api/v1/projects/{pid}/workflows/{wid}/runs/{run['run_id']}/nodes"

    first = (await client.post(f"{base}/g1/approve")).json()
    states = {n["node_id"]: n["state"] for n in first["node_executions"]}
    assert first["status"] == "waiting_approval"
    assert states == {"g1": "succeeded", "g2": "waiting_approval", "g3": "blocked"}

    second = (await client.post(f"{base}/g2/approve")).json()
    states = {n["node_id"]: n["state"] for n in second["node_executions"]}
    assert second["status"] == "succeeded"
    assert states == {"g1": "succeeded", "g2": "succeeded", "g3": "succeeded"}


# --- WF-001 --------------------------------------------------------------------------------


def _graph(nodes: list, edges: list | None = None) -> dict:
    return {"use_default_m3_template": False, "nodes": nodes, "edges": edges or []}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "graph",
    [
        # List-shaped ports on an unknown type used to crash with AttributeError (500).
        _graph([{"node_id": "a", "node_type": "Custom", "inputs": ["x"]}]),
        _graph([{"node_id": 1, "node_type": "ScriptInput"}, {"node_id": "b", "node_type": "ScriptInput"}]),
        _graph([{"node_id": "a", "node_type": "RmRf", "outputs": {"o": "Text"}}]),
        _graph([{"node_id": "a", "node_type": "ScriptInput", "config": ["not", "a", "dict"]}]),
        _graph([{"node_id": "a", "node_type": "ScriptInput", "outputs": {"script": 5}}]),
        # Catalog port override that would bypass edge type checking.
        _graph(
            [
                {"node_id": "a", "node_type": "ScriptInput"},
                {"node_id": "b", "node_type": "WaitForTask", "inputs": {"task": "Script"}},
            ],
            [{"edge_id": "e1", "source_node_id": "a", "source_port": "script", "target_node_id": "b", "target_port": "task"}],
        ),
        # Duplicate edge ids.
        _graph(
            [
                {"node_id": "a", "node_type": "ScriptInput"},
                {"node_id": "b", "node_type": "ScriptAnalyze"},
                {"node_id": "c", "node_type": "StoryBibleBuild"},
            ],
            [
                {"edge_id": "e", "source_node_id": "a", "source_port": "script", "target_node_id": "b", "target_port": "script"},
                {"edge_id": "e", "source_node_id": "b", "source_port": "script", "target_node_id": "c", "target_port": "script"},
            ],
        ),
        # Two edges into the same input port.
        _graph(
            [
                {"node_id": "a", "node_type": "ScriptInput"},
                {"node_id": "b", "node_type": "ScriptInput"},
                {"node_id": "c", "node_type": "ScriptAnalyze"},
            ],
            [
                {"edge_id": "e1", "source_node_id": "a", "source_port": "script", "target_node_id": "c", "target_port": "script"},
                {"edge_id": "e2", "source_node_id": "b", "source_port": "script", "target_node_id": "c", "target_port": "script"},
            ],
        ),
    ],
)
async def test_invalid_workflow_graphs_are_422(client: AsyncClient, graph: dict) -> None:
    pid = await _project(client)
    response = await client.post(f"/api/v1/projects/{pid}/workflows", json=graph)
    assert response.status_code == 422, response.text
    assert response.json()["category"] == "validation"


@pytest.mark.asyncio
async def test_catalog_ports_matching_the_catalog_are_accepted(client: AsyncClient) -> None:
    pid = await _project(client)
    graph = _graph([{"node_id": "a", "node_type": "ScriptInput", "inputs": {}, "outputs": {"script": "Script"}}])
    response = await client.post(f"/api/v1/projects/{pid}/workflows", json=graph)
    assert response.status_code == 201, response.text


# --- DATA-006 ------------------------------------------------------------------------------


async def _assemble(client: AsyncClient, pid: str, **body: object) -> httpx.Response:
    return await client.post(f"/api/v1/projects/{pid}/timeline/assemble", json={"name": "Cut", **body})


@pytest.mark.asyncio
async def test_timeline_rejects_foreign_shot_and_bad_clip_values(client: AsyncClient) -> None:
    mine, _ = await _project_with_shots(client)
    _other, other_shots = await _project_with_shots(client)

    foreign = await _assemble(
        client, mine, custom_clips=[{"shot_id": other_shots[0]["id"], "order_index": 1}]
    )
    assert foreign.status_code == 422
    assert "shot_id" in foreign.json()["detail"]

    for clip in (
        {"order_index": 1, "source_duration_seconds": 3.0, "trim_start_seconds": 2.0, "trim_end_seconds": 1.0},
        {"order_index": 1, "start_time_seconds": -1.0},
        {"order_index": 1, "trim_start_seconds": -0.5},
    ):
        response = await _assemble(client, mine, custom_clips=[clip])
        assert response.status_code == 422, (clip, response.text)

    nan_clip = await client.post(
        f"/api/v1/projects/{mine}/timeline/assemble",
        content=b'{"custom_clips": [{"order_index": 1, "trim_end_seconds": NaN}]}',
        headers={"content-type": "application/json"},
    )
    assert nan_clip.status_code == 422

    assert (await client.get(f"/api/v1/projects/{mine}/timeline")).status_code == 404


@pytest.mark.asyncio
async def test_timeline_title_card_duration_parse_error_is_422(client: AsyncClient) -> None:
    pid = await _project(client)
    for value in ("abc", [1], -1, 1e9):
        response = await _assemble(client, pid, custom_clips=[], title_card={"duration_seconds": value})
        assert response.status_code == 422, (value, response.text)
    ok = await _assemble(client, pid, custom_clips=[], title_card={"duration_seconds": "2.5"})
    assert ok.status_code == 201, ok.text
    assert ok.json()["total_duration_seconds"] == 2.5


@pytest.mark.asyncio
@pytest.mark.parametrize("resolution", ["1921x1080", "8192x4320", "10x10", "1920x1081", "abc"])
async def test_export_resolution_must_be_even_and_bounded(client: AsyncClient, resolution: str) -> None:
    pid = await _project(client)
    response = await client.post(
        f"/api/v1/projects/{pid}/timeline/exports", json={"resolution": resolution}
    )
    assert response.status_code == 422, response.text


# --- API-004 -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_story_bible_and_revisions_404_for_missing_project(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/projects/nope/story-bible")).status_code == 404
    assert (await client.get("/api/v1/projects/nope/revisions")).status_code == 404
    pid = await _project(client)
    assert (await client.get(f"/api/v1/projects/{pid}/story-bible")).status_code == 200
    assert (await client.get(f"/api/v1/projects/{pid}/revisions")).status_code == 200


# --- SEC-007 -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dev_host_guard_blocks_rebinding_hosts(client: AsyncClient) -> None:
    assert settings.insecure_dev_defaults
    for host in ("evil.example", "attacker.com:8000", "192.168.1.5"):
        response = await client.get("/healthz", headers={"host": host})
        assert response.status_code == 400, host
        assert response.json()["category"] == "invalid_request"
    for host in ("localhost:8000", "127.0.0.1", "[::1]:8000", "testserver"):
        response = await client.get("/healthz", headers={"host": host})
        assert response.status_code == 200, host


@pytest.mark.asyncio
async def test_host_guard_is_inactive_when_admin_token_is_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "studio_admin_token", "a-real-token")
    response = await client.get("/healthz", headers={"host": "studio.example.com"})
    assert response.status_code == 200


# --- SEC-004 -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, secret",
    [
        ("proxy HTTP://user:Pa/ss@w0rd@1.2.3.4:8080 failed", "Pa/ss@w0rd"),
        ("Socks5h://user:hunter2@proxy:1080", "hunter2"),
        ("socks4://user:s3cret@proxy:1080", "s3cret"),
        ("socks5://tok3n-only@proxy:1080", "tok3n-only"),
        ("refresh_token=rt-abc client_secret: cs-xyz password=pw-1", "rt-abc"),
        ("refresh_token=rt-abc client_secret: cs-xyz password=pw-1", "cs-xyz"),
        ("refresh_token=rt-abc client_secret: cs-xyz password=pw-1", "pw-1"),
        ("X-Api-Key: xk-123", "xk-123"),
    ],
)
def test_redactor_hides_proxy_credentials_and_tokens(raw: str, secret: str) -> None:
    assert secret not in SecretRedactor.redact_text(raw)


def test_redactor_keeps_non_credential_urls_and_sensitive_keys() -> None:
    assert SecretRedactor.redact_text("see https://ex.com/a@b") == "see https://ex.com/a@b"
    redacted = SecretRedactor.redact_dict(
        {
            "Token": "t",
            "access_token": "a",
            "refresh_token": "r",
            "apikey": "k",
            "X-API-Key": "x",
            "client_secret": "c",
            "private_key": "p",
            "Authorization": "Bearer z",
            "max_tokens": 5,
        }
    )
    assert redacted["max_tokens"] == 5
    assert all(value == "[REDACTED]" for key, value in redacted.items() if key != "max_tokens")


# --- SEC-003 / SEC-005 ---------------------------------------------------------------------

BASE = "https://muse.example.com"
API_KEY = "sk-secret-key"


async def _public_dns(host: str, port: int):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]


class _CountingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: int) -> None:
        self.total_chunks = chunks
        self.yielded = 0

    async def __aiter__(self):
        for _ in range(self.total_chunks):
            self.yielded += 1
            yield b"x" * 1024

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_provider_error_bodies_are_streamed_and_bounded() -> None:
    stream = _CountingStream(10_000)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, stream=stream)

    adapter = Muse2ApiAdapter(BASE, API_KEY, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderFailure) as excinfo:
        await adapter.list_models()
    assert excinfo.value.retryable is True
    assert stream.yielded <= 5  # ~4 KiB prefix, not the whole 10 MiB body


@pytest.mark.asyncio
async def test_provider_oversized_success_body_fails_instead_of_buffering() -> None:
    stream = _CountingStream(10_000)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    adapter = Muse2ApiAdapter(BASE, API_KEY, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderFailure, match="size limit"):
        await adapter.get_task("task_1")
    assert stream.yielded < 10_000


@pytest.mark.asyncio
async def test_provider_json_calls_still_parse_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        return httpx.Response(200, json={"id": "task_9", "status": "queued"})

    adapter = Muse2ApiAdapter(BASE, API_KEY, transport=httpx.MockTransport(handler))
    task = await adapter.generate_video(VideoGenerationRequest(shot_id="s", prompt="p"))
    assert task.external_task_id == "task_9"


@pytest.mark.asyncio
async def test_api_key_not_resent_after_redirect_chain_leaves_provider(tmp_path: Path) -> None:
    seen: list[tuple[str, str | None]] = []
    routes = {
        f"{BASE}/v1/media/start.mp4": httpx.Response(302, headers={"location": "https://cdn.example.net/hop"}),
        "https://cdn.example.net/hop": httpx.Response(302, headers={"location": f"{BASE}/v1/media/steal"}),
        f"{BASE}/v1/media/steal": httpx.Response(
            200, headers={"content-type": "video/mp4"}, content=b"\x00\x00\x00\x18ftypmp42VIDEO"
        ),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host")
        url = urlsplit(str(request.url))._replace(netloc=host).geturl() if host else str(request.url)
        seen.append((url, request.headers.get("authorization")))
        return routes[url]

    adapter = Muse2ApiAdapter(BASE, API_KEY, transport=httpx.MockTransport(handler), dns_resolver=_public_dns)
    await adapter.download_result("start.mp4", str(tmp_path))

    assert seen[0] == (f"{BASE}/v1/media/start.mp4", f"Bearer {API_KEY}")
    assert seen[1][1] is None
    assert seen[2] == (f"{BASE}/v1/media/steal", None)

