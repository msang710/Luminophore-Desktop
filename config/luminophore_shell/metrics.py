from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Callable

from .config import MetricsConfig, Threshold


@dataclass(frozen=True)
class MetricSnapshot:
    cpu_usage: float | None
    cpu_temperature: float | None
    cpu_clock_mhz: float | None
    gpu_usage: float | None
    gpu_temperature: float | None
    gpu_vram_used_mb: float | None
    gpu_vram_total_mb: float | None
    gpu_power_w: float | None
    ram_used_gb: float | None
    ram_total_gb: float | None
    swap_used_gb: float | None
    swap_total_gb: float | None
    root_used_percent: float | None
    network_interface: str
    network_rx_bps: float | None
    network_tx_bps: float | None
    nvme_0700_temperature: float | None
    nvme_0100_temperature: float | None
    pump_rpm: float | None
    fan_rpm: float | None
    coolant_temperature: float | None


def _read_float(path: Path, divisor: float = 1.0) -> float | None:
    try:
        return float(path.read_text(encoding="utf-8").strip()) / divisor
    except (OSError, ValueError):
        return None


def _hwmon_values() -> dict[str, float | None]:
    result: dict[str, float | None] = {
        "cpu": None, "coolant": None, "pump": None, "fan": None,
        "nvme_0700": None, "nvme_0100": None,
    }
    for directory in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            name = (directory / "name").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if name == "k10temp":
            result["cpu"] = _read_float(directory / "temp1_input", 1000)
        elif name == "z53":
            result["coolant"] = _read_float(directory / "temp1_input", 1000)
            result["pump"] = _read_float(directory / "fan1_input")
            result["fan"] = _read_float(directory / "fan2_input")
        elif name == "nvme":
            resolved = str((directory / "device").resolve())
            value = _read_float(directory / "temp1_input", 1000)
            if "0000:07:00.0" in resolved:
                result["nvme_0700"] = value
            elif "0000:01:00.0" in resolved:
                result["nvme_0100"] = value
    return result


