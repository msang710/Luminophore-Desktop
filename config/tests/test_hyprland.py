from __future__ import annotations
import tomllib

import json
import re
import subprocess
import unittest
from unittest.mock import patch

from luminophore_shell.hyprland import HyprlandClient, SpatialOutputView, SpatialView, WorkspaceRef, parse_monitors, parse_spatial_state, parse_windows


MONITORS = [
    {"id": 1, "name": "DP-1", "x": 1920, "y": 0, "width": 1920, "height": 1080,
     "activeWorkspace": {"id": 4, "name": "4"}},
    {"id": 0, "name": "DP-2", "x": 0, "y": 0, "width": 1920, "height": 1080,
     "activeWorkspace": {"id": 1, "name": "1"}},
]

CLIENTS = [
    {"address": "abc", "class": "ghostty", "initialClass": "com.mitchellh.ghostty",
     "title": "shell", "initialTitle": "shell", "pid": 42, "monitor": 0,
     "workspace": {"id": 1, "name": "1"}, "floating": False,
     "at": [30, 40], "size": [900, 700], "focusHistoryID": 0,
     "urgent": False, "mapped": True, "hidden": False},
]


class HyprlandPlacementTests(unittest.TestCase):
    def test_desktop_mode_allows_saved_wide_key_and_detects_visible_leak(self) -> None:
        payload = {
            "active": True, "committed": True, "revision": 2, "topologyRevision": 1,
            "committedRevision": {"model": 2, "topology": 1},
            "extent": {"columns": 15, "rows": 5},
            "view": {"x": 0, "y": 0, "columns": 1, "rows": 1},
            "presentationMode": "desktop", "wideKey": "0xabc",
            "outputViews": [{"output": 1, "x": 0, "y": 0, "columns": 1, "rows": 1}],
            "windows": [{"address": "0xabc", "x": 0, "y": 0, "visible": False, "mode": "tiled"}],
        }
        state = parse_spatial_state(payload)
        self.assertEqual(state.presentation_mode, "desktop")
        self.assertNotIn("unexpected-wide-key", state.diagnostics)
        self.assertNotIn("invalid-mode", state.diagnostics)
        self.assertNotIn("desktop-leak", state.diagnostics)
        payload["windows"][0]["visible"] = True
        self.assertIn("desktop-leak", parse_spatial_state(payload).diagnostics)

    def test_spatial_off_view_focus_and_display_origin_are_preserved(self) -> None:
        state = parse_spatial_state({
            "active": True, "committed": True, "revision": 2, "topologyRevision": 1,
            "committedRevision": {"model": 2, "topology": 1},
            "extent": {"columns": 15, "rows": 5}, "displayOrigin": {"x": 7, "y": 2},
            "view": {"x": 0, "y": 0, "columns": 1, "rows": 1},
            "outputViews": [{"output": 1, "x": 0, "y": 0, "columns": 1, "rows": 1}],
            "windows": [{"address": "0xabc", "x": 3, "y": 0, "visible": False, "mode": "tiled"}],
            "focusedKey": "0xabc", "selectedOutput": 1,
        })
        self.assertEqual(state.focused_key, "0xabc")
        self.assertNotIn("invalid-focus", state.diagnostics)
        self.assertEqual(state.display_point(3, 0), (-4, -2))

    def test_minimize_uses_native_placement_dispatcher(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        HyprlandClient(runner).minimize("0xabc")

        expression = calls[0][2]
        self.assertEqual(tomllib.loads(expression)["action"], "minimize")
        self.assertIn('action = "minimize"', expression)
        self.assertIn('window = "address:0xabc"', expression)


class HyprlandParsingTests(unittest.TestCase):
    def test_workspace_roles_are_inferred_for_legacy_payloads(self) -> None:
        self.assertEqual(WorkspaceRef(-10, "luminophore-base-DP-2").role, "base")
        self.assertEqual(WorkspaceRef(-99, "special:special").role, "unknown")
        self.assertEqual(WorkspaceRef(-97, "special:luminophore-spotify").role, "service")
        self.assertEqual(WorkspaceRef(1, "1").role, "unknown")

    def test_workspace_role_from_compositor_payload_is_preserved(self) -> None:
        monitors = parse_monitors([
            {
                "id": 0,
                "name": "DP-2",
                "width": 1920,
                "height": 1080,
                "activeWorkspace": {"id": -10, "name": "desktop", "role": "base"},
            }
        ])

        self.assertEqual(monitors[0].active_workspace.role, "base")

    def test_monitor_name_is_joined_to_window(self) -> None:
        monitors = parse_monitors(MONITORS)
        windows = parse_windows(CLIENTS, monitors)
        self.assertEqual(windows[0].address, "0xabc")
        self.assertEqual(windows[0].monitor_name, "DP-2")
        self.assertEqual(windows[0].workspace, WorkspaceRef(1, "1"))
        self.assertEqual(windows[0].app_key, "com.mitchellh.ghostty")

    def test_monitor_parser_preserves_open_special_workspace(self) -> None:
        monitors = parse_monitors([
            {
                "id": 0,
                "name": "DP-1",
                "width": 1920,
                "height": 1080,
                "activeWorkspace": {"id": 5, "name": "5"},
                "specialWorkspace": {"id": -99, "name": "special:special"},
                "focused": True,
            }
        ])

        self.assertEqual(monitors[0].active_workspace, WorkspaceRef(5, "5"))
        self.assertEqual(monitors[0].special_workspace, WorkspaceRef(-99, "special:special"))
        self.assertTrue(monitors[0].focused)

    def test_spatial_state_parser_preserves_board_view_and_window_coordinates(self) -> None:
        state = parse_spatial_state({
            "active": True,
            "revision": 7,
            "extent": {"columns": 4, "rows": 2},
            "view": {"x": 1, "y": 0, "columns": 2, "rows": 2},
            "windows": [
                {"address": "abc", "x": 1, "y": 0, "visible": True},
                {"address": "0xdef", "x": 3, "y": 1, "visible": False},
            ],
        })

        self.assertTrue(state.active)
        self.assertEqual(state.revision, 7)
        self.assertEqual((state.columns, state.rows), (4, 2))
        self.assertEqual((state.view.x, state.view.y, state.view.columns, state.view.rows), (1, 0, 2, 2))
        self.assertTrue(state.view.contains(2, 1))
        self.assertFalse(state.view.contains(3, 1))
        self.assertEqual(state.windows[0].address, "0xabc")
        self.assertFalse(state.windows[1].visible)

    def test_spatial_state_parser_preserves_committed_output_views_and_focus(self) -> None:
        state = parse_spatial_state({
            "active": True,
            "committed": True,
            "revision": 9,
            "topologyRevision": 4,
            "committedRevision": {"model": 9, "topology": 4},
            "presentationMode": "normal",
            "wideKey": None,
            "focusedKey": "abc",
            "extent": {"columns": 6, "rows": 3},
            "view": {"x": 0, "y": 0, "columns": 2, "rows": 1},
            "outputViews": [
                {"output": 20, "x": 0, "y": 0, "columns": 1, "rows": 1, "anchorKey": "abc"},
                {"output": 10, "x": 2, "y": 0, "columns": 1, "rows": 3, "anchorKey": "def"},
            ],
            "windows": [
                {"address": "abc", "mode": "tiled", "x": 0, "y": 0, "visible": True, "primaryOutput": 20, "fragments": [{"output": 20}]},
                {"address": "def", "mode": "tiled", "x": 2, "y": 1, "visible": True, "primaryOutput": 10, "fragments": [{"output": 10}]},
            ],
        })

        self.assertEqual(
            state.output_views,
            (
                SpatialOutputView(20, SpatialView(0, 0, 1, 1), "0xabc"),
                SpatialOutputView(10, SpatialView(2, 0, 1, 3), "0xdef"),
            ),
        )
        self.assertEqual(state.focused_key, "0xabc")
        self.assertEqual(state.target_output_id, 20)
        self.assertEqual(state.diagnostics, ())

    def test_spatial_state_reports_semantic_errors_instead_of_hiding_them(self) -> None:
        state = parse_spatial_state({
            "active": True,
            "committed": True,
            "revision": 9,
            "topologyRevision": 4,
            "committedRevision": {"model": 8, "topology": 4},
            "presentationMode": "normal",
            "focusedKey": "missing",
            "extent": {"columns": 4, "rows": 2},
            "view": {"x": 0, "y": 0, "columns": 2, "rows": 1},
            "outputViews": [
                {"output": 10, "x": 0, "y": 0, "columns": 2, "rows": 1},
                {"output": 20, "x": 1, "y": 0, "columns": 2, "rows": 1},
            ],
            "windows": [
                {"address": "abc", "mode": "tiled", "x": 3, "y": 1, "visible": True, "primaryOutput": 10, "fragments": [{"output": 10}]},
            ],
        })

        self.assertIn("stale-revision", state.diagnostics)
        self.assertIn("overlapping-views", state.diagnostics)
        self.assertIn("invalid-focus", state.diagnostics)
        self.assertIn("invalid-fragments", state.diagnostics)

    def test_maximized_is_not_immersive_fullscreen(self) -> None:
        maximized = dict(CLIENTS[0], fullscreen=1)
        fullscreen = dict(CLIENTS[0], fullscreen=2)
        windows = parse_windows([maximized, fullscreen], parse_monitors(MONITORS))
        self.assertFalse(windows[0].immersive_fullscreen)
        self.assertTrue(windows[1].immersive_fullscreen)

    def test_shell_projection_falls_back_without_canary(self) -> None:
        calls: list[list[str]] = []

        with patch.dict("os.environ", {}, clear=True):
            projected = HyprlandClient(lambda command, **_kwargs: calls.append(command)).shell_projection(
                "launcher", True, "generation-1", 1, {}
            )

        self.assertFalse(projected)
        self.assertEqual(calls, [])

    def test_shell_projection_prepares_then_commits_one_generation(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        with patch.dict("os.environ", {"LUMINOPHORE_COMPOSITOR": "1"}):
            projected = HyprlandClient(runner, hyprctl="/test/luminophore/hyprctl").shell_projection(
                "launcher", False, "generation-2", 7, {"red": 0.1, "green": 0.2, "blue": 0.3, "radius": 18.0}
            )

        self.assertTrue(projected)
        self.assertEqual(len(calls), 2)
        expressions = [call[2] for call in calls]
        data = tomllib.loads(expressions[0])
        self.assertEqual(data['version'], 1)
        self.assertEqual(data['action'], 'projection')
        self.assertEqual(data['role'], 'passive')
        self.assertTrue(data['revision'].isdigit())
        self.assertEqual(data['content_revision'], '7')
        self.assertEqual(data['surface'], 'luminophore-shell-launcher')
        self.assertEqual(data['generation'], 'generation-2')
        self.assertEqual(data['phase'], 'prepare')
        self.assertEqual(data['requested_plane'], 'bottom')
        self.assertEqual((data['red'], data['radius'], data['panel_width'], data['reveal_from']), (.1,18.,0.,1.))
        self.assertFalse(data['bloom'])
        self.assertEqual(tomllib.loads(expressions[1])['phase'], 'commit')

    def test_shell_projection_aborts_and_falls_back_when_commit_is_rejected(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            rejected = tomllib.loads(command[2])['phase'] == 'commit'
            return subprocess.CompletedProcess(command, 1 if rejected else 0, stdout="" if rejected else "ok", stderr="rejected" if rejected else "")

        with patch.dict("os.environ", {"LUMINOPHORE_COMPOSITOR": "1"}):
            projected = HyprlandClient(runner, hyprctl="/test/luminophore/hyprctl").shell_projection("launcher", True, "generation-3", 2, {})

        self.assertFalse(projected)
        self.assertEqual(
            [tomllib.loads(call[2])['phase'] for call in calls],
            ["prepare", "commit", "abort"],
        )

    def test_shell_projection_canary_bloom_is_explicitly_opted_in(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        with patch.dict("os.environ", {"LUMINOPHORE_COMPOSITOR": "1", "LUMINOPHORE_SHELL_BLOOM": "1"}):
            HyprlandClient(runner, hyprctl="/test/luminophore/hyprctl").shell_projection("weather", True, "generation-4", 3, {"glow_phase": 0.37})

        self.assertTrue(all(tomllib.loads(call[2])['bloom'] for call in calls))
        self.assertTrue(all(tomllib.loads(call[2])['glow_phase'] == .37 for call in calls))

    def test_shell_projection_acknowledges_only_the_matching_generation(self) -> None:
        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        client = HyprlandClient(runner)
        with patch.dict("os.environ", {"LUMINOPHORE_COMPOSITOR": "1"}):
            self.assertTrue(client.shell_projection("launcher", True, "generation-5", 4, {}))

        revision = client._projection_revisions["launcher"]
        self.assertFalse(client.shell_projection_presented(f"luminophore-shell-launcher,wrong,{revision},4"))
        self.assertTrue(client.shell_projection_presented(f"luminophore-shell-launcher,generation-5,{revision},4"))
        self.assertFalse(client.shell_projection_presented(f"luminophore-shell-launcher,generation-5,{revision},4"))

    def test_minimize_does_not_use_hidden_workspace(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        HyprlandClient(runner).minimize("0xabc")
        expression = calls[0][2]
        self.assertIn('action = "minimize"', expression)
        self.assertIn('window = "address:0xabc"', expression)
        self.assertNotIn("special:minimized", expression)

    def test_numbered_workspace_dispatch_adapter_is_retired(self) -> None:
        self.assertFalse(hasattr(HyprlandClient, "bring_to_workspace"))
        self.assertFalse(hasattr(WorkspaceRef(4, "4"), "dispatch_name"))

    def test_restore_tiled_does_not_force_geometry(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        client = HyprlandClient(runner)
        window = parse_windows(CLIENTS, parse_monitors(MONITORS))[0]
        client.restore(window)
        expressions = [call[2] for call in calls]
        self.assertEqual(len(expressions), 1)
        self.assertEqual(tomllib.loads(expressions[0])['action'], 'restore')
        self.assertFalse(any("resize" in value or "x=30" in value for value in expressions))

    def test_restore_floating_restores_geometry(self) -> None:
        raw = dict(CLIENTS[0])
        raw["floating"] = True
        calls: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        HyprlandClient(runner).restore(parse_windows([raw], parse_monitors(MONITORS))[0])
        expressions = [call[2] for call in calls]
        self.assertEqual(len(expressions), 1)
        self.assertEqual(tomllib.loads(expressions[0])['action'], 'restore')

    def test_blur_pair_is_saved_through_one_generation(self):
        from unittest.mock import patch, Mock
        service=Mock();service.snapshot.return_value=({},'generation')
        with patch('luminophore_shell.domain_client.DomainClient',return_value=service):
            HyprlandClient().set_blur(9,2)
        service.commit.assert_called_once_with({'compositor.blur_size':9,'compositor.blur_passes':2},'generation')

    def test_blur_failure_never_replays_outside_coordinator(self):
        from unittest.mock import patch, Mock
        service=Mock();service.snapshot.return_value=({},'generation');service.commit.side_effect=ValueError('rollback pending')
        with patch('luminophore_shell.domain_client.DomainClient',return_value=service),self.assertRaisesRegex(RuntimeError,'rollback pending'):
            HyprlandClient().set_blur(8,3)
        self.assertEqual(service.commit.call_count,1)

    def test_blur_rejects_values_below_one_before_calling_hyprctl(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        with self.assertRaisesRegex(RuntimeError, "at least 1"):
            HyprlandClient(runner).set_blur(0, 1)

        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()


class SurfaceSourceTests(unittest.TestCase):
    def payload(self):
        return {
            "active": True, "revision": 1, "extent": {"columns": 15, "rows": 5},
            "view": {"x": 0, "y": 0, "columns": 1, "rows": 1},
            "capabilities": ["surface-source-v1"], "sourceInstance": "instance-test",
            "windows": [{"address": "0xabc", "x": 3, "y": 0, "visible": False,
                         "presentationAvailable": False, "box": {"width": 0, "height": 0},
                         "source": {"token": "18446744073709551614", "revision": "42", "extentRevision": "3", "alive": True,
                                    "mapped": True, "hasBuffer": True, "extent": {"width": 706, "height": 830}}}],
        }

    def test_hidden_box_does_not_override_committed_source(self):
        window = parse_spatial_state(self.payload()).windows[0]
        self.assertFalse(window.presentation_available)
        self.assertEqual(window.source.extent, (706.0, 830.0))
        self.assertEqual(window.source.token, 18446744073709551614)

    def test_legacy_capability_does_not_guess_source_from_box(self):
        payload = self.payload()
        del payload["capabilities"]
        payload["windows"][0]["box"] = {"width": 1920, "height": 1080}
        window = parse_spatial_state(payload).windows[0]
        self.assertIsNone(window.source)
        self.assertIsNone(window.presentation_available)

    def test_content_commit_does_not_change_editor_spatial_equality(self):
        payload = self.payload()
        before = parse_spatial_state(payload)
        payload["windows"][0]["source"]["revision"] = "43"
        after = parse_spatial_state(payload)
        self.assertEqual(before, after)
        self.assertNotEqual(before.windows[0].source, after.windows[0].source)

    def test_unavailable_is_explicit_not_zero_extent(self):
        payload = self.payload()
        raw = payload["windows"][0]["source"]
        raw.update(mapped=False, hasBuffer=False, extent=None)
        window = parse_spatial_state(payload).windows[0]
        self.assertIsNone(window.source.extent)
        self.assertTrue(window.source.alive)

    def test_invalid_source_metadata_rejected(self):
        from luminophore_shell.hyprland import HyprlandError
        for field, value in (("token", "18446744073709551616"), ("revision", 42),
                             ("alive", False), ("mapped", False), ("hasBuffer", False),
                             ("extent", {"width": float("nan"), "height": 10}),
                             ("extent", {"width": 0, "height": 10})):
            with self.subTest(field=field, value=value):
                payload = self.payload()
                payload["windows"][0]["source"][field] = value
                with self.assertRaises(HyprlandError):
                    parse_spatial_state(payload)


    def test_restart_does_not_reuse_source_identity(self):
        payload = self.payload()
        before = parse_spatial_state(payload).windows[0].source
        payload["sourceInstance"] = "new-instance"
        after = parse_spatial_state(payload).windows[0].source
        self.assertEqual(before.token, after.token)
        self.assertNotEqual(before, after)
