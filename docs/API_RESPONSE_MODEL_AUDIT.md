# Audit hợp đồng response API

Tài liệu này theo dõi việc thu hẹp các response thành object/list tổng quát trong OpenAPI. Nguồn số liệu hiện tại là `apps/orchestrator/openapi.json`; chạy lại audit sau mỗi đợt thêm response model.

## Quy trình

- `python3 scripts/audit_openapi_responses.py`: đếm operation và in từng response thành công mà schema còn thiếu hoặc tổng quát. Chạy ở chế độ advisory trong giai đoạn di trú.
- `python3 scripts/audit_openapi_responses.py --strict`: gate cuối khi toàn bộ response body được kỳ vọng có schema cụ thể.
- `python3 scripts/audit_api_response_models.py --report --require-typed queue --require-typed batch`: kiểm tra khai báo `response_model` trực tiếp trên router bằng AST; đây là kiểm tra bổ sung, khác với audit schema đã sinh trong OpenAPI.
- `bash scripts/check_openapi.sh`: xác minh snapshot FastAPI và TypeScript schema đã sinh cùng khớp với source hiện tại.

Các endpoint trả `204 No Content` được bỏ qua bởi audit vì không có response body. Một endpoint chỉ được coi là typed khi schema mô tả được object fields, array items và enum thật; không dùng `dict[str, Any]` hoặc `Record<string, unknown>` để né contract gap.

## Thứ tự triển khai Phase 0c

| Lát cắt | Trạng thái | Điều kiện nghiệm thu |
|---|---|---|
| Queue tasks/takes + pipeline + batch/continuity | Đang triển khai trong PR #56 | Các queue task/take, script import, Story Bible, project shots, batch launch/tick/retry, compare A/B và continuity flag responses dùng response model; trạng thái và loại task xuất enum; OpenAPI contract tests bảo vệ hợp đồng |
| Workflow definition, graph/layout, run/approval | Một phần input validation trong PR #56; response DTO vẫn chưa triển khai | `run_mode` là enum Literal; `from_here`/`downstream` bắt buộc `start_node_id`; response DTO riêng cho graph, run và trạng thái node vẫn là bước sau |
| Timeline, export và asset-related routes | Chưa triển khai | Timeline sequence/manifest, export job lifecycle và URL/asset metadata có schema cụ thể |
| Operations: backup, maintenance, metrics, system | Chưa triển khai | Response JSON có model; file/text/204 endpoints khai báo response semantics tương ứng |
| Toàn bộ frontend API consumer | Chưa triển khai | Consumer migration route-by-route sang generated `components["schemas"]`/operation types; không giữ contract viết tay thay thế |

## Quy tắc

Không bắt đầu một UI feature chỉ vì endpoint tồn tại. Queue-monitor cần hợp đồng queue hoàn chỉnh và typed consumer; shot-board cần pipeline, queue, batch/takes đều đủ typed. Giữ `packages/contracts` đến khi mọi consumer đã được chuyển và kiểm tra không còn import cũ.
