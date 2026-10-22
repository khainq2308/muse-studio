# MuseStudio — shared engineering contract for coding agents

This file is the **platform-neutral source of truth** for repository work. Read it before changing code. Then read [the agent workflow](docs/AGENT_WORKFLOW.md) and the relevant architecture / decision / implementation documents. Platform entrypoints such as `CLAUDE.md` and `GEMINI.md` must stay thin and point back here; do not create competing copies of these rules.

## Product and repository map

MuseStudio orchestrates AI-video production: script → Story Bible → shot board → workflow DAG → generation/batch processing → media verification and timeline export.

- `apps/orchestrator`: FastAPI, domain models, application services, persistence, queue and provider adapters.
- `apps/studio-web`: React + TypeScript + Vite frontend.
- `apps/media-engine`: FFprobe / FFmpeg utilities for inspecting and transforming media.
- Frontend API contracts are generated into `apps/studio-web/src/lib/api/schema.d.ts` from the orchestrator OpenAPI snapshot; the retired `packages/contracts` package was removed in PR #60 after the import audit. Do not recreate or import it.
- `docs/`: architecture, decisions, capability matrix, implementation plan and operation notes.
- `ops/`: operations and provider probes.
- `scripts/`: the canonical, agent-independent test, lint, environment and architecture commands.
- `.agents/skills/`: reusable Agent Skills (portable skill format).
- `.claude/`, `.codex/`, `.gemini/`: platform-specific adapters, never the source of core engineering policy.

Reply to the user in Vietnamese unless requested otherwise. Write code, identifiers, tests and commit messages in English. Match the language of existing documentation when editing it.

## Required workflow

Follow every stage. For a trivial change, stages may be short, but never skip checking the working tree, validating the result, or reporting evidence.

1. **Understand the request and the repository.** Read this file, `docs/AGENT_WORKFLOW.md`, the closest implementation and its tests. Read `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/IMPLEMENTATION_PLAN.md`, or `docs/MUSE2API_CAPABILITY_MATRIX.md` when relevant. For any change under `apps/studio-web`, also read `docs/FRONTEND_ARCHITECTURE.md`; its stack, structure and anti-pattern rules are mandatory for frontend work. For provider-specific behavior, distinguish source-code support from evidence observed against a real provider.
2. **Protect existing work.** Inspect `git status --short`, the active branch and relevant diff before editing. Preserve uncommitted user changes. Do not work directly on `main`/`master`; use a focused `feat/*`, `fix/*`, `refactor/*`, or `docs/*` branch when branch operations are available. Never force-push, discard the user's work, or run destructive database / Docker-volume commands.
3. **Define observable acceptance criteria.** State what behavior changes, how it will be demonstrated, which tests prove it, and what is explicitly out of scope. Prefer repository evidence over assumptions. Ask a question only when an essential decision cannot safely be inferred; otherwise make and state a reasonable assumption.
4. **Delegate selectively.** If the host supports subagents, use narrow specialists for independent investigation, review, security, tests, frontend, media, migrations or documentation. Give each a bounded task and ask for file/symbol evidence. Parallelize read-only investigations; do not let multiple agents edit the same files. If subagents are unavailable, do the same phases sequentially—never claim a subagent was used when it was not.
5. **Implement the smallest complete slice.** Follow existing patterns and contracts. Add or update regression tests in the same change. Avoid unrelated refactors. Do not start the next roadmap task unless the user asks.
6. **Verify the actual behavior.** Run the smallest relevant test first, then the broader affected suite. Run architecture, lint, docs and asset checks when applicable. Fix root causes rather than weakening assertions, deleting tests, or hiding failures. If a tool/dependency is unavailable, record that check as not run and explain why.
7. **Review the diff and documentation.** Inspect `git diff --check`, `git diff --stat`, and the actual diff. Look for security regressions, untested branches, async/blocking mistakes, accidental secrets or generated files, broken references, stale milestone claims and out-of-scope changes. Update docs when behavior, contracts, architecture, setup, limitations or milestone status changes.
8. **Report evidence, not confidence.** Summarize changed files and behavior. List the exact checks run and observed pass/fail counts. Separate verified facts, assumptions, unresolved risks and checks not run. Never claim code is tested, UI is visually correct, an API is supported, an asset is valid, or a gap is closed without the corresponding evidence.

The detailed phase gates and task-specific paths are in [docs/AGENT_WORKFLOW.md](docs/AGENT_WORKFLOW.md). Reusable domain playbooks live in `.agents/skills/`; activate/read the relevant skill instead of rewriting its procedure in a platform-specific prompt.

## Architecture and security invariants

### Backend dependency direction

