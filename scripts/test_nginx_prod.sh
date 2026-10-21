#!/usr/bin/env bash
# Build (when needed) and exercise the actual production Nginx image with an API mock.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${STUDIO_WEB_IMAGE:-musestudio-studio-web-smoke:local}"
NETWORK="musestudio-nginx-smoke-$$_network"
API_CONTAINER="musestudio-nginx-smoke-$$_api"
WEB_CONTAINER="musestudio-nginx-smoke-$$_web"
PORT="${STUDIO_WEB_SMOKE_PORT:-$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')}"
BASE_URL="http://127.0.0.1:$PORT"
TMP_DIR="$(mktemp -d)"

cleanup() {
    docker rm -f "$WEB_CONTAINER" "$API_CONTAINER" >/dev/null 2>&1 || true
    docker network rm "$NETWORK" >/dev/null 2>&1 || true
    rm -f "$TMP_DIR/health.json" "$TMP_DIR/root.html" "$TMP_DIR/deep-link.html" "$TMP_DIR/api.json"
    rmdir "$TMP_DIR" 2>/dev/null || true
}
trap cleanup EXIT

command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 127; }
command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 127; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 127; }

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "==> Build production Studio Web image: $IMAGE"
    docker build -f "$ROOT/apps/studio-web/Dockerfile.prod" -t "$IMAGE" "$ROOT/apps/studio-web"
fi

echo "==> Start isolated mock API and production Nginx containers"
docker network create "$NETWORK" >/dev/null
docker run -d --name "$API_CONTAINER" --network "$NETWORK" --network-alias orchestrator-api \
    --mount "type=bind,src=$ROOT/scripts/nginx_smoke_mock.py,dst=/mock_api.py,readonly" \
    python:3.11-alpine python /mock_api.py >/dev/null
docker run -d --name "$WEB_CONTAINER" --network "$NETWORK" \
    -p "127.0.0.1:$PORT:8080" "$IMAGE" >/dev/null

echo "==> Wait for Nginx to become ready"
ready=0
for _ in $(seq 1 60); do
    if curl --silent --show-error --fail "$BASE_URL/healthz" -o "$TMP_DIR/health.json" 2>/dev/null \
        && grep -Fq '"source":"nginx-smoke-mock"' "$TMP_DIR/health.json" \
        && grep -Fq '"path":"/healthz"' "$TMP_DIR/health.json"; then
        ready=1
        break
    fi
    sleep 1
done
if [ "$ready" -ne 1 ]; then
    docker logs "$WEB_CONTAINER" >&2 || true
    docker logs "$API_CONTAINER" >&2 || true
    echo "Nginx did not proxy /healthz to the mock API within 60 seconds." >&2
    exit 1
fi
echo "[PASS] /healthz reaches the upstream mock through Nginx"

echo "==> Verify SPA fallback for a nested project/module route"
root_status="$(curl --silent --show-error --output "$TMP_DIR/root.html" --write-out '%{http_code}' "$BASE_URL/")"
route_status="$(curl --silent --show-error --output "$TMP_DIR/deep-link.html" --write-out '%{http_code}' "$BASE_URL/projects/test-project/queue-monitor")"
[ "$root_status" = "200" ] || { echo "Root route returned HTTP $root_status" >&2; exit 1; }
[ "$route_status" = "200" ] || { echo "Deep link returned HTTP $route_status" >&2; exit 1; }
grep -Fq '<div id="root"></div>' "$TMP_DIR/root.html" || { echo "Root response is not the Studio Web index.html" >&2; exit 1; }
cmp -s "$TMP_DIR/root.html" "$TMP_DIR/deep-link.html" || {
    echo "Deep link did not receive the SPA index.html fallback." >&2
    exit 1
}
echo "[PASS] /projects/test-project/queue-monitor returns the SPA index.html"

echo "==> Verify /api proxy preserves the absolute request path"
curl --silent --show-error --fail "$BASE_URL/api/__smoke" -o "$TMP_DIR/api.json"
grep -Fq '"source":"nginx-smoke-mock"' "$TMP_DIR/api.json"
grep -Fq '"path":"/api/__smoke"' "$TMP_DIR/api.json"
echo "[PASS] /api/__smoke reaches the upstream mock with the original path"

echo "Production Nginx smoke test passed."
