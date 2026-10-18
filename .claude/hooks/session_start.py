#!/usr/bin/env python3
"""SessionStart: give the agent a quick, factual picture of the repo state."""
from __future__ import annotations

from _common import project_dir, run

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
    lines.append(f"- Uncommitted changes: {len([l for l in status.splitlines() if l.strip()])}")

orch = root / "apps" / "orchestrator"
lines.append(f"- Alembic migrations: {'present' if (orch / 'migrations').exists() or (orch / 'alembic').exists() else 'MISSING (use skill alembic-migrations)'}")
compose = (root / "docker-compose.yml")
has_worker = compose.exists() and "arq" in compose.read_text(errors="ignore")
lines.append(f"- ARQ worker service in compose: {'yes' if has_worker else 'NO'}")
web_pkg = root / "apps" / "studio-web" / "package.json"
lines.append(f"- Workflow canvas lib in web: {'yes' if web_pkg.exists() and ('reactflow' in web_pkg.read_text() or '@xyflow' in web_pkg.read_text()) else 'NO'}")

for tool in ("python3", "ruff", "pytest", "ffmpeg", "ffprobe", "node"):
    code, _ = run(["which", tool], root, 5)
    if code != 0:
        lines.append(f"- Missing tool: {tool} (run scripts/doctor.sh)")

lines.append("- Read CLAUDE.md for rules. Verify with `scripts/test.sh` before claiming a task is done.")
print("\n".join(lines))
