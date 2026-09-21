from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from PIL import Image

from luminophore_shell.appearance_service import AppearanceService, AppearanceServiceError
from luminophore_shell.appearance_types import (
    AppearanceErrorCategory,
    AppearanceMode,
    AppearanceSource,
    AppearanceSourceKind,
    CompiledAppearance,
    MonitorAssignment,
    WallpaperProviderName,
)
from luminophore_shell.settings_backend import SettingsBackend
from luminophore_shell.settings_contract import SettingsResultCategory, decode_message, encode_message
from luminophore_shell.settings_coordinator import SettingsApplyCoordinator
from luminophore_shell.wallpaper_providers import WallpaperProviderError, _snapshot


class _Compiler:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.calls = 0

    def compile(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        return CompiledAppearance.build(
            {"primary": "#123456"}, {"primary": "#123456"},
            {name: f"compiled:{name}".encode() for name in request.required_outputs},
        )


class _Provider:
    name = WallpaperProviderName.HYPRPAPER

    def __init__(self, assignment: MonitorAssignment) -> None:
        self.assignment = assignment
        self.apply_calls = 0
        self.restore_calls = 0
        self.partial_failure = False

    def validate(self, assignments):
        return tuple(assignments)

    def query(self, connectors):
        return _snapshot(self.name, {self.assignment.connector: self.assignment.source}, connectors)

    def apply_batch(self, assignments, previous):
        self.apply_calls += 1
        candidate = tuple(assignments)[0]
        if self.partial_failure:
            # Models an adapter which changed one output and compensated it
            # before reporting the transaction failure.
            self.assignment = candidate
            self.assignment = previous.assignments[0]
            raise WallpaperProviderError(
                AppearanceErrorCategory.APPLY_FAILED_ROLLED_BACK,
                "provider batch failed and was compensated",
            )
        self.assignment = candidate
        return self.query((candidate.connector,))

    def verify(self, expected): return None

    def restore(self, snapshot):
        self.restore_calls += 1
        self.assignment = snapshot.assignments[0]


class _AppearanceState:
    def __init__(self) -> None:
        self.value = "old"
        self.applies = 0
    def snapshot(self): return self.value
    def apply(self, compiled, provider):
        self.applies += 1
        self.value = "new"
    def restore(self, snapshot): self.value = snapshot


class _SettingsWriter:
    def __init__(self) -> None:
        self.current_digest = "settings-1"
        self.values = {}
        self.writes = 0
    def digest(self): return self.current_digest
    def validate(self, changes): return None
    def snapshot(self): return (dict(self.values), self.current_digest)
    def write_atomic(self, changes, expected_digest):
        if expected_digest != self.current_digest:
            raise RuntimeError("unexpected digest in handoff test")
        self.writes += 1
        self.values.update(changes)
        self.current_digest = f"settings-{self.writes + 1}"
        return self.current_digest
    def restore_atomic(self, snapshot):
        self.values, self.current_digest = dict(snapshot[0]), snapshot[1]
        return self.current_digest


class _AppearanceRuntime:
    def __init__(self, service: AppearanceService, token) -> None:
        self.service = service
        self.token = token
        self.applies = 0
    def apply(self, changes):
        self.applies += 1
        self.service.apply(self.token)
    def verify(self, changes): return None
    def rollback(self, snapshot): return None


class _NoopRuntime:
    def apply(self, changes): return None
    def verify(self, changes): return None
    def rollback(self, snapshot): return None


class AppearanceIpcHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old = Path(self.temp.name) / "old-private-wallpaper.png"
        self.new = Path(self.temp.name) / "new-private-wallpaper.png"
        Image.new("RGB", (8, 8), "#111111").save(self.old)
        Image.new("RGB", (8, 8), "#222222").save(self.new)
        old_assignment = MonitorAssignment(
            "DP-1", AppearanceSource(AppearanceSourceKind.IMAGE, str(self.old)),
        )
        self.provider = _Provider(old_assignment)
        self.compiler = _Compiler()
        self.appearance_state = _AppearanceState()
        self.appearance = AppearanceService(
            {WallpaperProviderName.HYPRPAPER: self.provider}, self.compiler,
            self.appearance_state, lambda: ("DP-1",), lambda: "appearance-config-1",
            lambda: ("gtk4", "hyprland"),
        )

    def _preview(self):
        return self.appearance.preview(
            WallpaperProviderName.HYPRPAPER,
            {"DP-1": AppearanceSource(AppearanceSourceKind.IMAGE, str(self.new))},
            AppearanceMode.DARK,
        )

    def test_preview_token_single_apply_then_status_metadata(self) -> None:
        preview = self._preview()
        writer = _SettingsWriter()
        runtime = _AppearanceRuntime(self.appearance, preview.token)
        backend = SettingsBackend(SettingsApplyCoordinator(
            writer, runtime, lambda: True,
            timeout_call=lambda operation, _timeout: (operation(), True)[1],
        ))
        apply_wire = backend.handle(encode_message({
            "version": 1, "command": "apply", "request_id": "handoff-1",
            "expected_digest": "settings-1", "changes": {"appearance.preview_id": preview.token.preview_id},
        }))
        applied = decode_message(apply_wire)
        status_wire = backend.handle(encode_message({
            "version": 1, "command": "status", "request_id": "handoff-1",
        }))
        status = decode_message(status_wire)
        self.assertEqual((applied["category"], status["category"]), ("ok", "ok"))
        self.assertEqual((self.provider.apply_calls, self.appearance_state.applies, runtime.applies), (1, 1, 1))
        self.assertNotIn(str(self.new).encode(), status_wire)
        with self.assertRaises(AppearanceServiceError) as reused:
            self.appearance.apply(preview.token)
        self.assertEqual(reused.exception.category, AppearanceErrorCategory.STALE_PREVIEW)

    def test_compile_failure_has_zero_cross_boundary_mutation(self) -> None:
        self.compiler.error = RuntimeError(f"cannot compile {self.new}")
        with self.assertRaises(AppearanceServiceError):
            self._preview()
        self.assertEqual((self.provider.apply_calls, self.appearance_state.applies), (0, 0))
        self.assertEqual(self.provider.assignment.source.value, str(self.old))

    def test_provider_partial_failure_is_compensated_before_state_handoff(self) -> None:
        preview = self._preview()
        self.provider.partial_failure = True
        with self.assertRaises(AppearanceServiceError) as raised:
            self.appearance.apply(preview.token)
        self.assertEqual(raised.exception.category, AppearanceErrorCategory.APPLY_FAILED_ROLLED_BACK)
        self.assertEqual(self.provider.assignment.source.value, str(self.old))
        self.assertEqual(self.appearance_state.applies, 0)

    def test_timeout_duplicate_request_returns_exact_status_without_private_value(self) -> None:
        writer = _SettingsWriter()
        backend = SettingsBackend(SettingsApplyCoordinator(
            writer, _NoopRuntime(), lambda: True,
            timeout_call=lambda _operation, _timeout: False,
        ))
        private_source = str(self.new)
        request = encode_message({
            "version": 1, "command": "apply", "request_id": "timeout-1",
            "expected_digest": "settings-1", "changes": {"appearance.wallpaper_path": private_source},
        })
        first_wire = backend.handle(request)
        duplicate_wire = backend.handle(request)
        status_wire = backend.handle(encode_message({
            "version": 1, "command": "status", "request_id": "timeout-1",
        }))
        first = decode_message(first_wire)
        self.assertEqual(first["category"], SettingsResultCategory.COMPLETION_UNKNOWN.value)
        self.assertEqual(first_wire, duplicate_wire)
        status = decode_message(status_wire)
        self.assertEqual(status["category"], first["category"])
        self.assertEqual(status["phase"], first["phase"])
        self.assertEqual(status["digest"], first["digest"])
        self.assertNotIn(private_source.encode(), first_wire + duplicate_wire + status_wire)
        self.assertNotIn("values", status)


if __name__ == "__main__":
    unittest.main()
