"""Shell command policy: block destructive or policy-violating commands.

This is a policy guard, not a shell interpreter. It normalizes quoting (so `g"it" push`
is recognized), splits on shell separators *and newlines*, and fails closed when shell
expansion could hide a protected operation. Hooks are defense-in-depth, not OS isolation.
"""
from __future__ import annotations

import posixpath
import re
import shlex

# Raw-text rules. `[^|;&\n]*` keeps each rule inside one command so that a later line of a
# multi-line command (or heredoc) cannot complete an earlier line's match.
RULES: list[tuple[str, str, str]] = [
    (r"\bgit\s+reset\s+--hard\b", "reset-hard",
     "git reset --hard discards work. Use git stash or ask the user."),
    (r"\bgit\s+clean\b[^|;&\n]*\s-[a-zA-Z]*f", "git-clean",
     "git clean -f deletes untracked files. Ask the user first."),
    (r"\bgit\s+(filter-branch|filter-repo)\b", "history-rewrite",
     "Rewriting repository history is not allowed from the agent."),
    (r"\bgit\s+reflog\s+expire\b", "reflog-expire",
     "Expiring the reflog removes the last recovery path for lost commits."),
    (r"\bdocker\s+compose\b[^|;&\n]*\bdown\b[^|;&\n]*(\s-v\b|--volumes\b)", "compose-volumes",
     "docker compose down -v deletes Postgres/Redis/media volumes."),
    (r"\bdocker\s+volume\s+(rm|prune)\b", "docker-volume",
     "Removing Docker volumes can destroy project data."),
    (r"\bdocker\s+(system|image|container|builder|network)\s+prune\b", "docker-prune",
     "Docker prune commands delete shared local state. Ask the user first."),
    (r"(curl|wget)\b[^|;&\n]*\|\s*(sudo\s+)?(ba|z)?sh\b", "pipe-to-shell",
     "Piping a download into a shell is not allowed."),
    (r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b", "drop-sql",
     "Destructive SQL is not allowed from the agent. Write an Alembic migration instead."),
    (r"\bTRUNCATE\s+TABLE\b", "truncate-sql", "TRUNCATE is not allowed from the agent."),
    (r"\bsudo\b", "sudo", "sudo is not available to the agent."),
    (r"\bchmod\s+-R\s+777\b", "chmod-777", "chmod -R 777 is not allowed."),
    (r"\bfind\b[^|;&\n]*\s-delete\b", "find-delete",
     "find -delete is a recursive delete. Delete specific files instead."),
    (r"/proc/[^\s/]+/environ\b", "env-dump",
     "Reading process environments can expose secrets."),
]

SECRET_PATH = re.compile(r"""(?<![A-Za-z0-9_.-])\.env(?!\.example(?:$|[\s/'";|&)]))[\w.*?{}-]*(?=$|[\s/'";|&)])""", re.IGNORECASE)
_SEPARATOR_CHARS = {";", "&", "|", "(", ")", "\n"}
_SHELL_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "\n"}
_GLOB_OR_EXPANSION = re.compile(r"""[*?\[\]{}$\x60]""")
_DYNAMIC_SHELL_SYNTAX = re.compile(r"[$\x60]")
_SHELL_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_NAMES = {"bash", "sh", "dash", "zsh", "ksh"}
_WRAPPER_COMMANDS = {"env", "command", "exec", "nohup", "time"}
_SQL_CLIENT_NAMES = {"psql", "mysql", "mariadb", "sqlite3", "sqlcmd"}
_PROTECTED_BRANCHES = {"main", "master"}


