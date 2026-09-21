from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from luminophore_shell.greeter_power import GreeterPowerController, POWER_ARGV


class GreeterPowerTests(unittest.TestCase):
    def test_actions_use_only_exact_fixed_argv(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(argv):
            calls.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as raw:
            efi = Path(raw) / "efi"
            efi.mkdir()
            controller = GreeterPowerController(runner, efi)
            for action in ("reboot", "poweroff", "firmware"):
                self.assertTrue(controller.request(action).success)

        self.assertEqual(calls, [POWER_ARGV["reboot"], POWER_ARGV["poweroff"], POWER_ARGV["firmware"]])

    def test_firmware_unsupported_never_falls_back_to_reboot(self) -> None:
        calls: list[tuple[str, ...]] = []
        controller = GreeterPowerController(
            lambda argv: calls.append(tuple(argv)) or subprocess.CompletedProcess(argv, 0, "", ""),
            Path("/definitely/not/efi"),
        )
        result = controller.request("firmware")
        self.assertFalse(result.success)
        self.assertEqual(result.category, "unsupported")
        self.assertEqual(calls, [])

    def test_controller_is_single_flight(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def runner(argv):
            entered.set()
            release.wait(2)
            return subprocess.CompletedProcess(argv, 0, "", "")

        controller = GreeterPowerController(runner)
        worker = threading.Thread(target=lambda: controller.request("reboot"))
        worker.start()
        self.assertTrue(entered.wait(1))
        busy = controller.request("poweroff")
        release.set()
        worker.join()
        self.assertFalse(busy.success)
        self.assertEqual(busy.category, "busy")


if __name__ == "__main__":
    unittest.main()
