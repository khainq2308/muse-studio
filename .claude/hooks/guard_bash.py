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
_SHELL_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_NAMES = {"bash", "sh", "dash", "zsh", "ksh"}
_WRAPPER_COMMANDS = {"env", "command", "exec", "nohup", "time"}
_SQL_CLIENT_NAMES = {"psql", "mysql", "mariadb", "sqlite3", "sqlcmd"}


def _has_short_shell_c_option(tokens: list[str]) -> bool:
    """Recognize shell -c even when combined with other short options (e.g. -lc/-ec)."""
    return any(
        token.startswith("-") and not token.startswith("--") and "c" in token[1:]
        for token in tokens
    )


def _command_start(tokens: list[str]) -> int:
    """Skip leading shell assignment words before inspecting the executable."""
    index = 0
    while index < len(tokens) and _SHELL_ASSIGNMENT.match(tokens[index]):
        index += 1
    return index


def _effective_command_index(tokens: list[str], index: int) -> int:
    """Unwrap common command launchers so their child executable is checked too."""
    while index < len(tokens):
        wrapper = posixpath.basename(tokens[index]).lower()
        if wrapper not in _WRAPPER_COMMANDS:
            return index
        index += 1

        if wrapper == "env":
            while index < len(tokens):
                token = tokens[index]
                if token == "--":
                    index += 1
                    break
                if _SHELL_ASSIGNMENT.match(token):
                    index += 1
                    continue
                if token in {"-u", "--unset", "-C", "--chdir"}:
                    index += 2
                    continue
                if token.startswith(("--unset=", "--chdir=")):
                    index += 1
                    continue
                if token.startswith("-") and token != "--":
                    index += 1
                    continue
                break
        elif wrapper == "command":
            while index < len(tokens):
                if tokens[index] == "-p":
                    index += 1
                elif tokens[index] == "--":
                    index += 1
                    break
                else:
                    break
        elif wrapper == "exec":
            while index < len(tokens):
                token = tokens[index]
                if token in {"-a", "--argv0"}:
                    index += 2
                elif token in {"-c", "-l"}:
                    index += 1
                elif token == "--":
                    index += 1
                    break
                else:
                    break
        elif wrapper in {"nohup", "time"}:
            if index < len(tokens) and tokens[index] == "--":
                index += 1

        # A dynamic wrapped executable is rejected by the caller on the next pass.
    return index


def dynamic_shell_expansion_risk(
    segments: list[str], raw_command: str | None = None
) -> str | None:
    """Fail closed when expansion can hide a protected command or destructive input.

    This is a policy guard, not a shell interpreter. It permits ordinary argument
    expansion (for example printf "$HOME") but rejects dynamic executable names,
    dynamic git verbs/flags, destructive Docker arguments, recursive rm arguments,
    opaque shell -c/eval, and dynamically supplied SQL.
    """
    for segment in segments:
        if not _DYNAMIC_SHELL_SYNTAX.search(segment):
            continue

        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            return "unparseable shell text contains expansion"

        command_index = _command_start(tokens)
        if command_index >= len(tokens):
            # A leading variable assignment by itself is not an executable command.
            continue
        command_index = _effective_command_index(tokens, command_index)
        if command_index >= len(tokens):
            continue

        command_token = tokens[command_index]
        command_name = posixpath.basename(command_token).lower()
        args = tokens[command_index + 1 :]
        relevant_text = " ".join(tokens[command_index:])

        # A variable-constructed executable cannot be classified statically.
        # This includes concatenations with IFS expansion in the command word.
        if _DYNAMIC_SHELL_SYNTAX.search(command_token):
            return "command executable uses shell expansion"

        # Explicit evaluators must not receive opaque dynamically expanded text.
        if re.search(r"\beval\b", relevant_text, re.IGNORECASE):
            return "eval can execute opaque shell command text"

        # Recognize shell -c in combined options (-lc, -ec, -ce), including paths
        # and common launchers such as env and busybox.
        shell_index: int | None = command_index if command_name in _SHELL_NAMES else None
        if command_name == "busybox" and args:
            if posixpath.basename(args[0]).lower() in _SHELL_NAMES:
                shell_index = command_index + 1
        if shell_index is not None:
            if _has_short_shell_c_option(tokens[shell_index + 1 :]):
                return "shell -c uses dynamically expanded command text"

        dynamic_args = any(_DYNAMIC_SHELL_SYNTAX.search(token) for token in args)

        if command_name == "git" and dynamic_args:
            # The first argument is the subcommand. Expansion there can turn an
            # apparently harmless invocation into push/reset/clean.
            if args and _DYNAMIC_SHELL_SYNTAX.search(args[0]):
                return "git subcommand uses shell expansion"
            if any(token.lower() in {"push", "reset", "clean"} for token in args):
                return "shell expansion can hide a protected git option or target"

        if command_name == "docker" and dynamic_args:
            if not args or _DYNAMIC_SHELL_SYNTAX.search(args[0]):
                return "Docker subcommand uses shell expansion"
            # Global Docker flags can precede the subcommand; refuse dynamic
            # invocations in this form rather than mistaking a flag for the verb.
            if args[0].startswith("-"):
                return "Docker global options use shell expansion"
            first = args[0].lower()
            second = args[1].lower() if len(args) > 1 else ""
            destructive = (
                first in {"compose", "volume", "rm", "rmi", "prune", "system"}
                or (first in {"image", "container"} and second in {"rm", "prune"})
            )
            if destructive:
                return "shell expansion can hide a destructive Docker operation"

        # rm option parsing is sensitive to IFS splitting and globbing. The tokenizer
        # no longer retains quoting, so dynamic args cannot be proven to be safe.
        if command_name == "rm" and dynamic_args:
            return "rm arguments use shell expansion"

        # Do not allow a dynamically supplied SQL statement to reach common SQL CLIs.
        if command_name in _SQL_CLIENT_NAMES and dynamic_args:
            return "SQL client arguments use shell expansion"

    # Pipelines can place the expansion and SQL client in different normalized
    # segments; check the complete command for execution flags and data flow.
    complete_command = raw_command if raw_command is not None else "\n".join(segments)
    if _DYNAMIC_SHELL_SYNTAX.search(complete_command) and re.search(
        r"\b(?:psql|mysql|mariadb|sqlite3|sqlcmd)\b", complete_command, re.IGNORECASE
    ):
        sql_execution = re.search(
            r"\b(?:psql|mysql|mariadb|sqlite3|sqlcmd)\b[^;&|]*(?:--command\b|--execute\b|(?:^|\s)-[ce](?:\s|=))",
            complete_command,
            re.IGNORECASE,
        )
        sql_pipeline = re.search(
            r"\|[^;&|]*\b(?:psql|mysql|mariadb|sqlite3|sqlcmd)\b",
            complete_command,
            re.IGNORECASE,
        )
        if sql_execution or sql_pipeline:
            return "SQL client receives dynamically supplied command text"

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

    dynamic_risk = dynamic_shell_expansion_risk(normalized_segments, command)
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
