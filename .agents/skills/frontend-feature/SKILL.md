---
name: frontend-feature
description: Workflow for building a UI feature in apps/studio-web (React 18 + TypeScript + Vite) - read the backend contract, update packages/contracts, add a typed API client call, build the module under src/modules, handle loading/error/empty states, build and verify. Use for any request involving the studio UI, shot board, workflow canvas, timeline editor, takes review, connections/settings screens, or shared TypeScript types.
---

# Building a studio-web feature

## Current baseline (verify before relying on it)
`src/App.tsx` is a single dashboard with inline styles and ad-hoc `fetch`; `src/modules/` and `src/services/` only contain `.gitkeep`; `packages/contracts` has state union types but is not imported. `package.json` has only react/react-dom/vite/typescript (no router, no flow library, no test runner).

## Workflow
1. **Read the contract, don't guess.** Open the Pydantic models in `apps/orchestrator/muse_orchestrator/api/<resource>.py` (and enums in `models/domain.py`). Field names, nullability, and error shapes must match exactly.
2. **Update `packages/contracts/src/index.ts`** with the DTO types for this feature; consume them via the workspace alias (set up `paths`/workspace in tsconfig if not yet done — do this once in a dedicated step, and say so).
3. **API client** in `src/services/` — one typed function per endpoint using a shared `request<T>()` that: prefixes `VITE_ORCHESTRATOR_URL`, sets `X-Studio-Admin-Token` from in-memory state, parses `{detail, category?, retryable?}` errors into a typed `ApiError`, and treats 401 as "token required".
4. **Module** in `src/modules/<feature>/`: `components/`, `hooks/` (data loading), `types.ts` only for UI-local types. Each screen handles **loading, empty, error (with retry when `retryable`), and success**.
5. **Status honesty**: render backend states verbatim via small badge components keyed by the contract unions; anything `simulated`/`placeholder` is labeled "Simulated output".
6. **Long operations** (generation, export): poll with backoff/abort on unmount (`AbortController`), show progress/state, never block the UI; offer cancel if the API supports it.
7. **Wire into `App.tsx`** (routing/layout only). Add a router only when a second screen exists; justify new dependencies.
8. **Verify**: `scripts/test.sh web` (tsc + vite build). If you add logic worth testing (reducers, parsers, DAG validation), add Vitest and a script `npm test`; otherwise say it is untested. You cannot view the browser — state exactly what you verified.

## Feature-specific notes
- **Workflow canvas**: React Flow (`@xyflow/react`) with nodes/edges serialized exactly as `WorkflowEngine.parse_graph_json` expects; validate port types client-side for UX but treat the server's 422 as the authority.
- **Timeline editor**: model clip placement in frames/seconds as the exporter does; show the server's `unsupported_features` list on export.
- **Takes A/B**: show both takes with their states and let the user approve one via the batch/takes endpoints; surface frame-chaining blocks (`blocked_upstream_frame`).
- **Security**: don't persist the admin token in localStorage; escape/render provider text as text, never `dangerouslySetInnerHTML`.

## Quality bar
No `any` in new code, no unused deps, no inline `fetch` in components, consistent small design tokens (colors/spacing) in one place rather than repeated inline objects.
