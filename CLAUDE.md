# MuseStudio — hướng dẫn cho coding agent

Nền tảng điều phối sản xuất video AI: Script → Story Bible → Shot Board → Workflow DAG → Batch render (`muse2api`) → FFmpeg timeline export.
Monorepo: `apps/orchestrator` (FastAPI), `apps/studio-web` (React/Vite), `apps/media-engine` (FFprobe/FFmpeg utils), `packages/contracts` (TS types), `docs/`, `ops/`.

Trả lời người dùng bằng **tiếng Việt**. Code, tên biến, commit message dùng tiếng Anh.

## Lệnh thường dùng

| Việc | Lệnh |
|---|---|
| Chạy toàn bộ test | `scripts/test.sh all` |
| Test orchestrator | `scripts/test.sh orchestrator [pytest args]` (vd: `-k queue -x`) |
| Test media-engine | `scripts/test.sh media` |
| Typecheck + build web | `scripts/test.sh web` |
| Lint (ruff) | `scripts/lint.sh` (thêm `--fix` để tự sửa) |
| Kiểm tra layering | `python3 scripts/check_layering.py` |
| Kiểm tra docs ↔ thực tế | `python3 scripts/check_docs_sync.py` |
| Kiểm tra môi trường | `scripts/doctor.sh` |
| Chạy API dev (SQLite) | `scripts/dev.sh` |

CI (`.github/workflows/ci.yml`) chạy: pytest orchestrator với `REQUIRE_FFMPEG=1`, pytest media-engine, `npm run build` cho web. Một thay đổi chưa xong khi các lệnh tương ứng chưa xanh.

## Kiến trúc backend (bắt buộc tuân thủ)

Hướng phụ thuộc: **`api` → `application` → `infrastructure`**.

- `api/*.py`: chỉ HTTP — schema Pydantic, status code, serialize DTO. **Không** viết `select(...)`, `db.execute/add/commit` ở đây cho code mới.
- `application/*.py`: use case, quy tắc nghiệp vụ, ném `ApplicationError(status, detail)`.
- `infrastructure/repositories.py`: mọi truy vấn SQLAlchemy và ranh giới transaction.
- `services/`: logic lõi (queue, workflow engine, timeline, backup...). `providers/`: adapter ngoài (muse2api, mock).
- Dependency injection qua `api/dependencies.py`.
- Code cũ ở `api/pipeline.py`, `workflows.py`, `queue.py`, `batch.py`, `timeline.py` còn truy vấn DB trực tiếp — **đừng thêm** vào đó; khi chạm vào, ưu tiên đẩy xuống tầng dưới. `scripts/check_layering.py` dùng baseline để chặn vi phạm mới.

## Quy tắc không được phá

1. **Không bao giờ báo thành công giả.** Export không verify được bằng FFprobe → job `FAILED`, không tạo asset. Node workflow chưa có executor thật → nhãn `simulated/placeholder`.
2. **Bí mật:** API key mã hoá bằng `SecretVault`; log/response/exception phải đi qua `SecretRedactor`. Không log key, cookie, proxy credential. Không đọc/ghi `.env`.
3. **Tải media từ provider:** chỉ gửi key tới đúng origin của provider; không nới lỏng kiểm tra redirect/scheme/path traversal trong `Muse2ApiAdapter.download_result`.
4. **FFmpeg/FFprobe:** luôn `subprocess` với argv list, **không** `shell=True`, luôn có timeout, kiểm tra returncode, không nội suy input người dùng vào filtergraph chưa escape.
5. **Auth:** mọi router sản phẩm gắn `Depends(verify_single_admin)` ở `main.py`; chỉ `/healthz` public.
6. **Production fail-closed:** ngoài dev/test phải có `STUDIO_ADMIN_TOKEN` và `SECRET_ENCRYPTION_KEY` ≥32 ký tự khác mặc định.
7. **Schema DB:** hiện dùng `create_all` + `_ensure_schema_compatibility` (chưa có Alembic). Không thêm `ALTER TABLE` thủ công mới — dùng skill `alembic-migrations` (bootstrap Alembic trước nếu chưa có).
8. **Không commit** vào `main`, không `git push --force`. Làm việc trên nhánh `feat/*`, `fix/*`, `refactor/*`.

## Test

- Pytest async (`asyncio_mode=auto`), DB SQLite in-memory + `StaticPool`, override `get_db` (xem fixture `client` trong `tests/test_m0_m1_suite.py`).
- Chưa có `conftest.py` — fixture `client` đang bị lặp ở nhiều file. Khi viết test mới, ưu tiên dùng/tạo `tests/conftest.py` thay vì sao chép thêm.
- `with_for_update()` là no-op trên SQLite → test tranh chấp worker phải mô phỏng bằng logic, không dựa vào lock.
- Test render cần FFmpeg thật; đặt `REQUIRE_FFMPEG=1` để thiếu FFmpeg thì fail thay vì skip.
- Provider `mock` bỏ qua verify media; test đường FFprobe thật bằng test riêng.

## Khoảng trống đã biết (đừng giả vờ là đã xong)

- Frontend chỉ có dashboard 1 file; chưa có Workflow canvas, Shot board, Timeline editor. `packages/contracts` chưa được dùng.
- Chưa có service ARQ worker trong `docker-compose.yml` dù có `workers/queue_worker.py`.
- Chưa có Alembic migrations.
- Boundary frame trong `generation_queue.py` là PNG giả; `media-engine` chưa được nối vào.
- Capability matrix muse2api gần như toàn `mock-only/unverified`.
- Export chưa hỗ trợ audio clip, title card, phụ đề, nhiều track audio.

Chi tiết và cách xử lý từng khoảng trống: lệnh `/close-gap`.

## Bộ công cụ agent trong repo

- Sub-agents: `.claude/agents/` — `backend-dev`, `frontend-dev`, `test-writer`, `code-reviewer`, `security-auditor`, `migration-engineer`, `media-engineer`, `docs-sync`.
- Commands: `/test`, `/review`, `/new-endpoint`, `/migrate`, `/layering`, `/close-gap`, `/audit-roadmap`, `/fix-ci`, `/ship`.
- Skills: `add-api-endpoint`, `alembic-migrations`, `orchestrator-testing`, `safe-ffmpeg`, `provider-adapter`, `frontend-feature`.
- Hooks (`.claude/settings.json`): chặn lệnh nguy hiểm, bảo vệ file nhạy cảm, quét secret, tự `ruff` sau khi sửa, cổng test trước khi dừng.
