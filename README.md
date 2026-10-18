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
    pip install -e ".[dev]" -e "../media-engine"
    pytest -q

## Security configuration

- Local development defaults are intentionally permissive when STUDIO_ADMIN_TOKEN is absent. Do not expose this mode to a network you do not trust.
- For production, set a long, secret STUDIO_ADMIN_TOKEN and a stable, non-default SECRET_ENCRYPTION_KEY (at least 32 characters); the application refuses to start with the development encryption key.
- Keep .env, database files and generated media private. Restrict CORS and configure HTTPS before remote access.

## Current implementation boundary

Project/connection/asset endpoints now use application services and a shared repository adapter. Production video-task completion extracts real boundary frames with FFmpeg/FFprobe; mock-provider queue tests use small valid placeholder frames and do not prove real visual continuity. Passing a local frame Asset ID to the provider is not sufficient unless the provider's reference-image contract explicitly accepts that form. Workflow DAG validation/cache behavior is present, but production ARQ dispatch/polling, persistent media-engine job execution and the remaining export features (title card, subtitles, audio mixing) remain roadmap work. Timeline export itself renders the real clips with FFmpeg and requires FFmpeg/FFprobe on the server. See docs/ARCHITECTURE.md, docs/IMPLEMENTATION_PLAN.md, and docs/MUSE2API_CAPABILITY_MATRIX.md.
