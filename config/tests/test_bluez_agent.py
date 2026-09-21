from __future__ import annotations

import unittest

from luminophore_shell.bluez_agent import BluezAgentCore, PairingRejected
from luminophore_shell.system_controls import PairingDecision, SystemControlError


class BluezAgentCoreTests(unittest.TestCase):
    def test_confirmation_replies_only_after_explicit_accept(self) -> None:
        prompts = []
        replies = []
        errors = []
        core = BluezAgentCore(prompts.append)
        prompt = core.request("AA:BB:CC:DD:EE:FF", "Headset", "confirm", lambda: replies.append(True), errors.append, "123456")

        self.assertEqual(replies, [])
        self.assertEqual(prompts, [prompt])
        core.resolve(prompt.request_id, PairingDecision(True))
        self.assertEqual(replies, [True])
        self.assertEqual(errors, [])

    def test_pin_is_returned_once_and_not_retained(self) -> None:
        values = []
        core = BluezAgentCore(lambda _prompt: None)
        prompt = core.request("11:22:33:44:55:66", "Keyboard", "pin", values.append, lambda _error: None)
        core.resolve(prompt.request_id, PairingDecision(True, "0420"))
        self.assertEqual(values, ["0420"])
        self.assertEqual(core.broker.pending, ())
        self.assertEqual(core.replies, {})

    def test_reject_and_cancel_complete_pending_dbus_calls(self) -> None:
        errors = []
        core = BluezAgentCore(lambda _prompt: None)
        rejected = core.request("11:22:33:44:55:66", "Keyboard", "confirm", lambda: None, errors.append)
        core.resolve(rejected.request_id, PairingDecision(False))
        self.assertIsInstance(errors[-1], PairingRejected)
        canceled = core.request("11:22:33:44:55:66", "Keyboard", "confirm", lambda: None, errors.append)
        core.cancel_all()
        self.assertEqual(core.replies, {})
        with self.assertRaisesRegex(SystemControlError, "expired"):
            core.resolve(canceled.request_id, PairingDecision(True))


if __name__ == "__main__":
    unittest.main()
