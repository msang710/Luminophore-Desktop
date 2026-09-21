from __future__ import annotations

from dataclasses import dataclass
import re
import subprocess
from typing import Callable, Sequence

from .power_profiles import PowerProfileError, PowerProfileState, TunedPowerController


class SystemControlError(RuntimeError):
    pass


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
SensitiveRunner = Callable[[Sequence[str], str], subprocess.CompletedProcess[str]]


def run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=5)


def run_sensitive(argv: Sequence[str], secret: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, input=f"{secret}\n", check=False, capture_output=True, text=True, timeout=30)


def _checked(runner: Runner, argv: tuple[str, ...]) -> str:
    result = runner(argv)
    if result.returncode != 0:
        raise SystemControlError(f"backend_failed:{argv[0]}")
    return result.stdout


def _split_terse(line: str) -> list[str]:
    parts = re.split(r"(?<!\\):", line)
    return [part.replace(r"\:", ":").replace(r"\\", "\\") for part in parts]


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    signal: int
    security: str
    active: bool


@dataclass(frozen=True)
class NetworkDevice:
    interface: str
    kind: str
    state: str
    connection: str

    @property
    def connected(self) -> bool:
        return self.state.casefold().startswith("connected")


@dataclass(frozen=True)
class NetworkState:
    available: bool
    wifi_enabled: bool = False
    connectivity: str = "unknown"
    networks: tuple[WifiNetwork, ...] = ()
    error: str = ""
    wifi_available: bool = False
    devices: tuple[NetworkDevice, ...] = ()


@dataclass(frozen=True)
class BluetoothDevice:
    address: str
    name: str
    paired: bool
    connected: bool


