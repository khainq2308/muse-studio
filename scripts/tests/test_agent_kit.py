from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
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


class GuardTests(unittest.TestCase):
    def run_hook(self, script: Path, root: Path, payload: dict, hook_root: Path | None = None):
        env = os.environ.copy()
        adapter_root = hook_root or root
        env["CLAUDE_PROJECT_DIR"] = str(adapter_root)
        env["GEMINI_PROJECT_DIR"] = str(adapter_root)
        return subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            timeout=10,
        )

    def test_docs_sync_checks_status_vocabulary_not_feature_signals(self):
        checker = load_module("test_docs_sync_metadata", ROOT / "scripts" / "check_docs_sync.py")
        plan_lines = []
        for number in range(8):
            status = "Completed" if number < 2 else "Partial"
            plan_lines.append(f"* **M{number} ({status}):** milestone description")
        matrix = (
            "| Capability / Endpoint | Method & Path | Status | Evidence / Probe Date | Fallback |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| Health | GET /healthz | " + chr(96) + "mock-only" + chr(96) + " | No real probe | Mock test only |\n"
        )
        self.assertEqual(checker.validate_documents("\n".join(plan_lines), matrix), [])

        invalid_plan = "\n".join(plan_lines).replace("M3 (Partial)", "M3 (Almost done)")
        self.assertTrue(
            any("unknown status" in error for error in checker.validate_documents(invalid_plan, matrix))
        )
        invalid_matrix = matrix.replace("mock-only", "almost-verified")
        self.assertTrue(
            any("capability status" in error for error in checker.validate_documents("\n".join(plan_lines), invalid_matrix))
        )

    def test_verified_capability_requires_dated_existing_evidence(self):
        checker = load_module("test_docs_sync_evidence", ROOT / "scripts" / "check_docs_sync.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            checker.ROOT = root
            matrix = (
                "| Capability / Endpoint | Method & Path | Status | Evidence / Probe Date | Fallback |\n"
                "| --- | --- | --- | --- | --- |\n"
                "| Health | GET /healthz | " + chr(96) + "verified" + chr(96)
                + " | 2026-10-18 — docs/evidence/health-probe.md | Verified |\n"
            )
            errors = checker.validate_capability_matrix(matrix)
            self.assertTrue(any("artifact does not exist" in error for error in errors), errors)

            evidence = root / "docs" / "evidence" / "health-probe.md"
            evidence.parent.mkdir(parents=True)
            evidence.write_text("Instance version and dated probe response.")
            self.assertEqual(checker.validate_capability_matrix(matrix), [])

    def test_inline_code_path_checker_detects_missing_script_reference(self):
        checker = load_module("test_agent_asset_paths", ROOT / "scripts" / "check_agent_assets.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scripts").mkdir()
            (root / "scripts" / "test.sh").write_text("#!/bin/sh\nexit 0\n")
            docs = root / ".agents" / "skills" / "example"
            docs.mkdir(parents=True)
            source = docs / "SKILL.md"
            source.write_text("Run `scripts/test.sh all`; stale reference: `scripts/missing.py`.\n")
            checker.ROOT = root

            issues = checker.check_code_paths([source])
            self.assertFalse(any("scripts/test.sh" in issue for issue in issues), issues)
            self.assertTrue(any("scripts/missing.py" in issue for issue in issues), issues)

    def test_malformed_or_non_object_events_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for hook in ("pre_read_guard.py", "pre_edit_guard.py", "guard_bash.py"):
                result = self.run_hook(
                    HOOKS / hook,
                    root,
                    [],
                )
                self.assertEqual(result.returncode, 2, f"{hook}: {result.stderr}")

    def test_read_guard_blocks_real_env_and_allows_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (".env", ".env.production", ".envrc", ".env.example.local", ".ENV.production"):
                result = self.run_hook(
                    HOOKS / "pre_read_guard.py", root,
                    {"tool_name": "Read", "tool_input": {"file_path": name}},
                )
                self.assertEqual(result.returncode, 2, result.stderr)
            result = self.run_hook(
                HOOKS / "pre_read_guard.py", root,
                {"tool_name": "Read", "tool_input": {"file_path": ".env.example"}},
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_read_guard_detects_symlink_to_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = root / ".env"
            secret.write_text("TOKEN=placeholder")
            (root / "alias.conf").symlink_to(secret)
            result = self.run_hook(
                HOOKS / "pre_read_guard.py", root,
                {"tool_name": "Read", "tool_input": {"file_path": "alias.conf"}},
            )
            self.assertEqual(result.returncode, 2, result.stderr)

    def test_shell_guard_blocks_python_read_of_real_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            denied = self.run_hook(
                HOOKS / "guard_bash.py", root,
                {"tool_name": "Bash", "tool_input": {
                    "command": "python3 -c \"print(open('.env').read())\"",
                }},
            )
            self.assertEqual(denied.returncode, 2, denied.stderr)
            self.assertIn("real .env files", denied.stderr)

            # The shell concatenates adjacent quoted fragments before opening a path.
            # Ensure the guard inspects normalized tokens, not only raw command text.
            concatenated = self.run_hook(
                HOOKS / "guard_bash.py", root,
                {"tool_name": "Bash", "tool_input": {"command": 'cat .e"nv"'}},
            )
            self.assertEqual(concatenated.returncode, 2, concatenated.stderr)
            self.assertIn("real .env files", concatenated.stderr)

            for secret_name in (".envrc", ".env.example.local", ".ENVrc", ".ENV.example.local"):
                secret_command = f"python3 -c \"print(open('{secret_name}').read())\""
                result = self.run_hook(
                    HOOKS / "guard_bash.py", root,
                    {"tool_name": "Bash", "tool_input": {"command": secret_command}},
                )
                self.assertEqual(result.returncode, 2, result.stderr)

            glob_secret = self.run_hook(
                HOOKS / "guard_bash.py", root,
                {"tool_name": "Bash", "tool_input": {"command": "cat .env*"}},
            )
            self.assertEqual(glob_secret.returncode, 2, glob_secret.stderr)

            allowed = self.run_hook(
                HOOKS / "guard_bash.py", root,
                {"tool_name": "Bash", "tool_input": {
                    "command": "grep API_KEY .env.example",
                }},
            )
            self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_gemini_read_many_files_uses_include_patterns_and_blocks_secrets(self):
        adapter = load_module("test_gemini_guard", ROOT / ".gemini" / "hooks" / "before_tool.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter.ROOT = root
            adapter.HOOKS = HOOKS
            (root / ".env").write_text("API_KEY=secret")
            (root / ".envrc").write_text("API_KEY=secret")
            (root / ".env.example.local").write_text("API_KEY=secret")
            (root / ".env.example").write_text("API_KEY=replace-me")
            (root / "README.md").write_text("safe content")

            denied = adapter.check_many_file_paths({"include": [".env"]})
            self.assertEqual(denied, 2)
            denied_glob = adapter.check_many_file_paths({"include": ["**/.env*"]})
            self.assertEqual(denied_glob, 2)

            allowed_example = adapter.check_many_file_paths({"include": [".env.example"]})
            self.assertEqual(allowed_example, 0)
            allowed_readme = adapter.check_many_file_paths({"include": ["README.md"]})
            self.assertEqual(allowed_readme, 0)

    def test_gemini_broad_grep_blocks_unignored_env_variants(self):
        adapter = load_module("test_gemini_grep_scan", ROOT / ".gemini" / "hooks" / "before_tool.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True, text=True, check=False)
            (root / ".env.production").write_text("API_KEY=secret")
            (root / "README.md").write_text("safe")
            adapter.ROOT = root
            adapter.HOOKS = HOOKS

            # A broad grep could read an unignored .env.production file.
            self.assertEqual(adapter.check_grep_search_paths({"path": ".", "pattern": "API_KEY"}), 2)
            self.assertEqual(adapter.check_grep_search_paths({"path": ".", "include": "*.md", "pattern": "MuseStudio"}), 0)

            (root / ".gitignore").write_text(".env*\n!.env.example\n")
            self.assertEqual(adapter.check_grep_search_paths({"path": ".", "pattern": "API_KEY"}), 0)
            self.assertEqual(adapter.check_grep_search_paths({"path": ".", "include": ".env.production", "pattern": "API_KEY"}), 2)

    def test_gemini_grep_search_blocks_explicit_secret_path(self):
        adapter = ROOT / ".gemini" / "hooks" / "before_tool.py"
        denied = self.run_hook(
            adapter, ROOT,
            {"tool_name": "grep_search", "tool_input": {"path": ".env", "pattern": "TOKEN"}},
            hook_root=ROOT,
        )
        self.assertEqual(denied.returncode, 2, denied.stderr)

        allowed = self.run_hook(
            adapter, ROOT,
            {"tool_name": "grep_search", "tool_input": {"path": ".", "include": "*.py", "pattern": "def main"}},
            hook_root=ROOT,
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(json.loads(allowed.stdout)["decision"], "allow")

    def test_gemini_broad_read_respects_gitignored_env_files(self):
        adapter = load_module("test_gemini_guard_ignore", ROOT / ".gemini" / "hooks" / "before_tool.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True, text=True, check=False)
            (root / ".gitignore").write_text(".env*\n!.env.example\n")
            (root / ".env").write_text("API_KEY=secret")
            (root / ".env.example").write_text("API_KEY=replace-me")
            (root / "README.md").write_text("safe content")
            adapter.ROOT = root
            adapter.HOOKS = HOOKS

            # Broad repository context reads are allowed only because Git proves .env ignored.
            self.assertEqual(adapter.check_many_file_paths({"include": ["**/*"]}), 0)
            (root / "alias.conf").symlink_to(root / ".env")
            self.assertEqual(adapter.check_many_file_paths({"include": ["alias.conf"]}), 2)
            self.assertEqual(
                adapter.check_many_file_paths({
                    "include": ["**/*"],
                    "file_filtering_options": {"respect_git_ignore": False},
                }),
                2,
            )
            self.assertEqual(adapter.check_many_file_paths({"include": [".envrc"]}), 2)
            self.assertEqual(adapter.check_many_file_paths({"include": [".env.example.local"]}), 2)
            self.assertEqual(adapter.check_many_file_paths({"include": [".ENV.production"]}), 2)
            self.assertEqual(adapter.check_many_file_paths({"include": [".env.example"]}), 0)
            self.assertEqual(adapter.check_many_file_paths({"include": [".env"]}), 2)

    def test_gemini_read_many_files_fails_closed_when_patterns_missing(self):
        adapter = load_module("test_gemini_guard_invalid", ROOT / ".gemini" / "hooks" / "before_tool.py")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(adapter.check_many_file_paths({}), 2)
            self.assertEqual(adapter.check_many_file_paths({"include": "README.md"}), 2)

    def test_claude_search_hooks_block_explicit_env_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            grep = self.run_hook(
                HOOKS / "pre_read_guard.py", root,
                {"tool_name": "Grep", "tool_input": {"path": ".env", "pattern": "TOKEN"}},
            )
            self.assertEqual(grep.returncode, 2, grep.stderr)
            glob = self.run_hook(
                HOOKS / "pre_read_guard.py", root,
                {"tool_name": "Glob", "tool_input": {"path": ".", "pattern": "**/.env*"}},
            )
            self.assertEqual(glob.returncode, 2, glob.stderr)

    def test_edit_guard_scans_env_example_for_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_hook(
                HOOKS / "pre_edit_guard.py", Path(tmp),
                {"tool_name": "Write", "tool_input": {
                    "file_path": ".env.example",
                    "content": "GITHUB_TOKEN=ghp_" + "A" * 36,
                }},
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("GitHub token", result.stderr)

    def test_gemini_adapter_blocks_shell_and_secret_read(self):
        adapter = ROOT / ".gemini" / "hooks" / "before_tool.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            denied = self.run_hook(
                adapter, root,
                {"tool_name": "run_shell_command", "tool_input": {"command": "git reset --hard"}}, hook_root=ROOT,
            )
            self.assertEqual(denied.returncode, 2, denied.stderr)
            (root / ".env").write_text("TOKEN=placeholder")
            denied = self.run_hook(
                adapter, root,
                {"tool_name": "read_file", "tool_input": {"file_path": ".env"}}, hook_root=ROOT,
            )
            self.assertEqual(denied.returncode, 2, denied.stderr)
            allowed = self.run_hook(
                adapter, root,
                {"tool_name": "run_shell_command", "tool_input": {"command": "pwd"}}, hook_root=ROOT,
            )
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertEqual(json.loads(allowed.stdout)["decision"], "allow")


class LayeringBaselineTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module("test_check_layering", ROOT / "scripts" / "check_layering.py")
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.module.API_DIR = base / "api"
        self.module.BASELINE = base / "layering_baseline.json"
        self.module.API_DIR.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_stop_gate_detects_committed_branch_python_changes(self):
        module = load_module("test_stop_gate_paths", HOOKS / "stop_gate.py")
        def runner(command, cwd, timeout):
            if command[:3] == ["git", "status", "--porcelain"]:
                return 0, ""
            if command[:4] == ["git", "diff", "--name-only", "origin/main...HEAD"]:
                return 0, "apps/orchestrator/muse_orchestrator/api/projects.py\n"
            return 0, ""
        changed = module.collect_changed_paths(ROOT, runner=runner)
        self.assertIn("apps/orchestrator/muse_orchestrator/api/projects.py", changed)

    def test_layering_scan_fails_closed_when_api_directory_is_missing(self):
        self.module.API_DIR = Path(self.temp.name) / "missing-api"
        with self.assertRaises(FileNotFoundError):
            self.module.scan()

    def test_baseline_rejects_regression(self):
        self.module.BASELINE.write_text(json.dumps({"projects.py": 0}))
        (self.module.API_DIR / "projects.py").write_text("db.execute(stmt)\n")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = self.module.main(["--update-baseline"])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(self.module.BASELINE.read_text()), {"projects.py": 0})

    def test_baseline_rejects_no_decrease(self):
        self.module.BASELINE.write_text(json.dumps({"projects.py": 1}))
        (self.module.API_DIR / "projects.py").write_text("db.execute(stmt)\n")
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.module.main(["--update-baseline"])
        self.assertEqual(result, 1)

    def test_baseline_accepts_real_decrease(self):
        self.module.BASELINE.write_text(json.dumps({"projects.py": 1}))
        (self.module.API_DIR / "projects.py").write_text("return None\n")
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.module.main(["--update-baseline"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(self.module.BASELINE.read_text()), {"projects.py": 0})


class StopGateTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module("test_stop_gate", HOOKS / "stop_gate.py")

    def test_missing_test_prerequisites_are_not_pass(self):
        def runner(command, cwd, timeout):
            return (0, "") if "check_layering.py" in " ".join(command) else (127, "python3 not found")
        problems = self.module.validate_changed(ROOT, ["apps/orchestrator/example.py"], [], runner=runner)
        self.assertTrue(any("NOT RUN" in item for item in problems), problems)

    def test_no_tests_collected_is_not_pass(self):
        responses = iter([(0, ""), (0, ""), (5, "no tests ran")])
        problems = self.module.validate_changed(
            ROOT, ["apps/orchestrator/example.py"], [], runner=lambda *_: next(responses)
        )
        self.assertTrue(any("exit 5" in item for item in problems), problems)

    def test_tests_use_project_venv_python_when_present(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            venv_python = root / "apps" / "orchestrator" / ".venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text("")
            commands = []

            def runner(command, cwd, timeout):
                commands.append(command)
                return 0, "1 passed"

            problems = self.module.validate_changed(
                root,
                ["apps/orchestrator/example.py"],
                ["apps/media-engine/example.py"],
                runner=runner,
            )
            self.assertEqual(problems, [])
            pytest_interpreters = {c[0] for c in commands if "check_layering.py" not in " ".join(c)}
            self.assertEqual(pytest_interpreters, {str(venv_python)})

    def test_tests_fall_back_to_system_python_without_venv(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(self.module.resolve_python(Path(temp), "orchestrator"), "python3")

    def test_missing_media_pytest_is_not_pass(self):
        responses = iter([(0, ""), (1, "No module named pytest")])
        problems = self.module.validate_changed(
            ROOT, [], ["apps/media-engine/example.py"], runner=lambda *_: next(responses)
        )
        self.assertTrue(any("NOT RUN" in item for item in problems), problems)


class AgentHookReviewRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guard = load_module("test_guard_bash_normalization", HOOKS / "guard_bash.py")
        # SessionStart is a script as well as a module, so suppress its normal context output.
        with contextlib.redirect_stdout(io.StringIO()):
            cls.session_start = load_module("test_session_start_worker_detection", HOOKS / "session_start.py")

    def test_bash_guard_normalizes_quoted_token_concatenation_and_compound_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            denied = GuardTests().run_hook(
                HOOKS / "guard_bash.py",
                root,
                {"tool_name": "Bash", "tool_input": {"command": 'echo safe; g"it" push origin main'}},
            )
            self.assertEqual(denied.returncode, 2, denied.stderr)
            self.assertIn("push-main", denied.stderr)

            force_push = GuardTests().run_hook(
                HOOKS / "guard_bash.py",
                root,
                {"tool_name": "Bash", "tool_input": {"command": 'echo safe; git"" push origin feature --force-with-lease'}},
            )
            self.assertEqual(force_push.returncode, 2, force_push.stderr)
            self.assertIn("force-push", force_push.stderr)

    def test_session_start_requires_a_real_worker_service_and_arq_command(self):
        self.assertTrue(self.session_start._has_arq_worker_service(
            "services:\n"
            "  orchestrator-api:\n"
            "    command: uvicorn app:app\n"
            "  orchestrator-worker:\n"
            "    command: arq muse_orchestrator.workers.WorkerSettings\n"
        ))
        self.assertFalse(self.session_start._has_arq_worker_service(
            "services:\n"
            "  orchestrator-api:\n"
            "    command: uvicorn app:app # the arq worker is planned\n"
            "    environment:\n"
            "      QUEUE_MODE: arq\n"
        ))
        self.assertFalse(self.session_start._has_arq_worker_service(
            "# arq worker service removed\n"
            "services:\n"
            "  orchestrator-worker:\n"
            "    command: uvicorn app:app\n"
        ))


if __name__ == "__main__":
    unittest.main()
