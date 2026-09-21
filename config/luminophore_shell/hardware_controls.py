from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import threading
from typing import Callable, Sequence


class HardwareControlError(RuntimeError):
    pass


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=4)


def _checked(runner: Runner, argv: tuple[str, ...]) -> str:
    result = runner(argv)
    if result.returncode != 0:
        raise HardwareControlError(f"backend_failed:{argv[0]}")
    return result.stdout


class AudioController:
    def __init__(self, runner: Runner = run_command) -> None:
        self.runner = runner

    def volume(self, delta_percent: int) -> None:
        if delta_percent == 0 or abs(delta_percent) > 100:
            raise ValueError("volume delta must be between -100 and 100 and non-zero")
        sign = "+" if delta_percent > 0 else "-"
        _checked(self.runner, ("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{abs(delta_percent)}%{sign}", "--limit", "1.0"))

    def set_output_percent(self, percent: int) -> None:
        if not 0 <= percent <= 100:
            raise ValueError("volume percent must be between 0 and 100")
        _checked(self.runner, ("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{percent}%", "--limit", "1.0"))

    def set_application_percent(self, node_ids: Sequence[int], percent: int) -> None:
        if not 0 <= percent <= 100:
            raise ValueError("volume percent must be between 0 and 100")
        for node_id in _validated_node_ids(node_ids):
            _checked(self.runner, ("wpctl", "set-volume", str(node_id), f"{percent}%", "--limit", "1.0"))

    def set_application_muted(self, node_ids: Sequence[int], muted: bool) -> None:
        for node_id in _validated_node_ids(node_ids):
            _checked(self.runner, ("wpctl", "set-mute", str(node_id), "1" if muted else "0"))

    def toggle_output_mute(self) -> None:
        _checked(self.runner, ("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"))

    def toggle_input_mute(self) -> None:
        _checked(self.runner, ("wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"))

    def output_state(self) -> tuple[int, bool]:
        return parse_wpctl_volume(_checked(self.runner, ("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@")))

    def input_state(self) -> tuple[int, bool]:
        return parse_wpctl_volume(_checked(self.runner, ("wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@")))

    def application_states(self) -> tuple[AudioApplicationState, ...]:
        output = _checked(self.runner, ("pw-dump", "--no-colors"))
        try:
            targets = parse_audio_application_targets(json.loads(output))
        except json.JSONDecodeError as exc:
            raise HardwareControlError("decode_failed:pw-dump") from exc
        states: list[AudioApplicationState] = []
        for target in targets:
            values: list[int] = []
            muted: list[bool] = []
            errors: list[str] = []
            for node_id in target.node_ids:
                try:
                    percent, is_muted = parse_wpctl_volume(
                        _checked(self.runner, ("wpctl", "get-volume", str(node_id)))
                    )
                    values.append(percent)
                    muted.append(is_muted)
                except (HardwareControlError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
                    errors.append(str(exc))
            states.append(
                AudioApplicationState(
                    key=target.key,
                    name=target.name,
                    icon_hint=target.icon_hint,
                    node_ids=target.node_ids,
                    percent=round(sum(values) / len(values)) if values else None,
                    muted=bool(muted) and all(muted),
                    error=", ".join(errors),
                )
            )
        return tuple(states)


class MediaController:
    ACTIONS = {"play-pause", "next", "previous"}

    def __init__(self, runner: Runner = run_command) -> None:
        self.runner = runner

    def run(self, action: str) -> None:
        if action not in self.ACTIONS:
            raise ValueError("unsupported media action")
        _checked(self.runner, ("playerctl", action))


@dataclass(frozen=True)
class DdcTarget:
    connector: str
    bus: int
    edid_digest: str = ""


@dataclass(frozen=True)
class MonitorBrightnessState:
    connector: str
    percent: int | None = None
    error: str = ""
    powered: bool | None = None


@dataclass(frozen=True)
class AudioApplicationTarget:
    key: str
    name: str
    icon_hint: str
    node_ids: tuple[int, ...]


@dataclass(frozen=True)
class AudioApplicationState:
    key: str
    name: str
    icon_hint: str
    node_ids: tuple[int, ...]
    percent: int | None = None
    muted: bool = False
    error: str = ""


@dataclass(frozen=True)
class HardwareQuickState:
    volume_percent: int | None = None
    volume_muted: bool = False
    monitors: tuple[MonitorBrightnessState, ...] = ()
    applications: tuple[AudioApplicationState, ...] = ()
    audio_error: str = ""
    applications_error: str = ""


def parse_wpctl_volume(output: str) -> tuple[int, bool]:
    match = re.search(r"Volume:\s*([0-9]+(?:\.[0-9]+)?)", output)
    if not match:
        raise HardwareControlError("decode_failed:wpctl")
    return max(0, min(100, round(float(match.group(1)) * 100))), "[MUTED]" in output.upper()


def _validated_node_ids(node_ids: Sequence[int]) -> tuple[int, ...]:
    values = tuple(node_ids)
    if not values or any(isinstance(node_id, bool) or not isinstance(node_id, int) or node_id < 0 for node_id in values):
        raise ValueError("audio application node IDs must be non-negative integers")
    return values


def parse_audio_application_targets(payload: object) -> tuple[AudioApplicationTarget, ...]:
    if not isinstance(payload, list):
        raise HardwareControlError("decode_failed:pw-dump")
    grouped: dict[str, dict[str, object]] = {}
    for item in payload:
        if not isinstance(item, dict) or item.get("type") != "PipeWire:Interface:Node":
            continue
        info = item.get("info")
        if not isinstance(info, dict) or str(info.get("state", "")).casefold() != "running":
            continue
        props = info.get("props")
        if not isinstance(props, dict) or props.get("media.class") != "Stream/Output/Audio":
            continue
        try:
            node_id = int(item.get("id", -1))
        except (TypeError, ValueError):
            continue
        if node_id < 0:
            continue
        name = str(
            props.get("application.name")
            or props.get("node.description")
            or props.get("node.name")
            or props.get("media.name")
            or "알 수 없는 앱"
        ).strip()
        icon_hint = str(
            props.get("application.icon-name")
            or props.get("application.id")
            or props.get("application.process.binary")
            or name
        ).strip()
        identity = str(
            props.get("application.id")
            or props.get("application.process.binary")
            or props.get("application.name")
            or props.get("node.name")
            or node_id
        ).strip().casefold()
        entry = grouped.setdefault(identity, {"name": name, "icon_hint": icon_hint, "node_ids": []})
        node_ids = entry["node_ids"]
        assert isinstance(node_ids, list)
        node_ids.append(node_id)
    return tuple(
        AudioApplicationTarget(
            key=key,
            name=str(entry["name"]),
            icon_hint=str(entry["icon_hint"]),
            node_ids=tuple(sorted(set(entry["node_ids"]))),
        )
        for key, entry in sorted(grouped.items(), key=lambda pair: str(pair[1]["name"]).casefold())
    )


def parse_ddc_detect(output: str) -> dict[str, DdcTarget]:
    targets: dict[str, DdcTarget] = {}
    bus: int | None = None
    connector = ""
    for raw in (*output.splitlines(), ""):
        line = raw.strip()
        bus_match = re.search(r"I2C bus:\s*/dev/i2c-(\d+)", line)
        if bus_match:
            bus = int(bus_match.group(1))
        connector_match = re.search(r"DRM connector:\s*card\d+-(\S+)", line)
        if connector_match:
            connector = connector_match.group(1)
        if not line and bus is not None and connector:
            targets[connector] = DdcTarget(connector, bus)
            bus, connector = None, ""
    return targets


def discover_ddc_targets(
    root: Path = Path("/sys/class/drm"),
    runner: Runner = run_command,
) -> dict[str, DdcTarget]:
    targets: dict[str, DdcTarget] = {}
    connected: set[str] = set()
    if not root.is_dir():
        connector_paths: list[Path] = []
    else:
        connector_paths = sorted(root.iterdir())
    for connector_path in connector_paths:
        status = connector_path / "status"
        try:
            if status.read_text(encoding="utf-8").strip() != "connected":
                continue
        except OSError:
            continue
        connector = re.sub(r"^card\d+-", "", connector_path.name)
        connected.add(connector)
        buses = sorted((connector_path / "ddc" / "i2c-dev").glob("i2c-*"))
        if not buses:
            buses = sorted((connector_path / "i2c-dev").glob("i2c-*"))
        if not buses:
            continue
        try:
            bus = int(buses[0].name.removeprefix("i2c-"))
        except ValueError:
            continue
        targets[connector] = DdcTarget(connector, bus)
    if connected - set(targets) or not connected:
        try:
            detected = parse_ddc_detect(_checked(runner, ("ddcutil", "detect", "--brief")))
        except (HardwareControlError, OSError, subprocess.TimeoutExpired):
            detected = {}
        for connector, target in detected.items():
            if not connected or connector in connected:
                targets.setdefault(connector, target)
    return targets


def parse_ddc_brightness(output: str) -> tuple[int, int]:
    match = re.search(r"current value\s*=\s*(\d+).*?max value\s*=\s*(\d+)", output, re.IGNORECASE)
    if not match:
        match = re.search(r"VCP code 0x10.*?current value\s*=\s*(\d+).*?max value\s*=\s*(\d+)", output, re.IGNORECASE)
    if not match:
        match = re.search(r"^VCP\s+(?:0x)?10\s+C\s+(\d+)\s+(\d+)\s*$", output.strip(), re.IGNORECASE)
    if not match:
        raise HardwareControlError("decode_failed:ddcutil")
    current, maximum = map(int, match.groups())
    if maximum <= 0 or not 0 <= current <= maximum:
        raise HardwareControlError("invalid_value:ddcutil")
    return current, maximum


def parse_ddc_power_mode(output: str) -> bool:
    match = re.search(r"current value\s*=\s*(?:0?x)?([0-9a-f]+)", output, re.IGNORECASE)
    if not match:
        match = re.search(r"^VCP\s+(?:0x)?D6\s+SNC\s+(?:0?x)?([0-9a-f]+)", output.strip(), re.IGNORECASE)
    if not match:
        raise HardwareControlError("decode_failed:ddcutil-power")
    mode = int(match.group(1), 16)
    if mode == 0x01:
        return True
    if mode in {0x04, 0x05}:
        return False
    raise HardwareControlError("invalid_value:ddcutil-power")


class DdcBrightnessController:
    def __init__(self, runner: Runner = run_command) -> None:
        self.runner = runner

    def get(self, target: DdcTarget) -> tuple[int, int]:
        output = _checked(self.runner, ("ddcutil", "--bus", str(target.bus), "getvcp", "0x10", "--terse"))
        return parse_ddc_brightness(output)

    def set_percent(self, target: DdcTarget, percent: int) -> None:
        if not 0 <= percent <= 100:
            raise ValueError("brightness percent must be between 0 and 100")
        _checked(self.runner, ("ddcutil", "--bus", str(target.bus), "setvcp", "0x10", str(percent), "--noverify"))

    def get_powered(self, target: DdcTarget) -> bool:
        output = _checked(self.runner, ("ddcutil", "--bus", str(target.bus), "getvcp", "0xD6", "--terse"))
        return parse_ddc_power_mode(output)

    def set_powered(self, target: DdcTarget, powered: bool) -> None:
        value = "0x01" if powered else "0x05"
        _checked(self.runner, ("ddcutil", "--bus", str(target.bus), "setvcp", "0xD6", value, "--noverify"))

    def adjust_percent(self, target: DdcTarget, delta_percent: int) -> int:
        if delta_percent == 0 or abs(delta_percent) > 100:
            raise ValueError("brightness delta must be between -100 and 100 and non-zero")
        current, maximum = self.get(target)
        desired = max(0, min(100, round(current / maximum * 100) + delta_percent))
        self.set_percent(target, desired)
        return desired


class DdcBrightnessQueue:
    """One worker per I2C bus; repeated key and slider writes keep only the latest intent."""

    def __init__(self, controller: DdcBrightnessController | None = None) -> None:
        self.controller = controller or DdcBrightnessController()
        self._lock = threading.Lock()
        self._pending: dict[
            int,
            list[tuple[DdcTarget, str, int, Callable[[object], None], Callable[[Exception], None]]],
        ] = {}
        self._running: set[int] = set()

    def _submit(
        self,
        target: DdcTarget,
        operation: str,
        value: int,
        success: Callable[[object], None],
        failure: Callable[[Exception], None],
    ) -> None:
        start = False
        with self._lock:
            pending = self._pending.setdefault(target.bus, [])
            request = (target, operation, value, success, failure)
            if operation == "delta" and pending and pending[-1][1] in {"delta", "percent"}:
                previous = pending[-1]
                if previous[1] == "percent":
                    combined = max(0, min(100, previous[2] + value))
                    pending[-1] = (target, "percent", combined, success, failure)
                else:
                    combined = max(-100, min(100, previous[2] + value))
                    pending[-1] = (target, "delta", combined, success, failure)
            elif operation == "percent" and pending and pending[-1][1] in {"delta", "percent"}:
                pending[-1] = request
            elif pending and pending[-1][1] == operation:
                pending[-1] = request
            else:
                pending.append(request)
            if target.bus not in self._running:
                self._running.add(target.bus)
                start = True
        if start:
            threading.Thread(target=self._worker, args=(target.bus,), name=f"luminophore-ddc-{target.bus}", daemon=True).start()

    def submit_delta(
        self,
        target: DdcTarget,
        delta_percent: int,
        success: Callable[[int], None],
        failure: Callable[[Exception], None],
    ) -> None:
        if delta_percent == 0 or abs(delta_percent) > 100:
            raise ValueError("brightness delta must be between -100 and 100 and non-zero")
        self._submit(target, "delta", delta_percent, success, failure)

    def submit_percent(
        self,
        target: DdcTarget,
        percent: int,
        success: Callable[[int], None],
        failure: Callable[[Exception], None],
    ) -> None:
        if not 0 <= percent <= 100:
            raise ValueError("brightness percent must be between 0 and 100")
        self._submit(target, "percent", percent, success, failure)

    def submit_power(
        self,
        target: DdcTarget,
        powered: bool,
        success: Callable[[bool], None],
        failure: Callable[[Exception], None],
    ) -> None:
        self._submit(target, "power", int(powered), success, failure)

    def _worker(self, bus: int) -> None:
        while True:
            with self._lock:
                pending = self._pending.get(bus, [])
                if not pending:
                    self._pending.pop(bus, None)
                    self._running.discard(bus)
                    return
                request = pending.pop(0)
            target, operation, value, success, failure = request
            try:
                if operation == "percent":
                    self.controller.set_percent(target, value)
                    success(value)
                elif operation == "delta":
                    success(self.controller.adjust_percent(target, value))
                else:
                    powered = bool(value)
                    self.controller.set_powered(target, powered)
                    success(powered)
            except Exception as exc:
                failure(exc)


def read_audio_quick_state(audio: AudioController) -> HardwareQuickState:
    try:
        volume_percent, volume_muted = audio.output_state()
        audio_error = ""
    except (HardwareControlError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        volume_percent, volume_muted, audio_error = None, False, str(exc)
    try:
        applications = audio.application_states()
        applications_error = ""
    except (HardwareControlError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
        applications, applications_error = (), str(exc)
    return HardwareQuickState(
        volume_percent=volume_percent,
        volume_muted=volume_muted,
        applications=applications,
        audio_error=audio_error,
        applications_error=applications_error,
    )


def read_brightness_quick_state(
    brightness: DdcBrightnessController,
    targets: dict[str, DdcTarget],
) -> HardwareQuickState:
    monitors: list[MonitorBrightnessState] = []
    for connector, target in sorted(targets.items()):
        powered: bool | None = None
        power_error = ""
        try:
            powered = brightness.get_powered(target)
        except (HardwareControlError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
            power_error = str(exc)
        try:
            current, maximum = brightness.get(target)
            monitors.append(MonitorBrightnessState(
                connector,
                round(current / maximum * 100),
                power_error,
                powered,
            ))
        except (HardwareControlError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
            error = " · ".join(filter(None, (str(exc), power_error)))
            monitors.append(MonitorBrightnessState(connector, error=error, powered=powered))
    return HardwareQuickState(monitors=tuple(monitors))


def read_hardware_quick_state(
    audio: AudioController,
    brightness: DdcBrightnessController,
    targets: dict[str, DdcTarget],
) -> HardwareQuickState:
    audio_state = read_audio_quick_state(audio)
    brightness_state = read_brightness_quick_state(brightness, targets)
    return HardwareQuickState(
        volume_percent=audio_state.volume_percent,
        volume_muted=audio_state.volume_muted,
        monitors=brightness_state.monitors,
        applications=audio_state.applications,
        audio_error=audio_state.audio_error,
        applications_error=audio_state.applications_error,
    )
