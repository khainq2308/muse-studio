# MuseStudio — AI Video Production Studio (`muse2api`)

Nền tảng điều phối sản xuất phim AI từ kịch bản (Script) -> Story Bible -> Shot Board -> Workflow DAG -> Batch Render (`muse2api`) -> FFmpeg Timeline Export.

## Kiến trúc Monorepo
- `apps/studio-web`: Giao diện điều khiển trung tâm (React + TypeScript + Vite).
- `apps/orchestrator`: Bộ não điều phối (FastAPI + SQLAlchemy 2.0 Async + ARQ + DAG Engine + Prompt Compiler).
- `apps/media-engine`: Xử lý hậu kỳ FFmpeg, kiểm định `ffprobe` và trích xuất Boundary Frames (`first_frame` / `last_frame`).
- `packages/contracts`: Shared TypeScript & OpenAPI schemas.
- `docs/`: Tài liệu PRD, Capability Matrix, Architecture, ADRs và Test Strategy.
- `ops/`: Công cụ vận hành và script probe `muse2api`.

## Quick Start
```bash
# Chạy bộ kiểm thử M0 & M1
cd apps/orchestrator && pip install -e ".[dev]" && pytest -v

# Hoặc khởi chạy bằng Docker Compose
docker compose up -d --build
```
