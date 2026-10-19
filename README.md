# MuseStudio — AI Video Production Studio

Nền tảng điều phối sản xuất phim AI từ kịch bản (Script) -> Story Bible -> Shot Board -> Workflow DAG -> Batch Render (`muse2api`) -> FFmpeg Timeline Export.

## Monorepo

- apps/studio-web: Giao diện React + TypeScript + Vite.
- apps/orchestrator: FastAPI, application services/repositories, SQLAlchemy async, provider adapters và typed workflow DAG.
- apps/media-engine: FFprobe validation và FFmpeg boundary-frame utilities.
- packages/contracts: Shared TypeScript contracts.
- docs: Architecture, decisions, implementation plan, capability matrix và test strategy.
- ops: Công cụ vận hành và script probe muse2api.

## Quick start

Từ repo root, khởi chạy Docker Compose:

    docker compose up -d --build

Hoặc cài package và chạy bộ kiểm thử orchestrator:

    cd apps/orchestrator
    pip install -e "../media-engine"
    pip install -e ".[dev]"
    pytest -q

Orchestrator dùng package sibling `muse-media`; vì package này chưa được publish lên package index, hãy cài `apps/media-engine` trước khi cài orchestrator để dependency metadata được thỏa mãn bằng bản local.

## Database migrations

Schema được quản lý qua Alembic; application startup chạy `upgrade head`. Revision đầu tiên là bootstrap bridge để database từ các phiên bản dùng `create_all` có thể được nhận diện mà không xoá dữ liệu. Nó dùng snapshot cố định `muse_orchestrator/models/schema_baseline_v1.py` để tạo bảng còn thiếu, sau đó áp dụng compatibility repair đã đóng băng ngay trong revision. **Không sửa revision bootstrap hoặc snapshot sau khi đã phát hành.** Bất kỳ thay đổi schema mới nào phải nằm trong revision mới; không thêm model vào snapshot.

Khi thay đổi model/schema, tạo revision mới, đọc lại SQL và kiểm tra cả đường upgrade lẫn downgrade:

```sh
cd apps/orchestrator
alembic revision --autogenerate -m "describe schema change"
alembic upgrade head
```

Không dựa vào `Base.metadata.create_all` để cập nhật schema đã tồn tại; mọi thay đổi schema mới phải có migration revision riêng. Lệnh Alembic sử dụng `DATABASE_URL` trong môi trường hoặc giá trị từ cấu hình local.

## Security configuration

- Local development defaults are intentionally permissive when STUDIO_ADMIN_TOKEN is absent. Do not expose this mode to a network you do not trust.
- For production, set a long, secret STUDIO_ADMIN_TOKEN and a stable, non-default SECRET_ENCRYPTION_KEY (at least 32 characters); the application refuses to start with the development encryption key.
- Keep .env, database files and generated media private. Restrict CORS and configure HTTPS before remote access.

## Current implementation boundary

Project/connection/asset endpoints now use application services and a shared repository adapter. Production video-task completion extracts real boundary frames with FFmpeg/FFprobe; before dispatch, the queue resolves a first-frame Asset to a local image, validates its project ownership, size, MIME signature and SHA-256, then sends a data URI rather than an internal Asset ID. The adapter emits both documented muse2api frame aliases (`image` and `first_frame_image`) and both aspect-ratio aliases (`size` and `aspect_ratio`) to support known request-schema variants. This is source-contract compatibility only: live frame-conditioned video generation still requires a dated real-instance probe; mock-provider tests do not prove visual continuity. Generation tasks are persisted in the database and dispatched through ARQ/Redis, with worker polling/backoff and a periodic recovery scan for queued/running tasks. The orchestrator worker runs as a separate Docker Compose service. Workflow DAG validation/cache behavior is present; persistent media-engine job execution and the remaining export features (title card, subtitles, audio mixing) remain roadmap work. Timeline export itself renders the real clips with FFmpeg and requires FFmpeg/FFprobe on the server. See docs/ARCHITECTURE.md, docs/IMPLEMENTATION_PLAN.md, docs/MUSE2API_CAPABILITY_MATRIX.md, and docs/FRAME_CHAIN_PROVIDER_CONTRACT.md.
