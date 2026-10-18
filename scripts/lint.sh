#!/usr/bin/env bash
# Usage: scripts/lint.sh [--fix|--all]
# Default: lint changed Python files under apps/ and run the layering ratchet.
# The current legacy application tree has pre-existing Ruff debt; --all reports it.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
command -v ruff >/dev/null || { echo "ruff not installed: pip install ruff" >&2; exit 127; }
cd "$ROOT"
rc=0

if [ "$#" -gt 0 ] && [ "$1" = "--fix" ]; then
  ruff check --fix apps/orchestrator apps/media-engine || rc=1
  ruff format apps/orchestrator apps/media-engine || rc=1
elif [ "$#" -gt 0 ] && [ "$1" = "--all" ]; then
  ruff check apps/orchestrator apps/media-engine || rc=1
else
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
      apps/orchestrator/*.py|apps/media-engine/*.py) printf '%s\n' "$file" ;;
    esac
  done > "$temp_list"
  if [ -s "$temp_list" ]; then
    count="$(wc -l < "$temp_list" | tr -d ' ')"
    echo "Ruff check on $count changed application Python file(s)."
    # Repo-controlled paths have no spaces. Quote via one line at a time to preserve the argv.
    failed=0
    while IFS= read -r file; do
      [ -n "$file" ] || continue
      ruff check "$file" || failed=1
    done < "$temp_list"
    [ "$failed" -eq 0 ] || rc=1
  else
    echo "No changed application Python files; skipping application Ruff check."
  fi
  rm -f "$temp_list"
fi

python3 scripts/check_layering.py || rc=1
exit "$rc"
