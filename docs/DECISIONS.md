# Architecture Decision Records (ADRs)

* **ADR-001 (Monorepo Structure):** Tổ chức thành `apps/orchestrator`, `apps/studio-web`, `apps/media-engine`, và `packages/contracts`.
* **ADR-002 (Queue Engine):** Chọn `ARQ` (AsyncIO Redis Queue) thay vì Celery để đồng bộ hoàn toàn với FastAPI + SQLAlchemy Async và hỗ trợ `defer_by` non-blocking polling.
* **ADR-003 (State Orthogonalization):** Tách biệt 4 trạng thái độc lập: `ShotApprovalState`, `ShotGenerationState`, `NodeExecutionState`, `ConnectionHealthState`.
* **ADR-004 (Secret Vault & Redaction):** Mã hoá API key bằng Fernet (`SECRET_ENCRYPTION_KEY`); lọc bỏ mọi key/cookie/proxy password khỏi log và response.
* **ADR-005 (Frame Chaining):** Dùng FFmpeg tách frame đầu/cuối tự động để nối mượt các shot liên tiếp.

* **ADR-006 (Provider Accounts & Proxies):** Keep provider endpoint/connection, account identity, and proxy endpoint as distinct concepts. Secrets (account credentials and proxy URLs containing credentials) use the existing secret vault and never appear in API outputs or diagnostics. Do not invent account-selection headers or quota semantics; only use upstream behavior verified by an adapter contract. Add a versioned schema upgrade path before changing existing-table columns.
* **ADR-007 (Operational Metrics & Media Safety):** Metrics use stable route templates and bounded labels only; never label by resource IDs, credentials, prompts, query values, or error strings. Orphan cleanup defaults to dry-run and is confined to the media root. Restore is staged and checksum-validated; the encryption key remains out-of-band from backup artifacts.
