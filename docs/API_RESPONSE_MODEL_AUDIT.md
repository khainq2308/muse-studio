# Audit hợp đồng response API

Tài liệu này theo dõi việc thu hẹp các response thành object/list tổng quát trong OpenAPI. Nguồn số liệu hiện tại là `apps/orchestrator/openapi.json`; chạy lại audit sau mỗi đợt thêm response model.

## Quy trình

- `python3 scripts/audit_openapi_responses.py`: đếm operation và in từng response thành công mà schema còn thiếu hoặc tổng quát. Chạy ở chế độ advisory trong giai đoạn di trú.
- `python3 scripts/audit_openapi_responses.py --strict`: gate cuối khi toàn bộ response body được kỳ vọng có schema cụ thể.
- `python3 scripts/audit_api_response_models.py --report --require-typed projects --require-typed connections --require-typed assets --require-typed pipeline --require-typed workflows --require-typed queue --require-typed batch --require-typed timeline --require-typed connection_accounts --require-typed metrics --require-typed maintenance --require-typed backups --require-typed system`: kiểm tra khai báo `response_model` trực tiếp trên cả 13 router; đây là kiểm tra bổ sung, khác với audit schema đã sinh trong OpenAPI.
- `bash scripts/check_openapi.sh`: xác minh snapshot FastAPI và TypeScript schema đã sinh cùng khớp với source hiện tại.

`ExportJobStatusOut` dùng `response_model_exclude_unset=True` để chỉ gửi các trường phụ thuộc trạng thái khi thực sự tồn tại (ví dụ `download_url`/`ffmpeg_args` sau khi thành công, hoặc `message` khi thất bại), tránh biến trường không áp dụng thành `null` trong API.

Audit kiểm tra mọi response 2xx được khai báo: nếu response không có `content`, `content` rỗng, hoặc media type không có schema cụ thể thì báo contract gap. Audit cũng resolve các local `#/components/responses/...` references; response reference không resolve được sẽ bị báo lỗi thay vì im lặng bỏ qua. Status `204 No Content` và `205 Reset Content` được miễn vì HTTP quy định không có response body. Không mặc định coi các response 200/201/202 thiếu `content` là hợp lệ; nếu endpoint chủ đích không có body, hãy khai báo status bodyless phù hợp. Một endpoint chỉ được coi là typed khi schema mô tả được object fields, array items và enum thật; không dùng `dict[str, Any]` hoặc `Record<string, unknown>` để né contract gap.

## Thứ tự triển khai Phase 0c

| Lát cắt | Trạng thái | Điều kiện nghiệm thu |
|---|---|---|
| Queue tasks/takes + pipeline + batch/continuity | Đã merge ở PR #56 | Các queue task/take, script import, Story Bible, project shots, batch launch/tick/retry, compare A/B và continuity flag responses dùng response model; trạng thái và loại task xuất enum; OpenAPI contract tests bảo vệ hợp đồng |
| Workflow definition, graph/layout, run/approval | Đã merge ở PR #57 | Response DTO cho workflow create, graph update, layout save, dry-run, run/node execution và approval; giữ nguyên các trường node/edge mở rộng; test OpenAPI và integration bảo vệ cả dry-run lẫn run mô phỏng |
| Timeline, export và asset-related routes | Đã merge ở PR #58 | Timeline sequence/manifest và export-job launch/status responses dùng DTO lồng nhau có type; trạng thái export là enum; integration test bảo vệ assemble, latest sequence, export status; CI yêu cầu typed timeline router |
| Operations: backup, maintenance, metrics, system | Đã merge ở PR #59 | Restore report, orphan scan/cleanup, metrics summary, structured logs và diagnostics dùng DTO lồng nhau; JSON tùy biến dùng kiểu JSON đệ quy; endpoint file/text/204 được giữ đúng semantics |
| Frontend API consumer migration | Đang triển khai trong PR #60 | Các query hiện có dùng response suy từ generated OpenAPI `paths`; không giữ response DTO viết tay; type assertions bảo vệ project/connection/health GET responses |

## Quy tắc

Không bắt đầu một UI feature chỉ vì endpoint tồn tại. Queue-monitor cần hợp đồng queue hoàn chỉnh và typed consumer; shot-board cần pipeline, queue, batch/takes đều đủ typed. `packages/contracts` đã được xóa trong PR #60 sau khi xác minh không còn import/reference; không tạo lại package và không khai báo response types viết tay thay thế generated OpenAPI.
