# M7 — Operational Reliability & Scale Plan

Status: **In progress**. The first implementation slice adds authenticated HTTP request metrics. Account/proxy routing, orphan cleanup, and backup/restore are planned but are not delivered by this slice.

## Goals and non-goals

M7 adds the operational controls needed to safely run multiple provider accounts/connections and keep media storage recoverable. The system must distinguish configured capability from verified runtime behavior. It must never display credentials, proxy authentication, cookies, or raw resource identifiers in logs/metric labels.

Not in scope: inventing undocumented provider-specific account-selection headers or endpoints, claiming quota access without a verified source, silently deleting media, or restoring over a live data directory without validation.

## Delivery slices

### M7.0 — Observability foundation (this PR)

- Instrument HTTP requests with a process-local counter grouped by method, registered route template, and status code.
- Record latency using a Prometheus histogram with finite buckets and cumulative counts.
- Expose `GET /metrics` in Prometheus text exposition format; protect it with the existing single-admin dependency. `/healthz` stays public.
- Use registered route templates rather than literal URLs to prevent project, shot, connection, task, or asset IDs from producing unbounded time series.
- Do not label or emit authorization headers, query values, request bodies, API keys, cookies, proxy URLs, or exception messages.
- Keep the first slice dependency-free. Metrics are process-local and reset on process restart; they are operational telemetry, not a durable analytics store.

Acceptance:
- Tests prove `/metrics` rejects requests without the configured token and accepts the token.
- Tests prove `/healthz` remains public.
- Tests prove route labels use templates rather than user-provided IDs.
- Histogram bucket samples are cumulative and the CI suite passes.

### M7.1 — Multiple accounts, connections, and real proxy routing

Current state: multiple `ProviderConnection` records are supported, but `proxy_display_host` is display-only and the provider adapter does not apply a configured proxy.

Planned work:
- Define the domain distinction between a provider endpoint/connection, provider account identity/credential, and proxy endpoint so account rotation does not overload the existing display-only field.
- Store account credentials and proxy URLs (including embedded proxy credentials) using the existing secret vault. Expose only masked credentials and a sanitized proxy host to API/UI responses.
- Pass decrypted proxy configuration only to the outbound HTTP client. Apply connection timeouts consistently to health, model discovery, submit, poll, and download requests.
- Support deterministic enabled/disabled state, concurrency limits, account cooldown/health state, and an auditable account-selection result.
- Use only account selection/identity semantics supported by the configured upstream adapter. A returned `account_id` is observational metadata, not proof that a request can select an account.
- Add tests with mocked HTTP transports/proxies; no real account credentials or external provider traffic in CI.
- Add a versioned schema migration/upgrade path before introducing changes to existing tables. `Base.metadata.create_all()` alone does not alter columns on an already-initialized database.

Acceptance:
- Credentials and proxy passwords never appear in JSON, logs, errors, metrics, or audit detail.
- Requests actually use configured proxy transport; tests verify the adapter's transport configuration.
- Disabling an account/connection prevents new dispatches; in-flight tasks keep their recorded connection/account provenance.
- Account rotation, cooldown, retry, and concurrency behavior are deterministic and covered by tests.
- Upgrade and rollback constraints are documented.

### M7.2 — Queue/provider observability

- Extend the initial HTTP metrics with bounded-cardinality queue/task state counts, provider-call duration/outcome, retry counts, and export outcomes where each signal can be measured at a trusted code boundary.
- Never use API keys, account IDs, project IDs, shot IDs, task IDs, prompts, or provider error text as metric labels.
- Document that counters and histograms are process-local unless a later deployment adds a Prometheus scrape/storage lifecycle; do not imply cross-restart retention.
- Add readiness guidance that distinguishes API liveness from DB/Redis/provider readiness.

Acceptance:
- Unit tests validate increments for success, retryable failure, terminal failure, and poll timeout without generating high-cardinality labels.
- Documentation explains scrape configuration and metric reset behavior.

### M7.3 — Orphan media reconciliation and cleanup

- Add a dry-run-first reconciliation command/service that compares files under `MEDIA_ROOT` with persisted `Asset.storage_rel_path` references and any additional registered media manifests.
- Resolve and verify all candidate paths beneath the configured media root; reject symlinks/path traversal and never traverse outside the root.
- Require a configurable minimum age/grace period to protect files still being written or awaiting DB commit.
- Report relative path, byte size, and reason only; do not include absolute host paths or secrets.
- Deletion requires an explicit apply mode. Dry-run is the default; preserve database-referenced, locked-reference, and backup-staging content.
- Make reconciliation idempotent and produce an operator audit record.

Acceptance:
- Tests cover referenced files, orphan files, too-new files, symlinks/path traversal, dry-run no-op, explicit deletion, and repeat runs.
- The command can never delete anything outside `MEDIA_ROOT`.

### M7.4 — Backup and restore

- Back up the database and media storage as a coordinated set, including a manifest with timestamps, schema/application version, relative file paths, sizes, and SHA-256 checksums.
- Use supported DB-native tools for the production database (PostgreSQL) and separately documented workflows for developer SQLite databases. Do not copy a live PostgreSQL data directory as a substitute for a database backup.
- Restore into a staging target first, validate the manifest/checksums and database/media references, and only then allow an explicit operator-controlled cutover.
- Treat encrypted credential columns as ciphertext; the stable `SECRET_ENCRYPTION_KEY` must be backed up separately through the deployment's secret manager. Never bundle the key or raw environment secrets into the artifact.
- Reject unsafe archive paths, symlinks, path traversal, checksum mismatches, and overwrites of non-empty targets unless an explicit safe replacement procedure is selected.
- Document recovery point/recovery time assumptions, retention, verification, and restore drills.

Acceptance:
- CI tests perform a temp-directory media backup/restore round trip and detect corruption/path traversal.
- Database backup/restore scripts support the documented DB type and fail closed on non-zero tool exit codes.
- A runbook covers backup verification and a restore drill; backup success is not reported until all artifacts/checksums are validated.

## Dependency and rollout order

1. M7.0 establishes visibility into API behavior first.
2. M7.1 introduces account/proxy behavior behind tests and schema upgrades.
3. M7.2 adds queue/provider telemetry after the account-selection boundary exists.
4. M7.3 introduces dry-run reconciliation before enabling deletion.
5. M7.4 adds staged backup/restore and a recovery runbook.

The slices can be reviewed independently. No later slice should be described as complete merely because its schema, UI, or command skeleton exists; completion requires runtime behavior, safeguards, and tests above.
