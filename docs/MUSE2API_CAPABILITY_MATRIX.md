# muse2api Capability Matrix (M0 Audit)

| Capability / Endpoint | Method & Path | Status | Fallback / Xử lý trong MuseStudio |
| --- | --- | --- | --- |
| Health Check | `GET /healthz` | `mock-only` | Đánh dấu `unavailable` nếu timeout > 5s |
| Readiness Check | `GET /readyz` | `mock-only` | Đánh dấu `degraded` nếu `/healthz` OK nhưng `/readyz` lỗi |
| List Models | `GET /v1/models` | `mock-only` | Chỉ hiển thị đúng model được trả về; không hardcode |
| Chat / Script Analysis | `POST /v1/chat/completions` | `unverified` | Cho phép import kịch bản Markdown/JSON trực tiếp |
| Image Generation | `POST /v1/images/generations` | `mock-only` | Cho phép upload ảnh tham chiếu thủ công |
| Video Generation | `POST /v1/videos` | `mock-only` | Xử lý bất đồng bộ qua ARQ non-blocking polling |
| Poll Video Task | `GET /v1/videos/{task_id}` | `mock-only` | Exponential backoff + jitter |
| Media Download | `GET /v1/media/{name}` | `mock-only` | Tải về storage, kiểm tra bằng `ffprobe` |
| Per-Instance Proxy | `MUSE2API_BROWSER_PROXY` | `unverified` | Tạo nhiều `ConnectionProfile` trỏ tới các instance riêng |
| Per-Account Proxy (Single Instance) | N/A | `unsupported` | Hiển thị rõ `unsupported` trên UI |
| Quota Lookup | N/A | `unsupported` | Hiển thị `unsupported` trên UI |
