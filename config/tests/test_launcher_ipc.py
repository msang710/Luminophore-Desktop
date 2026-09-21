from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from luminophore_shell.__main__ import _ctl_request, _parser
from luminophore_shell.app import LuminophoreShellApplication, overview_occupied_monitor_names
from luminophore_shell.glow import FrameTimingSnapshot
from luminophore_shell.hyprland import MonitorRecord, WindowRecord, WorkspaceRef


class FakeLauncherSurface:
    def __init__(self, name: str, expanded: bool, connector: str = "DP-1") -> None:
        self.name = name
        self.monitor = SimpleNamespace(get_connector=lambda: connector)
        self.expanded = expanded
        self.expansion_requests: list[bool] = []
        self.released = False
        self.overview_owned = False

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = expanded
        self.expansion_requests.append(expanded)

    def toggle(self) -> None:
        raise AssertionError("launcher toggle must use the focus-aware open path")

    def begin_overview_reveal(self, visible: bool, _delay: int, completed) -> None:
        self.overview_owned = True
        if not visible:
            self.expanded = False
        completed()

    def finish_overview_hide(self) -> None:
        self.set_expanded(False)
        self.release_overview_reveal()

    def release_overview_reveal(self) -> None:
        self.released = True
        self.overview_owned = False

    def overview_reveal_owned(self) -> bool:
        return self.overview_owned

    def contains_global_point(self, _x: int, _y: int) -> bool:
        return False


class FakeLauncherPanel:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def focus_query(self, query: str) -> None:
        self.queries.append(query)


class LauncherIpcHarness:
    _dispatch = LuminophoreShellApplication._dispatch
    _open_launcher = LuminophoreShellApplication._open_launcher

    def __init__(self, expanded: bool) -> None:
        self.surface = FakeLauncherSurface("launcher", expanded)
        self.surfaces = {"launcher": self.surface}
        self.launcher_panel = FakeLauncherPanel()
        self.catalog = None
        self.taskbar = None


