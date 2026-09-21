from __future__ import annotations

import unittest

from luminophore_shell.config import MetricsConfig
from luminophore_shell.metrics import MetricSnapshot, MetricsProvider


def snapshot(**changes):
    values = dict(
        cpu_usage=10.0, cpu_temperature=40.0, cpu_clock_mhz=4000.0,
        gpu_usage=None, gpu_temperature=None, gpu_vram_used_mb=None, gpu_vram_total_mb=None, gpu_power_w=None,
        ram_used_gb=4.0, ram_total_gb=32.0, swap_used_gb=0.0, swap_total_gb=4.0,
        root_used_percent=30.0, network_interface="eth0", network_rx_bps=0.0, network_tx_bps=0.0,
        nvme_0700_temperature=45.0, nvme_0100_temperature=45.0,
        pump_rpm=2500.0, fan_rpm=1000.0, coolant_temperature=35.0,
    )
    values.update(changes)
    return MetricSnapshot(**values)


class MetricsAlertTests(unittest.TestCase):
    def test_danger_is_emitted_once_per_transition(self) -> None:
        alerts = []
        provider = MetricsProvider(MetricsConfig(), lambda _data: None, lambda key, message: alerts.append((key, message)))
        provider._check_dangers(snapshot(cpu_temperature=96.0))
        provider._check_dangers(snapshot(cpu_temperature=97.0))
        provider._check_dangers(snapshot(cpu_temperature=70.0))
        provider._check_dangers(snapshot(cpu_temperature=98.0))
        self.assertEqual([key for key, _message in alerts], ["cpu", "cpu"])

    def test_low_fan_only_alerts_when_coolant_is_hot(self) -> None:
        alerts = []
        provider = MetricsProvider(MetricsConfig(), lambda _data: None, lambda key, message: alerts.append(key))
        provider._check_dangers(snapshot(fan_rpm=100.0, coolant_temperature=35.0))
        provider._check_dangers(snapshot(fan_rpm=100.0, coolant_temperature=41.0))
        self.assertIn("fan", alerts)

    def test_temperature_hysteresis_avoids_threshold_chatter(self) -> None:
        alerts = []
        provider = MetricsProvider(MetricsConfig(), lambda _data: None, lambda key, _message: alerts.append(key))
        provider._check_dangers(snapshot(cpu_temperature=95.2))
        provider._check_dangers(snapshot(cpu_temperature=94.5))
        provider._check_dangers(snapshot(cpu_temperature=92.5))
        provider._check_dangers(snapshot(cpu_temperature=95.1))
        self.assertEqual(alerts, ["cpu", "cpu"])

    def test_reconfigure_keeps_latest_history_and_changes_capacity(self) -> None:
        provider = MetricsProvider(MetricsConfig(sample_seconds=2.0, graph_seconds=60), lambda _data: None)
        for value in range(35):
            provider.history.append(snapshot(cpu_usage=float(value)))

        provider.reconfigure(MetricsConfig(sample_seconds=2.0, graph_seconds=20))

        self.assertEqual(provider.history.maxlen, 10)
        self.assertEqual(len(provider.history), 10)
        self.assertEqual(provider.history[-1].cpu_usage, 34.0)


if __name__ == "__main__":
    unittest.main()
