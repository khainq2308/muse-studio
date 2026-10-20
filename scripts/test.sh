#!/usr/bin/env bash
# Usage: scripts/test.sh [orchestrator|media|web|all] [pytest args]
# Pytest arguments apply only to a single pytest scope.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
scope=all
if [ "$#" -gt 0 ]; then scope="$1"; shift; fi
rc=0

if [ "$scope" = "all" ] && [ "$#" -gt 0 ]; then
  echo "pytest arguments cannot be combined with scope all; run one test scope at a time." >&2
  exit 2
fi
run_orchestrator() {
  echo "==> orchestrator tests"
  ( cd "$ROOT/apps/orchestrator" && PYTHONPATH="$ROOT/apps/media-engine${PYTHONPATH:+:$PYTHONPATH}" REQUIRE_FFMPEG="$(printenv REQUIRE_FFMPEG 2>/dev/null || echo 1)" python3 -m pytest -q -rs "$@" ) || rc=1
}
run_media() {
  echo "==> media-engine tests"
  ( cd "$ROOT/apps/media-engine" && PYTHONPATH=. python3 -m pytest -q "$@" ) || rc=1
}
run_web() {
  echo "==> Studio Web: deterministic install"
  if ! ( cd "$ROOT/apps/studio-web" && npm ci --no-audit --no-fund ); then rc=1; return; fi
  echo "==> Studio Web: lint + formatting"
  ( cd "$ROOT/apps/studio-web" && npm run lint && npm run format:check ) || rc=1
  echo "==> Studio Web: unit tests"
  ( cd "$ROOT/apps/studio-web" && npm test ) || rc=1
  echo "==> Studio Web: typecheck + production build"
  ( cd "$ROOT/apps/studio-web" && npm run build ) || rc=1
  if command -v npx >/dev/null 2>&1; then
    echo "==> Studio Web: Playwright Chromium smoke"
    ( cd "$ROOT/apps/studio-web" && npx playwright install chromium && npm run test:e2e ) || rc=1
  fi
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
