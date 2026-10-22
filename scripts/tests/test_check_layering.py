"""Regression tests for the ARCH-001 layering-checker detections."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_checker():
    spec = importlib.util.spec_from_file_location(
        "test_check_layering_arch001", ROOT / "scripts" / "check_layering.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load check_layering.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SiteDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker = load_checker()

    def sites(self, source: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "route.py"
            path.write_text(textwrap.dedent(source), encoding="utf-8")
            return self.checker.analyze_file(path)

    def kinds(self, source: str) -> list[str]:
        return [site.kind for site in self.sites(source)]

    # (a) aliasing a session reached through an attribute
    def test_alias_of_repository_session_commit_is_violation(self):
        kinds = self.kinds(
            """
            async def endpoint(repository=Depends(get_repo)):
                s = repository.session
                await s.commit()
            """
        )
        self.assertEqual(kinds, ["db_call"])

    def test_multi_hop_and_annotated_and_walrus_aliases(self):
        kinds = self.kinds(
            """
            async def endpoint(repository=Depends(get_repo)):
                first = repository.session
                second = first
                await second.execute(stmt)
                typed: AsyncSession = make()
                await typed.flush()
                if (w := self.db) is not None:
                    await w.rollback()
            """
        )
        self.assertEqual(kinds.count("db_call"), 3)

    def test_async_with_session_alias(self):
        kinds = self.kinds(
            """
            async def endpoint(repository=Depends(get_repo)):
                async with repository.session as s:
                    s.add(entity)
            """
        )
        self.assertEqual(kinds, ["db_call"])

    def test_annotated_field_without_value_is_not_an_alias(self):
        kinds = self.kinds(
            """
            class Payload(BaseModel):
                manifest: dict
            def route(manifest: dict):
                return manifest.get("title")
            """
        )
        self.assertEqual(kinds, [])

    # (b) session injected directly into a route
    def test_get_db_injection_without_db_call_is_violation(self):
        sites = self.sites(
            """
            from sqlalchemy.ext.asyncio import AsyncSession
            from muse_orchestrator.core.database import get_db

            async def summary(db: AsyncSession = Depends(get_db)):
                return await collect_summary(db)
            """
        )
        injections = [s for s in sites if s.kind == "session_injection"]
        self.assertEqual(len(injections), 1)
        self.assertEqual(injections[0].line, 5)
        self.assertEqual(injections[0].metric, "direct_db")

    def test_injection_via_renamed_get_db_and_annotated_alias(self):
        kinds = self.kinds(
            """
            from typing import Annotated
            from muse_orchestrator.core.database import get_db as provide_db

            DbSession = Annotated[AsyncSession, Depends(provide_db)]

            async def one(unit=Depends(provide_db)):
                return None

            async def two(db: DbSession):
                return None

            async def three(db: Annotated[AsyncSession, Depends(provide_db)]):
                return None
            """
        )
        self.assertEqual(kinds.count("session_injection"), 3)

    def test_typed_session_parameter_counts_once_per_parameter(self):
        kinds = self.kinds(
            """
            async def endpoint(database: AsyncSession, other: int):
                return other
            """
        )
        self.assertEqual(kinds, ["session_injection"])

    # (c) session pass-through is a separate metric
    def test_session_passthrough_is_separate_metric(self):
        sites = self.sites(
            """
            async def endpoint(repository=Depends(get_repo)):
                await GenerationQueueService.step_task(repository.session, task_id)
                await GenerationQueueService.enqueue(db=repository.session, shot=s)
                await repository.get_project(project_id)
                await service.run(*items, flag=project_id)
            """
        )
        self.assertEqual([s.kind for s in sites], ["session_passthrough", "session_passthrough"])
        self.assertTrue(all(s.metric == "session_passthrough" for s in sites))
        self.assertIn("repository.session", sites[0].detail)

    def test_alias_passthrough_counts(self):
        kinds = self.kinds(
            """
            async def endpoint(repository=Depends(get_repo)):
                s = repository.session
                await service.tick(s)
            """
        )
        self.assertEqual(kinds, ["session_passthrough"])

    def test_injected_session_passed_to_service_counts_both_metrics(self):
        kinds = self.kinds(
            """
            async def summary(db: AsyncSession = Depends(get_db)):
                return await collect_summary(db)
            """
        )
        self.assertEqual(sorted(kinds), ["session_injection", "session_passthrough"])


class RatchetTests(unittest.TestCase):
    def setUp(self):
        self.checker = load_checker()
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.checker.API_DIR = base / "api"
        self.checker.BASELINE = base / "layering_baseline.json"
        self.checker.API_DIR.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write_route(self, name: str, source: str) -> None:
        (self.checker.API_DIR / name).write_text(textwrap.dedent(source), encoding="utf-8")

    def run_main(self, *argv: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = self.checker.main(list(argv))
        return code, output.getvalue()

    def set_baseline(self, direct: dict, passthrough: dict) -> None:
        self.checker.BASELINE.write_text(
            json.dumps({"direct_db": direct, "session_passthrough": passthrough})
        )

    def test_passthrough_growth_fails_even_without_direct_db(self):
        self.set_baseline({"batch.py": 0}, {"batch.py": 1})
        self.write_route(
            "batch.py",
            """
            async def a(repository=Depends(get_repo)):
                await svc.one(repository.session)
                await svc.two(repository.session)
            """,
        )
        code, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("[session_passthrough] batch.py: current 2, baseline 1", output)
        self.assertIn("batch.py:4", output)

    def test_injection_growth_fails(self):
        self.set_baseline({"metrics.py": 0}, {"metrics.py": 0})
        self.write_route("metrics.py", "async def m(db: AsyncSession = Depends(get_db)):\n    return 1\n")
        code, output = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("[direct_db] metrics.py: current 1, baseline 0", output)

    def test_within_baseline_passes(self):
        self.set_baseline({"metrics.py": 1}, {"metrics.py": 1})
        self.write_route(
            "metrics.py",
            "async def m(db: AsyncSession = Depends(get_db)):\n    return await collect(db)\n",
        )
        code, output = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("1 session pass-through sites", output)

    def test_report_lists_each_site(self):
        self.set_baseline({"metrics.py": 1}, {"metrics.py": 1})
        self.write_route(
            "metrics.py",
            "async def m(db: AsyncSession = Depends(get_db)):\n    return await collect(db)\n",
        )
        code, output = self.run_main("--report")
        self.assertEqual(code, 0)
        self.assertIn("metrics.py:1 session_injection", output)
        self.assertIn("metrics.py:2 session_passthrough", output)
        self.assertIn("collect()", output)

    def test_update_baseline_requires_decrease_and_writes_both_metrics(self):
        self.set_baseline({"queue.py": 0}, {"queue.py": 2})
        self.write_route("queue.py", "async def q(repository):\n    await svc.step(repository.session)\n")
        code, _ = self.run_main("--update-baseline")
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(self.checker.BASELINE.read_text()),
            {"direct_db": {"queue.py": 0}, "session_passthrough": {"queue.py": 1}},
        )
        code, _ = self.run_main("--update-baseline")
        self.assertEqual(code, 1)

    def test_rejects_unknown_metric_in_baseline(self):
        self.checker.BASELINE.write_text(json.dumps({"direct_db": {}, "bogus": {}}))
        self.write_route("queue.py", "x = 1\n")
        with contextlib.redirect_stderr(io.StringIO()):
            code, _ = self.run_main()
        self.assertEqual(code, 2)


class RepositoryBaselineTests(unittest.TestCase):
    def test_repository_baseline_matches_ratchet_and_known_debt(self):
        checker = load_checker()
        baseline = checker.load_baseline()
        current = checker.scan()
        self.assertEqual(checker.find_regressions(current, baseline), {})
        # get_db injected directly into these routes is tracked as debt, not hidden.
        self.assertGreaterEqual(baseline["direct_db"].get("metrics.py", 0), 1)
        self.assertGreaterEqual(baseline["direct_db"].get("maintenance.py", 0), 1)


if __name__ == "__main__":
    unittest.main()
