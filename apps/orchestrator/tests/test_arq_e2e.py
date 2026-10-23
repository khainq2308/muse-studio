"""M4 end-to-end: real Redis, real ARQ worker, real API process, Alembic-migrated SQLite.

Everything else in the suite drives the queue with direct ``step_task`` calls and a fake
ARQ pool. This module proves the production wiring instead:

* ``uvicorn muse_orchestrator.main:app`` and ``arq muse_orchestrator.workers.queue_worker.
  WorkerSettings`` run as two separate processes (exactly as Compose deploys them) and
  share one throwaway SQLite file migrated by ``alembic upgrade head`` and one Redis.
* No worker internals are patched. Provider behavior comes from the two seams the code
  base already supports: the ``mock`` connection type, and a ``muse2api`` connection whose
  base URL points at a small in-process HTTP gateway (``FakeGateway``) that speaks the
  muse2api task contract, so the real ``Muse2ApiAdapter`` runs over real HTTP.
* Observations are made from the outside only: the public API, the gateway's request
  log and the ARQ keys in Redis (queue sorted set, job keys, cron results).

Activation: skipped unless ``TEST_REDIS_URL`` (``redis://host:port/db``) is set; with
``REQUIRE_ARQ_E2E=1`` (CI) a missing URL fails collection instead of skipping. The Redis
database must be empty when the module starts (use a dedicated DB index or a throwaway
server); the module flushes that database when it finishes.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TypeVar

import pytest

_REDIS_URL = os.environ.get("TEST_REDIS_URL", "").strip()
if not _REDIS_URL:
    if os.environ.get("REQUIRE_ARQ_E2E") == "1":
        pytest.fail(
            "REQUIRE_ARQ_E2E=1 but TEST_REDIS_URL is not set; the real Redis/ARQ "
            "end-to-end suite must not be silently skipped.",
            pytrace=False,
        )
    pytest.skip(
        "TEST_REDIS_URL not set (redis://host:port/db); real Redis/ARQ end-to-end tests skipped.",
        allow_module_level=True,
    )
if not _REDIS_URL.startswith("redis://"):
    pytest.fail("TEST_REDIS_URL must be a redis:// URL.", pytrace=False)

# Deliberately no muse_orchestrator import: the API and worker run in their own processes
# with their own settings. Importing the app here would bind this pytest process's
# settings, which other modules configure for in-memory SQLite.
import httpx  # noqa: E402
import redis  # noqa: E402

T = TypeVar("T")

ORCHESTRATOR_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ORCHESTRATOR_DIR.parents[1]
ADMIN_TOKEN = "e2e-admin-token-" + uuid.uuid4().hex
GATEWAY_KEY = "sk-e2e-gateway-" + uuid.uuid4().hex[:16]

ARQ_QUEUE = "arq:queue"
ARQ_JOB_PREFIX = "arq:job:"
ARQ_IN_PROGRESS_PREFIX = "arq:in-progress:"
ARQ_HEALTH_KEY = "arq:queue:health-check"
RECOVERY_CRON_PROGRESS_PATTERN = "arq:in-progress:cron:recover_pending_generation_tasks_job:*"

# Gateway scenarios are selected by the model id sent with the generate request.
MODEL_OK = "e2e-image-ok"  # first poll: completed
MODEL_ASK = "e2e-image-ask"  # needs_input until answered, then completed
MODEL_HOLD = "e2e-image-hold"  # running until the test releases it

QUESTION = {
    "kind": "question",
    "text": "Which style do you prefer?",
    "options": ["Cinematic", "Anime"],
    "asked_at": 1761200000.5,
}

SCRIPT = """
SCENE 1: Rooftop
SHOT 1: Hero leaps
- Action: Leaps across neon gap
SHOT 2: Hero lands
- Action: Rolls onto concrete roof
"""


def _png_1x1() -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


PNG_BYTES = _png_1x1()


# --------------------------------------------------------------------------- gateway


@dataclass
class GatewayTask:
    model: str
    idempotency_key: str
    polls: int = 0
    answered: bool = False
    released: bool = False
    replies: list[dict[str, str]] = field(default_factory=list)


class FakeGateway:
    """Minimal muse2api image-task contract over real HTTP (thread-safe request log)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.tasks: dict[str, GatewayTask] = {}
        self.unauthorized_requests = 0
        self.base_url = ""
        self._server: ThreadingHTTPServer | None = None

    # -- test-side accessors -------------------------------------------------
    def task(self, external_task_id: str) -> GatewayTask:
        upstream = external_task_id.removeprefix("image:")
        with self.lock:
            return self.tasks[upstream]

    def polls(self, external_task_id: str) -> int:
        return self.task(external_task_id).polls

    def generate_count(self, idempotency_key: str) -> int:
        with self.lock:
            return sum(1 for t in self.tasks.values() if t.idempotency_key == idempotency_key)

    def release(self, external_task_id: str) -> None:
        upstream = external_task_id.removeprefix("image:")
        with self.lock:
            self.tasks[upstream].released = True

    # -- HTTP ----------------------------------------------------------------
    def view(self, task_id: str, task: GatewayTask) -> dict[str, Any]:
        completed = {
            "id": task_id,
            "status": "completed",
            "account_id": "e2e-account",
            "result": {"data": [{"url": f"{self.base_url}/files/{task_id}.png"}]},
        }
        if task.model == MODEL_OK:
            return completed
        if task.model == MODEL_ASK:
            if task.answered:
                return completed
            return {"id": task_id, "status": "needs_input", "question": QUESTION}
        if task.model == MODEL_HOLD and task.released:
            return completed
        return {"id": task_id, "status": "processing"}

    def start(self) -> None:
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def _send(self, status: int, payload: Any, content_type: str = "application/json") -> None:
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self) -> bool:
                if self.headers.get("Authorization") == f"Bearer {GATEWAY_KEY}":
                    return True
                with gateway.lock:
                    gateway.unauthorized_requests += 1
                self._send(401, {"error": {"message": "invalid key"}})
                return False

            def _body(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(length) or b"{}")

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if path in ("/healthz", "/readyz"):
                    self._send(200, {"status": "ok"})
                    return
                if path.startswith("/files/") and path.endswith(".png"):
                    self._send(200, PNG_BYTES, "image/png")
                    return
                prefix = "/v1/images/generations/"
                if path.startswith(prefix) and self._authorized():
                    task_id = path[len(prefix):]
                    with gateway.lock:
                        task = gateway.tasks.get(task_id)
                        if task is None:
                            self._send(404, {"error": {"message": "unknown task"}})
                            return
                        task.polls += 1
                        view = gateway.view(task_id, task)
                    self._send(200, view)
                    return
                if not path.startswith(prefix):
                    self._send(404, {"error": {"message": "not found"}})

            def do_POST(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                if not self._authorized():
                    return
                if path == "/v1/images/generations":
                    body = self._body()
                    task_id = f"img_{uuid.uuid4().hex[:12]}"
                    with gateway.lock:
                        gateway.tasks[task_id] = GatewayTask(
                            model=str(body.get("model")),
                            idempotency_key=self.headers.get("Idempotency-Key", ""),
                        )
                    self._send(200, {"id": task_id, "status": "queued"})
                    return
                prefix, suffix = "/v1/images/generations/", "/reply"
                if path.startswith(prefix) and path.endswith(suffix):
                    task_id = path[len(prefix):-len(suffix)]
                    answer = self._body()
                    with gateway.lock:
                        task = gateway.tasks.get(task_id)
                        if task is None or task.model != MODEL_ASK or task.answered:
                            self._send(409, {"error": {"message": "task is not waiting for input"}})
                            return
                        task.answered = True
                        task.replies.append(answer)
                    self._send(200, {"id": task_id, "status": "processing"})
                    return
                self._send(404, {"error": {"message": "not found"}})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, name="fake-muse2api", daemon=True).start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