def _lex(command: str) -> list[str]:
    """Tokenize like a POSIX shell word splitter; newlines are command separators.

    Comments are not stripped: shlex would swallow the newline after `#` and merge two
    commands into one segment, which hides the second command from token-based rules.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


_QUOTED_HEREDOC = re.compile(r"<<(-?)\s*(['\"])([A-Za-z_][A-Za-z0-9_]*)\2")
_PIPE_TO_SHELL = re.compile(r"\|\s*(?:\S*/)?(?:ba|z|da|k)?sh\b")


# Consumers that only store or print a heredoc body; it never executes anything.
_DATA_SINKS = {"cat", "tee", "git", "gh", "wc", "head", "tail", "sort"}


def _heredoc_consumer(line: str, start: int, end: int) -> str:
    """Classify a quoted heredoc's consumer as "shell", "data" or "interpreter"."""
    if _PIPE_TO_SHELL.search(line[end:]):
        return "shell"
    head = re.split(r"[;&|(]", line[:start])[-1]
    try:
        tokens = shlex.split(head, posix=True)
    except ValueError:
        return "shell"
    index = _effective_command_index(tokens, _command_start(tokens))
    if index >= len(tokens):
        return "shell"
    name = posixpath.basename(tokens[index]).lower()
    if name in _SHELL_NAMES or name in {"busybox", "eval", "source", ".", "xargs"}:
        return "shell"
    if name in _DATA_SINKS and not re.search(r"\|\s*\S", line[end:]):
        return "data"
    return "interpreter"


def strip_data_heredocs(command: str, *, keep_interpreter_bodies: bool = False) -> str:
    """Drop bodies of quoted heredocs that are not shell code.

    A quoted delimiter disables expansion, so the body is shell code only when it is fed to a
    shell; those bodies (and unquoted heredocs) are always kept. With
    `keep_interpreter_bodies=True` only pure data bodies (`git commit -F -`, `cat > f`) are
    dropped, so raw-text rules still see code passed to `python3 -`, `node` or SQL clients.
    """
    lines = command.split("\n")
    kept: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        kept.append(line)
        index += 1
        match = _QUOTED_HEREDOC.search(line)
        if not match:
            continue
        consumer = _heredoc_consumer(line, match.start(), match.end())
        if consumer == "shell" or (consumer == "interpreter" and keep_interpreter_bodies):
            continue
        strip_tabs, delimiter = match.group(1) == "-", match.group(3)
        for end in range(index, len(lines)):
            candidate = lines[end].lstrip("\t") if strip_tabs else lines[end]
            if candidate == delimiter:
                kept.append(lines[end])
                index = end + 1
                break
        # No terminator: keep the remaining text for analysis (fail closed).
    return "\n".join(kept)


def _is_separator(token: str) -> bool:
    return token in _SHELL_SEPARATORS or bool(token) and set(token) <= _SEPARATOR_CHARS


def _segment_tokens(command: str) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in _lex(command):
        if _is_separator(token):
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _shell_command_segments(command: str) -> list[str]:
    """Return conservative token-normalized segments for rule matching.

    The raw command is still checked as well (important for pipes and secrets), but
    shlex normalization catches harmless-looking quote concatenation such as
    `g"it" push origin main`, which the shell executes as `git push origin main`.
    This is deliberately not a shell evaluator and never expands variables/substitutions.
    """
    return [" ".join(tokens) for tokens in _segment_tokens(command)]


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
        elif wrapper == "nohup":
            if index < len(tokens) and tokens[index] == "--":
                index += 1
        elif wrapper == "time":
            while index < len(tokens) and tokens[index] == "-p":
                index += 1
            if index < len(tokens) and tokens[index] == "--":
                index += 1

        # A dynamic wrapped executable is rejected by the caller on the next pass.
    return index


