"""Validate agent docs/links, skill mirrors, platform config and script syntax."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_DOCS = ("README.md", "AGENTS.md", "CLAUDE.md", "GEMINI.md", "docs/AGENT_WORKFLOW.md")
SKILL_ROOT = ROOT / ".agents" / "skills"
CLAUDE_SKILLS = ROOT / ".claude" / "skills"
FRONT = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
INLINE_CODE = re.compile(r"(?<!\x60)\x60([^\x60\n]+)\x60(?!\x60)")
CODE_PATH = re.compile(
    r"(?<![A-Za-z0-9_])(?:\./)?(?:scripts|docs|\.agents|\.claude|\.codex|\.gemini)/[^\s\x60'\"<>]+"
)


def frontmatter(path: Path) -> str | None:
    match = FRONT.match(path.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def check_frontmatter(path: Path, expected_name: str | None = None) -> list[str]:
    errors: list[str] = []
    meta = frontmatter(path)
    if meta is None:
        return [f"{path.relative_to(ROOT)}: missing YAML front matter"]

    name = re.search(r"(?m)^name:\s*['\"]?([^\s'\"#]+)", meta)
    description = re.search(r"(?m)^description:\s*(.+)\s*$", meta)
    if not name:
        errors.append(f"{path.relative_to(ROOT)}: missing name")
    elif expected_name and name.group(1) != expected_name:
        errors.append(f"{path.relative_to(ROOT)}: name does not match file name")
    if not description or not description.group(1).strip():
        errors.append(f"{path.relative_to(ROOT)}: missing description")
    return errors


def markdown_files() -> list[Path]:
    paths = {ROOT / rel for rel in REQUIRED_DOCS if (ROOT / rel).is_file()}
    folders = (
        "docs",
        ".agents/skills",
        ".claude/skills",
        ".claude/commands",
        ".claude/agents",
        ".gemini/agents",
    )
    for relative in folders:
        folder = ROOT / relative
        if folder.is_dir():
            paths.update(path for path in folder.rglob("*.md") if path.is_file())
    return sorted(paths)


def check_links(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    for source in paths:
        text = source.read_text(encoding="utf-8")
        for raw in LINK.findall(text):
            target = raw.strip().split()[0].strip("<>")
            target = target.split("#", 1)[0].split("?", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:", "data:")):
                continue

            candidate = (source.parent / target).resolve()
            try:
                candidate.relative_to(ROOT)
            except ValueError:
                errors.append(f"{source.relative_to(ROOT)}: link escapes repository: {raw}")
                continue
            if not candidate.exists():
                errors.append(f"{source.relative_to(ROOT)}: broken local link: {raw}")
    return errors


def check_code_paths(paths: list[Path]) -> list[str]:
    """Check path-like references inside inline code, not just Markdown links."""
    errors: list[str] = []
    for source in paths:
        text = source.read_text(encoding="utf-8")
        for code in INLINE_CODE.findall(text):
            for raw_path in CODE_PATH.findall(code):
                path_ref = raw_path.removeprefix("./").rstrip(".,;:!?)")
                path_ref = path_ref.split("#", 1)[0].split("?", 1)[0]
                if not path_ref:
                    continue
                # Per-user overrides are created on demand, so their absence is expected.
                if path_ref in {".claude/settings.local.json", ".gemini/settings.local.json"}:
                    continue

                candidate = ROOT / path_ref
                if any(char in path_ref for char in "*?["):
                    # For patterns like .gemini/agents/*.md, verify the literal parent directory.
                    prefix = path_ref.split("*", 1)[0].split("?", 1)[0].split("[", 1)[0]
                    candidate = ROOT / prefix.rstrip("/")
                if not candidate.exists():
                    # Skill-local scripts and references: check relative to parent skill directory
                    for parent in (source.parent, *source.parents):
                        if parent == ROOT:
                            break
                        alt = parent / path_ref
                        if any(char in path_ref for char in "*?["):
                            prefix = path_ref.split("*", 1)[0].split("?", 1)[0].split("[", 1)[0]
                            alt = parent / prefix.rstrip("/")
                        if alt.exists():
                            candidate = alt
                            break
                try:
                    candidate.relative_to(ROOT)
                except ValueError:
                    errors.append(
                        f"{source.relative_to(ROOT)}: code path escapes repository: {raw_path}"
                    )
                    continue
                if not candidate.exists():
                    errors.append(
                        f"{source.relative_to(ROOT)}: broken inline code path reference: {raw_path}"
                    )
    return errors


def check_configs() -> list[str]:
    errors: list[str] = []
    config_values: dict[str, dict] = {}

    for relative in (".claude/settings.json", ".gemini/settings.json"):
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"missing settings file: {relative}")
            continue
        try:
            config_values[relative] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{relative}: invalid JSON: {exc}")

    claude = config_values.get(".claude/settings.json", {})
    claude_hooks = claude.get("hooks", {})

    def hook_registered(
        event_name: str,
        expected_matchers: set[str],
        script_name: str,
        platform: str,
    ) -> bool:
        groups = claude_hooks.get(event_name, [])
        for group in groups:
            matcher = group.get("matcher", "")
            matchers = {item.strip() for item in matcher.split("|") if item.strip()}
            commands = [
                hook.get("command", "")
                for hook in group.get("hooks", [])
                if isinstance(hook, dict)
            ]
            if expected_matchers.issubset(matchers) and any(
                script_name in command for command in commands
            ):
                return True
        errors.append(
            f"{platform}: {event_name} hook for {script_name} "
            f"is missing or does not match {sorted(expected_matchers)}"
        )
        return False

    hook_registered("PreToolUse", {"Bash"}, "guard_bash.py", ".claude/settings.json")
    hook_registered(
        "PreToolUse",
        {"Read", "Grep", "Glob"},
        "pre_read_guard.py",
        ".claude/settings.json",
    )
    hook_registered(
        "PreToolUse",
        {"Edit", "Write", "MultiEdit"},
        "pre_edit_guard.py",
        ".claude/settings.json",
    )
    hook_registered(
        "SessionStart",
        set(),
        "session_start.py",
        ".claude/settings.json",
    )
    hook_registered(
        "PostToolUse",
        {"Edit", "Write", "MultiEdit"},
        "post_edit.py",
        ".claude/settings.json",
    )
    hook_registered("Stop", set(), "stop_gate.py", ".claude/settings.json")

    gemini = config_values.get(".gemini/settings.json", {})
    gemini_groups = gemini.get("hooks", {}).get("BeforeTool", [])
    gemini_matchers = {
        tool.strip()
        for group in gemini_groups
        for tool in group.get("matcher", "").split("|")
        if tool.strip()
    }
    required_gemini_tools = {
        "run_shell_command",
        "read_file",
        "read_many_files",
        "grep_search",
        "glob",
        "write_file",
        "replace",
    }
    missing_tools = sorted(required_gemini_tools - gemini_matchers)
    if missing_tools:
        errors.append(
            ".gemini/settings.json: safety hook does not match "
            + ", ".join(missing_tools)
        )

    gemini_hook_command = " ".join(
        hook.get("command", "")
        for group in gemini_groups
        for hook in group.get("hooks", [])
        if isinstance(hook, dict)
    )
    if "before_tool.py" not in gemini_hook_command:
        errors.append(".gemini/settings.json: BeforeTool does not run before_tool.py")
    if not (ROOT / ".gemini" / "hooks" / "before_tool.py").is_file():
        errors.append("missing .gemini/hooks/before_tool.py")

    codex = ROOT / ".codex" / "agents"
    if not codex.is_dir():
        errors.append("missing .codex/agents/")
    else:
        for path in sorted(codex.glob("*.toml")):
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                errors.append(f"{path.relative_to(ROOT)}: invalid TOML: {exc}")
                continue

            for key in ("name", "description", "developer_instructions"):
                value = data.get(key)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{path.relative_to(ROOT)}: missing {key}")
            if path.stem in {"code-reviewer", "security-auditor"}:
                if data.get("sandbox_mode") != "read-only":
                    errors.append(
                        f"{path.relative_to(ROOT)}: reviewer/auditor must set "
                        "sandbox_mode = read-only"
                    )

    gemini_agents = ROOT / ".gemini" / "agents"
    if not gemini_agents.is_dir():
        errors.append("missing .gemini/agents/")
    else:
        for path in sorted(gemini_agents.glob("*.md")):
            errors.extend(check_frontmatter(path, path.stem))
            meta = frontmatter(path)
            if meta and not re.search(r"(?m)^kind:\s*local\s*$", meta):
                errors.append(
                    f"{path.relative_to(ROOT)}: Gemini project agent should declare kind: local"
                )

    claude_agents = ROOT / ".claude" / "agents"
    if claude_agents.is_dir():
        for path in sorted(claude_agents.glob("*.md")):
            errors.extend(check_frontmatter(path, path.stem))

    claude_commands = ROOT / ".claude" / "commands"
    if claude_commands.is_dir():
        for path in sorted(claude_commands.glob("*.md")):
            meta = frontmatter(path)
            if meta is None:
                errors.append(f"{path.relative_to(ROOT)}: missing command front matter")
            elif not re.search(r"(?m)^description:\s*.+$", meta):
                errors.append(f"{path.relative_to(ROOT)}: command front matter requires description")
    return errors


def check_syntax() -> list[str]:
    errors: list[str] = []
    python_files: set[Path] = set()
    for relative in (".claude/hooks", ".gemini/hooks", "scripts"):
        folder = ROOT / relative
        if folder.is_dir():
            python_files.update(folder.rglob("*.py"))

    for path in sorted(python_files):
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (OSError, SyntaxError) as exc:
            errors.append(f"{path.relative_to(ROOT)}: Python syntax error: {exc}")

    bash = shutil.which("bash")
    if not bash:
        errors.append("bash is required to validate shell scripts")
        return errors

    for path in sorted((ROOT / "scripts").glob("*.sh")):
        try:
            proc = subprocess.run(
                [bash, "-n", str(path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{path.relative_to(ROOT)}: bash syntax check failed: {exc}")
            continue
        if proc.returncode:
            errors.append(
                f"{path.relative_to(ROOT)}: bash -n failed: {proc.stderr.strip()}"
            )
    return errors


def main() -> int:
    errors: list[str] = []
    for relative in REQUIRED_DOCS:
        if not (ROOT / relative).is_file():
            errors.append(f"missing required document: {relative}")

    for relative in ("CLAUDE.md", "GEMINI.md"):
        path = ROOT / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "AGENTS.md" not in text or "docs/AGENT_WORKFLOW.md" not in text:
            errors.append(
                f"{relative} must reference AGENTS.md and docs/AGENT_WORKFLOW.md"
            )

    if not SKILL_ROOT.is_dir():
        errors.append("missing .agents/skills/")
    else:
        canonical_skills = sorted(SKILL_ROOT.rglob("SKILL.md"))
        if not canonical_skills:
            errors.append("no shared SKILL.md found")
        for skill in canonical_skills:
            relative = skill.relative_to(SKILL_ROOT)
            errors.extend(check_frontmatter(skill, skill.parent.name))
            mirror = CLAUDE_SKILLS / relative
            if not mirror.is_file():
                errors.append(f"missing Claude skill mirror: {mirror.relative_to(ROOT)}")
            elif mirror.read_bytes() != skill.read_bytes():
                errors.append(
                    f"stale skill mirror: {mirror.relative_to(ROOT)}; edit canonical skill then sync"
                )

    markdown = markdown_files()
    errors.extend(check_links(markdown))
    errors.extend(check_code_paths(markdown))
    errors.extend(check_configs())
    errors.extend(check_syntax())

    if errors:
        for error in errors:
            print(f"[FAIL] {error}")
        print(f"Agent asset validation failed: {len(errors)} issue(s).")
        return 1

    print(
        f"[OK] {len(markdown)} agent Markdown files; shared skills mirrored; "
        "links, configs and script syntax valid."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
