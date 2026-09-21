from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from luminophore_shell.applications import ApplicationAction, ApplicationCatalog, ApplicationRecord


class FakeAppInfo:
    pass


class FakeExternalLauncher:
    def __init__(self, succeed: bool = True) -> None:
        self.succeed = succeed
        self.desktop_launches: list[tuple[str, str | None]] = []

    def launch_desktop(self, desktop_id: str, action_id: str | None = None) -> bool:
        self.desktop_launches.append((desktop_id, action_id))
        return self.succeed

    def open_uri(self, _uri: str) -> bool:
        return self.succeed

    def launch_argv(self, _argv: tuple[str, ...], *, app_name: str = "") -> bool:
        return self.succeed


def _record(
    desktop_id: str,
    name: str,
    info: FakeAppInfo | None = None,
    actions: tuple[ApplicationAction, ...] = (),
) -> ApplicationRecord:
    return ApplicationRecord(desktop_id, name, "", "", None, info or FakeAppInfo(), actions)  # type: ignore[arg-type]


def _catalog(app: ApplicationRecord, preferred: tuple[tuple[str, str], ...] = ()) -> ApplicationCatalog:
    catalog = ApplicationCatalog.__new__(ApplicationCatalog)
    catalog.path = Path("/unused/app-usage.json")
    catalog.usage = {}
    catalog.apps = [app]
    catalog._by_key = {app.key: app}
    catalog._by_desktop_id = {app.desktop_id.casefold(): app}
    catalog.revision = 1
    catalog._preferred_actions = {}
    catalog.external_launcher = FakeExternalLauncher()
    catalog.set_preferred_actions(preferred)
    return catalog


class ApplicationCatalogTests(unittest.TestCase):
    def test_refresh_replaces_added_and_removed_desktop_entries(self) -> None:
        old = _record("old.desktop", "Old")
        new = _record("new.desktop", "New")
        catalog = ApplicationCatalog.__new__(ApplicationCatalog)
        catalog.usage = {}
        catalog.apps = [old]
        catalog._by_key = {old.key: old}
        catalog._by_desktop_id = {old.desktop_id.casefold(): old}
        catalog.revision = 1
        catalog._load = lambda: [new]  # type: ignore[method-assign]

        catalog.refresh()

        self.assertIsNone(catalog.match_window_class("old"))
        self.assertIs(catalog.match_window_class("new"), new)
        self.assertIs(catalog.match_desktop_id("new.desktop"), new)
        self.assertEqual(catalog.revision, 2)
        self.assertEqual(catalog.search("new", 10), [new])

    @patch("luminophore_shell.applications.write_json_atomic")
    def test_preferred_action_launches_instead_of_default(self, write_usage: object) -> None:
        app = _record(
            "org.example.Settings.desktop",
            "Example Settings",
            FakeAppInfo(),
            (ApplicationAction("Settings", "Open Settings"),),
        )
        catalog = _catalog(app, ((app.desktop_id, "Settings"),))

        self.assertTrue(catalog.launch(app))

        self.assertEqual(catalog.external_launcher.desktop_launches, [(app.desktop_id, "Settings")])
        self.assertEqual(catalog.usage[app.desktop_id], 1)
        self.assertTrue(getattr(write_usage, "called"))

    @patch("luminophore_shell.applications.write_json_atomic")
    def test_explicit_default_bypasses_preferred_action(self, _write_usage: object) -> None:
        app = _record(
            "org.example.Settings.desktop",
            "Example Settings",
            FakeAppInfo(),
            (ApplicationAction("Settings", "Open Settings"),),
        )
        catalog = _catalog(app, ((app.desktop_id, "Settings"),))

        self.assertTrue(catalog.launch(app, use_preferred=False))

        self.assertEqual(catalog.external_launcher.desktop_launches, [(app.desktop_id, None)])

    @patch("luminophore_shell.applications.write_json_atomic")
    def test_stale_preferred_action_falls_back_to_default(self, _write_usage: object) -> None:
        app = _record("example.desktop", "Example", FakeAppInfo())
        catalog = _catalog(app, ((app.desktop_id, "Missing"),))

        with self.assertLogs("luminophore-shell", level="WARNING"):
            self.assertTrue(catalog.launch(app))

        self.assertEqual(catalog.external_launcher.desktop_launches, [(app.desktop_id, None)])

    @patch("luminophore_shell.applications.write_json_atomic")
    def test_failed_launch_does_not_increment_usage(self, write_usage: object) -> None:
        app = _record("broken.desktop", "Broken", FakeAppInfo())
        catalog = _catalog(app)
        catalog.external_launcher.succeed = False

        with self.assertLogs("luminophore-shell", level="WARNING"):
            self.assertFalse(catalog.launch(app))

        self.assertNotIn(app.desktop_id, catalog.usage)
        self.assertFalse(getattr(write_usage, "called"))


if __name__ == "__main__":
    unittest.main()
