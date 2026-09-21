from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
import json
from pathlib import Path
import statistics
import threading
import time
from typing import Callable

import requests

from .config import WeatherConfig
from .location import LocationSnapshot
from .network_policy import OfflineError, OfflinePolicy
from .state import cache_dir, read_json, state_dir, write_json_atomic


REGULAR_URL = "https://api.open-meteo.com/v1/forecast"
SEASONAL_URL = "https://seasonal-api.open-meteo.com/v1/seasonal"


@dataclass(frozen=True)
class WeatherDay:
    date: str
    code: int
    minimum: int
    maximum: int


@dataclass(frozen=True)
class WeatherSnapshot:
    fetched_at: float
    current_temperature: float
    current_code: int
    days: tuple[WeatherDay, ...]
    stale: bool = False

    @classmethod
    def from_json(cls, raw: object) -> "WeatherSnapshot | None":
        if not isinstance(raw, dict):
            return None
        try:
            days = tuple(WeatherDay(**day) for day in raw["days"])
            return cls(
                fetched_at=float(raw["fetched_at"]),
                current_temperature=float(raw["current_temperature"]),
                current_code=int(raw["current_code"]),
                days=days,
                stale=bool(raw.get("stale", False)),
            )
        except (KeyError, TypeError, ValueError):
            return None


def weather_icon(code: int) -> str:
    if code == 0:
        return "☀"
    if code in {1, 2}:
        return "🌤"
    if code == 3:
        return "☁"
    if code in {45, 48}:
        return "≋"
    if code in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}:
        return "🌧"
    if code in {71, 73, 75, 77, 85, 86}:
        return "❄"
    if code in {95, 96, 99}:
        return "ϟ"
    return "·"


def weather_icon_name(code: int) -> str:
    if code == 0:
        return "weather-clear-symbolic"
    if code in {1, 2}:
        return "weather-few-clouds-symbolic"
    if code == 3:
        return "weather-overcast-symbolic"
    if code in {45, 48}:
        return "weather-fog-symbolic"
    if code in {51, 53, 55, 56, 57, 80, 81, 82}:
        return "weather-showers-scattered-symbolic"
    if code in {61, 63, 65, 66, 67}:
        return "weather-showers-symbolic"
    if code in {71, 73, 75, 77, 85, 86}:
        return "weather-snow-symbolic"
    if code in {95, 96, 99}:
        return "weather-storm-symbolic"
    return "weather-severe-alert-symbolic"


def weather_text(code: int) -> str:
    if code == 0:
        return "맑음"
    if code in {1, 2}:
        return "구름 조금"
    if code == 3:
        return "흐림"
    if code in {45, 48}:
        return "안개"
    if code in {51, 53, 55, 56, 57}:
        return "이슬비"
    if code in {61, 63, 65, 66, 67, 80, 81, 82}:
        return "비"
    if code in {71, 73, 75, 77, 85, 86}:
        return "눈"
    if code in {95, 96, 99}:
        return "뇌우"
    return "날씨 정보 없음"


def _regular_days(raw: dict[str, object]) -> list[WeatherDay]:
    daily = raw.get("daily")
    if not isinstance(daily, dict):
        raise ValueError("daily forecast is missing")
    times = daily.get("time", [])
    codes = daily.get("weather_code", [])
    minima = daily.get("temperature_2m_min", [])
    maxima = daily.get("temperature_2m_max", [])
    rows: list[WeatherDay] = []
    for day, code, low, high in zip(times, codes, minima, maxima):
        if code is None or low is None or high is None:
            continue
        try:
            rows.append(WeatherDay(str(day), int(code), round(float(low)), round(float(high))))
        except (TypeError, ValueError):
            continue
    return rows


