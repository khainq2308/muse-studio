#!/usr/bin/env bash
# Usage: scripts/lint.sh [--fix]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v ruff >/dev/null || { echo "ruff not installed: pip install ruff" >&2; exit 127; }
cd "$ROOT"
rc=0
if [ "${1:-}" = "--fix" ]; then
  ruff check --fix apps/orchestrator apps/media-engine || rc=1
  ruff format apps/orchestrator apps/media-engine
else
  ruff check apps/orchestrator apps/media-engine || rc=1
  ruff format --check apps/orchestrator apps/media-engine || rc=1
fi
python3 scripts/check_layering.py || rc=1
exit "$rc"
