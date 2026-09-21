from __future__ import annotations

import subprocess
import unittest

from luminophore_shell.system_controls import (
    BluetoothDevice,
    PairingPromptBroker,
    SystemControlError,
    SystemControls,
    WifiNetwork,
    parse_bluetooth_state,
    parse_network_state,
)
from luminophore_shell.power_profiles import PowerProfileState


class FakePowerController:
    def __init__(self, state: PowerProfileState | None = None) -> None:
        self.state = state or PowerProfileState(True, "balanced", ("eco", "balanced", "gaming"))
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def snapshot(self) -> PowerProfileState:
        return self.state

    def set_profile(self, profile: str, available) -> None:
        self.calls.append((profile, tuple(available)))


class SystemControlTests(unittest.TestCase):
    def test_network_parser_preserves_escaped_ssid_and_active_network(self) -> None:
        state = parse_network_state(
            "enabled:enabled:connected:full\n",
            "yes:Cafe\\:Main:82:WPA2\nno:Cafe\\:Main:31:WPA2\nno:Guest:55:--\n",
            "enp5s0:ethernet:connected:Wired connection 1\nwlan0:wifi:connected:Cafe\\:Main\n",
        )
        self.assertTrue(state.wifi_enabled)
        self.assertEqual(state.connectivity, "full")
        self.assertEqual(state.networks[0].ssid, "Cafe:Main")
        self.assertTrue(state.networks[0].active)
        self.assertEqual(len(state.networks), 2)
        self.assertTrue(state.wifi_available)
        self.assertEqual(state.devices[0].kind, "ethernet")
        self.assertEqual(state.devices[1].connection, "Cafe:Main")

    def test_wired_connection_survives_missing_wifi_hardware(self) -> None:
        state = parse_network_state(
            "missing:disabled:connected:limited\n",
            "",
            "enp5s0:ethernet:connected:Wired connection 1\nlo:loopback:connected (externally):lo\n",
        )
        self.assertFalse(state.wifi_available)
        self.assertFalse(state.wifi_enabled)
        self.assertEqual(state.connectivity, "limited")
        self.assertTrue(state.devices[0].connected)

    def test_bluetooth_parser_tracks_pairing_and_connection(self) -> None:
        state = parse_bluetooth_state(
            "Controller AA:BB:CC:DD:EE:FF host\n\tPowered: yes\n",
            "Device 11:22:33:44:55:66 Keyboard\nDevice 22:33:44:55:66:77 Headset\n",
            "Device 11:22:33:44:55:66 Keyboard\nDevice 22:33:44:55:66:77 Headset\n",
            "Device 22:33:44:55:66:77 Headset\n",
        )
        self.assertTrue(state.powered)
        self.assertTrue(state.devices[0].paired)
        self.assertFalse(state.devices[0].connected)
        self.assertTrue(state.devices[1].connected)

    def test_actions_use_fixed_argv_and_reject_unpaired_connect(self) -> None:
        calls: list[tuple[str, ...]] = []

        def runner(argv):
            calls.append(tuple(argv))
            return subprocess.CompletedProcess(argv, 0, "", "")

        power = FakePowerController()
        controls = SystemControls(runner, power_controller=power)
        controls.set_wifi(False)
        controls.set_bluetooth(True)
        controls.set_power_profile("balanced", ("eco", "balanced", "gaming"))
        controls.connect_bluetooth(BluetoothDevice("11:22:33:44:55:66", "Keyboard", True, False))
        self.assertEqual(calls, [
            ("nmcli", "radio", "wifi", "off"),
            ("bluetoothctl", "power", "on"),
            ("bluetoothctl", "connect", "11:22:33:44:55:66"),
        ])
        self.assertEqual(power.calls, [("balanced", ("eco", "balanced", "gaming"))])
        with self.assertRaisesRegex(SystemControlError, "pairing_required"):
            controls.connect_bluetooth(BluetoothDevice("AA:BB:CC:DD:EE:FF", "New", False, False))

    def test_wifi_secret_uses_stdin_and_never_argv(self) -> None:
        calls: list[tuple[tuple[str, ...], str]] = []

        def sensitive_runner(argv, secret):
            calls.append((tuple(argv), secret))
            return subprocess.CompletedProcess(argv, 0, "", "")

        controls = SystemControls(
            lambda argv: subprocess.CompletedProcess(argv, 0, "", ""),
            sensitive_runner,
        )
        controls.connect_wifi(WifiNetwork("Private Net", 80, "WPA2", False), "top-secret")

        self.assertEqual(calls[0][0], ("nmcli", "--ask", "device", "wifi", "connect", "Private Net"))
        self.assertNotIn("top-secret", calls[0][0])
        self.assertEqual(calls[0][1], "top-secret")

    def test_secured_wifi_requires_secret_before_transport(self) -> None:
        calls: list[object] = []
        controls = SystemControls(
            lambda argv: (calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")),
            lambda argv, secret: (calls.append((argv, secret)) or subprocess.CompletedProcess(argv, 0, "", "")),
        )
        with self.assertRaisesRegex(SystemControlError, "wifi_secret_required"):
            controls.connect_wifi(WifiNetwork("Private", 80, "WPA3", False), "")
        self.assertEqual(calls, [])

    def test_provider_failures_are_isolated(self) -> None:
        def runner(argv):
            return subprocess.CompletedProcess(argv, 1, "", "missing")

        snapshot = SystemControls(runner, power_controller=FakePowerController(PowerProfileState(False, error="missing"))).snapshot()
        self.assertFalse(snapshot.network.available)
        self.assertFalse(snapshot.bluetooth.available)
        self.assertFalse(snapshot.power.available)

    def test_bluetooth_timeout_is_isolated_from_other_providers(self) -> None:
        def runner(argv):
            if argv[0] == "bluetoothctl":
                raise subprocess.TimeoutExpired(argv, 5)
            if argv[0] == "nmcli" and "general" in argv:
                return subprocess.CompletedProcess(argv, 0, "enabled:enabled:connected:full\n", "")
            if argv[0] == "nmcli" and "status" in argv:
                return subprocess.CompletedProcess(argv, 0, "wlan0:wifi:connected:Main\n", "")
            if argv[0] == "nmcli":
                return subprocess.CompletedProcess(argv, 0, "yes:Main:80:WPA2\n", "")
            return subprocess.CompletedProcess(argv, 0, "  performance:\n* balanced:\n", "")

        snapshot = SystemControls(runner, power_controller=FakePowerController()).snapshot()
        self.assertTrue(snapshot.network.available)
        self.assertFalse(snapshot.bluetooth.available)
        self.assertIn("timed out", snapshot.bluetooth.error)
        self.assertTrue(snapshot.power.available)

    def test_pairing_prompt_accept_cancel_and_expiry_are_explicit(self) -> None:
        broker = PairingPromptBroker()
        confirm = broker.begin("aa:bb:cc:dd:ee:ff", "Headset", "confirm", "123456")
        self.assertEqual(confirm.address, "AA:BB:CC:DD:EE:FF")
        self.assertTrue(broker.respond(confirm.request_id, accepted=True).accepted)
        pin = broker.begin("11:22:33:44:55:66", "Keyboard", "pin")
        with self.assertRaisesRegex(SystemControlError, "pin_required"):
            broker.respond(pin.request_id, accepted=True)
        stale = broker.begin("11:22:33:44:55:66", "Keyboard", "confirm")
        broker.cancel(stale.request_id)
        with self.assertRaisesRegex(SystemControlError, "expired"):
            broker.respond(stale.request_id, accepted=True)


if __name__ == "__main__":
    unittest.main()