def _current_observation(
    regular: object,
    regular_days: list[WeatherDay],
    cached: WeatherSnapshot | None,
) -> tuple[float, int, bool]:
    current = regular.get("current") if isinstance(regular, dict) else None
    current = current if isinstance(current, dict) else {}
    used_cache = False

    try:
        temperature = round(float(current["temperature_2m"]), 1)
    except (KeyError, TypeError, ValueError):
        if cached is None:
            raise ValueError("current temperature is missing") from None
        temperature = cached.current_temperature
        used_cache = True

    try:
        code = int(current["weather_code"])
    except (KeyError, TypeError, ValueError):
        if regular_days:
            code = regular_days[0].code
        elif cached is not None:
            code = cached.current_code
            used_cache = True
        else:
            raise ValueError("current weather code is missing") from None
    return temperature, code, used_cache


def _ensemble_days(raw: dict[str, object]) -> list[WeatherDay]:
    hourly = raw.get("hourly")
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise ValueError("ensemble hourly forecast is missing")
    times = [str(value) for value in hourly["time"]]
    temperature_keys = sorted(key for key in hourly if key.startswith("temperature_2m_member"))[:51]
    code_keys = sorted(key for key in hourly if key.startswith("weather_code_member"))[:51]
    if not temperature_keys:
        temperature_keys = ["temperature_2m"] if "temperature_2m" in hourly else []
    if not code_keys:
        code_keys = ["weather_code"] if "weather_code" in hourly else []
    if not temperature_keys or not code_keys:
        raise ValueError("ensemble members are missing")
    grouped: dict[str, list[int]] = {}
    for index, stamp in enumerate(times):
        grouped.setdefault(stamp[:10], []).append(index)
    rows: list[WeatherDay] = []
    for day, indices in grouped.items():
        member_lows: list[float] = []
        member_highs: list[float] = []
        codes: list[int] = []
        for key in temperature_keys:
            values = hourly.get(key)
            if not isinstance(values, list):
                continue
            temperatures = [float(values[index]) for index in indices if index < len(values) and values[index] is not None]
            if temperatures:
                member_lows.append(min(temperatures))
                member_highs.append(max(temperatures))
        for key in code_keys:
            values = hourly.get(key)
            if isinstance(values, list):
                codes.extend(int(values[index]) for index in indices if index < len(values) and values[index] is not None)
        if member_lows and codes:
            code = Counter(codes).most_common(1)[0][0]
            rows.append(WeatherDay(day, code, round(statistics.median(member_lows)), round(statistics.median(member_highs))))
    return rows


def _seasonal_days(raw: dict[str, object]) -> list[WeatherDay]:
    daily = raw.get("daily")
    if not isinstance(daily, dict) or not isinstance(daily.get("time"), list):
        raise ValueError("seasonal daily forecast is missing")
    times = [str(value) for value in daily["time"]]

    def member_keys(base: str) -> list[str]:
        keys = [base] if base in daily else []
        keys.extend(sorted(key for key in daily if key.startswith(f"{base}_member"))[:50])
        return keys

    minimum_keys = member_keys("temperature_2m_min")
    maximum_keys = member_keys("temperature_2m_max")
    code_keys = member_keys("weather_code")
    if len(minimum_keys) < 51 or len(maximum_keys) < 51 or len(code_keys) < 51:
        raise ValueError("EC46 did not return all 51 members")
    rows: list[WeatherDay] = []
    for index, day in enumerate(times):
        minima = [float(daily[key][index]) for key in minimum_keys if isinstance(daily.get(key), list) and index < len(daily[key]) and daily[key][index] is not None]
        maxima = [float(daily[key][index]) for key in maximum_keys if isinstance(daily.get(key), list) and index < len(daily[key]) and daily[key][index] is not None]
        codes = [int(daily[key][index]) for key in code_keys if isinstance(daily.get(key), list) and index < len(daily[key]) and daily[key][index] is not None]
        if minima and maxima and codes:
            rows.append(
                WeatherDay(
                    day,
                    Counter(codes).most_common(1)[0][0],
                    round(statistics.median(minima)),
                    round(statistics.median(maxima)),
                )
            )
    return rows


