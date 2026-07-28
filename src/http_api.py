"""Local HTTP API used by the CR5 WebUI to start configured servo motion."""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional


_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class StartResult:
    """Result returned by the Qt-thread start callback."""

    accepted: bool
    message: str


class _ApiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
            self,
            server_address: tuple[str, int],
            start_callback: Callable[[], StartResult]) -> None:
        super().__init__(server_address, _RequestHandler)
        self.start_callback = start_callback


class _RequestHandler(BaseHTTPRequestHandler):
    server: _ApiServer

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/health":
            self._send_json(200, {"ok": True, "service": "servo_ctrl"})
            return
        self._send_json(404, {"ok": False, "error": "接口不存在"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/start":
            self._send_json(404, {"ok": False, "error": "接口不存在"})
            return

        content_length = self.headers.get("Content-Length", "0")
        try:
            body_size = int(content_length)
        except ValueError:
            self._send_json(400, {"ok": False, "error": "Content-Length 无效"})
            return

        if body_size:
            self.rfile.read(body_size)
            self._send_json(400, {"ok": False, "error": "启动接口不接受参数"})
            return

        try:
            result = self.server.start_callback()
        except TimeoutError as exc:
            _LOG.warning("HTTP start request timed out: %s", exc)
            self._send_json(503, {"ok": False, "error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("HTTP start request failed")
            self._send_json(500, {"ok": False, "error": f"内部错误: {exc}"})
            return

        if result.accepted:
            self._send_json(200, {"ok": True, "message": result.message})
        else:
            self._send_json(409, {"ok": False, "error": result.message})

    def log_message(self, fmt: str, *args: object) -> None:
        _LOG.info("HTTP %s - %s", self.address_string(), fmt % args)


class EndEffectorApiServer:
    """Background HTTP server exposing ``POST /start`` on localhost."""

    def __init__(
            self,
            start_callback: Callable[[], StartResult],
            host: str = "127.0.0.1",
            port: int = 8877) -> None:
        self._server = _ApiServer((host, port), start_callback)
        self._thread: Optional[threading.Thread] = None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="end-effector-http-api",
            daemon=True,
        )
        self._thread.start()
        _LOG.info("End-effector HTTP API listening on http://%s:%d", *self.address)

    def stop(self) -> None:
        if self._thread is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)
        self._thread = None
        _LOG.info("End-effector HTTP API stopped")


__all__ = ["EndEffectorApiServer", "StartResult"]
