# Audit hợp đồng response API

Tài liệu này theo dõi việc thu hẹp các response thành object/list tổng quát trong OpenAPI. Nguồn số liệu hiện tại là `apps/orchestrator/openapi.json`; chạy lại audit sau mỗi đợt thêm response model.

## Quy trình

- `python3 scripts/audit_openapi_responses.py`: đếm operation và in từng response thành công mà schema còn thiếu hoặc tổng quát. Chạy ở chế độ advisory trong giai đoạn di trú.
- `python3 scripts/audit_openapi_responses.py --strict`: gate cuối khi toàn bộ response body được kỳ vọng có schema cụ thể.
- `python3 scripts/audit_api_response_models.py --report --require-typed queue --require-typed batch --require-typed workflows --require-typed timeline`: kiểm tra khai báo `response_model` trực tiếp trên các router đã được đưa vào gate; đây là kiểm tra bổ sung, khác với audit schema đã sinh trong OpenAPI.
- `bash scripts/check_openapi.sh`: xác minh snapshot FastAPI và TypeScript schema đã sinh cùng khớp với source hiện tại.

`ExportJobStatusOut` dùng `response_model_exclude_unset=True` để chỉ gửi các trường phụ thuộc trạng thái khi thực sự tồn tại (ví dụ `download_url`/`ffmpeg_args` sau khi thành công, hoặc `message` khi thất bại), tránh biến trường không áp dụng thành `null` trong API.

Audit kiểm tra mọi response 2xx được khai báo: nếu response không có `content`, `content` rỗng, hoặc media type không có schema cụ thể thì báo contract gap. Audit cũng resolve các local `#/components/responses/...` references; response reference không resolve được sẽ bị báo lỗi thay vì im lặng bỏ qua. Status `204 No Content` và `205 Reset Content` được miễn vì HTTP quy định không có response body. Không mặc định coi các response 200/201/202 thiếu `content` là hợp lệ; nếu endpoint chủ đích không có body, hãy khai báo status bodyless phù hợp. Một endpoint chỉ được coi là typed khi schema mô tả được object fields, array items và enum thật; không dùng `dict[str, Any]` hoặc `Record<string, unknown>` để né contract gap.

## Thứ tự triển khai Phase 0c

| Lát cắt | Trạng thái | Điều kiện nghiệm thu |
|---|---|---|
| Queue tasks/takes + pipeline + batch/continuity | Đã merge ở PR #56 | Các queue task/take, script import, Story Bible, project shots, batch launch/tick/retry, compare A/B và continuity flag responses dùng response model; trạng thái và loại task xuất enum; OpenAPI contract tests bảo vệ hợp đồng |
| Workflow definition, graph/layout, run/approval | Đã merge ở PR #57 | Response DTO cho workflow create, graph update, layout save, dry-run, run/node execution và approval; giữ nguyên các trường node/edge mở rộng; test OpenAPI và integration bảo vệ cả dry-run lẫn run mô phỏng |
| Timeline, export và asset-related routes | Đang triển khai trong PR #58 | Timeline sequence/manifest và export-job launch/status responses dùng DTO lồng nhau có type; trạng thái export là enum; integration test kiểm tra assemble, latest sequence, export launch/success; CI yêu cầu typed timeline router |
| Operations: backup, maintenance, metrics, system | Chưa triển khai | Response JSON có model; file/text/204 endpoints khai báo response semantics tương ứng |
| Toàn bộ frontend API consumer | Chưa triển khai | Consumer migration route-by-route sang generated `components["schemas"]`/operation types; không giữ contract viết tay thay thế |

## Quy tắc

Không bắt đầu một UI feature chỉ vì endpoint tồn tại. Queue-monitor cần hợp đồng queue hoàn chỉnh và typed consumer; shot-board cần pipeline, queue, batch/takes đều đủ typed. Giữ `packages/contracts` đến khi mọi consumer đã được chuyển và kiểm tra không còn import cũ.