# ----------------------------------------------------------------------------- stack


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _tail(path: Path, lines: int = 40) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return "<no log>"


@dataclass
class Stack:
    api_url: str
    api: httpx.Client
    redis: redis.Redis
    gateway: FakeGateway
    media_root: Path
    api_log: Path
    worker_log: Path

    def diagnostics(self) -> str:
        return (
            f"\n--- worker log tail ---\n{_tail(self.worker_log)}"
            f"\n--- api log tail ---\n{_tail(self.api_log)}"
        )

    def wait_for(self, predicate: Callable[[], T], timeout: float, what: str, interval: float = 0.1) -> T:
        deadline = time.monotonic() + timeout
        while True:
            value = predicate()
            if value:
                return value
            if time.monotonic() > deadline:
                raise AssertionError(f"Timed out after {timeout}s waiting for {what}.{self.diagnostics()}")
            time.sleep(interval)

    # -- API helpers ---------------------------------------------------------
    def setup_project(self, *, provider_type: str, max_concurrency: int = 2) -> tuple[str, str, list[str]]:
        project = self.api.post("/api/v1/projects", json={"name": f"arq-e2e-{uuid.uuid4().hex[:6]}"})
        assert project.status_code == 201, project.text
        project_id = project.json()["id"]
        connection = self.api.post(
            "/api/v1/connections",
            json={
                "name": f"e2e-{provider_type}-{uuid.uuid4().hex[:6]}",
                "provider_type": provider_type,
                "base_url": self.gateway.base_url,
                "api_key": GATEWAY_KEY,
                "timeout_seconds": 10,
                "max_concurrency": max_concurrency,
            },
        )
        assert connection.status_code == 201, connection.text
        imported = self.api.post(
            f"/api/v1/projects/{project_id}/scripts/import",
            json={"raw_content": SCRIPT, "auto_apply_proposal": True},
        )
        assert imported.status_code == 201, imported.text
        shots = self.api.get(f"/api/v1/projects/{project_id}/shots")
        assert shots.status_code == 200, shots.text
        shot_ids = [shot["id"] for shot in shots.json()]
        assert len(shot_ids) == 2
        return project_id, connection.json()["id"], shot_ids

    def enqueue(self, shot_id: str, connection_id: str, *, task_type: str, model_id: str) -> dict[str, Any]:
        response = self.api.post(
            "/api/v1/queue/tasks",
            json={
                "shot_id": shot_id,
                "connection_id": connection_id,
                "task_type": task_type,
                "model_id": model_id,
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["created_new"] is True
        assert body["state"] == "queued"
        return body

    def task(self, project_id: str, task_id: str) -> dict[str, Any]:
        response = self.api.get("/api/v1/queue/tasks", params={"project_id": project_id, "limit": 100})
        assert response.status_code == 200, response.text
        matches = [item for item in response.json()["items"] if item["id"] == task_id]
        assert len(matches) == 1
        return matches[0]

    def wait_state(self, project_id: str, task_id: str, state: str, timeout: float) -> dict[str, Any]:
        def check() -> dict[str, Any] | None:
            task = self.task(project_id, task_id)
            if task["state"] == state:
                return task
            if task["state"] in ("failed", "poll_timeout", "canceled") and task["state"] != state:
                raise AssertionError(f"Task ended in {task['state']} instead of {state}: {task}")
            return None

        return self.wait_for(check, timeout, f"task {task_id} to reach {state}", interval=0.2)

    # -- Redis helpers -------------------------------------------------------
    def step_jobs(self, task_id: str) -> dict[str, float]:
        """Pending (queued or in-progress) ``process_generation_task_step`` jobs of a task."""
        prefix = f"generation-step:{task_id}:"
        jobs = {
            member.decode(): score
            for member, score in self.redis.zrange(ARQ_QUEUE, 0, -1, withscores=True)
            if member.decode().startswith(prefix)
        }
        return jobs

    def job_alive(self, job_id: str) -> bool:
        return bool(
            self.redis.exists(ARQ_JOB_PREFIX + job_id)
            or self.redis.exists(ARQ_IN_PROGRESS_PREFIX + job_id)
            or self.redis.zscore(ARQ_QUEUE, job_id) is not None
        )


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@pytest.fixture(scope="module")
def stack() -> Iterator[Stack]:
    client = redis.Redis.from_url(_REDIS_URL, socket_timeout=5, socket_connect_timeout=5)
    try:
        client.ping()
    except redis.RedisError as exc:
        pytest.fail(f"TEST_REDIS_URL is set but Redis is unreachable ({type(exc).__name__}).", pytrace=False)
    if client.dbsize() != 0:
        pytest.fail(
            "The Redis database in TEST_REDIS_URL is not empty; point the suite at a dedicated, "
            "empty database (it is flushed after the run).",
            pytrace=False,
        )

    workdir = Path(tempfile.mkdtemp(prefix="muse_arq_e2e_"))
    media_root = workdir / "media"
    media_root.mkdir()
    db_path = workdir / "e2e.db"
    api_port = _free_port()
    api_log = workdir / "api.log"
    worker_log = workdir / "worker.log"

    env = {
        key: value
        for key, value in os.environ.items()
        if key.lower() not in {"http_proxy", "https_proxy", "all_proxy"}
    }
    env.update(
        {
            "ENVIRONMENT": "development",
            # A configured token also exercises the product-route admin dependency.
            "STUDIO_ADMIN_TOKEN": ADMIN_TOKEN,
            "SECRET_ENCRYPTION_KEY": "e2e-only-encryption-key-" + uuid.uuid4().hex,
            # Two processes share the file; wait for SQLite's write lock instead of failing.
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}?timeout=30",
            "REDIS_URL": _REDIS_URL,
            "MEDIA_ROOT": str(media_root),
            "ORPHAN_CLEANUP_ENABLED": "false",
            "MUSE2API_VIDEO_PAYLOAD_PROFILE": "compat",
            "LOG_LEVEL": "INFO",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": os.pathsep.join(
                [str(ORCHESTRATOR_DIR), str(REPO_ROOT / "apps" / "media-engine")]
            ),
        }
    )

    # The working directory is the temp dir so no developer .env is picked up.
    migrate = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ORCHESTRATOR_DIR / "alembic.ini"), "upgrade", "head"],
        cwd=workdir,
        env=env,
        capture_output=True,
        timeout=120,
    )
    assert migrate.returncode == 0, migrate.stderr.decode(errors="replace")[-2000:]
    with sqlite3.connect(db_path) as conn:
        versions = [row[0] for row in conn.execute("SELECT version_num FROM alembic_version")]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert len(versions) == 1 and versions[0]
    assert {"generation_tasks", "generation_dispatch_leases", "shot_takes"} <= tables

    gateway = FakeGateway()
    gateway.start()

    processes: list[subprocess.Popen[bytes]] = []
    api_out = api_log.open("wb")
    worker_out = worker_log.open("wb")
    api_client = httpx.Client(
        base_url=f"http://127.0.0.1:{api_port}",
        headers={"X-Studio-Admin-Token": ADMIN_TOKEN},
        timeout=30.0,
        trust_env=False,
    )
    try:
        processes.append(
            subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "muse_orchestrator.main:app",
                 "--host", "127.0.0.1", "--port", str(api_port), "--log-level", "warning"],
                cwd=workdir, env=env, stdout=api_out, stderr=subprocess.STDOUT,
            )
        )
        processes.append(
            subprocess.Popen(
                [sys.executable, "-m", "arq", "muse_orchestrator.workers.queue_worker.WorkerSettings"],
                cwd=workdir, env=env, stdout=worker_out, stderr=subprocess.STDOUT,
            )
        )
        built = Stack(
            api_url=f"http://127.0.0.1:{api_port}",
            api=api_client,
            redis=client,
            gateway=gateway,
            media_root=media_root,
            api_log=api_log,
            worker_log=worker_log,
        )

        def api_ready() -> bool:
            for process in processes:
                if process.poll() is not None:
                    raise AssertionError(f"A stack process exited early.{built.diagnostics()}")
            try:
                return api_client.get("/readyz").status_code == 200
            except httpx.TransportError:
                return False

        built.wait_for(api_ready, 60, "API /readyz (database + Redis)", interval=0.2)
        # The worker records its health-check key on its first loop iteration.
        built.wait_for(lambda: client.exists(ARQ_HEALTH_KEY), 60, "ARQ worker health-check key", interval=0.2)
        yield built
    finally:
        for process in reversed(processes):
            _stop(process)
        api_client.close()
        api_out.close()
        worker_out.close()
        gateway.stop()
        try:
            # Verified empty at start, so this only removes keys created by this run.
            client.flushdb()
        finally:
            client.close()
        shutil.rmtree(workdir, ignore_errors=True)


