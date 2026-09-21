from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import socketserver
import threading
from typing import Any, Callable

from .ipc_client import IpcClient, IpcError, socket_path


class _RequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(65_537)
        if not raw or len(raw) > 65_536:
            return
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            response = self.server.dispatch(request)  # type: ignore[attr-defined]
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        try:
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
        except (BrokenPipeError, ConnectionResetError):
            # The compositor's drag helper is intentionally fire-and-forget.
            return


class _UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, path: str, dispatch: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.dispatch = dispatch
        super().__init__(path, _RequestHandler)


class IpcServer:
    def __init__(self, dispatch: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self.path = socket_path()
        self.dispatch = dispatch
        self._server: _UnixServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.path.exists():
            try:
                IpcClient().request({"command": "status"}, timeout=0.25)
            except IpcError as exc:
                if exc.category is SettingsTransportCategory.CONNECTION_UNAVAILABLE:
                    self.path.unlink(missing_ok=True)
                else:
                    raise IpcError("another luminophore-shell instance may already be running", exc.category) from exc
            else:
                raise IpcError("another luminophore-shell instance is already running")
        self._server = _UnixServer(str(self.path), self.dispatch)
        os.chmod(self.path, 0o600)
        self._thread = threading.Thread(target=self._server.serve_forever, name="luminophore-ipc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self.path.unlink(missing_ok=True)
