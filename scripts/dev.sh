#!/usr/bin/env bash
# Run the orchestrator API locally with SQLite and a temp media dir (no Docker needed).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/apps/orchestrator"
export ENVIRONMENT="${ENVIRONMENT:-development}"
export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///./musestudio_dev.db}"
export MEDIA_ROOT="${MEDIA_ROOT:-./storage/media}"
mkdir -p "$MEDIA_ROOT"
exec python3 -m uvicorn muse_orchestrator.main:app --reload --host 127.0.0.1 --port "${PORT:-8000}"
