#!/usr/bin/env bash
# Report development prerequisites. Docker is optional unless using Docker Compose.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ok=0
check_required() {
  if command -v "$1" >/dev/null 2>&1; then
    printf "  [ok]   %-10s %s\n" "$1" "$("$1" "$2" 2>&1 | head -n1)"
  else
    printf "  [MISS] %-10s %s\n" "$1" "$3"
    ok=1
  fi
}
echo "Required tools:"
check_required python3 --version "install Python >= 3.11"
check_required ruff --version "install ruff"
check_required ffmpeg -version "install FFmpeg for real-render tests"
check_required ffprobe -version "install FFprobe (usually comes with FFmpeg)"
check_required node --version "install Node 20"
check_required npm --version "install npm (shipped with Node)"
if command -v docker >/dev/null 2>&1; then
  printf "  [optional] docker %s\n" "$(docker --version 2>&1 | head -n1)"
else
  echo "  [optional] docker not found (needed only for Docker Compose workflows)"
fi
echo "Python packages (orchestrator):"
for m in fastapi sqlalchemy arq pytest pytest_asyncio aiosqlite httpx cryptography; do
  if python3 -c "import $m" 2>/dev/null; then
    printf "  [ok]   %s\n" "$m"
  else
    printf "  [MISS] %s -> cd apps/orchestrator && pip install -e '.[dev]'\n" "$m"
    ok=1
  fi
done
echo "Repo state:"
[ -f "$ROOT/.env" ] && echo "  .env present (never commit it)" || echo "  .env absent (ok for dev defaults)"
exit "$ok"