class MetricsProvider:
    def __init__(
        self,
        config: MetricsConfig,
        changed: Callable[[MetricSnapshot], None],
        danger: Callable[[str, str], None] | None = None,
    ) -> None:
        self.config = config
        self.changed = changed
        self.danger = danger or (lambda _key, _message: None)
        count = max(2, round(config.graph_seconds / config.sample_seconds))
        self.history: deque[MetricSnapshot] = deque(maxlen=count)
        self._config_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._previous_cpu: tuple[int, int] | None = None
        self._previous_network: tuple[str, int, int, float] | None = None
        self._levels: dict[str, str] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="luminophore-metrics", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def reconfigure(self, config: MetricsConfig) -> None:
        """Adopt new sampling settings without restarting the provider thread."""
        count = max(2, round(config.graph_seconds / config.sample_seconds))
        with self._config_lock:
            self.config = config
            self.history = deque(self.history, maxlen=count)

    def _loop(self) -> None:
        while not self._stop.is_set():
            snapshot = self.sample()
            with self._config_lock:
                self.history.append(snapshot)
                interval = self.config.sample_seconds
            self._check_dangers(snapshot)
            self.changed(snapshot)
            self._stop.wait(interval)

    def sample(self) -> MetricSnapshot:
        cpu_usage = self._cpu_usage()
        memory = self._memory()
        network = self._network()
        hwmon = _hwmon_values()
        gpu = self._gpu()
        disk = shutil.disk_usage("/")
        return MetricSnapshot(
            cpu_usage=cpu_usage,
            cpu_temperature=hwmon["cpu"],
            cpu_clock_mhz=self._cpu_clock(),
            gpu_usage=gpu[0], gpu_temperature=gpu[1], gpu_vram_used_mb=gpu[2], gpu_vram_total_mb=gpu[3], gpu_power_w=gpu[4],
            ram_used_gb=memory[0], ram_total_gb=memory[1], swap_used_gb=memory[2], swap_total_gb=memory[3],
            root_used_percent=(disk.used / disk.total * 100) if disk.total else None,
            network_interface=network[0], network_rx_bps=network[1], network_tx_bps=network[2],
            nvme_0700_temperature=hwmon["nvme_0700"], nvme_0100_temperature=hwmon["nvme_0100"],
            pump_rpm=hwmon["pump"], fan_rpm=hwmon["fan"], coolant_temperature=hwmon["coolant"],
        )

    def _cpu_usage(self) -> float | None:
        try:
            values = [int(value) for value in Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]]
        except (OSError, ValueError, IndexError):
            return None
        total = sum(values)
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        current = (total, idle)
        previous = self._previous_cpu
        self._previous_cpu = current
        if not previous or total <= previous[0]:
            return None
        return max(0.0, min(100.0, (1 - (idle - previous[1]) / (total - previous[0])) * 100))

    def _cpu_clock(self) -> float | None:
        try:
            rows = Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines()
            values = [float(row.split(":", 1)[1]) for row in rows if row.startswith("cpu MHz")]
            return sum(values) / len(values) if values else None
        except (OSError, ValueError, IndexError):
            return None

    def _memory(self) -> tuple[float | None, float | None, float | None, float | None]:
        try:
            data = {
                row.split(":", 1)[0]: float(row.split()[1])
                for row in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
            }
            total = data["MemTotal"] / 1024**2
            used = (data["MemTotal"] - data["MemAvailable"]) / 1024**2
            swap_total = data["SwapTotal"] / 1024**2
            swap_used = (data["SwapTotal"] - data["SwapFree"]) / 1024**2
            return used, total, swap_used, swap_total
        except (OSError, ValueError, KeyError, IndexError):
            return None, None, None, None

    def _network(self) -> tuple[str, float | None, float | None]:
        now = time.monotonic()
        candidates: list[tuple[str, int, int]] = []
        try:
            for row in Path("/proc/net/dev").read_text(encoding="utf-8").splitlines()[2:]:
                name, values = row.split(":", 1)
                interface = name.strip()
                path = Path("/sys/class/net") / interface
                if interface == "lo" or not (path / "device").exists():
                    continue
                carrier = _read_float(path / "carrier")
                if carrier != 1:
                    continue
                fields = values.split()
                candidates.append((interface, int(fields[0]), int(fields[8])))
        except (OSError, ValueError, IndexError):
            return "", None, None
        if not candidates:
            return "", None, None
        interface, received, sent = candidates[0]
        previous = self._previous_network
        self._previous_network = (interface, received, sent, now)
        if not previous or previous[0] != interface or now <= previous[3]:
            return interface, None, None
        elapsed = now - previous[3]
        return interface, max(0, (received - previous[1]) / elapsed), max(0, (sent - previous[2]) / elapsed)

    def _gpu(self) -> tuple[float | None, float | None, float | None, float | None, float | None]:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw", "--format=csv,noheader,nounits"],
                check=False, capture_output=True, text=True, timeout=0.8,
            )
            if result.returncode:
                raise ValueError
            values = [float(value.strip()) for value in result.stdout.splitlines()[0].split(",")]
            return values[0], values[1], values[2], values[3], values[4]
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
            return None, None, None, None, None

    def _temperature_level(self, key: str, value: float | None, threshold: Threshold) -> str:
        if value is None:
            return "missing"
        previous = self._levels.get(key)
        if previous == "danger" and value >= threshold.danger - 2:
            return "danger"
        if previous == "warning" and value >= threshold.warning - 2 and value < threshold.danger:
            return "warning"
        if value >= threshold.danger:
            return "danger"
        if value >= threshold.warning:
            return "warning"
        return "normal"

    def _check_dangers(self, snapshot: MetricSnapshot) -> None:
        pump_level = "missing"
        if snapshot.pump_rpm is not None:
            if self._levels.get("pump") == "danger" and snapshot.pump_rpm <= self.config.pump_danger_rpm + 100:
                pump_level = "danger"
            elif snapshot.pump_rpm <= self.config.pump_danger_rpm:
                pump_level = "danger"
            elif self._levels.get("pump") == "warning" and snapshot.pump_rpm <= self.config.pump_warning_rpm + 100:
                pump_level = "warning"
            elif snapshot.pump_rpm <= self.config.pump_warning_rpm:
                pump_level = "warning"
            else:
                pump_level = "normal"
        fan_danger = (
            snapshot.fan_rpm is not None and snapshot.coolant_temperature is not None
            and snapshot.coolant_temperature >= (self.config.fan_coolant_gate - (2 if self._levels.get("fan") == "danger" else 0))
            and snapshot.fan_rpm < (self.config.fan_warning_rpm + (50 if self._levels.get("fan") == "danger" else 0))
        )
        levels = {
            "cpu": self._temperature_level("cpu", snapshot.cpu_temperature, self.config.cpu),
            "gpu": self._temperature_level("gpu", snapshot.gpu_temperature, self.config.gpu),
            "coolant": self._temperature_level("coolant", snapshot.coolant_temperature, self.config.coolant),
            "nvme_0700": self._temperature_level("nvme_0700", snapshot.nvme_0700_temperature, self.config.nvme_0700),
            "nvme_0100": self._temperature_level("nvme_0100", snapshot.nvme_0100_temperature, self.config.nvme_0100),
            "pump": pump_level,
            "fan": "danger" if fan_danger else "normal",
        }
        values = {
            "cpu": snapshot.cpu_temperature, "gpu": snapshot.gpu_temperature, "coolant": snapshot.coolant_temperature,
            "nvme_0700": snapshot.nvme_0700_temperature, "nvme_0100": snapshot.nvme_0100_temperature,
            "pump": snapshot.pump_rpm, "fan": snapshot.fan_rpm,
        }
        labels = {"cpu": "CPU", "gpu": "GPU", "coolant": "냉각수", "nvme_0700": "NVMe 07:00", "nvme_0100": "NVMe 01:00", "pump": "펌프", "fan": "팬"}
        for key, level in levels.items():
            if level == "danger" and self._levels.get(key) != "danger":
                self.danger(key, f"{labels[key]} 위험 상태: {values[key]:.1f}")
            self._levels[key] = level
