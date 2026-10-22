"""Completion gate: refuse to finish while changed code is unverified.

The gate runs only the suites affected by the branch's changes. A passing result is cached
against a fingerprint of HEAD + working tree, so a turn that changes nothing does not rerun
the suites. Missing prerequisites are reported as NOT RUN, never as a pass.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Callable

Runner = Callable[[list[str], "Path | None", int], tuple[int, str]]

CACHE_NAME = "muse-agent-gate.json"
MAX_BLOCKS_PER_STATE = 2
_HASHED_UNTRACKED_SUFFIXES = (".py", ".ts", ".tsx", ".json", ".toml")


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(cmd)}: timed out after {timeout}s"
    return proc.returncode, (proc.stdout + proc.stderr)


def collect_changed_paths(root: Path, runner: Runner | None = None) -> set[str]:
    """Include uncommitted files and branch commits so committing cannot bypass the gate."""
    runner = runner or run
    changed: set[str] = set()

    status_code, status = runner(["git", "status", "--porcelain"], root, 15)
    if status_code != 0:
        raise RuntimeError(f"git status failed (exit {status_code}): {status[-1000:]}")
    for line in status.splitlines():
        if len(line) > 3:
            path = line[3:].split(" -> ")[-1].strip().strip('"')
            changed.add(path.replace("\\", "/"))

    # Never use HEAD^-HEAD as a silent fallback: it misses older commits in this branch.
    branch_code, branch_diff = runner(
        ["git", "diff", "--name-only", "origin/main...HEAD"], root, 15
    )
    if branch_code == 0:
        changed.update(p.replace("\\", "/") for p in branch_diff.splitlines() if p.strip())
    else:
        merge_base = ""
        baseline_errors: list[str] = []
        for candidate in ("origin/main", "main", "master"):
            base_code, base_output = runner(
                ["git", "merge-base", candidate, "HEAD"], root, 15
            )
            if base_code == 0 and base_output.strip():
                merge_base = base_output.strip().splitlines()[0]
                break
            baseline_errors.append(f"{candidate}: exit {base_code} {base_output[-300:]}")

        if not merge_base:
            raise RuntimeError(
                "No reliable branch baseline is available; refusing to check only HEAD^..HEAD. "
                "Fetch origin/main or create a local main branch, then retry. Details: "
                + "; ".join(baseline_errors)
            )

        diff_code, baseline_diff = runner(
            ["git", "diff", "--name-only", f"{merge_base}..HEAD"], root, 15
        )
        if diff_code != 0:
            raise RuntimeError(
                f"Branch diff against merge-base failed (exit {diff_code}): "
                f"{baseline_diff[-1000:]}"
            )
        changed.update(p.replace("\\", "/") for p in baseline_diff.splitlines() if p.strip())

    staged_code, staged = runner(["git", "diff", "--cached", "--name-only"], root, 15)
    if staged_code != 0:
        raise RuntimeError(f"git diff --cached failed (exit {staged_code}): {staged[-1000:]}")
    changed.update(p.replace("\\", "/") for p in staged.splitlines() if p.strip())

    work_code, work = runner(["git", "diff", "--name-only"], root, 15)
    if work_code != 0:
        raise RuntimeError(f"git diff failed (exit {work_code}): {work[-1000:]}")
    changed.update(p.replace("\\", "/") for p in work.splitlines() if p.strip())

    untracked_code, untracked = runner(["git", "ls-files", "--others", "--exclude-standard"], root, 15)
    if untracked_code != 0:
        raise RuntimeError(f"git ls-files failed (exit {untracked_code}): {untracked[-1000:]}")
    changed.update(p.replace("\\", "/") for p in untracked.splitlines() if p.strip())
    return changed


def resolve_python(root: Path, *apps: str) -> str:
    """Prefer the project's locked virtualenv over a system python3 without test deps."""
    for app in apps:
        for relative in (".venv/bin/python", ".venv/Scripts/python.exe"):
            candidate = root / "apps" / app / relative
            if candidate.is_file():
                return str(candidate)
    return "python3"


def classify(changed: set[str]) -> tuple[list[str], list[str], list[str]]:
    py_changed = [p for p in changed if p.endswith(".py")]
    backend = sorted(p for p in py_changed if p.startswith("apps/orchestrator/"))
    media = sorted(p for p in py_changed if p.startswith("apps/media-engine/"))
    web = sorted(
        p for p in changed
        if p.startswith("apps/studio-web/") and p.endswith((".ts", ".tsx")) and "/node_modules/" not in p
    )
    return backend, media, web


