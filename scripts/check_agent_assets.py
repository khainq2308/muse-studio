#!/usr/bin/env python3
"""Validate agent docs/links, skill mirrors, platform config and script syntax."""
from __future__ import annotations
import json, re, shutil, subprocess, sys, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_DOCS = ("AGENTS.md", "CLAUDE.md", "GEMINI.md", "docs/AGENT_WORKFLOW.md")
SKILL_ROOT = ROOT / ".agents/skills"
CLAUDE_SKILLS = ROOT / ".claude/skills"
FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.S)
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")

def check_frontmatter(path: Path, expected_name: str | None = None) -> list[str]:
    errors = []
    content = path.read_text(encoding="utf-8")
    m = FRONT.match(content)
    if not m: return [f"{path.relative_to(ROOT)}: missing YAML front matter"]
    meta = m.group(1)
    name = re.search(r"(?m)^name:\s*['\"]?([^\s'\"#]+)", meta)
    desc = re.search(r"(?m)^description:\s*(.+)\s*$", meta)
    if not name: errors.append(f"{path.relative_to(ROOT)}: missing name")
    elif expected_name and name.group(1) != expected_name: errors.append(f"{path.relative_to(ROOT)}: name mismatch")
    if not desc or not desc.group(1).strip(): errors.append(f"{path.relative_to(ROOT)}: missing description")
    return errors

def markdown_files() -> list[Path]:
    paths = {ROOT / p for p in REQUIRED_DOCS if (ROOT / p).is_file()}
    for rel in (".agents/skills", ".claude/skills", ".claude/commands", ".claude/agents", ".gemini/agents"):
        folder = ROOT / rel
        if folder.is_dir(): paths.update(p for p in folder.rglob("*.md") if p.is_file())
    return sorted(paths)

def check_links(paths: list[Path]) -> list[str]:
    errors = []
    for source in paths:
        for raw in LINK.findall(source.read_text(encoding="utf-8")):
            target = raw.strip().split()[0].strip("<>").split("#", 1)[0].split("?", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:", "data:")): continue
            candidate = (source.parent / target).resolve()
            try: candidate.relative_to(ROOT)
            except ValueError:
                errors.append(f"{source.relative_to(ROOT)}: link escapes repo: {raw}"); continue
            if not candidate.exists(): errors.append(f"{source.relative_to(ROOT)}: broken local link: {raw}")
    return errors

def check_configs() -> list[str]:
    errors = []
    for rel in (".claude/settings.json", ".gemini/settings.json"):
        path = ROOT / rel
        if not path.is_file(): errors.append(f"missing settings: {rel}"); continue
        try: json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc: errors.append(f"{rel}: invalid JSON: {exc}")
    codex = ROOT / ".codex/agents"
    if not codex.is_dir(): errors.append("missing .codex/agents/")
    else:
        for path in sorted(codex.glob("*.toml")):
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                for key in ("name", "description", "developer_instructions"):
                    if not isinstance(data.get(key), str) or not data[key].strip():
                        errors.append(f"{path.relative_to(ROOT)}: missing {key}")
                if path.stem in {"code-reviewer", "security-auditor"} and data.get("sandbox_mode") != "read-only":
                    errors.append(f"{path.relative_to(ROOT)}: reviewer/auditor must set sandbox_mode = read-only")
            except (OSError, tomllib.TOMLDecodeError) as exc: errors.append(f"{path.relative_to(ROOT)}: invalid TOML: {exc}")
    gemini = ROOT / ".gemini/agents"
    if not gemini.is_dir(): errors.append("missing .gemini/agents/")
    else:
        for path in sorted(gemini.glob("*.md")):
            errors.extend(check_frontmatter(path, path.stem))
            m = FRONT.match(path.read_text(encoding="utf-8"))
            if m and not re.search(r"(?m)^kind:\s*local\s*$", m.group(1)):
                errors.append(f"{path.relative_to(ROOT)}: Gemini agent should declare kind: local")
    return errors

def check_syntax() -> list[str]:
    errors = []
    py_files = set()
    for rel in (".claude/hooks", ".gemini/hooks", "scripts"):
        folder = ROOT / rel
        if folder.is_dir(): py_files.update(folder.rglob("*.py"))
    for path in sorted(py_files):
        try: compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (OSError, SyntaxError) as exc: errors.append(f"{path.relative_to(ROOT)}: Python syntax error: {exc}")
    bash = shutil.which("bash")
    if not bash: errors.append("bash is required to validate shell scripts")
    else:
        for path in sorted((ROOT / "scripts").glob("*.sh")):
            proc = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True, timeout=10)
            if proc.returncode: errors.append(f"{path.relative_to(ROOT)}: bash -n failed: {proc.stderr.strip()}")
    return errors

def main() -> int:
    errors = []
    for rel in REQUIRED_DOCS:
        if not (ROOT / rel).is_file(): errors.append(f"missing required document: {rel}")
    for rel in ("CLAUDE.md", "GEMINI.md"):
        path = ROOT / rel
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            if "AGENTS.md" not in content or "docs/AGENT_WORKFLOW.md" not in content:
                errors.append(f"{rel} must reference AGENTS.md and docs/AGENT_WORKFLOW.md")
    if not SKILL_ROOT.is_dir(): errors.append("missing .agents/skills/")
    else:
        canonical = sorted(SKILL_ROOT.rglob("SKILL.md"))
        if not canonical: errors.append("no shared SKILL.md found")
        for skill in canonical:
            rel = skill.relative_to(SKILL_ROOT)
            errors.extend(check_frontmatter(skill, skill.parent.name))
            mirror = CLAUDE_SKILLS / rel
            if not mirror.is_file(): errors.append(f"missing Claude mirror: {mirror.relative_to(ROOT)}")
            elif mirror.read_bytes() != skill.read_bytes(): errors.append(f"stale skill mirror: {mirror.relative_to(ROOT)}")
    docs = markdown_files()
    errors.extend(check_links(docs))
    errors.extend(check_configs())
    errors.extend(check_syntax())
    if errors:
        for item in errors: print("[FAIL] " + item)
        print(f"Agent asset validation failed: {len(errors)} issue(s).")
        return 1
    print(f"[OK] {len(docs)} agent Markdown files; shared skills mirrored; links, configs and script syntax valid.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
