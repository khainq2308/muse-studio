#!/usr/bin/env bash
# Usage: scripts/test.sh [orchestrator|media|web|all] [extra pytest args...]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
scope="${1:-all}"; shift || true
rc=0

run_orchestrator() {
  echo "==> orchestrator tests"
  ( cd "$ROOT/apps/orchestrator" && REQUIRE_FFMPEG="${REQUIRE_FFMPEG:-1}" python3 -m pytest -q -rs "$@" ) || rc=1
}
run_media() {
  echo "==> media-engine tests"
  ( cd "$ROOT/apps/media-engine" && PYTHONPATH=. python3 -m pytest -q "$@" ) || rc=1
}
run_web() {
  echo "==> studio-web typecheck + build"
  ( cd "$ROOT/apps/studio-web" && { [ -d node_modules ] || npm install --no-audit --no-fund; } && npm run build ) || rc=1
}

case "$scope" in
  orchestrator|orch|backend) run_orchestrator "$@" ;;
  media)                     run_media "$@" ;;
  web|frontend)              run_web ;;
  all)                       run_orchestrator "$@"; run_media; run_web ;;
  *) echo "Unknown scope: $scope (use orchestrator|media|web|all)" >&2; exit 2 ;;
esac

[ "$rc" -eq 0 ] && echo "OK: all selected suites passed" || echo "FAIL: see output above"
exit "$rc"
