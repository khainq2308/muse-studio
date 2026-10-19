#!/usr/bin/env python3
"""SessionStart: give the agent a quick, factual picture of the repo state."""
from __future__ import annotations

import re

from _common import project_dir, run


def _has_arq_worker_service(compose_text: str) -> bool:
    """Return True only when Compose declares the worker service and an ARQ command."""
    in_services = False
    current_service: str | None = None
    found_worker = False
    worker_has_arq_command = False

    for line in compose_text.splitlines():
        if line and not line[0].isspace():
            in_services = line.strip() == "services:"
            current_service = None
            continue
        if not in_services:
            continue

        service_match = re.match(r"^  ([A-Za-z0-9_.-]+):\\s*(?:#.*)?$", line)
        if service_match:
            current_service = service_match.group(1)
            continue

        if current_service == "orchestrator-worker":
            found_worker = True
            if re.match(r"^    command:\\s*(?:arq(?:\\s|$)|\\[\\s*['\\\"]arq['\\\"])", line):
                worker_has_arq_command = True

    return found_worker and worker_has_arq_command


root = project_dir()
lines = ["## MuseStudio session context"]

code, branch = run(["git", "symbolic-ref", "--short", "HEAD"], root, 10)
if code != 0:
    lines.append("- Git: not a git repository here (branch rules cannot be checked)")
else:
    branch = branch.strip()
    warn = "  (!! on main: create a feat/*, fix/* or refactor/* branch before editing)" if branch in {"main", "master"} else ""
    lines.append(f"- Branch: {branch}{warn}")
    _, status = run(["git", "status", "--porcelain"], root, 10)
    lines.append(f"- Uncommitted changes: {len([line for line in status.splitlines() if line.strip()])}")

orch = root / "apps" / "orchestrator"
lines.append(f"- Alembic migrations: {'present' if (orch / 'migrations').exists() or (orch / 'alembic').exists() else 'MISSING (use skill alembic-migrations)'}")
compose = root / "docker-compose.yml"
has_worker = compose.exists() and _has_arq_worker_service(compose.read_text(errors="ignore"))
lines.append(f"- ARQ worker service in compose: {'yes' if has_worker else 'NO'}")
web_pkg = root / "apps" / "studio-web" / "package.json"
lines.append(f"- Workflow canvas lib in web: {'yes' if web_pkg.exists() and ('reactflow' in web_pkg.read_text() or '@xyflow' in web_pkg.read_text()) else 'NO'}")

for tool in ("python3", "ruff", "pytest", "ffmpeg", "ffprobe", "node"):
    code, _ = run(["which", tool], root, 5)
    if code != 0:
        lines.append(f"- Missing tool: {tool} (run scripts/doctor.sh)")

lines.append("- Read AGENTS.md and docs/AGENT_WORKFLOW.md. Use a relevant shared skill under .agents/skills/. A missing check is NOT RUN, not PASS.")
print("\n".join(lines))
