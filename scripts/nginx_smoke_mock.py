"""Tiny standard-library HTTP mock used by the production Nginx smoke test."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class SmokeHandler(BaseHTTPRequestHandler):
    server_version = "MuseStudioSmokeMock/1.0"

    def do_GET(self) -> None:
        payload = {
            "source": "nginx-smoke-mock",
            "path": self.path,
            "status": "ok" if self.path == "/healthz" else None,
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        # Drain the body and report its size so the smoke test can prove that bodies above
        # nginx's 1 MiB default reach the upstream intact (Content-Length or chunked).
        received = 0
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            while True:
                size = int(self.rfile.readline().strip() or b"0", 16)
                if size == 0:
                    self.rfile.readline()
                    break
                received += len(self.rfile.read(size))
                self.rfile.readline()
        else:
            remaining = int(self.headers.get("Content-Length", "0"))
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                received += len(chunk)
                remaining -= len(chunk)
        payload = {"source": "nginx-smoke-mock", "path": self.path, "received_bytes": received}
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string: str, *args: object) -> None:
        # Keep CI logs stable and avoid printing arbitrary request data.
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), SmokeHandler).serve_forever()