# ----------------------------------------------------------------------------- tests


def test_enqueue_is_dispatched_polled_and_completed_by_worker_mock_provider(stack: Stack) -> None:
    """POST /queue/tasks -> real ARQ job -> worker dispatch, polls, take + asset (mock video)."""
    project_id, connection_id, shots = stack.setup_project(provider_type="mock")
    task = stack.enqueue(shots[0], connection_id, task_type="video", model_id="muse-video-mock-v1")

    done = stack.wait_state(project_id, task["id"], "succeeded", timeout=45)

    assert done["external_task_id"].startswith("mock_vid_")
    assert done["attempt_count"] == 1
    assert done["poll_count"] == 2  # mock provider: running, then succeeded
    assert done["result_asset_id"] and done["take_id"]
    takes = stack.api.get(f"/api/v1/queue/shots/{shots[0]}/takes")
    assert takes.status_code == 200, takes.text
    assert [(t["id"], t["task_id"], t["asset_id"]) for t in takes.json()] == [
        (done["take_id"], task["id"], done["result_asset_id"])
    ]
    download = stack.api.get(f"/api/v1/assets/{done['result_asset_id']}/download")
    assert download.status_code == 200
    assert download.content[4:8] == b"ftyp"
    assert any((stack.media_root / project_id / "generated").iterdir())
    # The chain ends with the terminal step: nothing left scheduled for this task.
    stack.wait_for(lambda: not stack.step_jobs(task["id"]), 10, "no pending step jobs")


