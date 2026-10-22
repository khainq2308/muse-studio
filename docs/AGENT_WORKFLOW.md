# MuseStudio — agent workflow and platform integration guide

This document describes the required execution process for Claude Code, Codex, Gemini CLI, and any other coding agent. `AGENTS.md` is the compact policy entrypoint; this file is the operational playbook.

## 1. Architecture of the agent kit

The kit has four layers. Keep platform-specific configuration thin and keep engineering logic in the most portable layer possible.

| Layer | Canonical location | Purpose | Portability |
|---|---|---|---|
| Repository contract | `AGENTS.md` | Project context, engineering invariants, task lifecycle, definition of done | Portable Markdown. Codex reads it natively; other hosts should be pointed to it by their entrypoint. |
| Task playbooks | `.agents/skills/<skill>/SKILL.md` | On-demand, repeatable procedures and their references | Shared Agent Skills format; Codex and Gemini CLI support `.agents/skills`. Claude's project skills are mirrored under `.claude/skills`. |
| Deterministic checks | `scripts/` | Tests, lint, layering, policy/asset validation and local dev commands | Tool-neutral shell/Python where dependencies are available. Prefer these over host-specific command logic. |
| Host adapters | `.claude/`, `.codex/`, `.gemini/` | Native subagent definitions, slash commands, hooks, permissions and event mapping | Platform-specific. Keep these files small and point them to shared rules/scripts. |

The same instruction prose is not enough to make hooks portable: each host has its own event names, payload shape, permission model, and block/continue contract. Share the policy function or check, then implement a thin adapter that translates the host event. Never copy a host's hook JSON and assume another runtime will understand it.

## 2. Required lifecycle for every task

### Phase A — intake and preflight

1. Restate the task internally in one sentence. Identify affected area(s), risk and expected outcome.
2. Read `AGENTS.md`, this document, and the nearest existing implementation/tests. Add architecture, decisions, implementation-plan or capability-matrix docs where the change touches them.
3. Inspect `git status --short`, branch, recent history and the target diff. Never overwrite user work or assume the tree is clean.
4. Build an acceptance checklist that describes observable behavior and tests. Do not use "file exists" as proof that a feature works.
5. If the task is complex, write a short plan and expose it before substantial editing. Ask only for essential missing input; otherwise proceed with an explicit safe assumption.

### Phase B — delegation and design

Delegate only when the host actually offers subagents. Assign independent tasks such as:
- `backend-dev`: API/application/repository/service behavior and backend tests.
- `frontend-dev`: UI, shared API transport and generated OpenAPI types under the mandatory frontend architecture.
- `test-writer`: regression and negative tests.
- `code-reviewer`: read-only review of behavior, security, architecture and coverage.
- `security-auditor`: read-only security analysis.
- `media-engineer`: FFmpeg/FFprobe, downloaded-media validation and frame continuity.
- `migration-engineer`: migration design and reversible schema changes.
- `docs-sync`: documentation accuracy and roadmap status.

Give each specialist a bounded goal, relevant paths, constraints, expected evidence and output format. For parallel work, prefer read-only investigations. Do not have separate agents modify the same file or commit independently without an explicit coordination plan. The main agent owns integrating results, running checks and communicating the final status.

When no subagent support exists, adopt these roles sequentially in the same session. Never say a subagent reviewed or tested something unless a real delegation result exists.

### Phase C — implementation

1. Load the relevant shared skill before beginning specialized work:
   - API endpoint: `add-api-endpoint`
   - database schema: `alembic-migrations`
   - frontend or contracts: `frontend-feature`
   - orchestrator tests: `orchestrator-testing`
   - muse2api/provider: `provider-adapter`
   - FFmpeg/FFprobe/media validation: `safe-ffmpeg`
   - general task, review, tests or release readiness: `repository-workflow`
2. Reuse existing patterns and contracts. Read current schemas and enums rather than guessing field names or states.
3. Keep the diff focused. Add regression tests in the same change. Do not remove assertions or turn failures into skips simply to get green.
4. For uncertain external capability, record the evidence and uncertainty in the capability matrix; do not infer support from adapter code alone.
5. Update documentation and ADRs when the behavior, security boundary, architecture, deployment or status changes.

### Phase D — validation gates

Run the smallest relevant check first. Then select the wider checks based on risk:

| Change | Required validation |
|---|---|
| Backend behavior / API / queue | `scripts/test.sh orchestrator`; `scripts/lint.sh`; `python3 scripts/check_layering.py` |
| FFmpeg or media engine | `scripts/test.sh media`; relevant orchestrator integration tests with FFmpeg available; verify output with FFprobe |
| Frontend / contracts | `scripts/test.sh web` and `bash scripts/check_openapi.sh`; for backend response-model migration also run `python3 scripts/audit_openapi_responses.py` and relevant orchestrator tests; browser behavior remains unverified unless browser testing actually ran |
| Schema / migrations | Migration-specific upgrade/downgrade tests on throwaway databases; orchestrator tests; never use a production database |
| Security-sensitive behavior | Negative tests for malicious input, secret leakage and path/origin validation; read-only security review when available |
| Roadmap signals | `python3 scripts/audit_roadmap.py`; use output only as a heuristic inventory, then inspect implementation/tests |
| Docs status metadata | `python3 scripts/check_docs_sync.py`; validates explicit milestone status labels and capability status vocabulary, not feature behavior |
| Shared agent-kit files | `python3 scripts/check_agent_assets.py`; validate front matter, cross-platform entrypoint links and skill mirror consistency |
| Broad refactor / pre-PR | `scripts/test.sh all`, `scripts/lint.sh`, `python3 scripts/audit_roadmap.py`, `python3 scripts/check_docs_sync.py`, `python3 scripts/check_agent_assets.py`, then a read-only diff review |

