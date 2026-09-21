from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest

from PIL import Image

from luminophore_shell.appearance_service import AppearanceService, AppearanceServiceError
from luminophore_shell.appearance_types import (
    AppearanceErrorCategory, AppearanceMode, AppearanceSource, AppearanceSourceKind,
    CompiledAppearance, MonitorAssignment, WallpaperProviderName,
)
from luminophore_shell.wallpaper_providers import HyprpaperProvider, WallpaperProviderError, _snapshot


class Compiler:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.requests = []

    def compile(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return CompiledAppearance.build(
            {"primary": "#112233"}, {"primary": "#112233"},
            {name: f"{request.source.value}:{name}".encode() for name in request.required_outputs},
        )


class Provider:
    name = WallpaperProviderName.HYPRPAPER

    def __init__(self, assignments):
        self.assignments = tuple(assignments)
        self.apply_calls = 0
        self.restore_calls = 0
        self.fail_apply = False
        self.fail_restore = False

    def validate(self, assignments):
        return tuple(sorted(assignments, key=lambda row: row.connector))

    def query(self, connectors):
        return _snapshot(self.name, {row.connector: row.source for row in self.assignments}, connectors)

    def apply_batch(self, assignments, previous):
        self.apply_calls += 1
        if self.fail_apply:
            raise WallpaperProviderError(AppearanceErrorCategory.APPLY_FAILED_ROLLED_BACK, "failed")
        self.assignments = tuple(assignments)
        return self.query(row.connector for row in self.assignments)

    def verify(self, expected):
        return None

    def restore(self, snapshot):
        self.restore_calls += 1
        if self.fail_restore:
            raise RuntimeError("restore failed")
        self.assignments = snapshot.assignments


class Store:
    def __init__(self) -> None:
        self.value = "old"
        self.fail_apply = False
        self.restore_calls = 0
        self.compiled = None

    def snapshot(self):
        return self.value

    def apply(self, compiled, provider):
        self.compiled = dict(compiled)
        self.value = "partial" if self.fail_apply else "new"
        if self.fail_apply:
            raise RuntimeError("state write failed")

    def restore(self, snapshot):
        self.restore_calls += 1
        self.value = snapshot


class AppearanceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old = Path(self.temp.name) / "old.png"
        self.new = Path(self.temp.name) / "new.png"
        Image.new("RGB", (8, 8), "#111111").save(self.old)
        Image.new("RGB", (8, 8), "#222222").save(self.new)
        self.provider = Provider((MonitorAssignment("DP-1", AppearanceSource(AppearanceSourceKind.IMAGE, str(self.old))),))
        self.compiler = Compiler()
        self.store = Store()
        self.config = ["config-1"]
        self.connectors = ["DP-1"]
        self.service = AppearanceService(
            {WallpaperProviderName.HYPRPAPER: self.provider}, self.compiler, self.store,
            lambda: tuple(self.connectors), lambda: self.config[0], lambda: ("gtk4", "hyprland"),
        )

    def _preview(self):
        return self.service.preview(
            WallpaperProviderName.HYPRPAPER,
            {"DP-1": AppearanceSource(AppearanceSourceKind.IMAGE, str(self.new))},
            AppearanceMode.DARK,
        )

    def test_compile_failure_has_zero_mutation(self) -> None:
        self.compiler.error = RuntimeError("compile failed")
        with self.assertRaises(AppearanceServiceError):
            self._preview()
        self.assertEqual(self.provider.apply_calls, 0)
        self.assertEqual(self.store.value, "old")

    def test_palette_only_empty_outputs_preview_and_apply_without_system_theme_mutation(self) -> None:
        system_theme_mutations: list[object] = []
        service = AppearanceService(
            {WallpaperProviderName.HYPRPAPER: self.provider}, self.compiler, self.store,
            lambda: tuple(self.connectors), lambda: self.config[0], lambda: (),
        )
        preview = service.preview(
            WallpaperProviderName.HYPRPAPER,
            {"DP-1": AppearanceSource(AppearanceSourceKind.IMAGE, str(self.new))},
            AppearanceMode.DARK,
        )
        self.assertEqual(self.compiler.requests[-1].required_outputs, ())
        self.assertEqual(dict(preview.compiled)["DP-1"].outputs, ())
        service.apply(preview.token)
        self.assertEqual(self.store.value, "new")
        self.assertEqual(dict(self.store.compiled)["DP-1"].palette, (("primary", "#112233"),))
        # AppearanceService has no system-theme collaborator; it only persists
        # the widget palette. The separate action therefore remains untouched.
        self.assertEqual(system_theme_mutations, [])

    def test_stale_config_or_topology_blocks_before_mutation(self) -> None:
        preview = self._preview()
        self.config[0] = "config-2"
        with self.assertRaises(AppearanceServiceError) as raised:
            self.service.apply(preview.token)
        self.assertEqual(raised.exception.category, AppearanceErrorCategory.STALE_PREVIEW)
        self.assertEqual(self.provider.apply_calls, 0)

    def test_state_failure_restores_exact_wallpaper_and_state(self) -> None:
        preview = self._preview()
        self.store.fail_apply = True
        with self.assertRaises(AppearanceServiceError) as raised:
            self.service.apply(preview.token)
        self.assertEqual(raised.exception.category, AppearanceErrorCategory.APPLY_FAILED_ROLLED_BACK)
        self.assertEqual(self.provider.assignments[0].source.value, str(self.old))
        self.assertEqual(self.store.value, "old")
        self.assertEqual((self.provider.restore_calls, self.store.restore_calls), (1, 1))

    def test_rollback_failure_is_distinct(self) -> None:
        preview = self._preview()
        self.store.fail_apply = True
        self.provider.fail_restore = True
        with self.assertRaises(AppearanceServiceError) as raised:
            self.service.apply(preview.token)
        self.assertEqual(raised.exception.category, AppearanceErrorCategory.ROLLBACK_FAILED)

    def test_single_lock_rejects_concurrent_apply(self) -> None:
        preview = self._preview()
        self.service._lock.acquire()
        try:
            with self.assertRaises(AppearanceServiceError) as raised:
                self.service.apply(preview.token)
        finally:
            self.service._lock.release()
        self.assertEqual(raised.exception.category, AppearanceErrorCategory.BUSY)


if __name__ == "__main__":
    unittest.main()
