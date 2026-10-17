# Test Strategy

* **Unit & Domain Tests:** Kiểm thử `SecretRedactor`, `PromptCompiler` (bắt lỗi `BLOCKED_REFERENCE`, `CAMERA_MOTION_CONFLICT`), `WorkflowEngine` (chặn cycle, Merkle cache).
* **M1 Foundation Integration Tests:** Kiểm thử bất đồng bộ trên FastAPI + SQLite in-memory cho Projects, Revisions, Connections (mã hoá Fernet) và Assets (Magic Bytes & khoá Reference).