def dynamic_shell_expansion_risk(
    segments: list[str], raw_command: str | None = None
) -> str | None:
    """Fail closed when expansion can hide a protected command or destructive input.

    It permits ordinary argument expansion (for example printf "$HOME") but rejects
    dynamic executable names, dynamic git verbs/flags, destructive Docker arguments,
    recursive rm arguments, opaque shell -c/eval, and dynamically supplied SQL.
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
        if _DYNAMIC_SHELL_SYNTAX.search(command_token):
            return "command executable uses shell expansion"

        if re.search(r"\beval\b", relevant_text, re.IGNORECASE):
            return "eval can execute opaque shell command text"

        shell_index: int | None = command_index if command_name in _SHELL_NAMES else None
        if command_name == "busybox" and args:
            if posixpath.basename(args[0]).lower() in _SHELL_NAMES:
                shell_index = command_index + 1
        if shell_index is not None:
            if _has_short_shell_c_option(tokens[shell_index + 1 :]):
                return "shell -c uses dynamically expanded command text"

        dynamic_args = any(_DYNAMIC_SHELL_SYNTAX.search(token) for token in args)

        if command_name == "git" and dynamic_args:
            if args and _DYNAMIC_SHELL_SYNTAX.search(args[0]):
                return "git subcommand uses shell expansion"
            protected = {"push", "reset", "clean", "checkout", "restore", "branch", "stash"}
            if any(token.lower() in protected for token in args):
                return "shell expansion can hide a protected git option or target"

        if command_name == "docker" and dynamic_args:
            if not args or _DYNAMIC_SHELL_SYNTAX.search(args[0]):
                return "Docker subcommand uses shell expansion"
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

        if command_name == "rm" and dynamic_args:
            return "rm arguments use shell expansion"

        if command_name in _SQL_CLIENT_NAMES and dynamic_args:
            return "SQL client arguments use shell expansion"

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


def _git_args(tokens: list[str]) -> list[str] | None:
    """Return git arguments after global options, or None when the segment is not git."""
    index = _effective_command_index(tokens, _command_start(tokens))
    if index >= len(tokens) or posixpath.basename(tokens[index]).lower() != "git":
        return None
    index += 1
    while index < len(tokens) and tokens[index].startswith("-"):
        index += 2 if tokens[index] in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"} else 1
    return tokens[index:]


def _push_violation(args: list[str]) -> tuple[str, str] | None:
    targets: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token in {"-f", "--force"} or token.startswith(("--force-with-lease", "--force-if-includes")):
            return "force-push", "Force-push is not allowed. Push a feature branch normally."
        if token in {"-d", "--delete", "--prune"}:
            return "push-delete", "Deleting remote branches is not allowed from the agent."
        if token in {"--mirror", "--all"}:
            return "push-main", "Pushing every ref includes main/master. Push one feature branch."
        if token in {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}:
            index += 2
            continue
        if token.startswith("-") and not token.startswith("--") and "f" in token[1:]:
            return "force-push", "Force-push is not allowed. Push a feature branch normally."
        if not token.startswith("-"):
            targets.append(token)
        index += 1

    for refspec in targets[1:]:  # targets[0] is the remote
        if refspec.startswith("+"):
            return "force-push", "A '+' refspec force-pushes. Push a feature branch normally."
        if refspec.startswith(":"):
            return "push-delete", "Deleting remote branches is not allowed from the agent."
        destination = refspec.split(":")[-1].removeprefix("refs/heads/")
        if destination.lower() in _PROTECTED_BRANCHES:
            return "push-main", "Do not push to main/master. Work on feat/*, fix/* or refactor/* and open a PR."
    return None


def _git_violation(args: list[str]) -> tuple[str, str] | None:
    if not args:
        return None
    verb, rest = args[0].lower(), args[1:]
    if verb == "push":
        return _push_violation(rest)
    if verb == "checkout" and ("--" in rest or "." in rest or {"-f", "--force"} & set(rest)):
        return "checkout-discard", "git checkout of paths/--force discards working-tree changes. Ask the user first."
    if verb == "restore":
        staged_only = ("--staged" in rest or "-S" in rest) and not ({"--worktree", "-W"} & set(rest))
        if not staged_only:
            return "restore-discard", "git restore discards working-tree changes. Use --staged to unstage, or ask the user."
    if verb == "branch" and ("-D" in rest or ({"-d", "--delete"} & set(rest) and {"-f", "--force"} & set(rest))):
        return "branch-force-delete", "Force-deleting a branch can lose unmerged commits. Ask the user first."
    if verb == "stash" and rest and rest[0] in {"drop", "clear"}:
        return "stash-drop", "Dropping stashes discards saved work. Ask the user first."
    return None


def _segment_violation(tokens: list[str]) -> tuple[str, str] | None:
    git_args = _git_args(tokens)
    if git_args is not None:
        return _git_violation(git_args)

    start = _command_start(tokens)
    if start < len(tokens):
        name = posixpath.basename(tokens[start]).lower()
        # `env VAR=1 cmd` is a launcher; `env` without a child command dumps the environment.
        bare_env = name == "env" and _effective_command_index(tokens, start) >= len(tokens)
        if name == "printenv" or bare_env:
            return "env-dump", "Dumping the environment can expose secrets. Print one named, non-secret variable."

    lowered = [token.lower() for token in tokens]
    for position, token in enumerate(lowered):
        if posixpath.basename(token) != "alembic":
            continue
        cursor = position + 1
        while cursor < len(lowered) and lowered[cursor].startswith("-"):
            cursor += 2 if lowered[cursor] in {"-c", "--config", "-n", "--name", "-x"} else 1
        verb = lowered[cursor] if cursor < len(lowered) else ""
        if verb in {"upgrade", "downgrade", "stamp"}:
            offline = "--sql" in lowered
            throwaway_sqlite = any("sqlite" in item for item in lowered)
            if not (offline or throwaway_sqlite):
                return (
                    "alembic-live-db",
                    "Run Alembic only offline (--sql) or against an explicit throwaway SQLite URL, "
                    "e.g. DATABASE_URL=sqlite+aiosqlite:////tmp/muse_mig.db alembic upgrade head.",
                )
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
        tokens = _lex(command)
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
        while cursor < len(tokens) and not _is_separator(tokens[cursor]):
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


def check_command(command: object) -> str | None:
    """Return a block message for a shell command, or None when it may run."""
    if not isinstance(command, str) or not command.strip():
        return "[agent-guard:empty] Shell event has no command; refusing to bypass the guard."

    shell_text = strip_data_heredocs(command)
    try:
        token_segments = _segment_tokens(shell_text)
    except ValueError:
        # Other checks still run against the raw command. An unparseable shell string may
        # not be silently treated as normalized/safe.
        token_segments = []
        if re.search(r"\bgit\s+push\b|\bdocker\s+compose\s+down\b|\bDROP\s+(TABLE|DATABASE|SCHEMA)\b", command, re.IGNORECASE):
            return "[agent-guard:parse] Refusing an unparseable command containing a protected operation."
    normalized_segments = [" ".join(tokens) for tokens in token_segments]

    dynamic_risk = dynamic_shell_expansion_risk(normalized_segments, shell_text)
    if dynamic_risk:
        return (
            f"[agent-guard:dynamic-shell] Refusing ambiguous protected operation: {dynamic_risk}. "
            f"Use literal command arguments.\nCommand: {command}"
        )

    candidates = [strip_data_heredocs(command, keep_interpreter_bodies=True), *normalized_segments]
    for pattern, rule, message in RULES:
        if any(re.search(pattern, candidate, re.IGNORECASE) for candidate in candidates):
            return f"[agent-guard:{rule}] {message}\nCommand: {command}"

    for tokens in token_segments:
        violation = _segment_violation(tokens)
        if violation:
            rule, message = violation
            return f"[agent-guard:{rule}] {message}\nCommand: {command}"

    bad = rm_is_dangerous(shell_text)
    if bad:
        return f"[agent-guard:rm] Refusing recursive delete of '{bad}'. Delete specific files inside the project instead."

    # Inspect normalized tokens as well as the raw command. Shell quote concatenation
    # such as `cat .e"nv"` executes against `.env`, but a raw-only regex misses it.
    if any(SECRET_PATH.search(candidate) for candidate in candidates):
        return "[agent-guard:secrets] Do not access real .env files from the shell. Use .env.example for variable names/placeholders."
    return None
