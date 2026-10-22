"""Regression tests for the shared agent_guard policy and every host adapter."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_guard import files, shell, verify  # noqa: E402
from agent_guard.session import tool_available  # noqa: E402

E = "." + "env"  # keep the literal out of commands this test file itself runs
COMMITTED_REVISION = "apps/orchestrator/migrations/versions/0001_bootstrap_existing_schema.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_adapter(script: Path, payload: object, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(ROOT)
    env["MUSE_STOP_GATE"] = "0"
    return subprocess.run(
        [sys.executable, str(script), *args],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        text=True, capture_output=True, env=env, timeout=20,
    )


class ShellPolicyTests(unittest.TestCase):
    def assertBlocked(self, command: str, rule: str):
        message = shell.check_command(command)
        self.assertIsNotNone(message, command)
        self.assertIn(rule, message, command)

    def assertAllowed(self, command: str):
        self.assertIsNone(shell.check_command(command), command)

    def test_blocks_destructive_git_operations_that_previously_passed(self):
        cases = {
            "git restore .": "restore-discard",
            "git checkout -- apps/": "checkout-discard",
            "git checkout -f feat/x": "checkout-discard",
            "git branch -D feat/old": "branch-force-delete",
            "git stash drop": "stash-drop",
            "git stash clear": "stash-drop",
            "git push --delete origin feat/x": "push-delete",
            "git push origin :feat/x": "push-delete",
            "git push origin +feat/x": "force-push",
            "git push origin HEAD:main": "push-main",
            "git push origin refs/heads/master": "push-main",
            "git -C apps push origin main": "push-main",
            "git push --all origin": "push-main",
            "git filter-branch --tree-filter x HEAD": "history-rewrite",
        }
        for command, rule in cases.items():
            with self.subTest(command=command):
                self.assertBlocked(command, rule)

    def test_blocks_other_destructive_or_leaky_commands(self):
        cases = {
            "find . -name '*.pyc' -delete": "find-delete",
            "docker system prune -af": "docker-prune",
            "docker image prune -a": "docker-prune",
            "printenv": "env-dump",
            "env": "env-dump",
            "env | grep TOKEN": "env-dump",
            "cat /proc/self/environ": "env-dump",
            "alembic upgrade head": "alembic-live-db",
            "uv run alembic downgrade -1": "alembic-live-db",
            "docker compose exec api alembic stamp head": "alembic-live-db",
        }
        for command, rule in cases.items():
            with self.subTest(command=command):
                self.assertBlocked(command, rule)

    def test_allows_safe_lookalikes(self):
        for command in (
            "git push -u origin feat/main-fix",
            "git push origin feat/x",
            "git checkout -b chore/agent-kit origin/main",
            "git checkout main",
            "git restore --staged apps/x.py",
            "git branch -d merged-branch",
            "git stash list",
            "env FOO=1 python3 -m pytest -q",
            "DATABASE_URL=sqlite+aiosqlite:////tmp/muse_mig.db alembic upgrade head",
            "DATABASE_URL='postgresql+asyncpg://u:p@localhost/m' alembic upgrade head --sql",
            "alembic history",
            "find . -name '*.py' -newer x",
        ):
            with self.subTest(command=command):
                self.assertAllowed(command)

    def test_rules_do_not_match_across_lines(self):
        # A commit message mentioning push followed by a later `main` on another line.
        self.assertAllowed("git commit -m 'fix: push retry'\ngit log main --oneline")
        self.assertAllowed("docker compose down\necho -v")

    def test_newline_separated_commands_are_each_checked(self):
        self.assertBlocked("echo safe\ngit push origin main", "push-main")
        # A comment must not swallow the newline and merge the next command into it.
        self.assertBlocked("echo safe # note\ngit push origin main", "push-main")

    def test_quoted_data_heredoc_is_not_parsed_as_shell(self):
        command = (
            "python3 - <<'PY'\n"
            "text = \"the host's `hooks` panel\"\n"
            "print('git push origin main')\n"
            "PY"
        )
        self.assertAllowed(command)
        self.assertAllowed("cat > notes.md <<'EOF'\nit's `fine` $HOME\nEOF")

    def test_heredoc_fed_to_a_shell_is_still_checked(self):
        self.assertBlocked("bash <<'EOF'\ngit push origin main\nEOF", "push-main")
        self.assertBlocked("cat <<'EOF' | sh\ngit push origin main\nEOF", "push-main")
        # Raw-text rules still see bodies executed by an interpreter.
        self.assertBlocked("python3 - <<'PY'\nimport os; os.system('git reset --hard')\nPY", "reset-hard")

    def test_commit_message_heredoc_is_pure_data(self):
        self.assertAllowed(
            "git add -A && git commit -q -F - <<'MSG'\n"
            "chore: block find -delete, git reset --hard and docker system prune\n"
            "MSG\ngit log --oneline -1"
        )
        self.assertBlocked(f"cat <<'EOF' > {E}\nA=1\nEOF", "secrets")
        self.assertBlocked("cat <<'EOF' | python3\nimport os; os.system('git reset --hard')\nEOF", "reset-hard")

    def test_secret_paths_still_blocked(self):
        self.assertBlocked(f"cat {E}", "secrets")
        self.assertAllowed(f"cat {E}.example")


class FilePolicyTests(unittest.TestCase):
    def test_committed_revision_frozen_and_generated_files_are_protected(self):
        cases = {
            COMMITTED_REVISION: "committed Alembic revision",
            "apps/orchestrator/muse_orchestrator/models/schema_baseline_v1.py": "frozen",
            "scripts/layering_baseline.json": "frozen",
            "apps/studio-web/src/lib/api/schema.d.ts": "generated",
            "apps/orchestrator/openapi.json": "generated",
            "apps/orchestrator/uv.lock": "lockfiles",
            "apps/studio-web/package-lock.json": "lockfiles",
            f"apps/orchestrator/{E}": ".env",
            "apps/orchestrator/.venv/lib/x.py": "protected location",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                message = files.check_edit(ROOT, path, "x = 1")
                self.assertIsNotNone(message, path)
                self.assertIn(expected, message)

    def test_new_revision_and_normal_source_are_editable(self):
        self.assertIsNone(files.check_edit(ROOT, "apps/orchestrator/migrations/versions/0099_new.py", "x = 1"))
        self.assertIsNone(files.check_edit(ROOT, "apps/orchestrator/muse_orchestrator/models/domain.py", "x = 1"))

    def test_outside_repository_writes_allowed_but_secrets_still_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            outside = Path(temp) / "memory" / "MEMORY.md"
            self.assertIsNone(files.check_edit(ROOT, str(outside), "- note"))
            self.assertIn(".env", files.check_edit(ROOT, str(Path(temp) / E), "A=1"))
            leaked = files.check_edit(ROOT, str(outside), "token ghp_" + "A" * 36)
            self.assertIn("GitHub token", leaked)

    def test_read_policy(self):
        self.assertIsNotNone(files.check_read(ROOT, E, require_path=True))
        self.assertIsNone(files.check_read(ROOT, f"{E}.example", require_path=True))
        self.assertIsNotNone(files.check_read(ROOT, "", require_path=True))
        self.assertIsNotNone(files.check_read(ROOT, "", ["**/" + E + "*"]))
        self.assertIsNone(files.check_read(ROOT, "apps", ["*.py"]))


class CodexAdapterTests(unittest.TestCase):
    SCRIPT = ROOT / ".codex" / "hooks" / "agent_guard_hook.py"

    def event(self, tool_name: str, command: str) -> dict:
        return {"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": tool_name,
                "tool_input": {"command": command}}

    def test_bash_verdicts(self):
        denied = run_adapter(self.SCRIPT, self.event("Bash", "git reset --hard"))
        self.assertEqual(denied.returncode, 2, denied.stderr)
        self.assertIn("reset-hard", denied.stderr)
        allowed = run_adapter(self.SCRIPT, self.event("Bash", "git status --short"))
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_apply_patch_paths_and_content_are_checked(self):
        edit_revision = f"*** Begin Patch\n*** Update File: {COMMITTED_REVISION}\n@@\n-a\n+b\n*** End Patch"
        denied = run_adapter(self.SCRIPT, self.event("apply_patch", edit_revision))
        self.assertEqual(denied.returncode, 2, denied.stderr)
        self.assertIn("committed Alembic revision", denied.stderr)

        secret = "*** Begin Patch\n*** Add File: docs/x.md\n+key ghp_" + "A" * 36 + "\n*** End Patch"
        self.assertEqual(run_adapter(self.SCRIPT, self.event("apply_patch", secret)).returncode, 2)

        ok = "*** Begin Patch\n*** Add File: docs/new-note.md\n+hello\n*** End Patch"
        self.assertEqual(run_adapter(self.SCRIPT, self.event("apply_patch", ok)).returncode, 0)

    def test_patch_targets_include_moves_and_malformed_events_fail_closed(self):
        module = load_module("codex_adapter", self.SCRIPT)
        paths, added = module.patch_targets(
            "*** Update File: a.py\n*** Move to: b.py\n+x\n*** Delete File: c.py\n"
        )
        self.assertEqual(paths, ["a.py", "b.py", "c.py"])
        self.assertEqual(added, "x")
        self.assertEqual(run_adapter(self.SCRIPT, "not json").returncode, 2)
        self.assertEqual(run_adapter(self.SCRIPT, self.event("apply_patch", "no headers")).returncode, 2)

    def test_stop_returns_json_when_gate_disabled(self):
        result = run_adapter(self.SCRIPT, {"hook_event_name": "Stop", "session_id": "s"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})


class AntigravityAdapterTests(unittest.TestCase):
    SCRIPT = ROOT / ".agents" / "hooks" / "antigravity_hook.py"

    def decide(self, name: str, args: dict) -> dict:
        result = run_adapter(self.SCRIPT, {"conversationId": "c", "toolCall": {"name": name, "args": args}}, "PreToolUse")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_tool_verdicts(self):
        self.assertEqual(self.decide("run_command", {"CommandLine": "git push --force origin feat/x"})["decision"], "deny")
        self.assertEqual(self.decide("run_command", {"CommandLine": "npm test"})["decision"], "allow")
        self.assertEqual(self.decide("write_to_file", {"TargetFile": str(ROOT / E), "CodeContent": "A=1"})["decision"], "deny")
        self.assertEqual(self.decide("replace_file_content", {"TargetFile": str(ROOT / COMMITTED_REVISION), "ReplacementContent": "x"})["decision"], "deny")
        chunks = [{"TargetContent": "a", "ReplacementContent": "ghp_" + "A" * 36}]
        self.assertEqual(self.decide("multi_replace_file_content", {"TargetFile": str(ROOT / "README.md"), "ReplacementChunks": chunks})["decision"], "deny")
        self.assertEqual(self.decide("view_file", {"AbsolutePath": str(ROOT / E)})["decision"], "deny")
        self.assertEqual(self.decide("view_file", {"AbsolutePath": str(ROOT / "README.md")})["decision"], "allow")
        self.assertEqual(self.decide("grep_search", {"SearchPath": str(ROOT), "Includes": [E + "*"]})["decision"], "deny")

    def test_invalid_payload_is_denied_and_session_start_injects_context(self):
        bad = run_adapter(self.SCRIPT, "nope", "PreToolUse")
        self.assertEqual(json.loads(bad.stdout)["decision"], "deny")
        start = run_adapter(self.SCRIPT, {"conversationId": "c"}, "SessionStart")
        steps = json.loads(start.stdout)["injectSteps"]
        self.assertIn("MuseStudio session context", steps[0]["systemMessage"])
        stop = run_adapter(self.SCRIPT, {"conversationId": "c"}, "Stop")
        self.assertEqual(json.loads(stop.stdout), {})


class SharedAdaptersAgreeTests(unittest.TestCase):
    def test_claude_and_gemini_adapters_use_the_shared_shell_policy(self):
        command = "git restore ."
        claude = run_adapter(ROOT / ".claude" / "hooks" / "guard_bash.py", {"tool_name": "Bash", "tool_input": {"command": command}})
        gemini = run_adapter(ROOT / ".gemini" / "hooks" / "before_tool.py", {"tool_name": "run_shell_command", "tool_input": {"command": command}})
        for result in (claude, gemini):
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("restore-discard", result.stderr)

    def test_claude_edit_guard_allows_writes_outside_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            result = run_adapter(
                ROOT / ".claude" / "hooks" / "pre_edit_guard.py",
                {"tool_name": "Write", "tool_input": {"file_path": str(Path(temp) / "plan.md"), "content": "plan"}},
            )
            self.assertEqual(result.returncode, 0, result.stderr)


class CompletionGateTests(unittest.TestCase):
    def make_runner(self, pytest_exit: int, calls: list[list[str]]):
        def runner(command, cwd, timeout):
            calls.append(command)
            if command[:2] == ["git", "status"]:
                return 0, ""
            if command[:3] == ["git", "diff", "--name-only"] and "origin/main...HEAD" in command:
                return 0, "apps/orchestrator/muse_orchestrator/api/projects.py\n"
            if command[:2] == ["git", "rev-parse"]:
                return 0, "abc123\n"
            if "pytest" in command and "-m" in command:
                return pytest_exit, "1 failed" if pytest_exit else "1 passed"
            return 0, ""
        return runner

    def test_pass_is_cached_for_unchanged_state(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "gate.json"
            calls: list[list[str]] = []
            self.assertIsNone(verify.completion_gate(ROOT, runner=self.make_runner(0, calls), state_path=state))
            pytest_runs = sum("pytest" in c and "-m" in c for c in calls)
            self.assertEqual(pytest_runs, 1)
            calls.clear()
            self.assertIsNone(verify.completion_gate(ROOT, runner=self.make_runner(0, calls), state_path=state))
            self.assertFalse(any("pytest" in c and "-m" in c for c in calls), "cached pass must not rerun tests")

    def test_failing_state_blocks_twice_then_releases_the_session(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "gate.json"
            results = [
                verify.completion_gate(ROOT, session_key="conv", runner=self.make_runner(1, []), state_path=state)
                for _ in range(3)
            ]
            self.assertIn("did not pass", results[0])
            self.assertIn("did not pass", results[1])
            self.assertIsNone(results[2])
            # Without a session key (Claude uses stop_hook_active instead) it keeps blocking.
            self.assertIsNotNone(verify.completion_gate(ROOT, runner=self.make_runner(1, []), state_path=state))

    def test_frontend_changes_require_typecheck(self):
        with tempfile.TemporaryDirectory() as temp:
            problems = verify.validate_changed(Path(temp), [], [], runner=lambda *_: (0, ""), web=["apps/studio-web/src/a.ts"])
            self.assertTrue(any("Frontend typecheck NOT RUN" in p for p in problems), problems)

    def test_classify_routes_changes_to_suites(self):
        backend, media, web = verify.classify({
            "apps/orchestrator/a.py", "apps/media-engine/b.py", "apps/studio-web/src/c.tsx", "docs/d.md",
        })
        self.assertEqual((backend, media, web), (["apps/orchestrator/a.py"], ["apps/media-engine/b.py"], ["apps/studio-web/src/c.tsx"]))


class AgentAssetCheckerTests(unittest.TestCase):
    def test_mirror_compare_detects_drift_outside_skill_md(self):
        checker = load_module("asset_checker", ROOT / "scripts" / "check_agent_assets.py")
        with tempfile.TemporaryDirectory() as temp:
            canonical, mirror = Path(temp) / "a" / "skill", Path(temp) / "b" / "skill"
            for base in (canonical, mirror):
                (base / "scripts").mkdir(parents=True)
                (base / "SKILL.md").write_text("same")
            (canonical / "scripts" / "run.py").write_text("print(1)")
            (mirror / "scripts" / "run.py").write_text("print(2)")
            (mirror / "scripts" / "__pycache__").mkdir()
            (mirror / "scripts" / "__pycache__" / "x.pyc").write_bytes(b"0")
            errors = checker.compare_mirror(canonical, mirror)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("stale skill mirror", errors[0])
            self.assertIn("scripts/run.py", errors[0])

    def test_antigravity_rules_need_a_valid_trigger(self):
        checker = load_module("asset_checker_rules", ROOT / "scripts" / "check_agent_assets.py")
        with tempfile.TemporaryDirectory() as temp:
            rules = Path(temp)
            (rules / "bad.md").write_text("---\ntrigger: sometimes\n---\nbody\n")
            (rules / "glob.md").write_text("---\ntrigger: glob\ndescription: x\n---\nbody\n")
            (rules / "ok.md").write_text("---\ntrigger: always_on\ndescription: x\n---\nbody\n")
            checker.ANTIGRAVITY_RULES = rules
            checker.ROOT = rules
            errors = checker.check_antigravity_rules()
            self.assertEqual(len(errors), 2, errors)


class SessionContextTests(unittest.TestCase):
    def test_venv_tools_count_as_available(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            venv_bin = root / "apps" / "orchestrator" / ".venv" / "bin"
            venv_bin.mkdir(parents=True)
            (venv_bin / "pytest").write_text("")
            self.assertTrue(tool_available(root, "pytest"))
            self.assertFalse(tool_available(root, "definitely-not-a-tool-xyz"))


if __name__ == "__main__":
    unittest.main()
