import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from luminophore_shell.app import LuminophoreShellApplication


class SpatialHistoryFeedbackTests(unittest.TestCase):
    def test_rejection_is_not_presented_as_success_and_does_not_retry(self):
        for status, expected in (("busy", "조작"), ("unavailable", "복원할 수 없습니다"), ("commit-failed", "이력은 유지"), ("recovery-failed", "중지")):
            osd = Mock()
            shell = SimpleNamespace(osd_layers={"DP-1": osd})
            self.assertFalse(LuminophoreShellApplication._history_feedback(shell, json.dumps(dict(action="undo", status=status))))
            self.assertIn(expected, osd.show_spatial.call_args.args[0])
            osd.show_spatial.assert_called_once()

    def test_empty_redo_and_success_have_distinct_labels(self):
        osd = Mock()
        shell = SimpleNamespace(osd_layers={"DP-1": osd})
        LuminophoreShellApplication._history_feedback(shell, '{"action":"redo","status":"empty"}')
        self.assertIn("이력이 없습니다", osd.show_spatial.call_args.args[0])
        LuminophoreShellApplication._history_feedback(shell, '{"action":"redo","status":"applied"}')
        self.assertEqual(osd.show_spatial.call_args.args[0], "공간 다시 실행")

    def test_malformed_or_unknown_event_is_ignored(self):
        osd = Mock()
        shell = SimpleNamespace(osd_layers={"DP-1": osd})
        for payload in ('null', '[]', '{', '{}', '{"action":[],"status":"busy"}', '{"action":"undo","status":"unknown"}'):
            self.assertFalse(LuminophoreShellApplication._history_feedback(shell, payload))
        osd.show_spatial.assert_not_called()

    def test_open_editor_uses_its_existing_status_instead_of_an_osd(self):
        osd, status = Mock(), Mock()
        shell = SimpleNamespace(osd_layers={"DP-1": osd}, spatial_editor_surface=SimpleNamespace(state=SimpleNamespace(visible=True), status=status))
        LuminophoreShellApplication._history_feedback(shell, '{"action":"undo","status":"applied"}')
        status.set_label.assert_called_once_with("공간 되돌리기")
        osd.show_spatial.assert_not_called()