@dataclass(frozen=True)
class BluetoothState:
    available: bool
    powered: bool = False
    devices: tuple[BluetoothDevice, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class SystemControlSnapshot:
    network: NetworkState
    bluetooth: BluetoothState
    power: PowerProfileState


@dataclass(frozen=True)
class PairingPrompt:
    request_id: int
    address: str
    name: str
    kind: str
    passkey: str = ""


@dataclass(frozen=True)
class PairingDecision:
    accepted: bool
    pin: str = ""


class PairingPromptBroker:
    KINDS = frozenset({"confirm", "pin", "passkey"})

    def __init__(self) -> None:
        self._next_id = 1
        self._pending: dict[int, PairingPrompt] = {}

    def begin(self, address: str, name: str, kind: str, passkey: str = "") -> PairingPrompt:
        if kind not in self.KINDS:
            raise ValueError("unsupported pairing prompt")
        prompt = PairingPrompt(self._next_id, address.upper(), name, kind, passkey)
        self._next_id += 1
        self._pending[prompt.request_id] = prompt
        return prompt

    def respond(self, request_id: int, *, accepted: bool, pin: str = "") -> PairingDecision:
        prompt = self._pending.pop(request_id, None)
        if prompt is None:
            raise SystemControlError("pairing_prompt_expired")
        if accepted and prompt.kind == "pin" and not pin:
            raise SystemControlError("pairing_pin_required")
        return PairingDecision(accepted, pin if accepted and prompt.kind == "pin" else "")

    def cancel(self, request_id: int) -> None:
        if self._pending.pop(request_id, None) is None:
            raise SystemControlError("pairing_prompt_expired")

    @property
    def pending(self) -> tuple[PairingPrompt, ...]:
        return tuple(self._pending.values())


def parse_network_state(general: str, wifi: str, devices: str = "") -> NetworkState:
    fields = _split_terse(general.strip())
    if len(fields) < 4:
        raise SystemControlError("decode_failed:nmcli-general")
    wifi_enabled = fields[1].casefold() == "enabled"
    wifi_available = fields[0].casefold() not in {"missing", "unavailable", "unknown"}
    networks: list[WifiNetwork] = []
    for line in wifi.splitlines():
        values = _split_terse(line)
        if len(values) < 4 or not values[1]:
            continue
        try:
            signal = max(0, min(100, int(values[2])))
        except ValueError:
            continue
        networks.append(WifiNetwork(values[1], signal, values[3], values[0].casefold() == "yes"))
    unique: dict[str, WifiNetwork] = {}
    for item in sorted(networks, key=lambda item: (item.active, item.signal), reverse=True):
        unique.setdefault(item.ssid, item)
    device_rows: list[NetworkDevice] = []
    for line in devices.splitlines():
        values = _split_terse(line)
        if len(values) < 4 or not values[0]:
            continue
        device_rows.append(NetworkDevice(values[0], values[1], values[2], values[3]))
    return NetworkState(
        True,
        wifi_enabled,
        fields[3],
        tuple(unique.values()),
        wifi_available=wifi_available,
        devices=tuple(device_rows),
    )


def _parse_device_lines(output: str) -> dict[str, str]:
    devices: dict[str, str] = {}
    for line in output.splitlines():
        match = re.match(r"^Device\s+([0-9A-Fa-f:]{17})\s+(.+)$", line.strip())
        if match:
            devices[match.group(1).upper()] = match.group(2)
    return devices


def parse_bluetooth_state(show: str, devices: str, paired: str, connected: str) -> BluetoothState:
    if "Controller " not in show:
        raise SystemControlError("decode_failed:bluetooth-controller")
    all_devices = _parse_device_lines(devices)
    paired_addresses = set(_parse_device_lines(paired))
    connected_addresses = set(_parse_device_lines(connected))
    rows = tuple(
        BluetoothDevice(address, name, address in paired_addresses, address in connected_addresses)
        for address, name in all_devices.items()
    )
    return BluetoothState(True, bool(re.search(r"^\s*Powered:\s+yes$", show, re.MULTILINE)), rows)


class SystemControls:
    def __init__(
        self,
        runner: Runner = run_command,
        sensitive_runner: SensitiveRunner = run_sensitive,
        power_controller: TunedPowerController | None = None,
    ) -> None:
        self.runner = runner
        self.sensitive_runner = sensitive_runner
        self.power_controller = power_controller or TunedPowerController()

    def snapshot(self) -> SystemControlSnapshot:
        try:
            network = parse_network_state(
                _checked(self.runner, ("nmcli", "-t", "-f", "WIFI-HW,WIFI,STATE,CONNECTIVITY", "general")),
                _checked(self.runner, ("nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "no")),
                _checked(self.runner, ("nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status")),
            )
        except (SystemControlError, OSError, subprocess.TimeoutExpired) as exc:
            network = NetworkState(False, error=str(exc))
        try:
            bluetooth = parse_bluetooth_state(
                _checked(self.runner, ("bluetoothctl", "show")),
                _checked(self.runner, ("bluetoothctl", "devices")),
                _checked(self.runner, ("bluetoothctl", "devices", "Paired")),
                _checked(self.runner, ("bluetoothctl", "devices", "Connected")),
            )
        except (SystemControlError, OSError, subprocess.TimeoutExpired) as exc:
            bluetooth = BluetoothState(False, error=str(exc))
        power = self.power_controller.snapshot()
        return SystemControlSnapshot(network, bluetooth, power)

    def set_wifi(self, enabled: bool) -> None:
        _checked(self.runner, ("nmcli", "radio", "wifi", "on" if enabled else "off"))

    def connect_wifi(self, network: WifiNetwork, password: str = "") -> None:
        if network.active:
            return
        secured = network.security.strip() not in {"", "--", "NONE"}
        if secured and not password:
            raise SystemControlError("wifi_secret_required")
        argv = ("nmcli", "--ask", "device", "wifi", "connect", network.ssid) if secured else (
            "nmcli", "device", "wifi", "connect", network.ssid
        )
        if secured:
            result = self.sensitive_runner(argv, password)
            if result.returncode != 0:
                raise SystemControlError("backend_failed:nmcli")
        else:
            _checked(self.runner, argv)

    def set_bluetooth(self, enabled: bool) -> None:
        _checked(self.runner, ("bluetoothctl", "power", "on" if enabled else "off"))

    def connect_bluetooth(self, device: BluetoothDevice) -> None:
        if not device.paired:
            raise SystemControlError("pairing_required")
        _checked(self.runner, ("bluetoothctl", "connect", device.address))

    def set_power_profile(self, profile: str, available: Sequence[str]) -> None:
        try:
            self.power_controller.set_profile(profile, available)
        except PowerProfileError as exc:
            raise SystemControlError(str(exc)) from exc
