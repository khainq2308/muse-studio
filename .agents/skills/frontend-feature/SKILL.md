---
name: frontend-feature
description: Implement or modify a studio-web feature only under the mandatory frontend architecture in docs/FRONTEND_ARCHITECTURE.md. Use for the shot board, workflow canvas, timeline editor, takes review, connections/settings, projects, queue monitor, and shared frontend API types.
---

# Building a `studio-web` feature

This skill is a feature-workflow checklist, not an alternative architecture. **`docs/FRONTEND_ARCHITECTURE.md` is mandatory and wins if this skill ever conflicts with it.** Do not copy older frontend patterns from previous PRs or generated code.

## 1. Read the rules and inspect the actual phase

Before editing, read:

1. `AGENTS.md`
2. `docs/AGENT_WORKFLOW.md`
3. `docs/FRONTEND_ARCHITECTURE.md`
4. Relevant ADRs in `docs/DECISIONS.md` (especially ADR-006 and ADR-007).

Inspect the current branch, the relevant backend router/Pydantic schemas/domain enums, and the actual files and dependencies. Distinguish **implemented artifacts** from items the architecture document explicitly marks as planned for Phase 0. Never cite a planned file, command, dependency, endpoint, or capability as if it already exists.

### Phase gate — do not bypass it

Phase 0 is split into 0a (frontend foundation), 0b (offline OpenAPI snapshot/export + drift check), and 0c (typed API/response-model work by router). Do not bypass prerequisites, but apply the gate to the **feature's actual endpoint set** rather than blocking every feature until every unrelated backend route is typed.

- A normal API-backed feature is eligible only when Phase 0a and 0b are complete and every endpoint it consumes has a usable typed request/response schema.
- Queue-monitor may start after queue routes are typed. Shot-board additionally requires typed pipeline, batch and takes routes. Apply the same rule to each later feature's own routers.
- If an endpoint used by the requested feature still lacks an accurate response model/enum in generated OpenAPI, stop that feature and report the exact route/schema gap. Do not block an unrelated feature whose prerequisites are satisfied.
- When assigned Phase 0, implement only the named 0a/0b/0c slice, update the current-status table, and verify it. Do not quietly revive `src/modules/`, `src/services/`, hand-written API DTOs, custom fetch hooks, or the old single-screen app as a workaround.
- Do not install a partial substitute stack or expand the assigned slice without approval.

## 2. API contract: inspect first, never guess

1. Confirm the endpoint and HTTP method against the committed OpenAPI snapshot when available; cross-check its backend router and Pydantic request/response models and enums. A route mentioned in a document is not proof that it exists.
2. Use only fields, nullability, enums, status codes, and error shapes supported by the actual schema/implementation. If the OpenAPI schema cannot represent an endpoint accurately, report it as a backend contract/Phase 0 gap rather than inventing a frontend type.
3. API request/response types must come from the generated schema at `src/lib/api/schema.d.ts`. Treat that file as generated and read-only. **Do not add hand-written API DTOs to `packages/contracts` or parallel payload/response interfaces in feature files.** UI-only types that do not represent an API contract may be declared locally.
4. Never guess endpoints by naming convention. Verify each one before writing a call.

## 3. API transport and authentication

- `src/lib/api/client.ts` is the only place allowed to call `fetch`. It owns the base URL, auth header, query parameters, `AbortSignal`, response parsing, normalized errors, and authenticated file downloads.
- Use `VITE_ORCHESTRATOR_URL` and the exact admin header `X-Studio-Admin-Token`. If the base is `.` or `/`, normalize it to an empty same-origin base and build every API request as an absolute path beginning with `/api/`; never produce `./api/...` or another route-relative URL. Do not substitute `VITE_API_BASE_URL`, `X-Admin-Token`, or other guessed names.
- Keep the admin token in the in-memory Zustand store only. Never persist it in `localStorage` or `sessionStorage`.
- Normalize errors through the shared `ApiError` contract: preserve HTTP status, accept `detail` as a string/object/array, and retain optional `category` and `retryable` when the backend returns them. Do not coerce structured validation errors to strings or swallow failures.
- Send the query's `AbortSignal` through the shared client so canceled/unmounted queries can abort network work.
- For authenticated image/file downloads, follow the shared client pattern: use blob/object URLs only where appropriate and always revoke temporary URLs. Do not download many large videos into browser memory to populate a media grid; follow the media rules in the architecture document.

## 4. Place code in the approved layers

Use the structure in `docs/FRONTEND_ARCHITECTURE.md`:

- `src/lib/api/`: transport, generated schema, shared query keys and generic API utilities.
- `src/entities/<entity>/api.ts`: query/mutation hooks only when genuinely shared by at least two features.
- `src/features/<feature>/api.ts`: TanStack Query hooks and mutations used only by that feature.
- `src/features/<feature>/components/`: components local to a feature.
- `src/features/<feature>/pages/`: route-level screens.
- `src/features/<feature>/index.ts`: the feature's public surface.
- `src/components/ui/`: approved UI primitives; `src/components/shared/`: cross-feature presentational components.
- `src/app/`: app shell, providers, layout, routes and feature composition.
- `src/lib/stores/`: Zustand state for UI-only state and the in-memory admin token.