def test_muse2api_adapter_task_completes_through_worker(stack: Stack) -> None:
    """The real Muse2ApiAdapter, driven by the worker, dispatches once, polls and downloads."""
    project_id, connection_id, shots = stack.setup_project(provider_type="muse2api")
    task = stack.enqueue(shots[0], connection_id, task_type="image", model_id=MODEL_OK)

    done = stack.wait_state(project_id, task["id"], "succeeded", timeout=30)

    assert done["external_task_id"].startswith("image:img_")
    assert stack.gateway.generate_count(task["idempotency_key"]) == 1
    assert stack.gateway.polls(done["external_task_id"]) == 1
    assert stack.gateway.unauthorized_requests == 0
    download = stack.api.get(f"/api/v1/assets/{done['result_asset_id']}/download")
    assert download.status_code == 200
    assert download.content == PNG_BYTES
    stack.wait_for(lambda: not stack.step_jobs(task["id"]), 10, "no pending step jobs")


def test_manual_step_supersedes_scheduled_job_without_forking_chain(stack: Stack) -> None:
    """An extra HTTP step makes the already-scheduled ARQ job stale: it exits without polling."""
    project_id, connection_id, shots = stack.setup_project(provider_type="muse2api")
    task = stack.enqueue(shots[0], connection_id, task_type="image", model_id=MODEL_HOLD)

    running = stack.wait_for(
        lambda: (t := stack.task(project_id, task["id"]))["state"] == "running"
        and t["external_task_id"]
        and len(stack.step_jobs(task["id"])) == 1
        and t,
        20,
        "dispatch and one scheduled poll job",
    )
    external_id = running["external_task_id"]
    ((old_job, old_score),) = stack.step_jobs(task["id"]).items()
    # Dispatch backoff is >= 5 s, so the step below lands well before the old job is due.
    assert old_score / 1000 - time.time() > 1.0

    stepped = stack.api.post(f"/api/v1/queue/tasks/{task['id']}/step")
    assert stepped.status_code == 200, stepped.text
    assert stepped.json()["poll_count"] == 1
    assert stack.gateway.polls(external_id) == 1
    jobs_after_step = stack.step_jobs(task["id"])
    assert old_job in jobs_after_step and len(jobs_after_step) == 2
    (new_job,) = set(jobs_after_step) - {old_job}

    # Let both jobs run in the real worker.
    stack.wait_for(
        lambda: not stack.job_alive(old_job) and not stack.job_alive(new_job), 20, "both step jobs to run"
    )
    # The stale job skipped; only the new chain polled. Exactly one chain remains.
    assert stack.gateway.polls(external_id) == 2
    assert stack.task(project_id, task["id"])["poll_count"] == 2
    successors = stack.step_jobs(task["id"])
    assert len(successors) == 1 and not set(successors) & {old_job, new_job}

    stack.gateway.release(external_id)
    done = stack.wait_state(project_id, task["id"], "succeeded", timeout=30)
    assert done["poll_count"] == 3
    assert stack.gateway.polls(external_id) == 3
    assert stack.gateway.generate_count(task["idempotency_key"]) == 1
    stack.wait_for(lambda: not stack.step_jobs(task["id"]), 10, "no pending step jobs")