A missing command or dependency is **not a pass**. Use `scripts/doctor.sh`, record the exact blocker, and do not silently skip the gate.

### Phase E — review and completion

1. Inspect `git diff --check`, `git diff --stat` and the complete relevant diff.
2. Check for untracked generated media/databases, real secrets, accidental lockfile edits, new architecture violations and out-of-scope files.
3. Check docs/roadmap claims against the behavior and test evidence; describe partial implementation precisely.
4. Report outcome, file-level changes, exact validation commands/results, unverified behavior and remaining risks. Do not say "all tests pass" unless all intended suites ran and passed.
5. Do not automatically start the next task or deploy, push, merge, migrate production, or perform destructive cleanup unless explicitly authorized.

## 3. Platform mapping

| Capability | Claude Code | Codex | Gemini CLI | Any other agent |
|---|---|---|---|---|
| Persistent repo rules | `CLAUDE.md` points to `AGENTS.md` | `AGENTS.md` | `GEMINI.md` points to `AGENTS.md` | Configure its system/project prompt to read `AGENTS.md` |
| Reusable skills | `.claude/skills/` mirror | `.agents/skills/` | `.agents/skills/` | Read the relevant `SKILL.md` explicitly |
| Specialist agents | `.claude/agents/*.md` | `.codex/agents/*.toml` | `.gemini/agents/*.md` | Map the role brief into the host's supported agent format |
| Task commands | `.claude/commands/*.md` | Invoke a shared skill or script | Invoke a shared skill or script | Invoke documented workflow + script |
| Hooks | `.claude/settings.json` + `.claude/hooks/` | Use the installed Codex hook interface/config if enabled | Use Gemini CLI `settings.json` hook events | Write an adapter for the host's event contract |
| Tests/architecture commands | `scripts/` | `scripts/` | `scripts/` | `scripts/` |

Subagent and hook formats are intentionally not interchangeable. The Codex reviewer/auditor profiles set sandbox_mode to read-only, but this declaration is not proof that the active runtime enforces it. Follow [Codex read-only sandbox validation](CODEX_SANDBOX_VALIDATION.md): run profiles only under a confirmed read-only parent sandbox, and use a disposable checkout with no credentials if enforcement is uncertain. Before authoring a native adapter, verify the current host/version schema. Platform upgrades can change hooks, permission prompts, names and payloads.

## 4. Command-equivalent workflows

Do not require custom slash-command support to perform repository tasks. Start from the user intent and run the matching canonical steps:

| User intent / Claude command | Portable procedure |
|---|---|
| `/test [scope]` | Read `repository-workflow`; run `scripts/test.sh <scope>`; inspect and repair failures; rerun failed test then the complete scope; report observed counts. |
| `/review` | Read-only: inspect target diff + surrounding code, run relevant read-only checks, review correctness/security/coverage/docs and return severity-ordered findings. Never edit when acting as reviewer. |
| `/ship` | Run the pre-PR gate: tests, lint, layering, docs, asset consistency, diff review; fix Blocker/Major findings; stage only intended files. Do not push or merge unless authorized. |
| `/audit-roadmap` | Read current plan + architecture + tests; classify each milestone as done / backend-only / UI missing / simulated / unverified / not started; cite file and test evidence. |
| `/close-gap <name>` | Verify the named gap still exists; define acceptance criteria; branch; implement only that gap; add regression tests; validate; update docs from evidence. |
| `/fix-ci` | Reproduce CI failure locally, trace root cause, make minimal fix, run exact failing check then relevant suites. Do not loosen CI to hide a bug. |
| `/layering` | Run `python3 scripts/check_layering.py --report`. It ratchets two metrics per API route file against `scripts/layering_baseline.json`: `direct_db` (session method calls, session aliases and `get_db`/`AsyncSession` injection in routes) and `session_passthrough` (routes passing `repository.session` or an injected session into services); `--report` lists each site. Never increase either metric; lower a baseline only when the debt was actually removed and the diff explains why. A passing ratchet does not prove complete layering. |
| `/new-endpoint` | Read `add-api-endpoint`; follow API → application → repository layering, protected routing, bounded inputs, tests and documentation. |
| `/migrate` | Read `alembic-migrations`; inspect actual migration state; write reversible revisions; test on disposable databases; never touch production. |

## 5. Shared skills contract

- `.agents/skills/` is canonical for shared skill content.
- `.claude/skills/` mirrors those skill directories for Claude Code's native discovery. Mirror content is checked by `scripts/check_agent_assets.py`; edit the canonical copy and update its mirror in the same change.
- Keep front matter valid and portable: `name` and `description` first; do not require host-only tool names, slash commands or environment variables in a shared skill.
- Keep host-specific instructions in the entrypoint/adapter, not in the shared skill's core steps.
- Put stable detailed procedures in a skill; keep `AGENTS.md` short enough to act as an always-loaded contract.

## 6. Safety and permissions

Permission prompts and hook allow/deny results are owned by the host. Do not try to bypass them, and do not equate a configured hook with an enforced policy until tested. Keep safety logic in deterministic, testable scripts; let each hook adapter map host events to those scripts.

Protect `.env`, keys, databases, storage and user data. Do not hard reset, force-push, erase volumes, run destructive SQL, access production services, or apply production migrations from an agent. If an operation may discard user data or make an external irreversible change, stop and obtain explicit authorization.
