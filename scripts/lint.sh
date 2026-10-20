#!/usr/bin/env bash
# Usage: scripts/lint.sh [--fix|--all|--python-only]
# Python correctness checks, frontend lint/format, and the DB layering ratchet.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
command -v ruff >/dev/null || { echo "ruff not installed: pip install ruff" >&2; exit 127; }
cd "$ROOT"
rc=0
python_only=0
mode="${1:-}"
case "$mode" in
  --python-only)
    python_only=1
    shift
    ;;
  --fix)
    ruff check --fix apps/orchestrator apps/media-engine || rc=1
    ruff format apps/orchestrator apps/media-engine || rc=1
    shift
    ;;
  --all)
    ruff check apps/orchestrator apps/media-engine || rc=1
    shift
    ;;
  "")
    ;;
  *)
    echo "Unknown option: $mode (use --fix, --all, or --python-only)" >&2
    exit 2
    ;;
esac
if [ "$#" -gt 0 ]; then
  echo "Unexpected arguments: $*" >&2
  exit 2
fi

if [ "$mode" != "--fix" ] && [ "$mode" != "--all" ] && [ "$python_only" -eq 0 ]; then
  base="$(printenv LINT_BASE_REF 2>/dev/null || echo origin/main)"
  temp_list="/tmp/musestudio-lint-files-$$"
  {
    if git rev-parse --verify "$base" >/dev/null 2>&1; then
      git diff --name-only --diff-filter=ACMR "$base...HEAD" 2>/dev/null || true
    fi
    git diff --name-only --diff-filter=ACMR
    git diff --cached --name-only --diff-filter=ACMR
    git ls-files --others --exclude-standard
  } | sort -u | while IFS= read -r file; do
    case "$file" in
      */skills/*) ;;
      *.py) printf '%s\n' "$file" ;;
    esac
  done > "$temp_list"
  if [ -s "$temp_list" ]; then
    count="$(wc -l < "$temp_list" | tr -d ' ')"
    echo "Ruff correctness check on $count changed Python file(s)."
    failed=0
    while IFS= read -r file; do
      [ -n "$file" ] || continue
      ruff check --select E4,E7,E9,F "$file" || failed=1
    done < "$temp_list"
    [ "$failed" -eq 0 ] || rc=1
  else
    echo "No changed Python files; skipping Ruff check."
  fi
  rm -f "$temp_list"
fi

if [ "$python_only" -eq 0 ]; then
  echo "==> Studio Web lint and formatting"
  if [ -x apps/studio-web/node_modules/.bin/eslint ] &&
     [ -x apps/studio-web/node_modules/.bin/prettier ]; then
    echo "Using existing Studio Web dependencies (no destructive reinstall)."
    ( cd apps/studio-web && npm run lint && npm run format:check ) || rc=1
  elif [ -d apps/studio-web/node_modules ]; then
    echo "Studio Web node_modules exists but is incomplete; refusing to delete it with npm ci." >&2
    echo "Repair dependencies explicitly with 'cd apps/studio-web && npm ci' when network access is available." >&2
    rc=1
  elif ( cd apps/studio-web && npm ci --no-audit --no-fund ); then
    ( cd apps/studio-web && npm run lint && npm run format:check ) || rc=1
  else
    echo "Frontend dependency install failed; skipping lint and format checks." >&2
    rc=1
  fi
else
  echo "Skipping frontend npm install/lint; frontend-build job owns that gate."
fi

python3 scripts/check_layering.py || rc=1
exit "$rc"