def test_cancel_running_task_stops_the_worker_chain(stack: Stack) -> None:
    """Cancelling a RUNNING task: its scheduled job runs, sees the terminal row, polls nothing."""
    project_id, connection_id, shots = stack.setup_project(provider_type="muse2api")
    task = stack.enqueue(shots[0], connection_id, task_type="image", model_id=MODEL_HOLD)
    running = stack.wait_for(
        lambda: (t := stack.task(project_id, task["id"]))["state"] == "running"
        and t["external_task_id"]
        and len(stack.step_jobs(task["id"])) == 1
        and t,
        20,
        "dispatch and one scheduled poll job",
    )
    ((pending_job, _score),) = stack.step_jobs(task["id"]).items()

    canceled = stack.api.post(f"/api/v1/queue/tasks/{task['id']}/cancel")
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["state"] == "canceled"

    stack.wait_for(lambda: not stack.job_alive(pending_job), 20, "the pending step job to run")
    final = stack.task(project_id, task["id"])
    assert final["state"] == "canceled"
    assert final["result_asset_id"] is None and final["take_id"] is None
    # Builds that propagate cancel to the provider append a "Provider cancel: ..." entry after it.
    assert "Canceled by operator." in final["history_log"]
    assert stack.gateway.polls(running["external_task_id"]) == 0
    assert stack.step_jobs(task["id"]) == {}
    assert stack.api.get(f"/api/v1/queue/shots/{shots[0]}/takes").json() == []


