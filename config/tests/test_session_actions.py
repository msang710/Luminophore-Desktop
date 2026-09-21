from __future__ import annotations

import subprocess
import unittest

from luminophore_shell.session_actions import SessionActionController, SessionActionError, action_argv, firmware_setup_supported


class SessionActionTests(unittest.TestCase):
    def test_only_approved_actions_have_fixed_argv(self) -> None:
        which = lambda name: f"/usr/bin/{name}"
        self.assertEqual(action_argv("reboot", which=which), ("/usr/bin/systemctl", "reboot"))
        self.assertEqual(action_argv("poweroff", which=which), ("/usr/bin/systemctl", "poweroff"))
        self.assertEqual(action_argv("firmware-setup", which=which), ("/usr/bin/systemctl", "reboot", "--firmware-setup"))
        for action in ("logout", "lock", "suspend", "lock-and-suspend"):
            with self.assertRaises(ValueError):
                action_argv(action, which=which)

    def test_session_scoped_actions_delegate_to_luminophore_lifecycle(self) -> None:
        calls = []
        controller = SessionActionController(
            lambda argv: subprocess.CompletedProcess(argv, 0, "", ""),
            lifecycle=lambda action: calls.append(action),
        )
        for action in ("lock", "suspend", "logout"):
            controller.execute(action, confirmed=True)
        self.assertEqual(calls, ["lock", "suspend", "logout"])

    def test_firmware_action_never_falls_back_to_reboot(self) -> None:
        calls: list[tuple[str, ...]] = []
        def runner(argv):
            calls.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 0, "no firmware option", "")
        controller = SessionActionController(runner, lambda name: f"/usr/bin/{name}")
        with self.assertRaisesRegex(SessionActionError, "unsupported"):
            controller.execute("firmware-setup", confirmed=True)
        self.assertEqual(calls, [("/usr/bin/systemctl", "--help")])

    def test_confirmation_is_required_before_any_action(self) -> None:
        controller = SessionActionController(lambda argv: subprocess.CompletedProcess(argv, 0, "", ""))
        with self.assertRaisesRegex(SessionActionError, "confirmation_required"):
            controller.execute("poweroff", confirmed=False)

    def test_failed_action_is_reported_without_a_second_fallback_command(self) -> None:
        calls = []
        def runner(argv):
            calls.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 1, "", "failed")
        controller = SessionActionController(runner, lambda name: f"/usr/bin/{name}")
        with self.assertRaisesRegex(SessionActionError, "action_failed:reboot"):
            controller.execute("reboot", confirmed=True)
        self.assertEqual(calls, [("/usr/bin/systemctl", "reboot")])


if __name__ == "__main__":
    unittest.main()
