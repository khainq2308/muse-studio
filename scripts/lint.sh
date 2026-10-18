#!/usr/bin/env bash
# Usage: scripts/lint.sh [--fix]
# The legacy app tree currently has formatting debt; default CI gates Ruff diagnostics and layering.
# Use --fix deliberately to apply Ruff's autofix and formatter to the full app tree.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
command -v ruff >/dev/null || { echo "ruff not installed: pip install ruff" >&2; exit 127; }
cd "$ROOT"
rc=0
if [ "$1" = "--fix" ]; then
  ruff check --fix apps/orchestrator apps/media-engine || rc=1
  ruff format apps/orchestrator apps/media-engine || rc=1
else
  ruff check apps/orchestrator apps/media-engine || rc=1
  echo "NOTE: whole-tree ruff format --check is deferred; legacy tree currently contains formatting debt." >&2
fi
python3 scripts/check_layering.py || rc=1
exit "$rc"
