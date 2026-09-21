from __future__ import annotations

import threading
import unittest

from luminophore_shell.settings_contract import SettingsApplyPhase, SettingsApplyRequest, SettingsResultCategory, SettingsCompletionUnknown
from luminophore_shell.settings_coordinator import SettingsApplyCoordinator, SettingsMutationError, SettingsMutationGate


class Writer:
    def __init__(self) -> None:
        self.values = {"theme.mode": "dark"}
        self.current_digest = "digest-1"
        self.writes = 0
        self.restores = 0

    def digest(self): return self.current_digest
    def validate(self, changes):
        if any(key.startswith("private.") for key in changes):
            raise ValueError("invalid setting")
    def snapshot(self): return (dict(self.values), self.current_digest)
    def write_atomic(self, changes, expected_digest):
        if expected_digest != self.current_digest:
            raise SettingsMutationError(SettingsResultCategory.CONFLICT, "digest conflict")
        self.writes += 1
        self.values.update(changes)
        self.current_digest = f"digest-{self.writes + 1}"
        return self.current_digest
    def restore_atomic(self, snapshot):
        self.restores += 1
        self.values, self.current_digest = dict(snapshot[0]), snapshot[1]
        return self.current_digest


class Runtime:
    def __init__(self) -> None:
        self.applies = 0
        self.rollbacks = 0
        self.fail = False
    def apply(self, changes):
        self.applies += 1
        if self.fail: raise RuntimeError("runtime failed")
    def verify(self, changes): return None
    def rollback(self, snapshot): self.rollbacks += 1


class SettingsCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.writer, self.runtime = Writer(), Runtime()

    def request(self, request_id="r-1", value="light"):
        return SettingsApplyRequest.build(request_id, "digest-1", {"theme.mode": value})

    def test_explicit_retirement_prevents_delayed_request_in_both_routes(self):
        gate = SettingsMutationGate()
        live = SettingsApplyCoordinator(self.writer, self.runtime, lambda: True, mutation_gate=gate)
        deferred = SettingsApplyCoordinator(self.writer, self.runtime, lambda: False, mutation_gate=gate)
        self.assertIsNone(live.status("r-1"))
        retired = live.retire_unstarted("r-1")
        self.assertEqual(retired.category, SettingsResultCategory.CONFLICT)
        self.assertEqual(live.apply(self.request()).category, SettingsResultCategory.CONFLICT)
        self.assertEqual(deferred.apply(self.request()).category, SettingsResultCategory.CONFLICT)
        self.assertEqual((self.writer.writes, self.runtime.applies), (0, 0))

    def test_request_id_cannot_cross_live_and_deferred_ledgers(self):
        gate = SettingsMutationGate()
        live = SettingsApplyCoordinator(self.writer, self.runtime, lambda: True, mutation_gate=gate)
        deferred = SettingsApplyCoordinator(self.writer, self.runtime, lambda: False, mutation_gate=gate)
        completed = deferred.apply(self.request())
        self.assertIs(live.apply(self.request()), completed)
        self.assertEqual(live.apply(self.request(value="system")).category, SettingsResultCategory.CONFLICT)
        self.assertEqual((self.writer.writes, self.runtime.applies), (1, 0))

    def test_request_id_is_idempotent_and_payload_reuse_conflicts(self) -> None:
        coordinator = SettingsApplyCoordinator(self.writer, self.runtime, lambda: False)
        first = coordinator.apply(self.request())
        repeated = coordinator.apply(self.request())
        conflict = coordinator.apply(self.request(value="system"))
        self.assertIs(first, repeated)
        self.assertEqual(self.writer.writes, 1)
        self.assertEqual(conflict.category, SettingsResultCategory.CONFLICT)

    def test_offline_write_is_validated_atomic_and_pending(self) -> None:
        coordinator = SettingsApplyCoordinator(self.writer, self.runtime, lambda: False)
        result = coordinator.apply(self.request())
        self.assertEqual((result.category, result.phase), (
            SettingsResultCategory.SAVED_PENDING_NEXT_START, SettingsApplyPhase.PENDING_NEXT_START,
        ))
        self.assertEqual(self.runtime.applies, 0)

    def test_online_offline_race_fails_before_write(self) -> None:
        states = iter((False, True))
        coordinator = SettingsApplyCoordinator(self.writer, self.runtime, lambda: next(states))
        result = coordinator.apply(self.request())
        self.assertEqual(result.category, SettingsResultCategory.COMPLETION_UNKNOWN)
        self.assertEqual(self.writer.writes, 0)

    def test_validation_status_does_not_echo_private_value(self) -> None:
        coordinator = SettingsApplyCoordinator(self.writer, self.runtime, lambda: False)
        request = SettingsApplyRequest.build("r-private", "digest-1", {"private.token": "secret-value"})
        result = coordinator.apply(request)
        self.assertEqual(result.category, SettingsResultCategory.VALIDATION)
        self.assertNotIn("secret-value", result.message)

    def test_timeout_is_completion_unknown_and_never_rolls_back(self) -> None:
        coordinator = SettingsApplyCoordinator(
            self.writer, self.runtime, lambda: True, timeout_call=lambda _operation, _timeout: False,
        )
        result = coordinator.apply(self.request())
        self.assertEqual(result.category, SettingsResultCategory.COMPLETION_UNKNOWN)
        self.assertEqual((self.writer.restores, self.runtime.rollbacks), (0, 0))
        self.assertIs(coordinator.status("r-1"), result)

    def test_ambiguous_dispatched_mutation_is_not_rolled_back_or_replayed(self):
        def uncertain(changes):
            self.runtime.applies += 1
            raise SettingsCompletionUnknown("ack lost")
        self.runtime.apply = uncertain
        coordinator = SettingsApplyCoordinator(self.writer, self.runtime, lambda: True,
            timeout_call=lambda operation, _timeout: operation())
        result = coordinator.apply(self.request())
        self.assertEqual(result.category, SettingsResultCategory.COMPLETION_UNKNOWN)
        self.assertEqual((self.runtime.applies, self.runtime.rollbacks, self.writer.restores), (1, 0, 0))
        self.assertEqual(self.writer.values["theme.mode"], "light")
        self.assertIs(coordinator.apply(self.request()), result)
        self.assertEqual(self.runtime.applies, 1)

    def test_late_success_blocks_new_writes_until_worker_and_readback_complete(self):
        release = threading.Event()
        finished = threading.Event()
        workers = []
        original_apply = self.runtime.apply
        def slow_apply(changes):
            release.wait()
            original_apply(changes)
        self.runtime.apply = slow_apply
        def timeout(operation, limit):
            def worker():
                try:
                    operation()
                finally:
                    finished.set()
            thread = threading.Thread(target=worker)
            workers.append(thread)
            thread.start()
            return False
        coordinator = SettingsApplyCoordinator(self.writer, self.runtime, lambda: True, timeout_call=timeout)
        try:
            unknown = coordinator.apply(self.request())
            self.assertIs(coordinator.reconcile("r-1"), unknown)
            other = SettingsApplyRequest.build("r-2", self.writer.digest(), {"theme.mode": "system"})
            self.assertEqual(coordinator.apply(other).category, SettingsResultCategory.BUSY)
            self.assertEqual(self.writer.writes, 1)
            release.set()
            self.assertTrue(finished.wait(2))
            result = coordinator.reconcile("r-1")
            self.assertEqual(result.category, SettingsResultCategory.OK)
            self.assertEqual((self.runtime.applies, self.runtime.rollbacks, self.writer.restores), (1, 0, 0))
            self.assertIs(coordinator.apply(self.request()), result)
            self.assertEqual(self.writer.writes, 1)
        finally:
            release.set()
            for worker in workers:
                worker.join(2)

    def test_completed_worker_does_not_override_changed_file_or_failed_readback(self):
        coordinator = SettingsApplyCoordinator(self.writer, self.runtime, lambda: True,
            timeout_call=lambda operation, limit: (operation(), False)[1])
        unknown = coordinator.apply(self.request())
        def failed_verify(changes):
            raise RuntimeError("readback unavailable")
        self.runtime.verify = failed_verify
        self.assertIs(coordinator.reconcile("r-1"), unknown)
        self.writer.current_digest = "external"
        result = coordinator.reconcile("r-1")
        self.assertEqual(result.category, SettingsResultCategory.CONFLICT)
        self.assertEqual(coordinator.pending_request_id(), "")
        self.assertEqual(self.writer.current_digest, "external")
        self.assertEqual((self.writer.writes, self.writer.restores, self.runtime.applies), (1, 0, 1))

    def test_lost_ack_stays_unknown_even_after_worker_exits(self):
        def uncertain(changes):
            raise SettingsCompletionUnknown("ack lost")
        self.runtime.apply = uncertain
        coordinator = SettingsApplyCoordinator(self.writer, self.runtime, lambda: True,
            timeout_call=lambda operation, limit: operation())
        unknown = coordinator.apply(self.request())
        self.assertIs(coordinator.reconcile("r-1"), unknown)
        other = SettingsApplyRequest.build("r-2", self.writer.digest(), {"theme.mode": "system"})
        self.assertEqual(coordinator.apply(other).category, SettingsResultCategory.BUSY)
        self.assertEqual(self.writer.writes, 1)

    def test_unknown_live_request_also_blocks_deferred_writer(self):
        gate = SettingsMutationGate()
        live = SettingsApplyCoordinator(self.writer, self.runtime, lambda: True,
            timeout_call=lambda operation, limit: False, mutation_gate=gate)
        deferred = SettingsApplyCoordinator(self.writer, self.runtime, lambda: False, mutation_gate=gate)
        live.apply(self.request())
        request = SettingsApplyRequest.build("r-deferred", self.writer.digest(), {"motion.speed": 1.2})
        self.assertEqual(deferred.apply(request).category, SettingsResultCategory.BUSY)
        self.assertEqual(self.writer.writes, 1)

    def test_runtime_failure_rolls_back_persisted_and_runtime_state(self) -> None:
        self.runtime.fail = True
        coordinator = SettingsApplyCoordinator(
            self.writer, self.runtime, lambda: True,
            timeout_call=lambda operation, _timeout: (operation() is None),
        )
        result = coordinator.apply(self.request())
        self.assertEqual(result.category, SettingsResultCategory.RUNTIME_APPLY_FAILED_ROLLED_BACK)
        self.assertEqual(self.writer.values["theme.mode"], "dark")
        self.assertEqual((self.writer.restores, self.runtime.rollbacks), (1, 1))

    def test_nonblocking_mutation_lock_returns_busy_without_ledger_entry(self) -> None:
        coordinator = SettingsApplyCoordinator(self.writer, self.runtime, lambda: False)
        coordinator._mutation_lock.acquire()
        try:
            result = coordinator.apply(self.request())
        finally:
            coordinator._mutation_lock.release()
        self.assertEqual(result.category, SettingsResultCategory.BUSY)
        self.assertIsNone(coordinator.status("r-1"))


if __name__ == "__main__":
    unittest.main()