class LauncherIpcTests(unittest.TestCase):
    @staticmethod
    def _monitor(name: str, workspace: WorkspaceRef, special: WorkspaceRef = WorkspaceRef(0, "")) -> MonitorRecord:
        return MonitorRecord(0, name, 0, 0, 1920, 1080, workspace, special)

    @staticmethod
    def _window(
        monitor: str,
        workspace: WorkspaceRef,
        *,
        floating: bool = False,
        mapped: bool = True,
        hidden: bool = False,
    ) -> WindowRecord:
        return WindowRecord(
            "0x1", "app", "app", "title", "title", 1, 0, monitor,
            workspace, floating, (0, 0), (800, 600), 0, False, mapped, hidden,
        )

    def test_overview_occupied_monitors_require_visible_tiled_client(self) -> None:
        workspace_1 = WorkspaceRef(1, "1")
        workspace_2 = WorkspaceRef(2, "2")
        monitors = [
            self._monitor("DP-1", workspace_1),
            self._monitor("DP-2", workspace_2),
        ]
        windows = [
            self._window("DP-1", workspace_1),
            self._window("DP-2", workspace_2, floating=True),
            self._window("DP-2", workspace_2, mapped=False),
            self._window("DP-2", workspace_2, hidden=True),
            replace(self._window("DP-2", workspace_2), placement="minimized"),
            self._window("DP-2", workspace_1),
        ]

        self.assertEqual(overview_occupied_monitor_names(monitors, windows), frozenset({"DP-1"}))

    def test_overview_occupied_monitors_include_open_special_workspace(self) -> None:
        active = WorkspaceRef(1, "1")
        special = WorkspaceRef(-2, "special:magic")
        monitors = [self._monitor("DP-1", active, special)]

        self.assertEqual(
            overview_occupied_monitor_names(monitors, [self._window("DP-1", special)]),
            frozenset({"DP-1"}),
        )

    def test_toggle_open_focuses_search_entry(self) -> None:
        app = LauncherIpcHarness(expanded=False)

        with patch(
            "luminophore_shell.app.GLib.idle_add",
            side_effect=lambda callback, *args: callback(*args),
        ):
            result = app._dispatch({"command": "toggle", "panel": "launcher"})

        self.assertEqual(result, {"ok": True})
        self.assertEqual(app.surface.expansion_requests, [True])
        self.assertEqual(app.launcher_panel.queries, [""])

    def test_toggle_close_does_not_refocus_search_entry(self) -> None:
        app = LauncherIpcHarness(expanded=True)

        result = app._dispatch({"command": "toggle", "panel": "launcher"})

        self.assertEqual(result, {"ok": True})
        self.assertEqual(app.surface.expansion_requests, [False])
        self.assertEqual(app.launcher_panel.queries, [])

    def test_overview_cli_builds_toggle_request(self) -> None:
        args = _parser().parse_args(["ctl", "toggle", "overview"])
        self.assertEqual(
            _ctl_request(args),
            {"command": "toggle", "panel": "overview"},
        )

    def test_overview_opens_all_surfaces_and_focuses_launcher(self) -> None:
        surfaces = {
            name: FakeLauncherSurface(name, False)
            for name in ("launcher", "notifications", "weather", "system", "spotify")
        }

        class Coordinator:
            def open_group(self, selected) -> None:
                for surface in selected:
                    surface.set_expanded(True)

        harness = SimpleNamespace(
            surfaces=surfaces,
            coordinator=Coordinator(),
            launcher_panel=FakeLauncherPanel(),
            catalog=None,
            taskbar=None,
            _close_expanded=lambda: [surface.set_expanded(False) for surface in surfaces.values()],
        )
        harness._toggle_overview = LuminophoreShellApplication._toggle_overview.__get__(harness)
        harness._overview_surface_revealed = LuminophoreShellApplication._overview_surface_revealed.__get__(harness)
        harness._overview_surface_hidden = LuminophoreShellApplication._overview_surface_hidden.__get__(harness)
        harness._hide_overview = LuminophoreShellApplication._hide_overview.__get__(harness)
        with patch("luminophore_shell.app.GLib.idle_add", side_effect=lambda callback, *args: callback(*args)):
            result = LuminophoreShellApplication._dispatch(harness, {"command": "toggle", "panel": "overview"})

        self.assertEqual(result, {"ok": True})
        self.assertTrue(all(surface.expanded for surface in surfaces.values()))
        self.assertEqual(harness.launcher_panel.queries, [""])
        self.assertFalse(any(surface.overview_owned for surface in surfaces.values()))
        self.assertEqual(harness._overview_reveal_names, set())

    def test_overview_reveal_targets_only_monitors_with_tiled_clients(self) -> None:
        workspace_1 = WorkspaceRef(1, "1")
        workspace_2 = WorkspaceRef(2, "2")
        surfaces = {
            "launcher": FakeLauncherSurface("launcher", False, "DP-1"),
            "notifications": FakeLauncherSurface("notifications", False, "DP-2"),
            "weather": FakeLauncherSurface("weather", False, "DP-2"),
            "system": FakeLauncherSurface("system", False, "DP-2"),
            "spotify": FakeLauncherSurface("spotify", False, "DP-2"),
        }

        class Coordinator:
            @staticmethod
            def open_group(selected) -> None:
                for surface in selected:
                    surface.set_expanded(True)

        harness = SimpleNamespace(
            surfaces=surfaces,
            coordinator=Coordinator(),
            launcher_panel=None,
            catalog=None,
            taskbar=None,
            hypr_monitors=[self._monitor("DP-1", workspace_1), self._monitor("DP-2", workspace_2)],
            windows=[self._window("DP-1", workspace_1)],
            _overview_surface_names=set(),
            _overview_reveal_names=set(),
            _overview_phase="hidden",
            _overview_pending=0,
        )
        harness._overview_surface_revealed = LuminophoreShellApplication._overview_surface_revealed.__get__(harness)
        harness._toggle_overview = LuminophoreShellApplication._toggle_overview.__get__(harness)

        harness._toggle_overview()

        self.assertTrue(all(surface.expanded for surface in surfaces.values()))
        self.assertTrue(surfaces["launcher"].overview_owned)
        self.assertFalse(any(surface.overview_owned for name, surface in surfaces.items() if name != "launcher"))
        self.assertEqual(harness._overview_reveal_names, {"launcher"})

    def test_overview_second_toggle_closes_all_surfaces(self) -> None:
        surfaces = {
            name: FakeLauncherSurface(name, True)
            for name in ("launcher", "notifications", "weather", "system", "spotify")
        }
        harness = SimpleNamespace(
            surfaces=surfaces,
            launcher_panel=FakeLauncherPanel(),
            catalog=None,
            taskbar=None,
            _close_expanded=lambda: [surface.set_expanded(False) for surface in surfaces.values()],
        )
        harness._overview_surface_revealed = LuminophoreShellApplication._overview_surface_revealed.__get__(harness)
        harness._overview_surface_hidden = LuminophoreShellApplication._overview_surface_hidden.__get__(harness)
        harness._hide_overview = LuminophoreShellApplication._hide_overview.__get__(harness)
        LuminophoreShellApplication._toggle_overview(harness)
        self.assertFalse(any(surface.expanded for surface in surfaces.values()))

    def test_overview_keeper_survives_while_other_surfaces_hide_in_reverse(self) -> None:
        surfaces = {
            name: FakeLauncherSurface(name, True)
            for name in ("launcher", "notifications", "weather", "system", "spotify")
        }
        harness = SimpleNamespace(
            surfaces=surfaces,
            _overview_surface_names=set(surfaces),
            _overview_phase="visible",
            _overview_pending=0,
        )
        harness._overview_surface_hidden = LuminophoreShellApplication._overview_surface_hidden.__get__(harness)
        harness._hide_overview = LuminophoreShellApplication._hide_overview.__get__(harness)
        surfaces["weather"].overview_owned = True

        harness._hide_overview(surfaces["weather"])

        self.assertTrue(surfaces["weather"].expanded)
        self.assertTrue(surfaces["weather"].overview_owned)
        self.assertFalse(surfaces["weather"].released)
        self.assertFalse(any(surface.expanded for name, surface in surfaces.items() if name != "weather"))

    def test_promoted_keeper_uses_reverse_reveal_on_later_outside_click(self) -> None:
        keeper = FakeLauncherSurface("weather", True)
        keeper.overview_owned = True
        harness = SimpleNamespace(
            surfaces={"weather": keeper},
            _overview_surface_names=set(),
            _outside_click_suppressed_until=0.0,
        )
        harness._hide_overview = LuminophoreShellApplication._hide_overview.__get__(harness)
        harness._dismiss_expanded_at = LuminophoreShellApplication._dismiss_expanded_at.__get__(harness)

        harness._dismiss_expanded_at(1000, 1000)

        self.assertFalse(keeper.expanded)
        self.assertTrue(keeper.released)

    def test_overview_toggle_reverses_a_hide_in_progress(self) -> None:
        workspace = WorkspaceRef(1, "1")

        class DeferredSurface(FakeLauncherSurface):
            def __init__(self, name: str) -> None:
                super().__init__(name, False)
                self.reveal_requests: list[bool] = []

            def begin_overview_reveal(self, visible: bool, _delay: int, _completed) -> None:
                self.reveal_requests.append(visible)

        surfaces = {
            name: DeferredSurface(name)
            for name in ("launcher", "notifications", "weather", "system", "spotify")
        }

        class Coordinator:
            @staticmethod
            def open_group(selected) -> None:
                for surface in selected:
                    surface.set_expanded(True)

        harness = SimpleNamespace(
            surfaces=surfaces,
            coordinator=Coordinator(),
            launcher_panel=None,
            catalog=None,
            taskbar=None,
            _overview_surface_names=set(),
            _overview_phase="hidden",
            _overview_pending=0,
            hypr_monitors=[self._monitor("DP-1", workspace)],
            windows=[self._window("DP-1", workspace)],
        )
        harness._overview_surface_revealed = LuminophoreShellApplication._overview_surface_revealed.__get__(harness)
        harness._overview_surface_hidden = LuminophoreShellApplication._overview_surface_hidden.__get__(harness)
        harness._hide_overview = LuminophoreShellApplication._hide_overview.__get__(harness)
        harness._toggle_overview = LuminophoreShellApplication._toggle_overview.__get__(harness)

        harness._toggle_overview()
        harness._toggle_overview()
        harness._toggle_overview()

        self.assertEqual(surfaces["launcher"].reveal_requests, [True, False, True])
        self.assertEqual(harness._overview_phase, "revealing")

    def test_glow_probe_cli_builds_operator_request(self) -> None:
        args = _parser().parse_args(["ctl", "glow-probe", "reset"])
        self.assertEqual(
            _ctl_request(args),
            {"command": "glow-probe", "action": "reset"},
        )

    def test_glow_probe_dispatch_controls_all_surfaces(self) -> None:
        calls: list[tuple[str, bool]] = []

        def surface(name: str):
            glow = SimpleNamespace(
                enable_frame_timing_probe=lambda enabled: calls.append((name, enabled)),
            )
            return SimpleNamespace(glow=glow)

        harness = SimpleNamespace(surfaces={"launcher": surface("launcher"), "weather": surface("weather")})
        result = LuminophoreShellApplication._dispatch(harness, {"command": "glow-probe", "action": "start"})
        self.assertEqual(result["surfaces"], ["launcher", "weather"])
        self.assertEqual(calls, [("launcher", True), ("weather", True)])

    def test_glow_status_reports_effective_renderer_error_and_timing(self) -> None:
        timing = FrameTimingSnapshot(10, 6944, 6942.0, 6944.0, 7000.0, 0.0)
        glow = SimpleNamespace(
            effective_renderer="layer",
            renderer_error=None,
            renderer_status=lambda: {},
            frame_timing_enabled=True,
            frame_timing_snapshot=lambda: timing,
        )
        harness = SimpleNamespace(
            surfaces={"launcher": SimpleNamespace(glow=glow)},
            config=SimpleNamespace(theme=SimpleNamespace(glow_renderer="layer")),
        )
        with patch.dict("os.environ", {"GSK_RENDERER": "gl"}):
            result = LuminophoreShellApplication._glow_status(harness)
        self.assertEqual(result["requested_renderer"], "layer")
        self.assertEqual(result["process_gsk_renderer"], "gl")
        self.assertEqual(result["surfaces"]["launcher"]["effective_renderer"], "layer")
        self.assertEqual(result["surfaces"]["launcher"]["timing"]["sample_count"], 10)


if __name__ == "__main__":
    unittest.main()