Dependency direction is **`api → application → infrastructure`**.

- `api/*.py`: HTTP schemas, validation, status codes, DTO serialization and dependency injection only. Do not add `select(...)` or direct `db.execute/add/commit/...` calls to route handlers.
- `application/`: use cases and business rules; use `ApplicationError` for expected application failures; keep HTTP framework types out of core business rules.
- `infrastructure/repositories.py`: SQLAlchemy queries and transaction boundaries.
- `services/`: domain workflows such as queue, workflow execution, timeline and backup. `providers/`: external-provider adapters.
- Wire dependencies via `api/dependencies.py`. Product API routers must retain the shared admin-auth dependency; only deliberately public health-check routes may bypass it.

Legacy code may not fully meet the target layering. Do not add new violations; when changing a legacy route, move the touched logic down a layer where reasonably in scope. Use `python3 scripts/check_layering.py` to validate the ratchet.

### Truthfulness and failure handling

- Never fabricate completion, provider results, generated media, asset metadata or successful exports.
- A workflow node without a real executor must remain labelled `simulated`/`placeholder`; an unsupported or unverified provider capability must remain so until evidence supports a change.
- Exported media must be checked with FFprobe before success is recorded. A failed verification must not create or mark a successful asset.
- Frame chaining may proceed only when a real validated boundary-frame asset exists. Missing or failed upstream data must block downstream work clearly.
- Preserve error categories and retryability; do not turn permanent errors into retry loops or transient errors into false success.

### Secrets, files, networking and media

- Never read, print, copy into source, commit or expose real `.env` secrets. Document new variable names in `.env.example`. Do not log API keys, cookies, proxy credentials or encryption material. Pass error text through the repository's secret-redaction utilities.
- Treat provider URLs and downloaded files as hostile. Keep origin/redirect checks, scheme and path validation, size limits and magic-byte / media verification. Never send provider credentials to an untrusted origin.
- For FFmpeg/FFprobe, use an argv list—not `shell=True`—with a timeout, checked return code, bounded/redacted error text and output verification. Do not interpolate untrusted text into filter graphs. Avoid blocking the async event loop.
- Do not run migrations against a real/production database. Do not add ad-hoc `ALTER TABLE` code; use the repository's migration workflow and verify the current migration setup first.
- Do not hand-edit package lockfiles. Use the package manager.
- Do not run `git reset --hard`, recursive destructive deletes, `docker compose down -v`, destructive SQL, or force-pushes.

## Commands (run from repository root)

| Purpose | Command |
|---|---|
| Discover missing prerequisites | `scripts/doctor.sh` |
| All test suites | `scripts/test.sh all` |
| Focused orchestrator tests | `scripts/test.sh orchestrator -k "<pattern>" -x` |
| Media-engine tests | `scripts/test.sh media` |
| Frontend typecheck/build | `scripts/test.sh web` |
| OpenAPI snapshot + generated TypeScript drift check | `bash scripts/check_openapi.sh` (also included in `scripts/test.sh all`) |
| Typed OpenAPI success responses (CI gate) | `python3 scripts/audit_openapi_responses.py --strict` (fails on any generic/missing success schema; without `--strict` it only reports) |
| Python Ruff lint + frontend lint + layering ratchet | `scripts/lint.sh` (Ruff with the committed `[tool.ruff]` config in `apps/orchestrator/pyproject.toml` over the whole `apps/orchestrator` + `apps/media-engine` tree plus changed Python files elsewhere, then frontend ESLint/Prettier and the layering ratchet; `--python-only` skips the frontend, `--all` equals the default, `--fix` modifies application code) |
| Layering details | `python3 scripts/check_layering.py --report` |
| Roadmap gap hints (heuristic, not behavioral proof) | `python3 scripts/audit_roadmap.py` |
| Documentation status metadata validation | `python3 scripts/check_docs_sync.py` (not feature-completion proof) |
| Cross-platform agent assets | `python3 scripts/check_agent_assets.py` |
| Development API (SQLite) | `scripts/dev.sh` |

Read the script before relying on its semantics. A checker can only validate the conditions implemented in its rules; passing one checker does not prove all product behavior is complete. Run checks relevant to the change, not just whichever is quickest.

## Definition of done

A task is complete only when its agreed acceptance criteria are implemented and the relevant checks have actually run, or the remaining unverified items are stated explicitly. The final summary must include:

- **Outcome:** done / partially done / blocked.
- **Implementation:** behavior and files changed.
- **Validation:** command → observed result (pass, fail, or not run).
- **Remaining risks / assumptions:** only those that matter.
- **Scope:** confirm no unrequested next task was started.

