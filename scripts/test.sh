#!/usr/bin/env bash
# Usage: scripts/test.sh [orchestrator|media|web|all] [pytest args]
# Pytest arguments apply only to a single pytest scope. The all scope always runs unfiltered suites.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
scope="$1"
if [ -z "$scope" ]; then scope=all; fi
if [ "$#" -gt 0 ]; then shift; fi
rc=0

if [ "$scope" = "all" ] && [ "$#" -gt 0 ]; then
  echo "pytest arguments cannot be combined with scope all; run one test scope at a time." >&2
  exit 2
fi
run_orchestrator() {
  echo "==> orchestrator tests"
  ( cd "$ROOT/apps/orchestrator" && REQUIRE_FFMPEG="$(printenv REQUIRE_FFMPEG 2>/dev/null || echo 1)" python3 -m pytest -q -rs "$@" ) || rc=1
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
  web|frontend)
    if [ "$#" -gt 0 ]; then echo "web scope does not accept pytest arguments" >&2; exit 2; fi
    run_web ;;
  all)                       run_orchestrator; run_media; run_web ;;
  *) echo "Unknown scope: $scope (use orchestrator|media|web|all)" >&2; exit 2 ;;
esac
[ "$rc" -eq 0 ] && echo "OK: all selected suites passed" || echo "FAIL: see output above"
exit "$rc"
