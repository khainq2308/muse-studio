#!/usr/bin/env bash
# Usage: scripts/check_openapi.sh
# Verify the committed OpenAPI snapshot and generated frontend TypeScript schema.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required; see scripts/doctor.sh for repository prerequisites." >&2
  exit 127
}
command -v npm >/dev/null 2>&1 || {
  echo "npm is required; install Node.js before checking frontend contracts." >&2
  exit 127
}

echo "==> Check committed OpenAPI snapshot"
uv run --locked --project "$ROOT/apps/orchestrator" --extra dev python "$ROOT/scripts/export_openapi.py" --check

echo "==> Check generated Studio Web API types"
npm --prefix "$ROOT/apps/studio-web" run gen:api:check
