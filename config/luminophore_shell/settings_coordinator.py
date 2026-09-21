from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
import hashlib
import threading
from typing import Any, Callable, Mapping, Protocol

from .settings_contract import (
    SettingsApplyPhase,
    SettingsCompletionUnknown,
    SettingsApplyRequest,
    SettingsApplyResult,
    SettingsResultCategory,
    encode_message,
    _validate_request_id,
)


from .settings_contract import SettingsMutationError

class SettingsWriteAdapter(Protocol):
    def digest(self) -> str: ...
    def validate(self, changes: Mapping[str, Any]) -> None: ...
    def snapshot(self) -> object: ...
    def write_atomic(self, changes: Mapping[str, Any], expected_digest: str) -> str: ...
    def restore_atomic(self, snapshot: object) -> str: ...


class RuntimeSettingsAdapter(Protocol):
    def apply(self, changes: Mapping[str, Any]) -> None: ...
    def verify(self, changes: Mapping[str, Any]) -> None: ...
    def rollback(self, snapshot: object) -> None: ...


TimeoutCall = Callable[[Callable[[], None], float], bool]


def _call_with_timeout(operation: Callable[[], None], timeout: float) -> bool:
    # The worker is deliberately not cancelled: after timeout its completion is
    # unknown, so no competing rollback may be started.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="luminophore-settings")
    future = executor.submit(operation)
    try:
        future.result(timeout=timeout)
        return True
    except FutureTimeout:
        executor.shutdown(wait=False, cancel_futures=False)
        return False
    finally:
        if future.done():
            executor.shutdown(wait=True)


@dataclass
class _PendingOperation:
    request: SettingsApplyRequest
    digest: str
    done: threading.Event = field(default_factory=threading.Event)
    succeeded: bool = False


class SettingsMutationGate:
    """One writer boundary for live and deferred routes sharing a config file."""
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: _PendingOperation | None = None
        self.ledger_lock = threading.Lock()
        self.ledger: dict[str, tuple[str, SettingsApplyResult]] = {}


