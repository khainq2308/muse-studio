#!/usr/bin/env python3
"""PreToolUse(Bash): block destructive or policy-violating shell commands."""
from __future__ import annotations

import posixpath
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

SECRET_PATH = re.compile(r"""(?<![A-Za-z0-9_.-])\.env(?!\.example(?:$|[\s/'";|&)]))[\w.*?{}-]*(?=$|[\s/'";|&)])""", re.IGNORECASE)
_SHELL_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")"}
_GLOB_OR_EXPANSION = re.compile(r"""[*?\[\]{}$\x60]""")


def _shell_command_segments(command: str) -> list[str]:
    """Return conservative token-normalized segments for rule matching.

    The raw command is still checked as well (important for pipes and secrets), but
    shlex normalization catches harmless-looking quote concatenation such as
    `g"it" push origin main`, which the shell executes as `git push origin main`.
    This is deliberately not a shell evaluator and never expands variables/substitutions.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    tokens = list(lexer)
    segments: list[str] = []
    current: list[str] = []
    for token in tokens:
        if token in _SHELL_SEPARATORS or (token and set(token) <= {";", "&", "|", "(", ")"}):
            if current:
                segments.append(" ".join(current))
                current = []
            continue
        current.append(token)
    if current:
        segments.append(" ".join(current))
    return segments


_DYNAMIC_SHELL_SYNTAX = re.compile(r"[$\x60]")

def dynamic_shell_expansion_risk(segments: list[str]) -> str | None:
    """Reject opaque shell expansion when it can alter a protected operation.

    Dynamic arguments on sensitive commands are rejected conservatively: this guard
    does not evaluate shell variables or substitutions, so it cannot prove that an
    expanded destination, option, or subcommand is safe.
    """
    for segment in segments:
        if not _DYNAMIC_SHELL_SYNTAX.search(segment):
            continue

        lowered = segment.lower()
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            tokens = []

        # These explicitly evaluate command strings rather than exposing a command
        # for static policy checks. Dynamic command text cannot be proven safe here.
        if re.search(r"\beval\b", segment, re.IGNORECASE):
            return "eval can execute opaque shell command text"
        if re.search(r"\b(?:bash|sh|dash|zsh)\b[^;&|]*\s-c(?:\s|$)", segment, re.IGNORECASE):
            return "shell -c uses dynamically expanded command text"

        # Reject every dynamically expanded git push, not just commands where a
        # literal 'main' or '--force' happens to remain visible. The destination or
        # option may be supplied entirely by a variable (e.g. "$TARGET" / "$FLAGS").
        if re.search(r"\bgit\s+push\b", segment, re.IGNORECASE):
            return "git push uses shell expansion"

        # Also catch a hidden executable such as '$cmd push ...' or 'g${IFS}it push'.
        # If the first word is dynamically constructed, the verb alone is enough to
        # make a sensitive git operation ambiguous.
        if tokens and _DYNAMIC_SHELL_SYNTAX.search(tokens[0]):
            if any(token.lower() in {"push", "reset", "clean"} for token in tokens[1:]):
                return "dynamic executable can invoke a protected git operation"

        # A variable or command substitution can hide a destructive flag even when
        # the command name remains literal.
        if re.search(r"\breset\b", lowered):
            return "shell expansion can hide a destructive git reset"
        if re.search(r"\bclean\b", lowered):
            return "shell expansion can hide a destructive git clean"

        # Docker subcommands/options are likewise ambiguous when expanded; reject
        # dynamic docker-compose commands instead of trying to infer the expansion.
        if re.search(r"\bdocker\s+compose\b", lowered):
            return "shell expansion can hide a destructive docker compose operation"
        if re.search(r"\bdocker\s+volume\b", lowered):
            return "shell expansion can hide a destructive docker volume operation"

        if re.search(r"\b(drop\s+(?:table|database|schema)|truncate\s+table)\b", segment, re.IGNORECASE):
            return "shell expansion appears in destructive SQL"

        # A dynamic executable combined with recursive-delete flags or a broad target
        # cannot be classified safely (e.g. a variable command followed by -rf /).
        if tokens and _DYNAMIC_SHELL_SYNTAX.search(tokens[0]):
            if _recursive_flags(tokens[1:]) or any(
                token in {"/", "~", "..", "./", "../"} for token in tokens[1:]
            ):
                return "dynamic shell command with recursive-delete flags or a broad target"

    return None

def _recursive_flags(tokens: list[str]) -> bool:
    options = True
    for token in tokens:
        if options and token == "--":
            options = False
            continue
        if not options:
            continue
        if token == "--recursive":
            return True
        if token.startswith("-") and token != "-" and not token.startswith("--"):
            if "r" in token[1:] or "R" in token[1:]:
                return True
    return False


def _unsafe_rm_target(target: str) -> bool:
    """Conservatively reject targets whose expansion or normalization is ambiguous."""
    if not target or _GLOB_OR_EXPANSION.search(target):
        return True
    if ".." in target.split("/"):
        return True
    if posixpath.normpath(target) in {"", ".", ".."}:
        return True
    if target in {"/", "~", ".", "..", "./", "../"} or target.startswith("~"):
        return True

    # Explicit descendants of OS temp dirs are exempt only after traversal/expansion checks.
    if target.startswith("/tmp/") or target.startswith("/var/tmp/"):
        normalized = posixpath.normpath(target)
        return not (normalized.startswith("/tmp/") or normalized.startswith("/var/tmp/"))
    if target.startswith("/"):
        return True

    normalized_parts = [part for part in posixpath.normpath(target).split("/") if part not in {"", "."}]
    return any(part in {"storage", ".git", "media"} for part in normalized_parts)


def rm_is_dangerous(command: str) -> str | None:
    """Return a reason to block recursive rm, failing closed on ambiguous parsing."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        if re.search(r"\brm\b[^;\n]*(?:--recursive|(?:^|\s)-[^\s]*[rR])", command):
            return "<unparseable recursive rm command>"
        return None

    index = 0
    while index < len(tokens):
        if posixpath.basename(tokens[index]) != "rm":
            index += 1
            continue

        segment: list[str] = []
        cursor = index + 1
        while cursor < len(tokens) and tokens[cursor] not in _SHELL_SEPARATORS:
            segment.append(tokens[cursor])
            cursor += 1

        if _recursive_flags(segment):
            options = True
            targets: list[str] = []
            for token in segment:
                if options and token == "--":
                    options = False
                elif options and token.startswith("-") and token != "-":
                    continue
                else:
                    targets.append(token)
            for target in targets:
                if _unsafe_rm_target(target):
                    return target or "<empty recursive rm target>"
        index = max(index + 1, cursor)
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

    try:
        normalized_segments = _shell_command_segments(command)
    except ValueError:
        # Other guard checks still run against the raw command. An unparseable shell
        # string may not be silently treated as normalized/safe.
        normalized_segments = []
        if re.search(r"\bgit\s+push\b|\bdocker\s+compose\s+down\b|\bDROP\s+(TABLE|DATABASE|SCHEMA)\b", command, re.IGNORECASE):
            block("[guard_bash:parse] Refusing an unparseable command containing a protected operation.")

    dynamic_risk = dynamic_shell_expansion_risk(normalized_segments)
    if dynamic_risk:
        block(
            f"[guard_bash:dynamic-shell] Refusing ambiguous protected operation: {dynamic_risk}. "
            f"Use literal command arguments.\nCommand: {command}"
        )

    candidates = [command, *normalized_segments]
    for pattern, rule, message in RULES:
        if any(re.search(pattern, candidate, re.IGNORECASE) for candidate in candidates):
            block(f"[guard_bash:{rule}] {message}\nCommand: {command}")

    bad = rm_is_dangerous(command)
    if bad:
        block(f"[guard_bash:rm] Refusing recursive delete of '{bad}'. Delete specific files inside the project instead.")

    if SECRET_PATH.search(command):
        block("[guard_bash:secrets] Do not access real .env files from the shell. Use .env.example for variable names/placeholders.")


if __name__ == "__main__":
    main()
