#!/usr/bin/env bash
# Report which tools needed for development are present.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ok=0
check() {
  if command -v "$1" >/dev/null 2>&1; then printf "  [ok]   %-10s %s\n" "$1" "$($1 ${2:---version} 2>&1 | head -n1)"
  else printf "  [MISS] %-10s %s\n" "$1" "$3"; ok=1; fi
}
echo "Tools:"
check python3 --version "install Python >= 3.11"
check ruff --version "pip install ruff"
check ffmpeg -version "apt-get install ffmpeg (required by render tests)"
check ffprobe -version "comes with ffmpeg"
check node --version "install Node 20"
check npm --version "comes with Node"
check docker --version "optional: docker compose stack"
echo "Python packages (orchestrator):"
for m in fastapi sqlalchemy arq pytest pytest_asyncio aiosqlite httpx cryptography; do
  python3 -c "import $m" 2>/dev/null && printf "  [ok]   %s\n" "$m" || { printf "  [MISS] %s  -> cd apps/orchestrator && pip install -e .[dev]\n" "$m"; ok=1; }
done
echo "Repo state:"
[ -f "$ROOT/.env" ] && echo "  .env present (never commit it)" || echo "  .env absent (ok for dev defaults)"
exit $ok
