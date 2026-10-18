#!/usr/bin/env python3
"""Validate shared agent policy, skills and Claude mirrors using only the stdlib."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_DOCS = (
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "docs/AGENT_WORKFLOW.md",
)
SKILL_ROOT = ROOT / ".agents" / "skills"
CLAUDE_SKILLS = ROOT / ".claude" / "skills"


def error(message: str) -> None:
    print(f"[FAIL] {message}")


def main() -> int:
    errors: list[str] = []
    for rel in REQUIRED_DOCS:
        if not (ROOT / rel).is_file():
            errors.append(f"missing required agent document: {rel}")

    # Every platform entrypoint must defer to the shared contract, not fork it.
    for rel in ("CLAUDE.md", "GEMINI.md"):
        path = ROOT / rel
        if path.exists():
            content = path.read_text(encoding="utf-8")
            if "AGENTS.md" not in content or "docs/AGENT_WORKFLOW.md" not in content:
                errors.append(f"{rel} must link back to AGENTS.md and docs/AGENT_WORKFLOW.md")

    if not SKILL_ROOT.is_dir():
        errors.append("missing canonical shared skills directory: .agents/skills/")
    else:
        canonical = sorted(p for p in SKILL_ROOT.rglob("SKILL.md") if p.is_file())
        if not canonical:
            errors.append("no SKILL.md found under .agents/skills/")
        for skill in canonical:
            rel = skill.relative_to(SKILL_ROOT)
            folder_name = skill.parent.name
            body = skill.read_text(encoding="utf-8")
            front = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", body, re.S)
            if not front:
                errors.append(f"{skill.relative_to(ROOT)}: missing YAML front matter")
                continue
            metadata = front.group(1)
            name = re.search(r"(?m)^name:\s*['\"]?([^\s'\"#]+)", metadata)
            description = re.search(r"(?m)^description:\s*(.+)\s*$", metadata)
            if not name:
                errors.append(f"{skill.relative_to(ROOT)}: front matter must include name")
            elif name.group(1) != folder_name:
                errors.append(
                    f"{skill.relative_to(ROOT)}: name '{name.group(1)}' must match folder '{folder_name}'"
                )
            if not description or not description.group(1).strip():
                errors.append(f"{skill.relative_to(ROOT)}: front matter must include description")

            mirror = CLAUDE_SKILLS / rel
            if not mirror.is_file():
                errors.append(f"missing Claude skill mirror: {mirror.relative_to(ROOT)}")
            elif mirror.read_bytes() != skill.read_bytes():
                errors.append(
                    f"stale Claude skill mirror: {mirror.relative_to(ROOT)}; edit .agents/skills/ then sync"
                )

    # Local Markdown links to repository paths must not silently rot.
    markdowns = [ROOT / p for p in REQUIRED_DOCS if (ROOT / p).is_file()]
    markdowns += [ROOT / "docs/AGENT_WORKFLOW.md"]
    link_re = re.compile(r"!??\[[^\]]*\]\(([^)]+)\)")
    for source in sorted(set(markdowns)):
        content = source.read_text(encoding="utf-8")
        for raw in link_re.findall(content):
            target = raw.strip().split()[0].strip("<>")
            if not target or target.startswith(("http://", "https://", "mailto:", "#", "data:")):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            candidate = (source.parent / target).resolve()
            try:
                candidate.relative_to(ROOT)
            except ValueError:
                errors.append(f"{source.relative_to(ROOT)}: link escapes repo: {raw}")
                continue
            if not candidate.exists():
                errors.append(f"{source.relative_to(ROOT)}: broken local link: {raw}")

    if errors:
        for item in errors:
            error(item)
        print(f"Agent asset validation failed: {len(errors)} issue(s).")
        return 1

    skills = sum(1 for _ in SKILL_ROOT.rglob("SKILL.md"))
    print(f"[OK] required entrypoints present; {skills} shared skills match Claude mirrors; local links valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
