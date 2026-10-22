#!/usr/bin/env bash
# Usage: scripts/lint.sh [--fix|--all|--python-only]
# Python correctness checks (whole apps/orchestrator + apps/media-engine tree, plus changed
# Python files elsewhere), frontend lint/format, and the DB layering ratchet.
# --all is accepted for compatibility and equals the default.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
command -v ruff >/dev/null || { echo "ruff not installed: pip install ruff" >&2; exit 127; }
cd "$ROOT"
rc=0
python_only=0
mode="${1:-}"
# One committed Ruff configuration (correctness rules E4/E7/E9/F) is applied to every Python
# file so results do not depend on Ruff's version-specific defaults. CI pins the Ruff version
# locked in apps/orchestrator/uv.lock.
RUFF_CONFIG="apps/orchestrator/pyproject.toml"
case "$mode" in
  --python-only)
    python_only=1
    shift
    ;;
  --fix)
    ruff check --config "$RUFF_CONFIG" --fix apps/orchestrator apps/media-engine || rc=1
    ruff format apps/orchestrator apps/media-engine || rc=1
    shift
    ;;
  --all|"")
    [ -n "$mode" ] && shift
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

if [ "$mode" != "--fix" ]; then
  echo "==> Ruff correctness check on the whole Python tree ($(ruff --version))"
  ruff check --config "$RUFF_CONFIG" apps/orchestrator apps/media-engine || rc=1

  # Changed Python files elsewhere in the repository (scripts, ops, hooks) get the same rules.
  base="$(printenv LINT_BASE_REF 2>/dev/null || echo origin/main)"
  temp_list="$(mktemp "${TMPDIR:-/tmp}/musestudio-lint-files.XXXXXX")"
  {
    if git rev-parse --verify "$base" >/dev/null 2>&1; then
      git diff --name-only --diff-filter=ACMR "$base...HEAD" 2>/dev/null || true
    fi
    git diff --name-only --diff-filter=ACMR
    git diff --cached --name-only --diff-filter=ACMR
    git ls-files --others --exclude-standard
  } | sort -u | while IFS= read -r file; do
    case "$file" in
      */skills/*|apps/orchestrator/*|apps/media-engine/*) ;;
      *.py) [ -f "$file" ] && printf '%s\n' "$file" ;;
    esac
  done > "$temp_list"
  if [ -s "$temp_list" ]; then
    count="$(wc -l < "$temp_list" | tr -d ' ')"
    echo "Ruff correctness check on $count changed Python file(s) outside apps/."
    failed=0
    while IFS= read -r file; do
      [ -n "$file" ] || continue
      ruff check --config "$RUFF_CONFIG" "$file" || failed=1
    done < "$temp_list"
    [ "$failed" -eq 0 ] || rc=1
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
