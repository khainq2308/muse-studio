# Provider capability evidence

Do not mark a row in docs/MUSE2API_CAPABILITY_MATRIX.md as verified unless an evidence artifact exists here.

For each real-instance probe, commit an evidence Markdown file (for example, docs/evidence/muse2api/2026-10-18-model-list.md) containing:

- Probe date in ISO format (YYYY-MM-DD).
- Provider instance identity/version, with host secrets and API keys redacted.
- Exact endpoint/method and non-sensitive request parameters.
- Observed status, response shape, and a redacted response excerpt or sanitized probe output.
- Whether the behavior was repeated, plus timeouts/failures and known limitations.
- The associated capability-matrix row.

Never commit access tokens, authorization headers, cookies, full proxy URLs with credentials, or unredacted provider responses. If the real instance cannot be probed, keep the status as unverified or mock-only.

scripts/check_docs_sync.py requires a verified row to contain an ISO date and a path under docs/evidence/ that exists. This structural check cannot establish that evidence is genuine; a reviewer must inspect the artifact before approving a status change.
