---
name: repository-workflow
description: Run a task end-to-end in the MuseStudio repository: preflight, implementation, tests, architecture and documentation gates, read-only review, or pre-PR readiness. Use whenever the task spans multiple files, tests, review, roadmap status, CI, or shipping.
---

# MuseStudio repository workflow

This skill routes work through the project’s platform-neutral contract. Read `AGENTS.md` and the relevant sections of `docs/AGENT_WORKFLOW.md` before acting.

## Task router

- **Run/fix tests:** run `scripts/test.sh <orchestrator|media|web|all>`. Reproduce the failure and inspect the test and code; fix the root cause, then rerun the failing test and full relevant scope.
- **Review code:** read-only. Inspect `git diff` and surrounding implementation; check correctness, security, architecture, test coverage and docs. Report findings ordered Blocker → Major → Minor with path/line and fix. Do not edit while in reviewer role.
- **Audit roadmap/docs:** read `docs/IMPLEMENTATION_PLAN.md`, `docs/ARCHITECTURE.md`, relevant code and tests. Mark completion only with behavior/test evidence; distinguish backend, UI, simulated and externally unverified capability.
- **Close a known gap:** first verify it still exists. Define observable acceptance criteria, make a focused change, add regression tests, run required gates and update docs from evidence. Do not auto-start adjacent gaps.
- **Prepare a PR:** run relevant tests, `scripts/lint.sh`, `python3 scripts/check_layering.py`, `python3 scripts/check_docs_sync.py`, and `python3 scripts/check_agent_assets.py`; inspect `git diff --check`, status and full diff. Fix confirmed Blocker/Major findings. Do not push, merge, deploy or run migrations on production unless authorized.
- **Architecture review:** run `python3 scripts/check_layering.py --report`; do not add DB access to API route handlers.
- **New endpoint:** activate `add-api-endpoint`.
- **Schema change:** activate `alembic-migrations`; verify current setup and use only disposable test DBs.
- **Frontend/contracts:** activate `frontend-feature`.
- **Backend testing:** activate `orchestrator-testing`.
- **Provider adapter:** activate `provider-adapter`.
- **FFmpeg/FFprobe:** activate `safe-ffmpeg`.

## Required rules

1. Read the working tree and preserve user changes before editing.
2. Do not assume a command, subagent, browser, hook, provider endpoint or package is available: verify it.
3. Keep the change focused and add regression coverage for behavior changes.
4. Run checks and report the actual command/output. Missing tools mean “not run”, not “passed”.
5. Never fabricate success or hide failures by weakening tests.
6. Update architecture/roadmap/capability docs when warranted; a script checking only its own rules does not prove system-wide completion.
7. Report outcome, files changed, validation status and remaining risks in Vietnamese.