Use the internal `@/` alias. Do not import outside `apps/studio-web`.

**Forbidden legacy patterns:** creating a monolithic `src/services/`; creating features under `src/modules/`; API `fetch` in components/hooks; hand-written API DTOs; custom `useAsyncData` or `usePolling` built with `useEffect` + `setInterval`; cross-imports between feature folders; or keeping project/module navigation only in local/store state instead of the URL.

## 5. Query, mutation, navigation and long-running jobs

- Use TanStack Query v5 for server state; use Zustand only for client/UI state. Do not copy server responses into Zustand.
- Follow the shared query-key convention in `src/lib/api/queryKeys.ts`; mutations must invalidate the affected keys and surface failures to the user.
- Use TanStack Router. The selected project and active module belong in the URL (for example, `/projects/:projectId/<module>`) so links survive refresh and can be deep-linked. Do not add ad-hoc conditional routing in `App.tsx`.
- Use TanStack Query polling via `refetchInterval`, not hand-built intervals. Implement backoff and a bounded polling window; stop on terminal states derived from generated enums. Current `QueueTaskState` values are `queued | paused | running | succeeded | failed | poll_timeout | canceled`; terminal states are `succeeded | failed | poll_timeout | canceled`. `paused` is not terminal, but polling/re-enqueue must stop until explicit resume. `poll_timeout` is terminal. `ExportJobState` terminal states are `succeeded | failed | canceled`. These states must be represented in backend response models/OpenAPI before typing them as generated enums; do not infer state unions from arbitrary strings.
- Pass cancellation signals where supported. Offer cancel only when the backend endpoint supports it; do not fake cancellation in the UI.
- Confirm expensive/quota-consuming actions, report backend status honestly, and show progress when the backend provides it.

## 6. UI and media quality bar

- Every server-backed screen/list must support **loading, empty, error (with retry where appropriate), and data/success** states.
- Render designed, typed domain UI rather than dumping API responses. `<pre>{JSON.stringify(...)}` is forbidden in the normal product UI; allow it only inside an explicitly labeled debug panel that is hidden by default.
- Use the approved Tailwind CSS v4 + shadcn/ui/Radix + `lucide-react` design system and CSS theme tokens. Do not introduce emoji icons, hand-drawn substitute SVGs for available Lucide icons, repeated inline style objects, or unapproved UI libraries.
- Shot/take/asset views should show real images/video previews when supported. Use the verified asset download endpoint for previews, never a backup ZIP endpoint. Do not infer the existence of a thumbnail/poster endpoint from frame-chaining documentation alone; follow the limits in the architecture document.
- Render backend status values faithfully. Label simulated/placeholder output explicitly; never present it as generated media or a successful backend operation.
- Escape/render provider text as text; never use `dangerouslySetInnerHTML`.

## 7. Feature-specific notes

- **Workflow canvas:** use `@xyflow/react`; serialize nodes/edges exactly as the backend workflow parser expects. The UI must send `run_mode: "dry_run"` explicitly for its default run; the backend default is `full`. Send `run_mode: "full"` only after an explicit quota/action confirmation. Client validation is for UX only; server validation remains authoritative.
- **Shot board / takes A/B:** use verified shot, take and batch endpoints; show both takes and their states; surface continuity blocks such as `blocked_upstream_frame` only when supported by the backend response. For queue enqueue, omit `idempotency_key` by default and let the backend deduplicate; only submit `force_retry: true` after a clear confirmation that a new take may consume quota. For batch launch, `only_changed=false` must be an explicit confirmation choice showing the full rerun count and quota warning.
- **Timeline editor:** represent timing consistently with the export contract; display the server's `unsupported_features` and terminal export state. Do not mark an export complete until the backend says it succeeded.
- **Connections/settings:** never expose or persist credentials in browser storage; follow the actual backend connection/test/health contract.

## 8. Verify and report honestly

Run the scoped repository check:

```bash
bash scripts/test.sh web
```

Also run relevant unit/type/lint tests and the applicable Playwright smoke tests when they exist and are in scope. If the frontend test/lint/smoke-test foundation is missing, identify that as a Phase 0 gap rather than claiming it passed. If a change needs tests, add them using the approved Vitest/Testing Library/MSW setup; do not introduce a second test framework without approval.

In the PR summary, state exactly which commands ran and their outcomes. Distinguish typecheck/build/test results from browser visual verification; never claim to have inspected a browser or screenshot unless that was actually done. Do not report unrun tests as passing.

## Quality checklist

- [ ] Architecture docs and current phase inspected before coding.
- [ ] Every endpoint/field verified against the actual backend/OpenAPI contract.
- [ ] Generated schema used; no hand-written API DTOs.
- [ ] Shared client only; exact base URL/header; token is memory-only.
- [ ] Code placed in the approved `lib/`, `entities/`, `features/`, `components/`, and `app/` layers.
- [ ] Query keys/invalidation, cancellation, polling and terminal states handled correctly.
- [ ] Loading/empty/error/data states and real-media limitations handled honestly.
- [ ] Relevant checks run and reported precisely.