def test_awaiting_input_reply_resumes_and_completes(stack: Stack) -> None:
    """needs_input -> AWAITING_INPUT -> POST /reply -> RUNNING -> SUCCEEDED, all via the worker."""
    project_id, connection_id, shots = stack.setup_project(provider_type="muse2api")
    task = stack.enqueue(shots[0], connection_id, task_type="image", model_id=MODEL_ASK)

    waiting = stack.wait_state(project_id, task["id"], "awaiting_input", timeout=20)
    assert waiting["pending_input"] == {
        "kind": "question",
        "text": QUESTION["text"],
        "options": QUESTION["options"],
        "asked_at": QUESTION["asked_at"],
    }
    assert waiting["next_poll_delay_seconds"] == 15.0

    replied = stack.api.post(f"/api/v1/queue/tasks/{task['id']}/reply", json={"choice": "Cinematic"})
    assert replied.status_code == 200, replied.text
    assert replied.json()["state"] == "running"
    assert replied.json()["pending_input"] is None
    assert stack.gateway.task(waiting["external_task_id"]).replies == [{"choice": "Cinematic"}]

    done = stack.wait_state(project_id, task["id"], "succeeded", timeout=20)
    history = done["history_log"]
    asked = next(i for i, entry in enumerate(history) if entry.startswith("Provider is waiting for operator input"))
    answered = next(i for i, entry in enumerate(history) if entry.startswith("Operator answered (choice): Cinematic"))
    assert asked < answered
    assert done["result_asset_id"] and done["take_id"]
    assert stack.gateway.generate_count(task["idempotency_key"]) == 1


