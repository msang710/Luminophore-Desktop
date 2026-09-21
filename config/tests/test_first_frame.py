from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from luminophore_shell.first_frame import FirstFrameSignalWriter, GtkFirstFrameProbe, runtime_signal_path


class FirstFrameSignalWriterTests(unittest.TestCase):
    def test_runtime_path_is_collector_contract(self) -> None:
        self.assertEqual(runtime_signal_path(1234), Path("/run/user/1234/luminophore-shell/first-frame-ready.json"))

    def test_atomic_private_signal_contains_process_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boot_id = root / "boot-id"
            boot_id.write_text("boot-1\n")
            signal = root / "runtime" / "first-frame-ready.json"
            writer = FirstFrameSignalWriter(
                signal,
                boot_id_path=boot_id,
                process_id=4321,
                monotonic_ns=lambda: 987654321,
                environment={"HYPRLAND_INSTANCE_SIGNATURE": "instance", "WAYLAND_DISPLAY": "wayland-1"},
            )
            self.assertEqual(writer.write(), signal)
            value = json.loads(signal.read_text())
            self.assertEqual(value["boot_id"], "boot-1")
            self.assertEqual(value["process_id"], 4321)
            self.assertEqual(value["signal_monotonic_ns"], 987654321)
            self.assertTrue(value["surface_committed"])
            self.assertTrue(value["frame_callback"])
            self.assertEqual(signal.stat().st_mode & 0o777, 0o600)
            self.assertEqual(signal.parent.stat().st_mode & 0o777, 0o700)

    def test_writer_refuses_to_claim_hyprland_without_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boot_id = root / "boot-id"
            boot_id.write_text("boot-1\n")
            writer = FirstFrameSignalWriter(
                root / "signal.json",
                boot_id_path=boot_id,
                environment={"WAYLAND_DISPLAY": "wayland-1"},
            )
            with self.assertRaisesRegex(RuntimeError, "Hyprland compositor identity"):
                writer.write()


class GtkFirstFrameProbeTests(unittest.TestCase):
    def test_signal_waits_for_completed_presentation_timing(self) -> None:
        writer = Mock()
        widget = Mock()
        clock = Mock()
        widget.get_frame_clock.return_value = clock
        clock.get_frame_counter.side_effect = [10, 11, 12]
        widget.add_tick_callback.return_value = 91
        incomplete = Mock()
        incomplete.get_complete.return_value = False
        presented = Mock()
        presented.get_complete.return_value = True
        presented.get_presentation_time.return_value = 123456
        clock.get_timings.side_effect = lambda counter: {10: incomplete, 11: presented}.get(counter)
        probe = GtkFirstFrameProbe(writer)

        probe.arm(widget)
        writer.write.assert_not_called()
        self.assertTrue(probe._on_later_tick(widget, clock))
        writer.write.assert_not_called()
        self.assertFalse(probe._on_later_tick(widget, clock))
        writer.write.assert_called_once_with()
        probe._on_later_tick(widget, clock)
        writer.write.assert_called_once_with()

    def test_complete_timing_without_presentation_time_does_not_signal(self) -> None:
        writer = Mock()
        widget = Mock()
        clock = Mock()
        widget.get_frame_clock.return_value = clock
        clock.get_frame_counter.side_effect = [3, 4]
        timing = Mock()
        timing.get_complete.return_value = True
        timing.get_presentation_time.return_value = 0
        clock.get_timings.return_value = timing
        probe = GtkFirstFrameProbe(writer)
        probe.arm(widget)
        self.assertTrue(probe._on_later_tick(widget, clock))
        writer.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
