from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import tempfile
import threading
from typing import Callable, Mapping

from .binding_registry import BindingFlags, BindingRegistry, default_registry
from .generated_bindings import (
    BINDING_PAYLOAD_VERSION,
    binding_generation_id,
    serialize_binding_data,
    serialize_binding_lua,
)


from .legacy_bindings import BindingServiceError

@dataclass(frozen=True, slots=True)
class BindingUpdate:
    chord: str | None
    flags: BindingFlags | None = None


@dataclass(frozen=True, slots=True)
class BindingSnapshot:
    registry: BindingRegistry
    digest: str
    state_bytes: bytes | None
    lua_bytes: bytes | None
    customized: bool
    generation_id: str
    reload_status: str = "unknown"


@dataclass(frozen=True, slots=True)
class BindingApplyResult:
    digest: str
    changed_actions: tuple[str, ...]
    generation_id: str
    reload_required: bool = True


AtomicWriter = Callable[[Path, bytes], None]


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            os.chmod(temporary_path, path.stat().st_mode & 0o777)
        else:
            os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _digest(state: bytes | None, lua: bytes | None) -> str:
    fingerprint = hashlib.sha256()
    for name, payload in ((b"state", state), (b"lua", lua)):
        fingerprint.update(name)
        fingerprint.update(b"\0missing\0" if payload is None else b"\0present\0" + payload)
    return fingerprint.hexdigest()


class BindingPersistenceService:
    def __init__(
        self,
        state_path: Path,
        lua_path: Path,
        *,
        atomic_writer: AtomicWriter = _write_atomic,
    ) -> None:
        self.state_path = state_path
        self.lua_path = lua_path
        self._baseline = default_registry()
        self._atomic_writer = atomic_writer
        self._transaction_lock = threading.RLock()

    def _read(self, path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None

    def _decode_registry(self, payload: bytes) -> BindingRegistry:
        from .legacy_bindings import decode_registry
        return decode_registry(payload, self._baseline)

    def snapshot(self) -> BindingSnapshot:
        with self._transaction_lock:
            state = self._read(self.state_path)
            lua = self._read(self.lua_path)
            if state is None:
                # The repository ships a packaged default Lua artifact without
                # a custom JSON state.  Its mere presence is neither a custom
                # configuration nor evidence that Hyprland has reloaded it.
                registry = self._baseline
            else:
                if lua is None:
                    raise BindingServiceError("validation", "custom binding state files are incomplete")
                registry = self._decode_registry(state)
                if state != serialize_binding_data(registry) or lua != serialize_binding_lua(registry):
                    raise BindingServiceError("validation", "binding state files are not canonical or are out of sync")
            return BindingSnapshot(
                registry, _digest(state, lua), state, lua,
                customized=registry != self._baseline,
                generation_id=binding_generation_id(registry),
            )

    def _restore_file(self, path: Path, payload: bytes | None) -> None:
        if payload is None:
            path.unlink(missing_ok=True)
        else:
            self._atomic_writer(path, payload)

    def apply(self, changes: Mapping[str, BindingUpdate], expected_digest: str) -> BindingApplyResult:
        with self._transaction_lock:
            return self._apply_locked(changes, expected_digest)

    def _apply_locked(self, changes: Mapping[str, BindingUpdate], expected_digest: str) -> BindingApplyResult:
        before = self.snapshot()
        if expected_digest != before.digest:
            raise BindingServiceError("conflict", "binding state changed; reopen settings")
        if not changes or any(not isinstance(value, BindingUpdate) for value in changes.values()):
            raise BindingServiceError("validation", "typed binding updates are required")
        try:
            candidate = before.registry.update({
                action_id: (update.chord, update.flags) for action_id, update in changes.items()
            })
            candidate.validate()
        except ValueError as exc:
            raise BindingServiceError("validation", str(exc)) from exc
        state_payload = serialize_binding_data(candidate)
        lua_payload = serialize_binding_lua(candidate)
        try:
            self._atomic_writer(self.state_path, state_payload)
            self._atomic_writer(self.lua_path, lua_payload)
            if self._read(self.state_path) != state_payload or self._read(self.lua_path) != lua_payload:
                raise OSError("binding write verification failed")
        except Exception as exc:
            try:
                self._restore_file(self.lua_path, before.lua_bytes)
                self._restore_file(self.state_path, before.state_bytes)
                if self._read(self.state_path) != before.state_bytes or self._read(self.lua_path) != before.lua_bytes:
                    raise OSError("binding rollback verification failed")
            except Exception as rollback_exc:
                raise BindingServiceError("rollback_failed", "binding write and rollback failed") from rollback_exc
            raise BindingServiceError("write_failed_rolled_back", "binding write failed; previous state restored") from exc
        return BindingApplyResult(
            _digest(state_payload, lua_payload), tuple(sorted(changes)),
            binding_generation_id(candidate), reload_required=True,
        )


__all__ = [
    "BindingApplyResult", "BindingPersistenceService", "BindingServiceError",
    "BindingSnapshot", "BindingUpdate",
]
