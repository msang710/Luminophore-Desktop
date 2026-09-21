"""Stdlib-only IPC transport shared by interactive ctl and the settings facade."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
from typing import Any

from .settings_contract import SettingsTransportCategory


class IpcError(RuntimeError):
    def __init__(
        self,
        message: str,
        category: SettingsTransportCategory = SettingsTransportCategory.CONNECTION_UNAVAILABLE,
    ) -> None:
        super().__init__(message)
        self.category = category


def socket_path() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR")
    if not value:
        raise RuntimeError("XDG_RUNTIME_DIR is not set")
    path = Path(value) / "luminophore-shell"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path / "control.sock"


class IpcClient:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or socket_path()

    def request(self, payload: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
        try:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except OSError as exc:
            raise IpcError(
                "daemon connection is unavailable",
                SettingsTransportCategory.CONNECTION_UNAVAILABLE,
            ) from exc
        connection.settimeout(timeout)
        try:
            try:
                connection.connect(str(self.path))
            except OSError as exc:
                raise IpcError(
                    "daemon connection is unavailable",
                    SettingsTransportCategory.CONNECTION_UNAVAILABLE,
                ) from exc
            try:
                connection.sendall(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
                chunks = bytearray()
                while b"\n" not in chunks and len(chunks) <= 65_536:
                    block = connection.recv(4096)
                    if not block:
                        raise IpcError(
                            "daemon response completion is unknown",
                            SettingsTransportCategory.COMPLETION_UNKNOWN,
                        )
                    chunks.extend(block)
            except IpcError:
                raise
            except OSError as exc:
                raise IpcError(
                    "daemon response completion is unknown",
                    SettingsTransportCategory.COMPLETION_UNKNOWN,
                ) from exc
        finally:
            connection.close()
        if b"\n" not in chunks or len(chunks) > 65_536:
            raise IpcError("invalid daemon response", SettingsTransportCategory.PROTOCOL_ERROR)
        try:
            response = json.loads(bytes(chunks).split(b"\n", 1)[0])
        except (json.JSONDecodeError, IndexError) as exc:
            raise IpcError("invalid daemon response", SettingsTransportCategory.PROTOCOL_ERROR) from exc
        if not isinstance(response, dict):
            raise IpcError("invalid daemon response", SettingsTransportCategory.PROTOCOL_ERROR)
        if response.get("ok") is False and response.get("category") == "completion_unknown":
            raise IpcError("daemon response completion is unknown", SettingsTransportCategory.COMPLETION_UNKNOWN)
        return response


