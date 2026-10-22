# Test Strategy

Tài liệu này mô tả các lớp kiểm thử **đang tồn tại** trong repo và những gì chúng **không** chứng minh. Một lớp test pass chỉ chứng minh điều nó thực sự kiểm tra; không suy ra năng lực provider, hành vi PostgreSQL/Redis thật hay giao diện đúng về mặt thị giác từ các lớp này.

## Lệnh chạy

- `scripts/test.sh all` — orchestrator, media-engine, studio-web và kiểm tra OpenAPI drift. Script ưu tiên `apps/orchestrator/.venv` (tạo bằng `uv sync --locked --project apps/orchestrator --extra dev`) và mặc định đặt `REQUIRE_FFMPEG=1` cho orchestrator.
- `scripts/test.sh orchestrator -k "<pattern>" -x` — chạy tập trung.
- CI dùng cùng dependency đã khoá (`uv sync --locked`, `npm ci`); xem `.github/workflows/ci.yml` và mục Continuous integration trong [ARCHITECTURE.md](ARCHITECTURE.md#continuous-integration).

## Orchestrator (pytest, `apps/orchestrator/tests`)

- **Unit & domain:** `SecretRedactor`, `PromptCompiler`/linter (`BLOCKED_REFERENCE`, `CAMERA_MOTION_CONFLICT`), `WorkflowEngine` (chặn cycle, Merkle cache), script parser (giới hạn, warnings), payload profile config (`compat` bị từ chối ngoài development).
- **API integration:** FastAPI + `httpx.AsyncClient` trên SQLite (`sqlite+aiosqlite`) với `MEDIA_ROOT` tạm. Bao gồm projects/revisions, connections (mã hoá Fernet), assets (magic bytes, xoá 409 khi còn tham chiếu), script import (`replace_existing`, 409), queue/batch/continuity, timeline/export, backup/restore, maintenance, system, error envelope, `X-Request-ID`, `/readyz`, CORS và guard Host/Origin của chế độ dev.
- **SQLite có foreign key:** listener `connect` trong `core/database.py` bật `PRAGMA foreign_keys=ON` cho mọi kết nối SQLite để thứ tự insert và `ON DELETE` giống PostgreSQL. SQLite vẫn bỏ qua `SELECT ... FOR UPDATE`, nên test không chứng minh được loại trừ lẫn nhau giữa các worker.
- **Queue/worker:** gọi trực tiếp `GenerationQueueService.step_task` và các ARQ job function với Redis pool giả; kiểm tra guard enqueue/dispatch, request snapshot, `stale_conditioning_frame`, `dispatch_outcome_unknown`, schedule token, giới hạn recovery, huỷ export.
- **Provider:** `MockVideoProvider` và `httpx.MockTransport` để mô phỏng phản hồi muse2api, redirect, 401/429/5xx và URL kết quả độc hại. Đây là kiểm tra hợp đồng nguồn, không phải bằng chứng provider thật.
- **FFmpeg integration:** các test export và trích xuất boundary frame sinh video bằng `ffmpeg -f lavfi` trong `tmp_path` rồi kiểm tra bằng FFprobe. Khi `REQUIRE_FFMPEG=1` (CI và `scripts/test.sh`), thiếu ffmpeg/ffprobe làm test **fail**; khi biến này không bằng `1`, test được skip.
- **Migration:** `test_database_migrations.py` chạy `alembic upgrade head` hai lần trên SQLite rỗng (idempotent), kiểm tra lịch sử revision tuyến tính với head mong đợi, so sánh schema sau migration với `Base.metadata` bằng `alembic.autogenerate.compare_metadata` (không được có drift), và xác nhận revision bootstrap dùng snapshot đóng băng thay vì model hiện tại. Các test này chạy trên SQLite; phiên bản PostgreSQL của các kiểm tra migration nằm trong mục PostgreSQL integration bên dưới.
- **OpenAPI:** snapshot `apps/orchestrator/openapi.json` phải khớp app (`scripts/export_openapi.py --check`), `audit_openapi_responses.py --strict` không cho phép success response tổng quát, và `audit_api_response_models.py` yêu cầu 13 router đều typed.

## PostgreSQL integration (`apps/orchestrator/tests/test_postgres_integration.py`)

- **Kích hoạt:** module bị skip nếu không có `TEST_POSTGRES_URL` (`postgresql+asyncpg://...`). Khi `REQUIRE_POSTGRES=1`, thiếu URL làm collection **fail** thay vì skip. Job CI `postgres-integration` dùng service container `postgres:16` (health-check `pg_isready`), đặt cả hai biến và fail nếu JUnit có bất kỳ skip nào ngoài các marker `xfail(strict=True)` cho bug đã biết.
- **Cô lập:** mỗi test tạo database riêng `muse_it_<hex>` trên server đó, dựng schema bằng `alembic upgrade head` thật (`core.database._run_alembic_upgrade`, không dùng `create_all`) và xoá chính database tạm đó khi kết thúc. Database ghi trong URL chỉ dùng làm kết nối quản trị. App được gọi qua `httpx.ASGITransport` với `get_db` override sang engine PostgreSQL, settings dev (không admin token) và ARQ pool giả.
- **Migration:** `compare_metadata` sau `upgrade head` không có drift trên PostgreSQL; `upgrade head` lặp lại idempotent và partial unique index `uq_generation_tasks_active_shot_type` tồn tại với mệnh đề `WHERE`; dữ liệu trùng được seed ở revision `0001_bootstrap` (workflow version, Story Bible code name, hai task active cùng shot) được `0002_uniqueness` sửa đúng trước khi thêm constraint.
- **Đồng thời thật (session/connection riêng cho mỗi actor):** hai `enqueue_shot_generation` đồng thời (`force_retry` và thường, prompt hash khác nhau) cho cùng shot → đúng một task active, bên thua nhận task thắng, không lỗi; insert task active thứ hai bị partial unique index từ chối với SQLSTATE 23505; hai `step_task` đồng thời trên cùng task QUEUED → đúng một lần `generate_video` (lease + `FOR UPDATE`), trong đó lời gọi provider được giữ mở tới khi đối thủ chạy xong; `max_concurrency=1` với hai task → tối đa một task RUNNING tại thời điểm dispatch; sáu `PUT .../graph` đồng thời → version 2..7 liên tục và `current_version` trỏ tới version tồn tại; năm upsert Story Bible đồng thời cùng `code_name` → một hàng, không 5xx.
- **End-to-end:** batch render bằng `MockVideoProvider` → `timeline/assemble` qua API; backup → download → restore giữ nguyên số hàng theo manifest (thứ tự FK trên PostgreSQL).
- **Mapping lỗi asyncpg:** unique violation thật (23505) đi qua handler của app thành 409 `conflict` và không echo tham số; FK violation thật không bị coi là conflict (500); `POST /connections` thua race trên UNIQUE (pre-check bị vô hiệu hoá trong test) → 409.
- **Lỗi dữ liệu (SQLSTATE lớp 22):** SQLAlchemy + asyncpg dịch lỗi lớp 22 (ví dụ 22001 `value too long`) thành `sqlalchemy.exc.DBAPIError` chung chứ không phải `DataError`; `main.py` phân loại theo SQLSTATE nên lỗi này trả 422 envelope. Hai test `test_pg_*overlong*` chứng minh điều đó trên PostgreSQL thật.
- **Giới hạn:** không có Redis/ARQ thật; worker được mô phỏng bằng lời gọi `step_task` trực tiếp. Interleaving race được ép bằng `asyncio.Event` (không sleep) nhưng chỉ chứng minh các interleaving đã viết, không phải mọi lịch trình có thể.

## Media engine (`apps/media-engine`)

Unit test cho `muse_media` với subprocess được mock. Integration thật với FFmpeg nằm trong suite orchestrator ở trên.

## Studio Web (`apps/studio-web`)

- **Vitest + Testing Library:** test cho API client (timeout, chuẩn hoá lỗi), query hooks, logic polling của Queue Monitor, route deep link/404 và các component có trạng thái. `msw` đã có trong `devDependencies` nhưng hiện chưa có test nào trong `src/` sử dụng nó; handler sinh từ OpenAPI theo `docs/FRONTEND_ARCHITECTURE.md` §7 vẫn là việc cần làm.
- **Playwright:** chạy trên Vite production preview với API được mock bằng `page.route()` — deep link Queue Monitor dùng request cùng origin, module không hợp lệ và route lạ trả trang 404, project không tồn tại hiển thị trạng thái not-found. Không có backend thật trong các test này.
- **Lint/format/build:** ESLint, Prettier check, `tsc` + `vite build`.

## Production image smoke

`scripts/test_nginx_prod.sh` chạy production Nginx image thật cạnh một container API mock trong Docker network riêng: SPA fallback cho route lồng nhau, proxy `/api` giữ nguyên đường dẫn tuyệt đối, giới hạn body (10 MiB mặc định, lớn hơn chỉ cho upload/restore), API response không cache và giữ security headers, SPA shell tương thích CSP nghiêm ngặt, xử lý static asset. CI còn validate `docker-compose.prod.yml` và build production images.

## Kiểm tra dependency

- `pip-audit` trên dependency production đã khoá của orchestrator (blocking trong CI; không gồm dev extras và package local `muse-media`).
- `npm audit --omit=dev --audit-level=high` cho dependency production của frontend.

## Không được bao phủ

`.github/workflows/ci.yml` chạy PostgreSQL thật chỉ trong job `postgres-integration` (xem mục PostgreSQL integration ở trên) và không có job nào chạy Redis thật. Các hạng mục sau **chưa có bằng chứng tự động**:

- PostgreSQL ngoài các kịch bản trong `test_postgres_integration.py`: nhiều tiến trình worker/API thật cạnh tranh nhau, tải lớn, migration trên bản sao dữ liệu production và các interleaving race chưa được viết thành test. Mapping lỗi 22xxx → 422 hiện **sai** trên PostgreSQL (bug đã biết ở trên).
- Redis/ARQ thật: lập lịch, dedupe job id, cron recovery và huỷ job trong một worker thật.
- Provider muse2api thật, kể cả first-frame conditioning (xem [MUSE2API_CAPABILITY_MATRIX.md](MUSE2API_CAPABILITY_MATRIX.md) và [FRAME_CHAIN_PROVIDER_CONTRACT.md](FRAME_CHAIN_PROVIDER_CONTRACT.md)).
- Kiểm tra trực quan giao diện; ảnh chụp Queue Monitor do CI upload chỉ là artifact tham khảo.
- Backup tạo trên một deployment và restore trên deployment khác.

`scripts/audit_roadmap.py` là heuristic điều hướng và `scripts/check_docs_sync.py` chỉ validate metadata tài liệu; cả hai không phải bằng chứng nghiệm thu hành vi.
