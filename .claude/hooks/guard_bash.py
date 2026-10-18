#!/usr/bin/env python3
"""PreToolUse(Bash): block destructive or policy-violating shell commands."""
from __future__ import annotations

import re
import shlex

from _common import block, read_event

RULES: list[tuple[str, str, str]] = [
    (r"\bgit\s+push\b.*(--force\b|--force-with-lease\b|\s-f\b)", "force-push",
     "Force-push is not allowed. Push a feature branch normally."),
    (r"\bgit\s+push\b[^|;&]*\b(origin\s+)?(main|master)\b", "push-main",
     "Do not push to main/master. Work on feat/*, fix/* or refactor/* and open a PR."),
    (r"\bgit\s+reset\s+--hard\b", "reset-hard",
     "git reset --hard discards work. Use git stash or ask the user."),
    (r"\bgit\s+clean\b.*-[a-zA-Z]*f", "git-clean",
     "git clean -f deletes untracked files. Ask the user first."),
    (r"\bgit\s+checkout\s+(--\s+)?\.\s*$", "checkout-dot",
     "Discarding all working-tree changes is not allowed."),
    (r"\bdocker\s+compose\s+down\b.*(-v\b|--volumes\b)", "compose-volumes",
     "docker compose down -v deletes Postgres/Redis/media volumes."),
    (r"\bdocker\s+volume\s+(rm|prune)\b", "docker-volume",
     "Removing Docker volumes can destroy project data."),
    (r"(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z)?sh\b", "pipe-to-shell",
     "Piping a download into a shell is not allowed."),
    (r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b", "drop-sql",
     "Destructive SQL is not allowed from the agent. Write an Alembic migration instead."),
    (r"\bTRUNCATE\s+TABLE\b", "truncate-sql", "TRUNCATE is not allowed from the agent."),
    (r"\bsudo\b", "sudo", "sudo is not available to the agent."),
    (r"\bchmod\s+-R\s+777\b", "chmod-777", "chmod -R 777 is not allowed."),
]

# Reading or writing secrets via the shell.
SECRET_PATH = re.compile(r"(^|[\s'\"=/])\.env(\.(?!example)[\w.-]*)?(\s|$|['\"])")


def rm_is_dangerous(command: str) -> str | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    for i, tok in enumerate(tokens):
        if tok != "rm":
            continue
        flags = [t for t in tokens[i + 1:] if t.startswith("-")]
        if not any("r" in f.lstrip("-") or "R" in f for f in flags):
            continue
        targets = [t for t in tokens[i + 1:] if not t.startswith("-")]
        for t in targets:
            if t in {"/", "~", "$HOME", ".", "..", "*", "./", "../"} or t.startswith(("/", "~")):
                if t.startswith(("/tmp/", "/var/tmp/")):
                    continue
                return t
            if t.rstrip("/").split("/")[-1] in {"storage", ".git", "media"}:
                return t
    return None


def main() -> None:
    event = read_event()
    if not event:
        block("[guard_bash] Missing or invalid hook event; refusing to run an unvalidated shell command.")
    if event.get("tool_name") != "Bash":
        return
    tool_input = event.get("tool_input")
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not isinstance(command, str) or not command.strip():
        block("[guard_bash] Bash hook event has no command; refusing to bypass the guard.")

    for pattern, rule, message in RULES:
        if re.search(pattern, command, re.IGNORECASE):
            block(f"[guard_bash:{rule}] {message}\nCommand: {command}")

    bad = rm_is_dangerous(command)
    if bad:
        block(f"[guard_bash:rm] Refusing recursive delete of '{bad}'. Delete specific files inside the project instead.")

    if SECRET_PATH.search(command) and re.search(r"\b(cat|less|more|head|tail|grep|sed|awk|cp|mv|echo|tee|source|export)\b|>", command):
        block("[guard_bash:secrets] Do not read or modify .env files from the shell. Edit .env.example for documentation; secrets belong to the user.")


if __name__ == "__main__":
    main()