def validate_changed(
    root: Path,
    backend: list[str],
    media: list[str],
    runner: Runner | None = None,
    web: list[str] | None = None,
) -> list[str]:
    runner = runner or run
    problems: list[str] = []

    code, out = runner(["python3", "scripts/check_layering.py", "--quiet"], root, 30)
    if code != 0:
        problems.append(f"Layering checker did not pass (exit {code}):\n{out.strip()[-1200:]}")

    if backend:
        cwd = root / "apps" / "orchestrator"
        python = resolve_python(root, "orchestrator")
        code, out = runner([python, "-c", "import pytest, fastapi"], cwd, 20)
        if code != 0:
            problems.append(
                f"Orchestrator tests NOT RUN: prerequisites failed (exit {code}):\n{out.strip()[-1200:]}"
            )
        else:
            code, out = runner(
                [python, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
                cwd,
                200,
            )
            if code != 0:
                problems.append(
                    f"Orchestrator tests did not pass (exit {code}; no tests collected is not pass):\n{out.strip()[-2000:]}"
                )

    if media:
        cwd = root / "apps" / "media-engine"
        # Match scripts/test.sh: the locked orchestrator venv carries pytest and an editable
        # muse_media install, while a bare media-engine .venv may have no test dependencies.
        python = resolve_python(root, "orchestrator")
        code, out = runner([python, "-c", "import pytest"], cwd, 20)
        if code != 0:
            problems.append(
                f"Media-engine tests NOT RUN: pytest unavailable (exit {code}):\n{out.strip()[-1200:]}"
            )
        else:
            code, out = runner(
                [python, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
                cwd,
                60,
            )
            if code != 0:
                problems.append(
                    f"Media-engine tests did not pass (exit {code}; no tests collected is not pass):\n{out.strip()[-1200:]}"
                )

    if web:
        cwd = root / "apps" / "studio-web"
        tsc = cwd / "node_modules" / ".bin" / "tsc"
        if not tsc.is_file():
            problems.append("Frontend typecheck NOT RUN: apps/studio-web/node_modules is missing (run npm ci).")
        else:
            code, out = runner([str(tsc), "-p", "tsconfig.json", "--noEmit"], cwd, 150)
            if code != 0:
                problems.append(f"Frontend typecheck did not pass (exit {code}):\n{out.strip()[-1500:]}")
    return problems


def change_fingerprint(root: Path, runner: Runner | None = None) -> str | None:
    """Hash HEAD, the tracked diff and relevant untracked sources; None if git fails."""
    runner = runner or run
    digest = hashlib.sha256()
    for command in (["git", "rev-parse", "HEAD"], ["git", "diff", "HEAD", "--binary"]):
        code, out = runner(command, root, 30)
        if code != 0:
            return None
        digest.update(out.encode("utf-8", "surrogateescape"))
    code, untracked = runner(["git", "ls-files", "--others", "--exclude-standard"], root, 15)
    if code != 0:
        return None
    for relative in sorted(p for p in untracked.splitlines() if p.strip()):
        digest.update(relative.encode())
        if relative.startswith("apps/") and relative.endswith(_HASHED_UNTRACKED_SUFFIXES):
            try:
                digest.update((root / relative).read_bytes())
            except OSError:
                return None
    return digest.hexdigest()


def _state_path(root: Path) -> Path | None:
    code, out = run(["git", "rev-parse", "--git-path", CACHE_NAME], root, 10)
    if code != 0 or not out.strip():
        return None
    path = Path(out.strip().splitlines()[0])
    return path if path.is_absolute() else root / path


def _load_state(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(path: Path | None, state: dict) -> None:
    if path is None:
        return
    try:
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass  # The cache is an optimization; failing to write it only costs a rerun.


def completion_gate(
    root: Path,
    session_key: str | None = None,
    runner: Runner | None = None,
    state_path: Path | None = None,
) -> str | None:
    """Return a message explaining why the agent must keep working, or None to allow stop.

    `session_key` enables a loop guard for hosts without a "stop hook already active" flag:
    the same unchanged failing state blocks at most MAX_BLOCKS_PER_STATE times per session.
    """
    try:
        changed = collect_changed_paths(root, runner)
    except RuntimeError as exc:
        return f"[stop_gate] Cannot determine changed paths; do not claim checks passed.\n{exc}"

    backend, media, web = classify(changed)
    if not (backend or media or web):
        return None

    fingerprint = change_fingerprint(root, runner)
    if state_path is None and runner is None:
        state_path = _state_path(root)
    state = _load_state(state_path)
    if fingerprint and state.get("passed") == fingerprint:
        return None

    problems = validate_changed(root, backend, media, runner, web)
    if not problems:
        if fingerprint:
            _save_state(state_path, {"passed": fingerprint})
        return None

    if session_key and fingerprint:
        attempts = state.get("blocked") if isinstance(state.get("blocked"), dict) else {}
        key = f"{session_key}:{fingerprint}"
        count = int(attempts.get(key, 0)) + 1
        if count > MAX_BLOCKS_PER_STATE:
            return None  # The agent was told twice; let it stop and report NOT RUN/FAIL.
        _save_state(state_path, {"passed": state.get("passed"), "blocked": {key: count}})

    return (
        "[stop_gate] Work is not verified:\n\n"
        + "\n\n".join(problems)
        + "\n\nFix the issue or report exactly which required check could not run. "
        "Deliberate local override: MUSE_STOP_GATE=0."
    )
