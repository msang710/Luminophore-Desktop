from dataclasses import replace
import json
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

from luminophore_shell.spatial_feedback import SpatialFeedbackQueue, parse_spatial_feedback
from luminophore_shell.ui.osd import OsdLayer


class SpatialFeedbackTests(unittest.TestCase):
    def event(self, **updates):
        data = dict(schema=1, action="focus", applied=True, reason="", direction="left", visible=True, revision=3, topologyRevision=1,
                    connector="DP-2", window="0x123", fromPoint=[7, 2], toPoint=[6, 3],
                    fromRect=[6, 2, 2, 1], toRect=[6, 3, 2, 1], displayOrigin=[7, 2])
        data.update(updates)
        return parse_spatial_feedback(json.dumps(data))

    def test_signed_coordinates_and_app_name(self):
        self.assertEqual(self.event().label("Terminal"), "Terminal · 선택 (-1, +1)")
        self.assertEqual(self.event(action="view-resize").label(), "DP-2 · 뷰 (-1, +1) · 2×1")

    def test_off_view_move_is_explicit(self):
        self.assertEqual(self.event(action="window-move", visible=False).label("Terminal"),
                         "Terminal · 이동 (-1, +1) · 화면 밖")

    def test_invalid_payload_does_not_reach_ui(self):
        for payload in ("null", "{", "[]"):
            self.assertIsNone(parse_spatial_feedback(payload))
        for changes in ({"schema": 2}, {"action": []}, {"applied": 1}, {"displayOrigin": [1]}, {"toPoint": [1, "x"]}):
            self.assertIsNone(self.event(**changes))

    def test_blocked_action_has_no_success_label(self):
        self.assertEqual(self.event(applied=False, reason="stale-topology").label(), "화면 구성이 변경되었습니다")

    def test_coalescing_preserves_each_output_and_latest_revision(self):
        queue = SpatialFeedbackQueue()
        first = self.event()
        latest = replace(first, revision=5, to_point=(8, 3))
        queue.push(first)
        queue.push(latest)
        queue.push(replace(first, connector="DP-1"))
        queue.push(first)
        self.assertEqual(queue.drain(), (latest, replace(first, connector="DP-1")))
        self.assertEqual(queue.drain(), ())
        # A new compositor generation may restart revision counters.
        queue.push(replace(first, revision=0, topology_revision=0))
        self.assertEqual(len(queue.drain()), 1)

    def test_spatial_osd_displays_text_and_app_icon_without_percentage(self):
        layer = object.__new__(OsdLayer)
        layer.icon, layer.progress, layer.label = Mock(), Mock(), Mock()
        layer._present = Mock()
        icon = Mock()
        layer.show_spatial("Terminal · 선택 (-1, +1)", icon)
        layer.icon.set_from_gicon.assert_called_once_with(icon)
        layer.progress.set_visible.assert_called_once_with(False)
        layer.label.set_label.assert_called_once_with("Terminal · 선택 (-1, +1)")
        layer._present.assert_called_once_with()

    def test_shell_routes_feedback_to_result_output_and_resolves_app_icon(self):
        from luminophore_shell.app import LuminophoreShellApplication
        queue = SpatialFeedbackQueue()
        queue.push(self.event())
        icon = Mock()
        osd = Mock()
        shell = SimpleNamespace(_spatial_feedback_timer=1, _spatial_feedback=queue,
                                osd_layers={"DP-2": osd, "DP-1": Mock()},
                                windows=[SimpleNamespace(address="0x123", app_class="terminal")],
                                catalog=Mock())
        shell.catalog.match_window_class.return_value = SimpleNamespace(name="Terminal", icon=icon)
        self.assertFalse(LuminophoreShellApplication._flush_spatial_feedback(shell))
        osd.show_spatial.assert_called_once_with("Terminal · 선택 (-1, +1)", icon)
        shell.osd_layers["DP-1"].show_spatial.assert_not_called()
        self.assertEqual(shell._spatial_feedback_timer, 0)


class FullscreenFeedbackTests(unittest.TestCase):
    def test_actual_state_and_failure(self):
        from luminophore_shell.spatial_feedback import parse_fullscreen_feedback
        data = dict(schema=1, connector="DP-1", window="0x123", enabled=True, applied=True)
        self.assertEqual(parse_fullscreen_feedback(json.dumps(data)), ("DP-1", "0x123", "전체화면"))
        data["enabled"] = False
        self.assertEqual(parse_fullscreen_feedback(json.dumps(data))[2], "전체화면 해제")
        data["applied"] = False
        self.assertEqual(parse_fullscreen_feedback(json.dumps(data))[2], "전체화면 전환을 적용하지 못했습니다")
        for payload in ["null", "[]", "{}", json.dumps({**data, "enabled": 1}), json.dumps({**data, "connector": []})]:
            self.assertIsNone(parse_fullscreen_feedback(payload))

    def test_routes_to_result_monitor(self):
        from luminophore_shell.app import LuminophoreShellApplication
        shell = SimpleNamespace(osd_layers={"DP-1": Mock(), "DP-2": Mock()}, windows=[], catalog=None)
        LuminophoreShellApplication._fullscreen_feedback(shell, json.dumps(dict(schema=1,connector="DP-2",window="0x123",enabled=True,applied=True)))
        shell.osd_layers["DP-1"].show_spatial.assert_not_called()
        shell.osd_layers["DP-2"].show_spatial.assert_called_once_with("전체화면", None)
