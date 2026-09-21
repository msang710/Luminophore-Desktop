from __future__ import annotations

import unittest

from luminophore_shell.config import LocationConfig
from luminophore_shell.location import CityResult, GEOCODING_URL, LocationProvider, city_config_changes
from luminophore_shell.network_policy import OfflineError, OfflinePolicy


class FakeBackend:
    def __init__(self, result: object) -> None:
        self.result = result

    def locate(self, timeout_seconds: float) -> tuple[float, float, float | None]:
        if isinstance(self.result, Exception):
            raise self.result
        assert isinstance(self.result, tuple)
        return self.result


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.payload


class LocationProviderTests(unittest.TestCase):
    def test_city_selection_disables_automatic_location_as_one_change_set(self) -> None:
        changes = city_config_changes(CityResult("부산", "대한민국", 35.18, 129.08, "Asia/Seoul"))
        self.assertEqual(changes, {
            "location.automatic": False,
            "location.latitude": 35.18,
            "location.longitude": 129.08,
            "location.timezone": "Asia/Seoul",
        })

    def test_geoclue_is_preferred_without_http(self) -> None:
        http_calls: list[object] = []
        provider = LocationProvider(
            LocationConfig(), backend=FakeBackend((35.1, 129.0, 250.0)),
            get=lambda *args, **kwargs: http_calls.append((args, kwargs)),
        )
        location = provider.resolve()
        self.assertEqual(location.source, "geoclue")
        self.assertEqual((location.latitude, location.longitude, location.accuracy_m), (35.1, 129.0, 250.0))
        self.assertEqual(http_calls, [])

    def test_geoclue_failure_falls_back_to_manual_without_http(self) -> None:
        http_calls: list[object] = []
        provider = LocationProvider(
            LocationConfig(latitude=33.45, longitude=126.57),
            backend=FakeBackend(RuntimeError("permission denied")),
            get=lambda *args, **kwargs: http_calls.append((args, kwargs)),
        )
        location = provider.resolve()
        self.assertEqual(location.source, "manual")
        self.assertEqual((location.latitude, location.longitude), (33.45, 126.57))
        self.assertEqual(provider.last_error, "permission denied")
        self.assertEqual(http_calls, [])

    def test_city_search_is_explicit_and_parses_results(self) -> None:
        calls: list[tuple[object, object]] = []

        def get(url: str, **kwargs: object) -> FakeResponse:
            calls.append((url, kwargs))
            return FakeResponse({"results": [{
                "name": "부산", "country": "대한민국", "latitude": 35.18,
                "longitude": 129.08, "timezone": "Asia/Seoul",
            }]})

        provider = LocationProvider(LocationConfig(automatic=False), backend=FakeBackend((0, 0, None)), get=get)
        self.assertEqual(calls, [])
        results = provider.search_city("부산")
        self.assertEqual((results[0].name, results[0].timezone), ("부산", "Asia/Seoul"))
        self.assertEqual(calls[0][0], GEOCODING_URL)
        self.assertEqual(calls[0][1]["params"]["language"], "ko")

    def test_offline_policy_blocks_city_transport(self) -> None:
        calls: list[object] = []
        provider = LocationProvider(
            LocationConfig(), policy=OfflinePolicy(True), backend=FakeBackend((0, 0, None)),
            get=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        with self.assertRaisesRegex(OfflineError, "offline:city_search"):
            provider.search_city("서울")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
