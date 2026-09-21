from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from luminophore_shell.config import WeatherConfig
from luminophore_shell.location import LocationSnapshot
from luminophore_shell.network_policy import OfflineError, OfflinePolicy
from luminophore_shell.weather import (
    WeatherDay,
    WeatherProvider,
    WeatherSnapshot,
    _current_observation,
    _ensemble_days,
    _regular_days,
    _seasonal_days,
    weather_icon_name,
    weather_text,
)


class WeatherParsingTests(unittest.TestCase):
    def test_weather_requests_use_shared_location_snapshot(self) -> None:
        calls: list[dict[str, object]] = []

        def get(_url: str, **kwargs: object) -> object:
            calls.append(kwargs["params"])
            raise RuntimeError("stop after request capture")

        provider = WeatherProvider(
            WeatherConfig(),
            lambda _snapshot, _error: None,
            lambda: LocationSnapshot("geoclue", 35.1, 129.0, "Asia/Seoul", 200.0),
            get=get,
        )

        with self.assertRaisesRegex(RuntimeError, "request capture"):
            provider._fetch()
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(item["latitude"] == 35.1 for item in calls))
        self.assertTrue(all(item["longitude"] == 129.0 for item in calls))
        self.assertTrue(all(item["timezone"] == "Asia/Seoul" for item in calls))

    def test_offline_policy_prevents_weather_transport(self) -> None:
        calls: list[object] = []
        with tempfile.TemporaryDirectory() as raw:
            provider = WeatherProvider(
                WeatherConfig(),
                lambda _snapshot, _error: None,
                lambda: LocationSnapshot("manual", 37.5665, 126.978, "Asia/Seoul"),
                OfflinePolicy(True),
                lambda *args, **kwargs: calls.append((args, kwargs)),
            )
            provider.cache_path = Path(raw) / "weather.json"
            with self.assertRaisesRegex(OfflineError, "offline:weather"):
                provider._fetch()
        self.assertEqual(calls, [])
    def test_weather_codes_use_fixed_symbolic_icons(self) -> None:
        self.assertEqual(weather_icon_name(0), "weather-clear-symbolic")
        self.assertEqual(weather_icon_name(2), "weather-few-clouds-symbolic")
        self.assertEqual(weather_icon_name(63), "weather-showers-symbolic")
        self.assertEqual(weather_icon_name(95), "weather-storm-symbolic")

    def test_regular_daily_values_round(self) -> None:
        rows = _regular_days({"daily": {
            "time": ["2026-08-11"], "weather_code": [2],
            "temperature_2m_min": [21.4], "temperature_2m_max": [29.6],
        }})
        self.assertEqual((rows[0].minimum, rows[0].maximum), (21, 30))
        self.assertEqual(weather_text(rows[0].code), "구름 조금")

    def test_regular_daily_skips_partial_null_row(self) -> None:
        rows = _regular_days({"daily": {
            "time": ["2026-08-11", "2026-08-12"],
            "weather_code": [None, 2],
            "temperature_2m_min": [21.4, 22.0],
            "temperature_2m_max": [29.6, 30.0],
        }})
        self.assertEqual([row.date for row in rows], ["2026-08-12"])

    def test_null_current_code_uses_daily_forecast(self) -> None:
        temperature, code, stale = _current_observation(
            {"current": {"temperature_2m": 24.6, "weather_code": None}},
            [WeatherDay("2026-08-11", 63, 20, 27)],
            None,
        )
        self.assertEqual((temperature, code, stale), (24.6, 63, False))

    def test_missing_current_temperature_uses_cache_and_marks_stale(self) -> None:
        cached = WeatherSnapshot(1.0, 18.5, 3, ())
        temperature, code, stale = _current_observation(
            {"current": {"temperature_2m": None, "weather_code": 1}},
            [],
            cached,
        )
        self.assertEqual((temperature, code, stale), (18.5, 1, True))

    def test_ensemble_uses_member_median_and_modal_code(self) -> None:
        rows = _ensemble_days({"hourly": {
            "time": ["2026-08-27T00:00", "2026-08-27T12:00"],
            "temperature_2m_member01": [10.0, 20.0],
            "temperature_2m_member02": [12.0, 24.0],
            "temperature_2m_member03": [14.0, 22.0],
            "weather_code_member01": [3, 3],
            "weather_code_member02": [3, 61],
            "weather_code_member03": [3, 61],
        }})
        self.assertEqual((rows[0].minimum, rows[0].maximum), (12, 22))
        self.assertEqual(rows[0].code, 3)

    def test_seasonal_uses_control_plus_50_members(self) -> None:
        daily = {"time": ["2026-08-27"]}
        for base, control in (("temperature_2m_min", 10.0), ("temperature_2m_max", 20.0), ("weather_code", 3)):
            daily[base] = [control]
            for member in range(1, 51):
                daily[f"{base}_member{member:02d}"] = [control + (member % 3 if base != "weather_code" else 0)]
        rows = _seasonal_days({"daily": daily})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].code, 3)
        self.assertEqual((rows[0].minimum, rows[0].maximum), (11, 21))


if __name__ == "__main__":
    unittest.main()
