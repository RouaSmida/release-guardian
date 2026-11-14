"""Tiny HTTP server for /healthz, /readyz and /metrics (standard library only)."""

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest


def start_health_server(
    port: int, registry: CollectorRegistry, is_ready: Callable[[], bool]
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def do_GET(self) -> None:  # noqa: N802 - name required by BaseHTTPRequestHandler
            if self.path == "/healthz":
                self._json(200, {"status": "ok"})
            elif self.path == "/readyz":
                ready = is_ready()
                self._json(200 if ready else 503, {"status": "ready" if ready else "unavailable"})
            elif self.path == "/metrics":
                self._send(200, generate_latest(registry), CONTENT_TYPE_LATEST)
            else:
                self._json(404, {"detail": "not found"})

        def log_message(self, *args) -> None:  # probes would flood the logs
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104
    threading.Thread(target=server.serve_forever, name="health", daemon=True).start()
    return server
