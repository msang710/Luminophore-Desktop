from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Protocol

import requests

from .config import LocationConfig
from .network_policy import OfflinePolicy


GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


@dataclass(frozen=True)
class LocationSnapshot:
    source: str
    latitude: float
    longitude: float
    timezone: str
    accuracy_m: float | None = None


@dataclass(frozen=True)
class CityResult:
    name: str
    country: str
    latitude: float
    longitude: float
    timezone: str


def city_config_changes(city: CityResult) -> dict[str, object]:
    return {
        "location.automatic": False,
        "location.latitude": city.latitude,
        "location.longitude": city.longitude,
        "location.timezone": city.timezone,
    }


class LocationBackend(Protocol):
    def locate(self, timeout_seconds: float) -> tuple[float, float, float | None]: ...


class GeoClueBackend:
    """Small synchronous GeoClue client intended to run in a worker thread."""

    SERVICE = "org.freedesktop.GeoClue2"
    MANAGER_PATH = "/org/freedesktop/GeoClue2/Manager"
    MANAGER_IFACE = "org.freedesktop.GeoClue2.Manager"
    CLIENT_IFACE = "org.freedesktop.GeoClue2.Client"
    LOCATION_IFACE = "org.freedesktop.GeoClue2.Location"
    PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

    def locate(self, timeout_seconds: float) -> tuple[float, float, float | None]:
        try:
            import dbus
        except ImportError as exc:
            raise RuntimeError("GeoClue Python D-Bus bindings are unavailable") from exc

        bus = dbus.SystemBus()
        manager_object = bus.get_object(self.SERVICE, self.MANAGER_PATH)
        manager = dbus.Interface(manager_object, self.MANAGER_IFACE)
        client_path = manager.GetClient()
        client_object = bus.get_object(self.SERVICE, client_path)
        client = dbus.Interface(client_object, self.CLIENT_IFACE)
        properties = dbus.Interface(client_object, self.PROPERTIES_IFACE)

        try:
            properties.Set(self.CLIENT_IFACE, "DesktopId", "luminophore-shell")
            properties.Set(self.CLIENT_IFACE, "RequestedAccuracyLevel", dbus.UInt32(4))
            client.Start()
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                location_path = str(properties.Get(self.CLIENT_IFACE, "Location"))
                if location_path != "/":
                    location_object = bus.get_object(self.SERVICE, location_path)
                    location_properties = dbus.Interface(location_object, self.PROPERTIES_IFACE)
                    latitude = float(location_properties.Get(self.LOCATION_IFACE, "Latitude"))
                    longitude = float(location_properties.Get(self.LOCATION_IFACE, "Longitude"))
                    accuracy = float(location_properties.Get(self.LOCATION_IFACE, "Accuracy"))
                    return latitude, longitude, accuracy
                time.sleep(0.1)
            raise RuntimeError("GeoClue location request timed out")
        finally:
            try:
                client.Stop()
            except Exception:
                pass
            try:
                manager.DeleteClient(client_path)
            except Exception:
                pass


class LocationProvider:
    def __init__(
        self,
        config: LocationConfig,
        policy: OfflinePolicy | None = None,
        backend: LocationBackend | None = None,
        get: Callable[..., object] = requests.get,
    ) -> None:
        self.config = config
        self.policy = policy or OfflinePolicy()
        self.backend = backend or GeoClueBackend()
        self.get = get
        self.last_error = ""

    def resolve(self) -> LocationSnapshot:
        if self.config.automatic:
            try:
                latitude, longitude, accuracy = self.backend.locate(self.config.geoclue_timeout_seconds)
                if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                    raise ValueError("GeoClue returned coordinates outside the valid range")
                self.last_error = ""
                return LocationSnapshot(
                    "geoclue",
                    latitude,
                    longitude,
                    self.config.timezone,
                    accuracy,
                )
            except Exception as exc:
                self.last_error = str(exc)
        return LocationSnapshot(
            "manual",
            self.config.latitude,
            self.config.longitude,
            self.config.timezone,
        )

    def search_city(self, query: str, *, count: int = 10, language: str = "ko") -> tuple[CityResult, ...]:
        name = query.strip()
        if len(name) < 2:
            raise ValueError("city search requires at least two characters")
        if not 1 <= count <= 100:
            raise ValueError("city search count must be between 1 and 100")
        self.policy.require("city_search")
        response = self.get(
            GEOCODING_URL,
            params={"name": name, "count": count, "language": language, "format": "json"},
            timeout=self.config.city_search_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results", []) if isinstance(payload, dict) else []
        cities: list[CityResult] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            try:
                result = CityResult(
                    name=str(item["name"]),
                    country=str(item.get("country", "")),
                    latitude=float(item["latitude"]),
                    longitude=float(item["longitude"]),
                    timezone=str(item["timezone"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if -90 <= result.latitude <= 90 and -180 <= result.longitude <= 180 and result.timezone:
                cities.append(result)
        return tuple(cities)
