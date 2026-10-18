# muse2api Capability Matrix (M0 Audit)

| Capability / Endpoint | Method & Path | Status | Evidence / Probe Date | Fallback / Xử lý trong MuseStudio |
| --- | --- | --- | --- | --- |
| Health Check | GET /healthz | mock-only | No dated real-instance probe is recorded in this repository. | Đánh dấu unavailable nếu timeout > 5s |
| Readiness Check | GET /readyz | mock-only | No dated real-instance probe is recorded in this repository. | Đánh dấu degraded nếu /healthz OK nhưng /readyz lỗi |
| List Models | GET /v1/models | mock-only | Mock responses only; no dated real-instance probe is recorded. | Chỉ hiển thị đúng model được trả về; không hardcode |
| Chat / Script Analysis | POST /v1/chat/completions | unverified | No dated real-instance probe is recorded in this repository. | Cho phép import kịch bản Markdown/JSON trực tiếp |
| Image Generation | POST /v1/images/generations | mock-only | Mock provider/tests only; no dated real-instance probe is recorded. | Cho phép upload ảnh tham chiếu thủ công |
| Video Generation | POST /v1/videos | mock-only | Mock provider/tests only; no dated real-instance probe is recorded. | Xử lý bất đồng bộ qua ARQ non-blocking polling |
| Poll Video Task | GET /v1/videos/{task_id} | mock-only | Mock provider/tests only; no dated real-instance probe is recorded. | Exponential backoff + jitter |
| Media Download | GET /v1/media/{name} | mock-only | Mock responses only; no dated real-instance probe is recorded. | Tải về storage, kiểm tra bằng ffprobe |
| Per-Instance Proxy | MUSE2API_BROWSER_PROXY | unverified | No dated real-instance probe is recorded in this repository. | Tạo nhiều ConnectionProfile trỏ tới các instance riêng |
| Per-Account Proxy (Single Instance) | N/A | unsupported | Not implemented by this integration; no upstream support claim is made. | Hiển thị rõ unsupported trên UI |
| Quota Lookup | N/A | unsupported | Not implemented by this integration; no upstream support claim is made. | Hiển thị unsupported trên UI |

## Verification rule

Use status verified only when a dated evidence artifact is committed under docs/evidence/ and identifies the probe date, instance/version, request/response outcome and limitations. The CI metadata checker requires both the date and the artifact path to exist. Source inspection, mock tests and dependency presence do not count as real-provider verification.
