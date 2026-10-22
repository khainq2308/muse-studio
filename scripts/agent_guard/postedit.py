"""After-edit checks: Ruff correctness on the edited file and architectural nudges."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from agent_guard.verify import run

RUFF_CONFIG = "apps/orchestrator/pyproject.toml"
DB_CALLS = re.compile(r"\b(?:db|session)\.(?:execute|add|add_all|commit|delete|refresh|get|flush)\(|\bselect\(")


def resolve_ruff(root: Path) -> str | None:
    venv_ruff = root / "apps" / "orchestrator" / ".venv" / "bin" / "ruff"
    if venv_ruff.is_file():
        return str(venv_ruff)
    return shutil.which("ruff")


def check_edited_file(root: Path, raw_path: object) -> tuple[str | None, list[str]]:
    """Return (blocking message, advisory notes) for an edited file.

    Only safe Ruff fixes are applied, with the committed config used by scripts/lint.sh.
    The file is deliberately not reformatted: most legacy files are not ruff-format clean,
    and formatting them on every edit would bury the real change in unrelated diff noise.
    """
    if not isinstance(raw_path, str) or not raw_path:
        return None, []
    root = root.resolve()
    path = Path(raw_path)
    path = path if path.is_absolute() else root / path
    if not path.exists() or path.suffix != ".py":
        return None, []
    try:
        rel = path.resolve().relative_to(root).as_posix()
    except ValueError:
        return None, []

    notes: list[str] = []
    ruff = resolve_ruff(root)
    if ruff is None:
        notes.append(f"Ruff NOT RUN on {rel}: ruff is not installed (run scripts/doctor.sh).")
    else:
        config = ["--config", str(root / RUFF_CONFIG)] if (root / RUFF_CONFIG).is_file() else []
        run([ruff, "check", *config, "--fix", "--quiet", str(path)], root, 30)
        code, out = run([ruff, "check", *config, "--output-format", "concise", str(path)], root, 30)
        if code != 0 and out.strip():
            return f"[post_edit] ruff found issues in {rel} that need a manual fix:\n{out.strip()[:1500]}", notes

    text = path.read_text(encoding="utf-8", errors="ignore")
    if "/muse_orchestrator/api/" in f"/{rel}" and not rel.endswith("dependencies.py"):
        hits = len(DB_CALLS.findall(text))
        if hits:
            notes.append(
                f"{rel} still contains {hits} direct DB call(s). Routes must stay HTTP-only: "
                "new persistence logic belongs in application/ + infrastructure/repositories.py "
                "(run `python3 scripts/check_layering.py` to see the ratchet status)."
            )
    if re.search(r"shell\s*=\s*True", text):
        notes.append(f"{rel} uses shell=True. Use an argv list (see skill `safe-ffmpeg`).")
    return None, notes
