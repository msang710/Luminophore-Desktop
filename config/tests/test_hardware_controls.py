from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from luminophore_shell.hardware_controls import AudioController, DdcBrightnessController, DdcBrightnessQueue, DdcTarget, HardwareControlError, MediaController, discover_ddc_targets, parse_audio_application_targets, parse_ddc_brightness, parse_ddc_detect, parse_ddc_power_mode, parse_wpctl_volume, read_audio_quick_state, read_brightness_quick_state, read_hardware_quick_state


class HardwareControlTests(unittest.TestCase):
    def test_audio_and_media_use_fixed_argv(self) -> None:
        calls: list[tuple[str, ...]] = []
        runner = lambda argv: (calls.append(tuple(argv)) or subprocess.CompletedProcess(argv, 0, "", ""))
        AudioController(runner).volume(5)
        AudioController(runner).set_output_percent(37)
        AudioController(runner).toggle_input_mute()
        MediaController(runner).run("next")
        self.assertEqual(calls[0], ("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "5%+", "--limit", "1.0"))
        self.assertEqual(calls[1], ("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "37%", "--limit", "1.0"))
        self.assertEqual(calls[2], ("wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"))
        self.assertEqual(calls[3], ("playerctl", "next"))

    def test_ddc_is_bus_scoped_and_parses_value(self) -> None:
        calls: list[tuple[str, ...]] = []
        def runner(argv):
            calls.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 0, "VCP code 0x10 (Brightness): current value = 42, max value = 100", "")
        controller = DdcBrightnessController(runner)
        target = DdcTarget("DP-1", 7, "digest")
        self.assertEqual(controller.get(target), (42, 100))
        controller.set_percent(target, 55)
        self.assertEqual(calls[0], ("ddcutil", "--bus", "7", "getvcp", "0x10", "--terse"))
        self.assertEqual(calls[1], ("ddcutil", "--bus", "7", "setvcp", "0x10", "55", "--noverify"))

    def test_ddc_decode_rejects_unknown_output(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "decode_failed"):
            parse_ddc_brightness("not a VCP response")

    def test_ddc_decode_accepts_v227_terse_output(self) -> None:
        self.assertEqual(parse_ddc_brightness("VCP 10 C 70 100\n"), (70, 100))

    def test_wpctl_state_parses_percent_and_mute(self) -> None:
        self.assertEqual(parse_wpctl_volume("Volume: 0.42 [MUTED]"), (42, True))
        self.assertEqual(parse_wpctl_volume("Volume: 1.00"), (100, False))

    def test_audio_application_streams_group_running_nodes_by_program(self) -> None:
        payload = [
            {
                "id": 32,
                "type": "PipeWire:Interface:Node",
                "info": {
                    "state": "running",
                    "props": {
                        "media.class": "Stream/Output/Audio",
                        "application.name": "Google Chrome",
                        "application.process.binary": "chrome",
                        "application.icon-name": "google-chrome",
                    },
                },
            },
            {
                "id": 35,
                "type": "PipeWire:Interface:Node",
                "info": {
                    "state": "running",
                    "props": {
                        "media.class": "Stream/Output/Audio",
                        "application.name": "Google Chrome",
                        "application.process.binary": "chrome",
                    },
                },
            },
            {
                "id": 44,
                "type": "PipeWire:Interface:Node",
                "info": {
                    "state": "suspended",
                    "props": {
                        "media.class": "Stream/Output/Audio",
                        "application.name": "Spotify",
                        "application.process.binary": "spotify",
                    },
                },
            },
        ]

        targets = parse_audio_application_targets(payload)

        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].key, "chrome")
        self.assertEqual(targets[0].node_ids, (32, 35))
        self.assertEqual(targets[0].icon_hint, "google-chrome")

    def test_audio_application_volume_controls_each_grouped_node(self) -> None:
        calls: list[tuple[str, ...]] = []
        runner = lambda argv: (calls.append(tuple(argv)) or subprocess.CompletedProcess(argv, 0, "", ""))
        audio = AudioController(runner)

        audio.set_application_percent((32, 35), 61)
        audio.set_application_muted((32, 35), True)

        self.assertEqual(
            calls,
            [
                ("wpctl", "set-volume", "32", "61%", "--limit", "1.0"),
                ("wpctl", "set-volume", "35", "61%", "--limit", "1.0"),
                ("wpctl", "set-mute", "32", "1"),
                ("wpctl", "set-mute", "35", "1"),
            ],
        )

    def test_audio_application_state_averages_grouped_stream_volumes(self) -> None:
        payload = [
            {
                "id": node_id,
                "type": "PipeWire:Interface:Node",
                "info": {
                    "state": "running",
                    "props": {
                        "media.class": "Stream/Output/Audio",
                        "application.name": "Browser",
                        "application.process.binary": "browser",
                    },
                },
            }
            for node_id in (10, 11)
        ]

        def runner(argv):
            if tuple(argv) == ("pw-dump", "--no-colors"):
                return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
            volumes = {"10": "Volume: 0.20", "11": "Volume: 0.60 [MUTED]"}
            return subprocess.CompletedProcess(argv, 0, volumes[str(argv[-1])], "")

        state = AudioController(runner).application_states()[0]

        self.assertEqual(state.percent, 40)
        self.assertFalse(state.muted)
        self.assertEqual(state.node_ids, (10, 11))

    def test_audio_and_brightness_quick_reads_are_independent(self) -> None:
        class Audio:
            def output_state(self):
                return 52, False

            def application_states(self):
                return ()

        class Brightness:
            def get_powered(self, _target):
                return True

            def get(self, target):
                return 75 if target.connector == "DP-1" else 60, 100

        audio_state = read_audio_quick_state(Audio())
        brightness_state = read_brightness_quick_state(
            Brightness(),
            {"DP-1": DdcTarget("DP-1", 3), "DP-2": DdcTarget("DP-2", 5)},
        )

        self.assertEqual(audio_state.volume_percent, 52)
        self.assertEqual(audio_state.monitors, ())
        self.assertIsNone(brightness_state.volume_percent)
        self.assertEqual([monitor.percent for monitor in brightness_state.monitors], [75, 60])

    def test_ddc_discovery_preserves_connector_bus_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            connector = root / "card1-DP-2"
            (connector / "ddc/i2c-dev/i2c-7").mkdir(parents=True)
            (connector / "status").write_text("connected\n", encoding="utf-8")
            disconnected = root / "card1-DP-1"
            (disconnected / "ddc/i2c-dev/i2c-8").mkdir(parents=True)
            (disconnected / "status").write_text("disconnected\n", encoding="utf-8")

            targets = discover_ddc_targets(root)

        self.assertEqual(targets, {"DP-2": DdcTarget("DP-2", 7)})

    def test_ddc_detect_fills_connected_connectors_without_sysfs_ddc_link(self) -> None:
        output = """Display 1
   I2C bus:          /dev/i2c-3
   DRM connector:    card1-DP-1

Display 2
   I2C bus:          /dev/i2c-5
   DRM connector:    card1-DP-2
"""
        self.assertEqual(
            parse_ddc_detect(output),
            {"DP-1": DdcTarget("DP-1", 3), "DP-2": DdcTarget("DP-2", 5)},
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for name in ("card1-DP-1", "card1-DP-2"):
                connector = root / name
                connector.mkdir()
                (connector / "status").write_text("connected\n", encoding="utf-8")

            def runner(argv):
                self.assertEqual(tuple(argv), ("ddcutil", "detect", "--brief"))
                return subprocess.CompletedProcess(argv, 0, output, "")

            targets = discover_ddc_targets(root, runner)

        self.assertEqual(targets["DP-1"].bus, 3)
        self.assertEqual(targets["DP-2"].bus, 5)

    def test_adjust_brightness_clamps_and_uses_same_bus(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(argv):
            calls.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 0, "current value = 98, max value = 100", "")

        value = DdcBrightnessController(runner).adjust_percent(DdcTarget("DP-2", 7), 5)

        self.assertEqual(value, 100)
        self.assertEqual(calls[-1], ("ddcutil", "--bus", "7", "setvcp", "0x10", "100", "--noverify"))

    def test_ddc_power_mode_and_commands(self) -> None:
        self.assertTrue(parse_ddc_power_mode("VCP D6 SNC x01"))
        self.assertFalse(parse_ddc_power_mode("current value = 0x05"))
        calls: list[tuple[str, ...]] = []

        def runner(argv):
            calls.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 0, "VCP D6 SNC x01", "")

        controller = DdcBrightnessController(runner)
        target = DdcTarget("DP-1", 3)
        self.assertTrue(controller.get_powered(target))
        controller.set_powered(target, False)
        self.assertEqual(calls[-1], ("ddcutil", "--bus", "3", "setvcp", "0xD6", "0x05", "--noverify"))

    def test_power_request_is_not_replaced_by_following_brightness(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        calls: list[tuple[str, object]] = []

        class FakeController:
            def set_powered(self, _target, powered):
                calls.append(("power", powered))
                entered.set()
                release.wait(1)

            def set_percent(self, _target, value):
                calls.append(("percent", value))

        queue = DdcBrightnessQueue(FakeController())
        target = DdcTarget("DP-1", 3)
        queue.submit_power(target, True, lambda _value: None, lambda _exc: None)
        self.assertTrue(entered.wait(1))
        queue.submit_percent(target, 55, lambda _value: completed.set(), lambda _exc: None)
        release.set()
        self.assertTrue(completed.wait(1))
        self.assertEqual(calls, [("power", True), ("percent", 55)])

    def test_ddc_queue_coalesces_repeated_delta_per_bus(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        deltas: list[int] = []

        class FakeController:
            def adjust_percent(self, _target, delta):
                deltas.append(delta)
                if len(deltas) == 1:
                    entered.set()
                    release.wait(1)
                return 50 + delta

        queue = DdcBrightnessQueue(FakeController())
        target = DdcTarget("DP-2", 7)
        queue.submit_delta(target, 5, lambda _value: None, lambda _exc: None)
        self.assertTrue(entered.wait(1))
        queue.submit_delta(target, 5, lambda _value: None, lambda _exc: None)
        queue.submit_delta(target, 5, lambda _value: completed.set(), lambda _exc: None)
        release.set()
        self.assertTrue(completed.wait(1))
        self.assertEqual(deltas, [5, 10])

    def test_ddc_queue_keeps_latest_absolute_slider_value(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        values: list[int] = []

        class FakeController:
            def set_percent(self, _target, value):
                values.append(value)
                if len(values) == 1:
                    entered.set()
                    release.wait(1)

        queue = DdcBrightnessQueue(FakeController())
        target = DdcTarget("DP-2", 7)
        queue.submit_percent(target, 40, lambda _value: None, lambda _exc: None)
        self.assertTrue(entered.wait(1))
        queue.submit_percent(target, 55, lambda _value: None, lambda _exc: None)
        queue.submit_percent(target, 62, lambda _value: completed.set(), lambda _exc: None)
        release.set()
        self.assertTrue(completed.wait(1))
        self.assertEqual(values, [40, 62])

    def test_quick_state_isolates_monitor_read_failure(self) -> None:
        class Audio:
            def output_state(self):
                return 37, True

            def application_states(self):
                return ()

        class Brightness:
            def get_powered(self, _target):
                return True

            def get(self, target):
                if target.connector == "DP-2":
                    raise HardwareControlError("ddc failed")
                return 70, 100

        state = read_hardware_quick_state(
            Audio(),
            Brightness(),
            {"DP-2": DdcTarget("DP-2", 7), "DP-1": DdcTarget("DP-1", 8)},
        )
        self.assertEqual((state.volume_percent, state.volume_muted), (37, True))
        self.assertEqual(state.monitors[0].percent, 70)
        self.assertIsNone(state.monitors[1].percent)
        self.assertIn("ddc failed", state.monitors[1].error)


if __name__ == "__main__":
    unittest.main()
