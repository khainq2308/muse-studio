# muse2api Capability Matrix (M0 Audit)

| Capability / Endpoint | Method & Path | Status | Evidence / Probe Date | Fallback / Xử lý trong MuseStudio |
| --- | --- | --- | --- | --- |
| Health Check | GET /healthz | mock-only | Contract verified 2026-10-23 against www222fff/muse2api `949f326` with its **mock driver** (`tests/test_muse2api_contract.py`, CI job `muse2api-contract`); no real muse.ai account probed. | Đánh dấu unavailable nếu timeout > 5s |
| Readiness Check | GET /readyz | mock-only | Same 2026-10-23 mock-driver contract run; `readyz` also reports driver and account-pool state. | Đánh dấu degraded nếu /healthz OK nhưng /readyz lỗi |
| List Models | GET /v1/models | mock-only | Same 2026-10-23 mock-driver contract run: `muse-chat`, `muse-image`, `muse-video` plus aliases. | Chỉ hiển thị đúng model được trả về; không hardcode |
| Chat / Script Analysis | POST /v1/chat/completions | unverified | No dated real-instance probe is recorded in this repository. | Cho phép import kịch bản Markdown/JSON trực tiếp |
| Image Generation | POST /v1/images/generations | mock-only | Same 2026-10-23 mock-driver contract run: `"async": true` returns a task polled at `GET /v1/images/generations/{id}`; fields `size` / `image`; URL in `result.data[0].url`. | Cho phép upload ảnh tham chiếu thủ công |
| Video Generation | POST /v1/videos | mock-only | Same 2026-10-23 mock-driver contract run: `duration` must be an integer 1–60 (the adapter rounds and clamps); no `Idempotency-Key` support, so ambiguous dispatches are never re-sent. Mock output is a 16-byte placeholder, not a decodable MP4. | Xử lý bất đồng bộ qua ARQ non-blocking polling |
| Video first-frame conditioning | POST /v1/videos | unverified | Source-code/schema references are documented in `docs/FRAME_CHAIN_PROVIDER_CONTRACT.md`; no dated live-instance probe exists. | Resolve Asset to a validated data URI and map to `image` / `first_frame_image`; fail closed for missing or corrupt frames. |
| Poll Video Task | GET /v1/videos/{task_id} | mock-only | Same 2026-10-23 mock-driver contract run: result URL is nested in `result.url`. | Exponential backoff + jitter |
| Media Download | GET /v1/media/{name} | mock-only | Same 2026-10-23 mock-driver contract run: same-origin download with the API key succeeds; mock bytes fail ffprobe by design. | Tải về storage, kiểm tra bằng ffprobe |
| Per-Instance Proxy | MUSE2API_BROWSER_PROXY | unverified | No dated real-instance probe is recorded in this repository. | Tạo nhiều ConnectionProfile trỏ tới các instance riêng |
| Per-Account Proxy (Single Instance) | N/A | unsupported | Not implemented by this integration; no upstream support claim is made. | Hiển thị rõ unsupported trên UI |
| Quota Lookup | N/A | unsupported | Not implemented by this integration; no upstream support claim is made. | Hiển thị unsupported trên UI |

## Verification rule

Use status verified only when a dated evidence artifact is committed under docs/evidence/ and identifies the probe date, instance/version, request/response outcome and limitations. The CI metadata checker requires both the date and the artifact path to exist. Source inspection, mock tests and dependency presence do not count as real-provider verification.