def test_recovery_cron_reschedules_queued_task_whose_job_was_lost(stack: Stack) -> None:
    """A QUEUED task with no ARQ job is picked up by the real per-minute recovery cron."""
    project_id, connection_id, shots = stack.setup_project(provider_type="muse2api", max_concurrency=1)
    blocker = stack.enqueue(shots[0], connection_id, task_type="image", model_id=MODEL_HOLD)
    stack.wait_state(project_id, blocker["id"], "running", timeout=20)

    # The second task finds the only slot busy: the worker leaves it QUEUED with a backoff job.
    task = stack.enqueue(shots[1], connection_id, task_type="image", model_id=MODEL_OK)
    stack.wait_for(
        lambda: any(
            entry.startswith("Waiting for connection concurrency slot")
            for entry in stack.task(project_id, task["id"])["history_log"]
        )
        and len(stack.step_jobs(task["id"])) == 1,
        20,
        "the slot-wait step and its follow-up job",
    )
    # Simulate a lost job (Redis restart/eviction, failed enqueue): delete it before it runs.
    ((lost_job, _score),) = stack.step_jobs(task["id"]).items()
    deleted_at_ms = int(time.time() * 1000)
    assert stack.redis.zrem(ARQ_QUEUE, lost_job) == 1
    stack.redis.delete(ARQ_JOB_PREFIX + lost_job)
    # Free the slot. Cancelling does not schedule anything for the second task.
    assert stack.api.post(f"/api/v1/queue/tasks/{blocker['id']}/cancel").status_code == 200
    assert stack.task(project_id, task["id"])["state"] == "queued"
    if not _recovery_runs_since(stack, deleted_at_ms - 1000):
        # Unless the cron fired in this very instant, nothing is scheduled for the task.
        assert stack.step_jobs(task["id"]) == {}

    # Recovery runs at second 0 of every minute and re-creates the same (deduplicated) job id.
    stack.wait_for(
        lambda: lost_job in stack.step_jobs(task["id"])
        or stack.task(project_id, task["id"])["state"] != "queued",
        75,
        "the recovery cron to re-schedule the lost job",
        interval=0.2,
    )
    rescheduled_at_ms = int(time.time() * 1000)
    # A run scheduled just before the deletion may start up to ~0.5 s late (worker poll).
    runs = _recovery_runs_since(stack, deleted_at_ms - 1000)
    assert runs and min(runs) <= rescheduled_at_ms, "no recovery cron run explains the re-scheduled job"

    done = stack.wait_state(project_id, task["id"], "succeeded", timeout=30)
    assert done["attempt_count"] == 1
    assert stack.gateway.generate_count(task["idempotency_key"]) == 1
    stack.wait_for(lambda: not stack.step_jobs(task["id"]), 10, "no pending step jobs")


def _recovery_runs_since(stack: Stack, since_ms: int) -> list[int]:
    """Scheduled times (ms) of recovery cron runs ARQ recorded since ``since_ms``.

    ARQ keeps a finished cron job's in-progress key for 60 s
    (``arq:in-progress:cron:<name>:<scheduled ms>``) to prevent duplicate runs.
    """
    runs = []
    for key in stack.redis.keys(RECOVERY_CRON_PROGRESS_PATTERN):
        scheduled_ms = int(key.decode().rsplit(":", 1)[1])
        if scheduled_ms >= since_ms:
            runs.append(scheduled_ms)
    return runs
