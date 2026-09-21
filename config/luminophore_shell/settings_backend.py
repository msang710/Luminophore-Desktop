from __future__ import annotations

from typing import Any, Mapping

from .settings_contract import (
    SETTINGS_PROTOCOL_VERSION,
    SettingsApplyRequest,
    decode_message,
    encode_message,
)
from .settings_coordinator import SettingsApplyCoordinator


class SettingsBackend:
    """Protocol facade. Status replies intentionally contain no setting values."""

    def __init__(self, coordinator: SettingsApplyCoordinator) -> None:
        self._coordinator = coordinator

    def handle(self, message: bytes) -> bytes:
        payload = decode_message(message)
        command = payload.get("command")
        if command == "apply":
            self._require_fields(payload, {"version", "command", "request_id", "expected_digest", "changes"})
            changes = payload["changes"]
            if not isinstance(changes, dict):
                raise ValueError("settings changes must be an object")
            request = SettingsApplyRequest.build(
                str(payload["request_id"]), str(payload["expected_digest"]), changes,
            )
            return encode_message(self._coordinator.apply(request).status_wire())
        if command == "status":
            self._require_fields(payload, {"version", "command", "request_id"})
            request_id = str(payload["request_id"])
            result = self._coordinator.status(request_id)
            response: Mapping[str, Any]
            if result is None:
                response = {"version": SETTINGS_PROTOCOL_VERSION, "request_id": request_id, "found": False}
            else:
                response = {**result.status_wire(), "found": True}
            return encode_message(response)
        raise ValueError("unsupported settings command")

    @staticmethod
    def _require_fields(payload: Mapping[str, Any], expected: set[str]) -> None:
        if set(payload) != expected:
            raise ValueError("invalid settings command fields")