class SettingsApplyCoordinator:
    def __init__(
        self,
        writer: SettingsWriteAdapter,
        runtime: RuntimeSettingsAdapter,
        online: Callable[[], bool],
        *,
        timeout_seconds: float = 8.0,
        timeout_call: TimeoutCall = _call_with_timeout,
        mutation_gate: SettingsMutationGate | None = None,
    ) -> None:
        self._writer = writer
        self._runtime = runtime
        self._online = online
        self._timeout = timeout_seconds
        self._timeout_call = timeout_call
        self._gate = mutation_gate or SettingsMutationGate()
        self._mutation_lock = self._gate.lock
        self._ledger_lock = self._gate.ledger_lock
        self._ledger = self._gate.ledger


    @property
    def _pending(self) -> _PendingOperation | None:
        return self._gate.pending

    @_pending.setter
    def _pending(self, operation: _PendingOperation | None) -> None:
        self._gate.pending = operation

    @staticmethod
    def _fingerprint(request: SettingsApplyRequest) -> str:
        return hashlib.sha256(encode_message({
            "version": 1,
            "request_id": request.request_id,
            "expected_digest": request.expected_digest,
            "changes": dict(request.changes),
        })).hexdigest()

    def status(self, request_id: str) -> SettingsApplyResult | None:
        with self._ledger_lock:
            row = self._ledger.get(request_id)
            return row[1] if row else None

    def retire_unstarted(self, request_id: str) -> SettingsApplyResult | None:
        """Fence an explicitly cancelled ID before a delayed IPC job can write."""
        _validate_request_id(request_id)
        if not self._mutation_lock.acquire(blocking=False):
            return None
        try:
            previous = self.status(request_id)
            if previous is not None:
                return previous
            return self._remember("retired-before-start", SettingsApplyResult(
                request_id, SettingsResultCategory.CONFLICT, SettingsApplyPhase.COMPLETE,
                self._writer.digest(), (), "request retired before execution; reload before applying again"))
        finally:
            self._mutation_lock.release()

    def pending_request_id(self) -> str:
        pending = self._pending
        return pending.request.request_id if pending is not None else ""

    def reconcile(self, request_id: str, *, recover: bool = False) -> SettingsApplyResult | None:
        previous = self.status(request_id)
        if previous is None or not self._mutation_lock.acquire(blocking=False):
            return previous
        try:
            pending = self._pending
            if (pending is None or pending.request.request_id != request_id
                    or not pending.done.is_set()):
                return previous
            if not self._online():
                return previous
            try:
                outcome = "complete" if pending.succeeded else "pending"
                resolver = getattr(self._runtime, "reconcile", None)
                if resolver is not None:
                    outcome = resolver(dict(pending.request.changes), recover=recover)
                if outcome == "superseded" or (outcome == "complete" and self._writer.digest() != pending.digest):
                    self._pending = None
                    return self._remember(self._fingerprint(pending.request), SettingsApplyResult(
                        request_id, SettingsResultCategory.CONFLICT, SettingsApplyPhase.COMPLETE,
                        self._writer.digest(), tuple(key for key, _ in pending.request.changes),
                        "previous request retired; reload saved settings before applying again"))
                if outcome != "complete" or self._writer.digest() != pending.digest:
                    return previous
                self._runtime.verify(dict(pending.request.changes))
            except Exception:
                return previous
            if self._writer.digest() != pending.digest:
                return previous
            result = SettingsApplyResult(request_id, SettingsResultCategory.OK, SettingsApplyPhase.COMPLETE,
                pending.digest, tuple(key for key, _ in pending.request.changes), "completion verified after worker finished")
            self._pending = None
            return self._remember(self._fingerprint(pending.request), result)
        finally:
            self._mutation_lock.release()

    def _remember(self, fingerprint: str, result: SettingsApplyResult) -> SettingsApplyResult:
        with self._ledger_lock:
            self._ledger[result.request_id] = (fingerprint, result)
        return result

    def apply(self, request: SettingsApplyRequest) -> SettingsApplyResult:
        fingerprint = self._fingerprint(request)
        with self._ledger_lock:
            previous = self._ledger.get(request.request_id)
        if previous is not None:
            if previous[0] == fingerprint:
                return previous[1]
            return SettingsApplyResult(
                request.request_id, SettingsResultCategory.CONFLICT, SettingsApplyPhase.COMPLETE,
                self._writer.digest(), (), "request ID already belongs to another mutation",
            )
        if not self._mutation_lock.acquire(blocking=False):
            return SettingsApplyResult(
                request.request_id, SettingsResultCategory.BUSY, SettingsApplyPhase.APPLYING,
                self._writer.digest(), tuple(key for key, _value in request.changes), "another mutation is active",
            )
        try:
            # A different worker may have completed between the first ledger
            # lookup and acquisition of the writer lock.
            with self._ledger_lock:
                previous = self._ledger.get(request.request_id)
            if previous is not None:
                if previous[0] == fingerprint:
                    return previous[1]
                return SettingsApplyResult(request.request_id, SettingsResultCategory.CONFLICT, SettingsApplyPhase.COMPLETE,
                    self._writer.digest(), (), "request ID already belongs to another mutation")
            if self._pending is not None:
                return SettingsApplyResult(request.request_id, SettingsResultCategory.BUSY, SettingsApplyPhase.COMPLETION_UNKNOWN,
                    self._writer.digest(), tuple(key for key, _ in request.changes), "previous mutation requires completion verification")
            # Validate without persistence, then sample liveness twice. A change
            # between samples is ambiguous and must not choose either writer.
            changes = dict(request.changes)
            try:
                self._writer.validate(changes)
            except Exception as exc:
                return self._remember(fingerprint, SettingsApplyResult(
                    request.request_id, SettingsResultCategory.VALIDATION, SettingsApplyPhase.COMPLETE,
                    self._writer.digest(), tuple(sorted(changes)), "settings validation failed",
                ))
            first_online = self._online()
            second_online = self._online()
            if first_online != second_online:
                return self._remember(fingerprint, SettingsApplyResult(
                    request.request_id, SettingsResultCategory.COMPLETION_UNKNOWN,
                    SettingsApplyPhase.COMPLETION_UNKNOWN, self._writer.digest(), tuple(sorted(changes)),
                    "shell liveness changed before mutation",
                ))
            if not first_online:
                try:
                    digest = self._writer.write_atomic(changes, request.expected_digest)
                except SettingsMutationError as exc:
                    return self._remember(fingerprint, SettingsApplyResult(
                        request.request_id, exc.category, SettingsApplyPhase.COMPLETE,
                        self._writer.digest(), tuple(sorted(changes)), "settings write failed",
                    ))
                return self._remember(fingerprint, SettingsApplyResult(
                    request.request_id, SettingsResultCategory.SAVED_PENDING_NEXT_START,
                    SettingsApplyPhase.PENDING_NEXT_START, digest, tuple(sorted(changes)),
                    "saved for the next shell start",
                ))
            before = self._writer.snapshot()
            try:
                candidate_digest = self._writer.write_atomic(changes, request.expected_digest)
            except SettingsMutationError as exc:
                return self._remember(fingerprint, SettingsApplyResult(
                    request.request_id, exc.category, SettingsApplyPhase.COMPLETE,
                    self._writer.digest(), tuple(sorted(changes)), "settings write failed",
                ))
            pending = _PendingOperation(request, candidate_digest)
            def operation() -> None:
                try:
                    self._runtime.apply(changes)
                    self._runtime.verify(changes)
                    pending.succeeded = True
                finally:
                    pending.done.set()
            try:
                completed = self._timeout_call(operation, self._timeout)
            except SettingsCompletionUnknown:
                self._pending = pending
                return self._remember(fingerprint, SettingsApplyResult(
                    request.request_id, SettingsResultCategory.COMPLETION_UNKNOWN,
                    SettingsApplyPhase.COMPLETION_UNKNOWN, candidate_digest, tuple(sorted(changes)),
                    "runtime completion is unknown; inspect state before retrying",
                ))
            except Exception as exc:
                try:
                    self._runtime.rollback(before)
                    digest = self._writer.restore_atomic(before)
                except Exception as rollback_exc:
                    return self._remember(fingerprint, SettingsApplyResult(
                        request.request_id, SettingsResultCategory.ROLLBACK_FAILED,
                        SettingsApplyPhase.COMPLETE, self._writer.digest(), tuple(sorted(changes)),
                        "runtime apply and rollback failed",
                    ))
                return self._remember(fingerprint, SettingsApplyResult(
                    request.request_id, SettingsResultCategory.RUNTIME_APPLY_FAILED_ROLLED_BACK,
                    SettingsApplyPhase.COMPLETE, digest, tuple(sorted(changes)),
                    "runtime apply failed and previous settings were restored",
                ))
            if not completed:
                self._pending = pending
                return self._remember(fingerprint, SettingsApplyResult(
                    request.request_id, SettingsResultCategory.COMPLETION_UNKNOWN,
                    SettingsApplyPhase.COMPLETION_UNKNOWN, candidate_digest, tuple(sorted(changes)),
                    "runtime completion is unknown; inspect status before retrying",
                ))
            return self._remember(fingerprint, SettingsApplyResult(
                request.request_id, SettingsResultCategory.OK, SettingsApplyPhase.COMPLETE,
                candidate_digest, tuple(sorted(changes)), "applied",
            ))
        finally:
            self._mutation_lock.release()
