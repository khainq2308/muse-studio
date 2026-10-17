# Architecture Decision Records (ADRs)

* **ADR-001 (Monorepo Structure):** Tổ chức thành `apps/orchestrator`, `apps/studio-web`, `apps/media-engine`, và `packages/contracts`.
* **ADR-002 (Queue Engine):** Chọn `ARQ` (AsyncIO Redis Queue) thay vì Celery để đồng bộ hoàn toàn với FastAPI + SQLAlchemy Async và hỗ trợ `defer_by` non-blocking polling.
* **ADR-003 (State Orthogonalization):** Tách biệt 4 trạng thái độc lập: `ShotApprovalState`, `ShotGenerationState`, `NodeExecutionState`, `ConnectionHealthState`.
* **ADR-004 (Secret Vault & Redaction):** Mã hoá API key bằng Fernet (`SECRET_ENCRYPTION_KEY`); lọc bỏ mọi key/cookie/proxy password khỏi log và response.
* **ADR-005 (Frame Chaining):** Dùng FFmpeg tách frame đầu/cuối tự động để nối mượt các shot liên tiếp.
