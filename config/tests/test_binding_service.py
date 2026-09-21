from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest

from luminophore_shell.binding_registry import BindingFlags
from luminophore_shell.binding_service import (
    BindingPersistenceService, BindingServiceError, BindingUpdate, _write_atomic,
)


class FaultWriter:
    def __init__(self, fail_calls=(), corrupt_calls=()) -> None:
        self.calls = 0
        self.fail_calls = set(fail_calls)
        self.corrupt_calls = set(corrupt_calls)

    def __call__(self, path: Path, payload: bytes) -> None:
        self.calls += 1
        if self.calls in self.fail_calls:
            raise OSError(f"injected write failure {self.calls}")
        _write_atomic(path, b"corrupt" if self.calls in self.corrupt_calls else payload)


class BindingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.state_path = root / "bindings.json"
        self.lua_path = root / "luminophore_bindings.lua"
        self.service = BindingPersistenceService(self.state_path, self.lua_path)

    def test_baseline_snapshot_and_typed_update_write_canonical_pair(self) -> None:
        before = self.service.snapshot()
        self.assertIsNone(before.state_bytes)
        self.assertFalse(before.customized)
        self.assertEqual(len(before.generation_id), 64)
        self.assertEqual(before.reload_status, "unknown")
        result = self.service.apply(
            {"shell.settings": BindingUpdate("SUPER + Y", BindingFlags())}, before.digest,
        )
        after = self.service.snapshot()
        self.assertEqual(after.digest, result.digest)
        self.assertEqual(result.changed_actions, ("shell.settings",))
        self.assertTrue(result.reload_required)
        self.assertEqual(result.generation_id, after.generation_id)
        self.assertTrue(after.customized)
        self.assertEqual(after.reload_status, "unknown")
        self.assertIn(b'"action_id":"shell.settings","chord":"SUPER + Y"', self.state_path.read_bytes())
        self.assertIn(b'action_id = "shell.settings"', self.lua_path.read_bytes())

    def test_existing_files_do_not_imply_reload_pending_or_customization(self) -> None:
        before = self.service.snapshot()
        result = self.service.apply(
            {"shell.settings": BindingUpdate(None, BindingFlags())}, before.digest,
        )
        after = self.service.snapshot()
        self.assertTrue(result.reload_required)
        self.assertFalse(after.customized)
        self.assertEqual(after.reload_status, "unknown")
        self.assertEqual(result.generation_id, after.generation_id)

    def test_packaged_lua_without_custom_state_is_baseline_not_pending(self) -> None:
        self.lua_path.write_text("-- packaged source artifact\nreturn {}\n", encoding="utf-8")
        snapshot = self.service.snapshot()
        self.assertFalse(snapshot.customized)
        self.assertEqual(snapshot.reload_status, "unknown")
        self.assertIsNone(snapshot.state_bytes)
        self.assertIsNotNone(snapshot.lua_bytes)

    def test_generation_id_is_deterministic_and_changes_with_registry(self) -> None:
        baseline = self.service.snapshot()
        same = BindingPersistenceService(
            self.state_path.parent / "other.json", self.lua_path.parent / "other.lua",
        ).snapshot()
        self.assertEqual(baseline.generation_id, same.generation_id)
        result = self.service.apply(
            {"shell.settings": BindingUpdate("SUPER + Y", BindingFlags())}, baseline.digest,
        )
        self.assertNotEqual(result.generation_id, baseline.generation_id)

    def test_reapplying_same_candidate_is_digest_and_generation_idempotent(self) -> None:
        initial = self.service.snapshot()
        first = self.service.apply(
            {"shell.settings": BindingUpdate("SUPER + Y", BindingFlags())}, initial.digest,
        )
        second = self.service.apply(
            {"shell.settings": BindingUpdate("SUPER + Y", BindingFlags())}, first.digest,
        )
        self.assertEqual(second.digest, first.digest)
        self.assertEqual(second.generation_id, first.generation_id)

    def test_tampered_generation_id_is_rejected(self) -> None:
        initial = self.service.snapshot()
        self.service.apply(
            {"shell.settings": BindingUpdate("SUPER + Y", BindingFlags())}, initial.digest,
        )
        document = json.loads(self.state_path.read_bytes())
        document["generation_id"] = "0" * 64
        self.state_path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(BindingServiceError) as raised:
            self.service.snapshot()
        self.assertEqual(raised.exception.category, "validation")

    def test_expected_digest_conflict_has_zero_write(self) -> None:
        with self.assertRaises(BindingServiceError) as raised:
            self.service.apply({"shell.settings": BindingUpdate("SUPER + Y")}, "stale")
        self.assertEqual(raised.exception.category, "conflict")
        self.assertFalse(self.state_path.exists())
        self.assertFalse(self.lua_path.exists())

    def test_concurrent_same_digest_transactions_serialize_and_second_conflicts(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        calls = 0

        def blocking_writer(path: Path, payload: bytes) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                release.wait(1)
            _write_atomic(path, payload)

        service = BindingPersistenceService(
            self.state_path, self.lua_path, atomic_writer=blocking_writer,
        )
        digest = service.snapshot().digest
        outcomes: list[str] = []

        def apply(chord: str) -> None:
            try:
                service.apply({"shell.settings": BindingUpdate(chord)}, digest)
                outcomes.append("ok")
            except BindingServiceError as exc:
                outcomes.append(exc.category)

        first = threading.Thread(target=apply, args=("SUPER + Y",))
        second = threading.Thread(target=apply, args=("SUPER + U",))
        first.start()
        self.assertTrue(entered.wait(1))
        second.start()
        release.set()
        first.join(1)
        second.join(1)
        self.assertCountEqual(outcomes, ["ok", "conflict"])

    def test_custom_action_command_and_unknown_fields_are_forbidden(self) -> None:
        before = self.service.snapshot()
        with self.assertRaises(BindingServiceError) as raised:
            self.service.apply({"custom.command": BindingUpdate("SUPER + R")}, before.digest)
        self.assertEqual(raised.exception.category, "validation")

        payload = json.loads(before.registry.payload())
        payload["bindings"][0]["command"] = "rm -rf /"
        self.state_path.write_text(json.dumps(payload), encoding="utf-8")
        self.lua_path.write_bytes(b"placeholder")
        with self.assertRaises(BindingServiceError) as decoded:
            self.service.snapshot()
        self.assertEqual(decoded.exception.category, "validation")

    def test_all_registry_invariants_are_validated_before_write(self) -> None:
        before = self.service.snapshot()
        with self.assertRaises(BindingServiceError) as duplicate:
            self.service.apply({"shell.settings": BindingUpdate("SUPER + RETURN")}, before.digest)
        self.assertEqual(duplicate.exception.category, "validation")
        with self.assertRaises(BindingServiceError) as group:
            self.service.apply({"hardware.brightness_up.preview": BindingUpdate(None)}, before.digest)
        self.assertEqual(group.exception.category, "validation")
        self.assertFalse(self.state_path.exists())

    def test_second_file_failure_exactly_restores_existing_pair(self) -> None:
        initial = self.service.snapshot()
        self.service.apply({"shell.settings": BindingUpdate("SUPER + Y")}, initial.digest)
        old_state, old_lua = self.state_path.read_bytes(), self.lua_path.read_bytes()
        fault = FaultWriter(fail_calls=(2,))
        service = BindingPersistenceService(
            self.state_path, self.lua_path, atomic_writer=fault,
        )
        with self.assertRaises(BindingServiceError) as raised:
            service.apply({"shell.settings": BindingUpdate("SUPER + U")}, service.snapshot().digest)
        self.assertEqual(raised.exception.category, "write_failed_rolled_back")
        self.assertEqual((self.state_path.read_bytes(), self.lua_path.read_bytes()), (old_state, old_lua))

    def test_verification_failure_restores_absent_pre_state(self) -> None:
        fault = FaultWriter(corrupt_calls=(2,))
        service = BindingPersistenceService(
            self.state_path, self.lua_path, atomic_writer=fault,
        )
        with self.assertRaises(BindingServiceError) as raised:
            service.apply({"shell.settings": BindingUpdate("SUPER + Y")}, service.snapshot().digest)
        self.assertEqual(raised.exception.category, "write_failed_rolled_back")
        self.assertFalse(self.state_path.exists())
        self.assertFalse(self.lua_path.exists())

    def test_restore_failure_is_distinct(self) -> None:
        initial = self.service.snapshot()
        self.service.apply({"shell.settings": BindingUpdate("SUPER + Y")}, initial.digest)
        fault = FaultWriter(fail_calls=(2, 3))
        service = BindingPersistenceService(
            self.state_path, self.lua_path, atomic_writer=fault,
        )
        with self.assertRaises(BindingServiceError) as raised:
            service.apply({"shell.settings": BindingUpdate("SUPER + U")}, service.snapshot().digest)
        self.assertEqual(raised.exception.category, "rollback_failed")


if __name__ == "__main__":
    unittest.main()
