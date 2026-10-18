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

    def test_read_guard_blocks_real_env_and_allows_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (".env", ".env.production"):
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

    def test_missing_media_pytest_is_not_pass(self):
        responses = iter([(0, ""), (1, "No module named pytest")])
        problems = self.module.validate_changed(
            ROOT, [], ["apps/media-engine/example.py"], runner=lambda *_: next(responses)
        )
        self.assertTrue(any("NOT RUN" in item for item in problems), problems)


if __name__ == "__main__":
    unittest.main()
