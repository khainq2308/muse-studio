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
    rm -f "$TMP_DIR/health.json" "$TMP_DIR/root.html" "$TMP_DIR/deep-link.html" "$TMP_DIR/api.json" \
        "$TMP_DIR/large-body.bin" "$TMP_DIR/large-post.json" "$TMP_DIR/headers.txt" \
        "$TMP_DIR/over-limit.bin" "$TMP_DIR/over-limit.out" "$TMP_DIR/api-headers.txt"
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

echo "==> Verify /readyz reaches the API instead of the SPA fallback"
curl --silent --show-error --fail "$BASE_URL/readyz" -o "$TMP_DIR/ready.json"
grep -Fq '"path":"/readyz"' "$TMP_DIR/ready.json" || {
    echo "/readyz did not reach the upstream (SPA fallback would hide a down backend)." >&2
    exit 1
}
echo "[PASS] /readyz reaches the upstream mock through Nginx"

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

post_body() {
    # Usage: post_body <path> <file> <output>; prints the HTTP status.
    curl --silent --show-error --output "$3" --write-out '%{http_code}' \
        -X POST -H 'Content-Type: application/octet-stream' \
        --data-binary "@$2" "$BASE_URL$1"
}

echo "==> Verify request body limits (10 MiB default, larger only for upload/restore)"
LARGE_BYTES=$((5 * 1024 * 1024))
head -c "$LARGE_BYTES" /dev/zero > "$TMP_DIR/large-body.bin"
post_status="$(post_body /api/__smoke_upload "$TMP_DIR/large-body.bin" "$TMP_DIR/large-post.json")"
[ "$post_status" = "200" ] || { echo "5 MiB POST returned HTTP $post_status" >&2; exit 1; }
grep -Fq '"path":"/api/__smoke_upload"' "$TMP_DIR/large-post.json"
grep -Fq "\"received_bytes\":$LARGE_BYTES" "$TMP_DIR/large-post.json" || {
    echo "Upstream did not receive the full large body." >&2
    exit 1
}
echo "[PASS] 5 MiB POST reaches the upstream mock intact"

OVER_LIMIT_BYTES=$((12 * 1024 * 1024))
head -c "$OVER_LIMIT_BYTES" /dev/zero > "$TMP_DIR/over-limit.bin"
over_status="$(post_body /api/__smoke_upload "$TMP_DIR/over-limit.bin" "$TMP_DIR/over-limit.out")"
[ "$over_status" = "413" ] || {
    echo "12 MiB POST to a generic API path returned HTTP $over_status (expected 413)" >&2
    exit 1
}
echo "[PASS] 12 MiB POST to a generic API path is rejected with 413"

for large_path in /api/v1/assets/upload /api/v1/backups/restore; do
    large_status="$(post_body "$large_path" "$TMP_DIR/over-limit.bin" "$TMP_DIR/large-post.json")"
    [ "$large_status" = "200" ] || {
        echo "12 MiB POST to $large_path returned HTTP $large_status (expected 200)" >&2
        exit 1
    }
    grep -Fq "\"path\":\"$large_path\"" "$TMP_DIR/large-post.json"
    grep -Fq "\"received_bytes\":$OVER_LIMIT_BYTES" "$TMP_DIR/large-post.json" || {
        echo "Upstream did not receive the full body for $large_path." >&2
        exit 1
    }
    echo "[PASS] 12 MiB POST to $large_path reaches the upstream intact"
done

echo "==> Verify API responses are not cacheable and keep security headers"
curl --silent --show-error --fail --dump-header "$TMP_DIR/api-headers.txt" --output /dev/null "$BASE_URL/api/__smoke"
grep -iq '^cache-control: no-store' "$TMP_DIR/api-headers.txt" || {
    echo "/api/ response lacks Cache-Control: no-store" >&2
    exit 1
}
[ "$(grep -ic '^cache-control:' "$TMP_DIR/api-headers.txt")" = "1" ] || {
    echo "/api/ response has duplicate Cache-Control headers" >&2
    exit 1
}
for header in 'x-content-type-options: nosniff' 'x-frame-options: deny' 'referrer-policy:' \
    "content-security-policy: default-src 'self'"; do
    grep -iqF "$header" "$TMP_DIR/api-headers.txt" || { echo "/api/ response lacks header: $header" >&2; exit 1; }
done
grep -iq '^server: nginx[[:space:]]*$' "$TMP_DIR/api-headers.txt" || {
    echo "Server header discloses a version (server_tokens must be off)" >&2
    exit 1
}
echo "[PASS] /api/ responses are no-store, carry security headers and hide the nginx version"

echo "==> Verify the built SPA shell is compatible with a strict CSP"
if grep -Eiq '<script(>|[[:space:]][^>]*>)[[:space:]]*[^<[:space:]]' "$TMP_DIR/root.html" \
    || grep -Eiq '<style|[[:space:]]style=' "$TMP_DIR/root.html"; then
    echo "index.html contains inline scripts or styles that script-src/style-src 'self' would block" >&2
    exit 1
fi
echo "[PASS] index.html has no inline scripts or styles"

echo "==> Verify static asset handling and security headers"
missing_status="$(curl --silent --output /dev/null --write-out '%{http_code}' "$BASE_URL/assets/missing.js")"
[ "$missing_status" = "404" ] || { echo "/assets/missing.js returned HTTP $missing_status (expected 404)" >&2; exit 1; }
echo "[PASS] /assets/missing.js returns 404 instead of index.html"

asset_path="$(grep -oE '/assets/[^"]+\.js' "$TMP_DIR/root.html" | head -n 1)"
[ -n "$asset_path" ] || { echo "Could not find a built JS asset in index.html" >&2; exit 1; }
curl --silent --show-error --fail --dump-header "$TMP_DIR/headers.txt" --output /dev/null "$BASE_URL$asset_path"
grep -iq '^cache-control: .*immutable' "$TMP_DIR/headers.txt" || { echo "Asset is not immutable-cached" >&2; exit 1; }
grep -iq '^x-content-type-options: nosniff' "$TMP_DIR/headers.txt" || { echo "Asset lacks nosniff" >&2; exit 1; }
echo "[PASS] $asset_path is served with immutable caching and nosniff"

curl --silent --show-error --fail --dump-header "$TMP_DIR/headers.txt" --output /dev/null \
    "$BASE_URL/projects/test-project/queue-monitor"
grep -iq '^cache-control: no-cache' "$TMP_DIR/headers.txt" || { echo "Deep link index.html is cacheable" >&2; exit 1; }
for header in 'x-content-type-options: nosniff' 'x-frame-options: deny' 'referrer-policy:' \
    "content-security-policy: default-src 'self'; script-src 'self'; style-src 'self'" \
    "frame-ancestors 'none'" "base-uri 'self'" "form-action 'self'" "connect-src 'self'"; do
    grep -iqF "$header" "$TMP_DIR/headers.txt" || { echo "Deep link response lacks header: $header" >&2; exit 1; }
done
echo "[PASS] SPA shell is no-cache and carries security headers"

echo "Production Nginx smoke test passed."