class WeatherProvider:
    def __init__(
        self,
        config: WeatherConfig,
        changed: Callable[[WeatherSnapshot | None, str], None],
        location: Callable[[], LocationSnapshot],
        policy: OfflinePolicy | None = None,
        get: Callable[..., object] = requests.get,
    ) -> None:
        self.config = config
        self.changed = changed
        self.location = location
        self.cache_path = cache_dir() / "weather.json"
        self.attempt_path = state_dir() / "weather-boot.json"
        self.snapshot = self._load_cache()
        self.error = ""
        self._lock = threading.Lock()
        self.policy = policy or OfflinePolicy()
        self.get = get

    def _load_cache(self) -> WeatherSnapshot | None:
        result = WeatherSnapshot.from_json(read_json(self.cache_path, {}))
        if not result:
            return None
        stale = time.time() - result.fetched_at > self.config.cache_hours * 3600
        return WeatherSnapshot(result.fetched_at, result.current_temperature, result.current_code, result.days, stale)

    def start(self) -> None:
        boot_id = self._boot_id()
        attempted = read_json(self.attempt_path, {})
        if not isinstance(attempted, dict) or attempted.get("boot_id") != boot_id:
            write_json_atomic(self.attempt_path, {"boot_id": boot_id, "attempted_at": time.time()})
            self.refresh(manual=False)
        else:
            self.changed(self.snapshot, self.error)

    def _boot_id(self) -> str:
        try:
            return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        except OSError:
            return "unknown"

    def refresh(self, manual: bool = True) -> None:
        if not self._lock.acquire(blocking=False):
            return

        def worker() -> None:
            try:
                snapshot = self._fetch()
                self.snapshot = snapshot
                self.error = ""
                write_json_atomic(self.cache_path, asdict(snapshot))
            except (OfflineError, requests.RequestException, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                self.error = str(exc)
                if self.snapshot is not None and not self.snapshot.stale:
                    self.snapshot = replace(self.snapshot, stale=True)
            finally:
                self._lock.release()
                self.changed(self.snapshot, self.error)

        threading.Thread(target=worker, name="luminophore-weather", daemon=True).start()

    def _fetch(self) -> WeatherSnapshot:
        self.policy.require("weather")
        location = self.location()
        base = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "timezone": location.timezone,
        }
        regular_params = {
            **base,
            "current": "temperature_2m,weather_code",
            "daily": "weather_code,temperature_2m_min,temperature_2m_max",
            "forecast_days": 16,
        }
        seasonal_params = {
            **base,
            "daily": "temperature_2m_min,temperature_2m_max,weather_code",
            "forecast_days": self.config.forecast_days,
        }
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="luminophore-weather-http") as executor:
            regular_future = executor.submit(self.get, REGULAR_URL, params=regular_params, timeout=self.config.timeout_seconds)
            seasonal_future = executor.submit(self.get, SEASONAL_URL, params=seasonal_params, timeout=self.config.timeout_seconds)
            regular_response = regular_future.result()
            ensemble_response = seasonal_future.result()
        regular_response.raise_for_status()
        regular = regular_response.json()
        ensemble_response.raise_for_status()
        ensemble = ensemble_response.json()
        regular_days = _regular_days(regular)
        long_days = _seasonal_days(ensemble)
        by_date = {item.date: item for item in regular_days}
        regular_cutoff = regular_days[-1].date if regular_days else ""
        for item in long_days:
            if item.date > regular_cutoff:
                by_date[item.date] = item
        combined = sorted(by_date.values(), key=lambda item: item.date)[: self.config.forecast_days]
        if len(combined) < self.config.forecast_days:
            raise ValueError(f"forecast returned only {len(combined)} days")
        current_temperature, current_code, used_cache = _current_observation(
            regular, regular_days, self.snapshot,
        )
        return WeatherSnapshot(
            fetched_at=time.time(),
            current_temperature=current_temperature,
            current_code=current_code,
            days=tuple(combined),
            stale=used_cache,
        )
