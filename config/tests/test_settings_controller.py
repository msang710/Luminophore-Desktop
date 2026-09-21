from __future__ import annotations

from pathlib import Path
import re
import tempfile
import unittest

from luminophore_shell.config import load_config
from luminophore_shell.settings_controller import SettingsController, SettingsRuntimeApplyError
from luminophore_shell.settings_contract import SettingsCompletionUnknown


class SettingsControllerTests(unittest.TestCase):
    def test_unknown_completion_keeps_candidate_and_reports_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(Path("luminophore_shell/config.toml").read_text())
            calls = []
            def uncertain(old, new, changed):
                calls.append(new.visual)
                raise SettingsCompletionUnknown("lost acknowledgment")
            controller = SettingsController(path, uncertain)
            before = controller.open()
            draft = dict(before.values)
            draft["visual.enabled"] = False
            result = controller.apply(draft, before.digest)
            self.assertFalse(result.ok)
            self.assertEqual(result.state.error_category, "completion_unknown")
            self.assertFalse(load_config(path).visual.enabled)
            self.assertFalse(controller.snapshot.config.visual.enabled)
            self.assertEqual(len(calls), 1)

    def _controller(self, directory: str, applied: list[tuple]) -> SettingsController:
        path = Path(directory) / "config.toml"
        source = Path("luminophore_shell/config.toml").read_text(encoding="utf-8")
        source = re.sub(r'(?m)^palette_source\s*=.*$', 'palette_source = "hyprpaper"', source, count=1)
        source = re.sub(
            r"(?ms)(^\[theme\.fixed_monitor_palettes\]\n).*?(?=^\[)",
            r"\1\n",
            source,
        )
        path.write_text(source, encoding="utf-8")
        return SettingsController(path, lambda old, new, paths: applied.append((old, new, paths)))

    def test_apply_writes_all_dirty_fields_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            applied: list[tuple] = []
            controller = self._controller(directory, applied)
            snapshot = controller.open()
            draft = dict(snapshot.values)
            draft["layout.edge_margin"] = 29
            draft["notifications.toast_limit"] = 4

            result = controller.apply(draft, snapshot.digest)

            self.assertTrue(result.ok)
            self.assertEqual(result.snapshot.config.layout.edge_margin, 29)
            self.assertEqual(result.snapshot.config.notifications.toast_limit, 4)
            self.assertEqual(applied[0][2], frozenset({
                "layout.edge_margin",
                "notifications.toast_limit",
            }))
            self.assertEqual(controller.state.phase, "saved")

    def test_apply_persists_fixed_source_and_both_directly_edited_colors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            applied: list[tuple] = []
            controller = self._controller(directory, applied)
            snapshot = controller.open()
            draft = dict(snapshot.values)
            draft["theme.palette_source"] = "fixed"
            draft["theme.fixed_primary"] = "#112233"
            draft["theme.fixed_secondary"] = "#AABBCC"

            result = controller.apply(draft, snapshot.digest)

            self.assertTrue(result.ok)
            saved = load_config(controller.config_path)
            self.assertEqual(saved.theme.palette_source, "fixed")
            self.assertEqual(saved.theme.fixed_primary, "#112233")
            self.assertEqual(saved.theme.fixed_secondary, "#AABBCC")
            self.assertEqual(
                applied[0][2],
                frozenset({
                    "theme.palette_source",
                    "theme.fixed_primary",
                    "theme.fixed_secondary",
                }),
            )

    def test_apply_persists_monitor_fixed_palettes_in_same_atomic_patch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            applied: list[tuple] = []
            controller = self._controller(directory, applied)
            snapshot = controller.open()
            draft = dict(snapshot.values)
            draft["theme.palette_source"] = "fixed"
            draft["theme.fixed_monitor_palettes"] = {
                "DP-1": ["#112233", "#AABBCC"],
                "DP-2": ["#445566", "#DDEEFF"],
            }

            result = controller.apply(draft, snapshot.digest)

            self.assertTrue(result.ok)
            self.assertEqual(
                load_config(controller.config_path).theme.fixed_monitor_palettes,
                (("DP-1", "#112233", "#AABBCC"), ("DP-2", "#445566", "#DDEEFF")),
            )
            self.assertEqual(
                applied[0][2],
                frozenset({"theme.palette_source", "theme.fixed_monitor_palettes"}),
            )

    def test_conflict_keeps_dirty_draft_and_reports_category(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            applied: list[tuple] = []
            controller = self._controller(directory, applied)
            snapshot = controller.open()
            controller.config_path.write_text(
                controller.config_path.read_text(encoding="utf-8") + "\n# outside\n",
                encoding="utf-8",
            )
            draft = dict(snapshot.values)
            draft["layout.radius"] = 18

            result = controller.apply(draft, snapshot.digest)

            self.assertFalse(result.ok)
            self.assertEqual(result.state.error_category, "conflict")
            self.assertEqual(result.state.dirty_paths, frozenset({"layout.radius"}))
            self.assertEqual(applied, [])

    def test_invalid_draft_is_not_applied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            applied: list[tuple] = []
            controller = self._controller(directory, applied)
            snapshot = controller.open()
            draft = dict(snapshot.values)
            draft["metrics.pump_danger_rpm"] = draft["metrics.pump_warning_rpm"]

            result = controller.apply(draft, snapshot.digest)

            self.assertFalse(result.ok)
            self.assertEqual(result.state.error_category, "validation")
            self.assertEqual(applied, [])

    def test_runtime_failure_rolls_back_file_and_keeps_draft_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(Path("luminophore_shell/config.toml").read_text(encoding="utf-8"), encoding="utf-8")

            def fail_runtime(_old: object, _new: object, _paths: object) -> None:
                raise SettingsRuntimeApplyError("injected runtime failure")

            controller = SettingsController(path, fail_runtime)
            snapshot = controller.open()
            previous_bytes = path.read_bytes()
            old_blur_size = snapshot.config.visual.intensity
            draft = dict(snapshot.values)
            draft["visual.intensity"] = old_blur_size + 1

            result = controller.apply(draft, snapshot.digest)

            self.assertFalse(result.ok)
            self.assertEqual(result.state.error_category, "runtime_apply")
            self.assertEqual(result.state.dirty_paths, frozenset({"visual.intensity"}))
            self.assertEqual(load_config(path).visual.intensity, old_blur_size)
            self.assertEqual(controller.snapshot.config.visual.intensity, old_blur_size)
            self.assertEqual(path.read_bytes(), previous_bytes)


if __name__ == "__main__":
    unittest.main()
