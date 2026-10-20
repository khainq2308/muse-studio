from __future__ import annotations

import importlib.util
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecursiveDeleteGuardRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard = load_module("test_guard_bash_regressions", HOOKS / "guard_bash.py")

    def test_recursive_delete_rejects_globs_traversal_and_expansion(self):
        commands = [
            "rm -rf apps/../*",
            "rm -rf ././",
            "rm -rf /tmp/../*",
            "rm -rf apps/**",
            "rm -rf $HOME/project",
            "rm -rf $(pwd)",
            "rm -rf ~/project",
            "/bin/rm -rf project/{cache,storage}",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNotNone(self.guard.rm_is_dangerous(command), command)

    def test_recursive_delete_allows_specific_relative_target_and_explicit_tmp_child(self):
        self.assertIsNone(self.guard.rm_is_dangerous("rm -rf build/cache"))
        self.assertIsNone(self.guard.rm_is_dangerous("rm -rf /tmp/musestudio-cache"))

    def test_unparseable_recursive_delete_fails_closed(self):
        self.assertIsNotNone(self.guard.rm_is_dangerous("rm -rf 'unterminated"))


class DynamicShellExpansionGuardRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard = load_module(
            "test_dynamic_shell_expansion_guard",
            HOOKS / "guard_bash.py",
        )

    def risk(self, command: str):
        return self.guard.dynamic_shell_expansion_risk(
            self.guard._shell_command_segments(command)
        )

    def test_rejects_expansion_that_can_hide_protected_git_push(self):
        commands = [
            'git push origin "$(printf main)"',
            'git push origin "$BRANCH"',
            "g${IFS}it push origin main",
            "cmd=git; $cmd push origin main",
            'cmd=git; $cmd push origin "$TARGET"',
            'g${IFS}it push origin fix/$BRANCH',
            'git push origin feature `echo --force`',
            'git reset "$OPTIONS"',
            'git clean "$OPTIONS"',
            'docker compose "$ACTION" "$OPTIONS"',
            'eval "$COMMAND"',
            'bash -c "$COMMAND"',
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertIsNotNone(self.risk(command), command)

    def test_allows_literal_feature_push_and_unrelated_expansion(self):
        self.assertIsNone(self.risk("git push origin fix/shell-guard"))
        self.assertIsNone(self.risk('printf "%s" "$HOME"'))


class LayeringCheckerRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load_module("test_check_layering_regressions", ROOT / "scripts" / "check_layering.py")

    def count(self, source: str) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "route.py"
            path.write_text(source, encoding="utf-8")
            return self.checker.count_violations(path)

    def test_detects_orm_query_method(self):
        self.assertEqual(self.count("db.query(Project).filter(Project.id == project_id).first()\n"), 1)

    def test_detects_nested_session_execute(self):
        self.assertEqual(self.count("db.session.execute(select(Project))\n"), 2)

    def test_detects_session_query_but_not_unrelated_client_calls(self):
        source = "session.query(Project)\nhttp_client.get(url)\nrepository.execute(command)\n"
        self.assertEqual(self.count(source), 1)

    def test_detects_database_session_access_after_variable_rename(self):
        source = (
            "from sqlalchemy.ext.asyncio import AsyncSession as DatabaseSession\n"
            "async def endpoint(connection: DatabaseSession):\n"
            "    await connection.commit()\n"
        )
        self.assertEqual(self.count(source), 1)

    def test_detects_database_session_access_through_alias(self):
        source = (
            "async def endpoint(database: AsyncSession):\n"
            "    storage = database\n"
            "    await storage.refresh(entity)\n"
        )
        self.assertEqual(self.count(source), 1)

    def test_detects_untyped_session_parameter_injected_by_get_db(self):
        source = (
            "async def endpoint(unit=Depends(get_db)):\n"
            "    await unit.rollback()\n"
        )
        self.assertEqual(self.count(source), 1)


class StopGateBaselineRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gate = load_module("test_stop_gate_baseline_regressions", HOOKS / "stop_gate.py")

    def test_fallback_checks_full_branch_diff_from_local_main_merge_base(self):
        def runner(command, cwd, timeout):
            if command == ["git", "status", "--porcelain"]:
                return 0, ""
            if command == ["git", "diff", "--name-only", "origin/main...HEAD"]:
                return 128, "origin/main unavailable"
            if command == ["git", "merge-base", "origin/main", "HEAD"]:
                return 128, "not found"
            if command == ["git", "merge-base", "main", "HEAD"]:
                return 0, "merge-base-sha\n"
            if command == ["git", "diff", "--name-only", "merge-base-sha..HEAD"]:
                return 0, "apps/orchestrator/older_change.py\ndocs/readme.md\n"
            if command in (
                ["git", "diff", "--cached", "--name-only"],
                ["git", "diff", "--name-only"],
                ["git", "ls-files", "--others", "--exclude-standard"],
            ):
                return 0, ""
            return 2, f"unexpected command: {command}"

        changed = self.gate.collect_changed_paths(Path("/tmp/repo"), runner=runner)
        self.assertIn("apps/orchestrator/older_change.py", changed)

    def test_unavailable_baseline_fails_closed_instead_of_checking_only_tip_commit(self):
        def runner(command, cwd, timeout):
            if command == ["git", "status", "--porcelain"]:
                return 0, ""
            if command == ["git", "diff", "--name-only", "origin/main...HEAD"]:
                return 128, "no remote"
            if command[:2] == ["git", "merge-base"]:
                return 128, "baseline unavailable"
            return 0, ""

        with self.assertRaisesRegex(RuntimeError, "No reliable branch baseline"):
            self.gate.collect_changed_paths(Path("/tmp/repo"), runner=runner)


class CodexSandboxContractTests(unittest.TestCase):
    def test_review_profiles_declare_read_only_and_warn_about_runtime_enforcement(self):
        for path in (
            ROOT / ".codex" / "agents" / "code-reviewer.toml",
            ROOT / ".codex" / "agents" / "security-auditor.toml",
        ):
            with self.subTest(path=path.name):
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data.get("sandbox_mode"), "read-only")
                self.assertIn("not proof", data.get("developer_instructions", ""))

    def test_runtime_validation_doc_has_effective_write_canary(self):
        doc = (ROOT / "docs" / "CODEX_SANDBOX_VALIDATION.md").read_text(encoding="utf-8")
        self.assertIn("canary", doc.lower())
        self.assertIn("disposable", doc.lower())
        self.assertIn("file does not exist", doc.lower())


if __name__ == "__main__":
    unittest.main()
