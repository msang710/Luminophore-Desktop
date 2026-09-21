from __future__ import annotations

from dataclasses import replace
import unittest

from luminophore_shell.applications import ApplicationRecord
from luminophore_shell.hyprland import MonitorRecord, WindowRecord, WorkspaceRef
from luminophore_shell.minimize import MinimizedWindow
from luminophore_shell.ui.launcher import (
    _activate_window,
    _group_badge_text,
    _is_taskbar_window_visible,
    _windows_for_app,
)


def _window(address: str, workspace: WorkspaceRef) -> WindowRecord:
    return WindowRecord(
        address,
        "com.mitchellh.ghostty",
        "com.mitchellh.ghostty",
        "terminal",
        "Ghostty",
        42,
        0,
        "DP-1",
        workspace,
        False,
        (0, 0),
        (100, 100),
        0,
        False,
        True,
        False,
    )


class TaskbarBadgeTests(unittest.TestCase):
    def test_multiple_windows_use_exact_count_without_plus(self) -> None:
        self.assertEqual(_group_badge_text(2), "2")
        self.assertEqual(_group_badge_text(5), "5")

    def test_single_window_has_no_badge(self) -> None:
        self.assertEqual(_group_badge_text(1), "")
        self.assertEqual(_group_badge_text(0), "")

    def test_window_in_closed_special_workspace_stays_on_taskbar(self) -> None:
        window = _window("0xabc", WorkspaceRef(-99, "special:special"))

        self.assertTrue(_is_taskbar_window_visible(window, set()))

    def test_hidden_minimized_workspace_requires_managed_record(self) -> None:
        window = replace(_window("0xabc", WorkspaceRef(3, "3")), placement="minimized")

        self.assertFalse(_is_taskbar_window_visible(window, set()))
        self.assertTrue(_is_taskbar_window_visible(window, {"0xabc"}))

    def test_background_spotify_workspace_is_never_visible(self) -> None:
        window = _window("0xabc", WorkspaceRef(-97, "special:luminophore-spotify"))

        self.assertFalse(_is_taskbar_window_visible(window, set()))

    def test_inactive_normal_workspace_stays_visible(self) -> None:
        window = _window("0xabc", WorkspaceRef(3, "3"))

        self.assertTrue(_is_taskbar_window_visible(window, set()))

    def test_app_window_matching_includes_hidden_special_workspace(self) -> None:
        app = ApplicationRecord(
            "com.spotify.Client.desktop",
            "Spotify",
            "",
            "spotify",
            None,
            object(),  # type: ignore[arg-type]
        )
        spotify = _window("0xspotify", WorkspaceRef(-97, "special:luminophore-spotify"))
        spotify = WindowRecord(
            spotify.address,
            "spotify",
            "spotify",
            spotify.title,
            spotify.initial_title,
            spotify.pid,
            spotify.monitor_id,
            spotify.monitor_name,
            spotify.workspace,
            spotify.floating,
            spotify.at,
            spotify.size,
            4,
            spotify.urgent,
            spotify.mapped,
            spotify.hidden,
        )
        unrelated = _window("0xterminal", WorkspaceRef(4, "4"))

        class Catalog:
            def match_window_class(self, app_class: str) -> ApplicationRecord | None:
                return app if app_class == "spotify" else None

        self.assertEqual(
            _windows_for_app(app, [unrelated, spotify], Catalog()),  # type: ignore[arg-type]
            [spotify],
        )

    def test_app_windows_are_ranked_by_recent_focus(self) -> None:
        app = ApplicationRecord("browser.desktop", "Browser", "", "browser", None, object())  # type: ignore[arg-type]
        older = replace(_window("0xolder", WorkspaceRef(2, "2")), focus_history_id=7)
        recent = replace(_window("0xrecent", WorkspaceRef(3, "3")), focus_history_id=1)

        class Catalog:
            def match_window_class(self, _app_class: str) -> ApplicationRecord | None:
                return app

        self.assertEqual(
            [window.address for window in _windows_for_app(app, [older, recent], Catalog())],  # type: ignore[arg-type]
            ["0xrecent", "0xolder"],
        )

    def test_selected_window_is_focused_without_cross_monitor_rehome(self) -> None:
        class Client:
            def __init__(self) -> None:
                self.brought: list[tuple[str, WorkspaceRef]] = []
                self.focused: list[str] = []

            def bring_to_workspace(self, address: str, workspace: WorkspaceRef) -> None:
                self.brought.append((address, workspace))

            def focus(self, address: str) -> None:
                self.focused.append(address)

        class Minimize:
            records: dict[str, MinimizedWindow] = {}

            def restore(self, _address: str) -> None:
                raise AssertionError("ordinary window must not use minimized restore")

        client = Client()
        target = _window("0xabc", WorkspaceRef(3, "3"))
        monitors = [
            MonitorRecord(0, "DP-1", 0, 0, 1920, 1080, WorkspaceRef(4, "4"), focused=True)
        ]

        _activate_window("0xabc", [target], monitors, client, Minimize())  # type: ignore[arg-type]

        self.assertEqual(client.brought, [])
        self.assertEqual(client.focused, ["0xabc"])

    def test_minimized_window_keeps_original_restore_contract(self) -> None:
        class Client:
            def bring_to_workspace(self, _address: str, _workspace: WorkspaceRef) -> None:
                raise AssertionError("minimized window must restore instead of moving")

            def focus(self, _address: str) -> None:
                raise AssertionError("minimized restore owns focus")

        class Minimize:
            def __init__(self) -> None:
                self.records = {"0xabc": object()}
                self.restored: list[str] = []

            def restore(self, address: str) -> None:
                self.restored.append(address)

        minimized = Minimize()

        _activate_window("0xabc", [], [], Client(), minimized)  # type: ignore[arg-type]

        self.assertEqual(minimized.restored, ["0xabc"])

class AsyncLauncherPanelTests(unittest.TestCase):
    def test_app_web_command_file_are_submitted_without_sync_execution(self):
        from types import SimpleNamespace
        from pathlib import Path
        from luminophore_shell.ui.launcher import LauncherPanel
        class Catalog:
            def launch(self, *args, **kwargs):
                raise AssertionError('launch must not run on UI caller')
            open_uri = launch
            launch_argv = launch
        submitted = []
        panel = SimpleNamespace(catalog=Catalog(), config=SimpleNamespace(
            web_url='https://example.invalid/?q={query}', shell='/bin/bash', terminal='ghostty'),
            _submit_launch=lambda operation, label: submitted.append((operation, label)))
        app = SimpleNamespace(name='Example')
        LauncherPanel._launch(panel, app)
        LauncherPanel._web(panel, 'test')
        LauncherPanel._command(panel, 'echo test')
        LauncherPanel._open_path(panel, Path('/tmp/example'))
        self.assertEqual([label for _, label in submitted], ['Example', '웹 브라우저', '터미널', 'example'])

    def test_completion_from_closed_panel_does_not_close_reopened_panel(self):
        from types import SimpleNamespace
        from luminophore_shell.ui.launcher import LauncherPanel
        class Label:
            def set_label(self, value): pass
            def set_visible(self, value): pass
            def set_tooltip_text(self, value): pass
        callbacks, closed = [], []
        panel = SimpleNamespace(_launch_epoch=1, launch_status=Label(), get_mapped=lambda: True,
            catalog=SimpleNamespace(submit_launch=lambda op, done: callbacks.append(done) or True),
            close_panel=lambda: closed.append(True), _show_launch_failure=lambda label: None)
        LauncherPanel._submit_launch(panel, lambda: True, 'app')
        panel._launch_epoch += 1
        callbacks[0](True)
        self.assertEqual(closed, [])
        LauncherPanel._submit_launch(panel, lambda: True, 'app')
        callbacks[1](True)
        self.assertEqual(closed, [True])

    def test_failure_is_displayed_only_on_current_panel(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from luminophore_shell.ui.launcher import LauncherPanel
        class Label:
            def set_label(self, value): pass
            def set_visible(self, value): pass
            def set_tooltip_text(self, value): pass
        callbacks, failed = [], []
        panel = SimpleNamespace(_launch_epoch=1, launch_status=Label(), get_mapped=lambda: True,
            catalog=SimpleNamespace(submit_launch=lambda op, done: callbacks.append(done) or True),
            close_panel=lambda: self.fail('failed launch closed panel'), _show_launch_failure=failed.append)
        LauncherPanel._submit_launch(panel, lambda: False, 'app')
        callbacks[0](False)
        self.assertEqual(failed, ['app'])
        LauncherPanel._submit_launch(panel, lambda: False, 'other')
        panel._launch_epoch += 1
        with patch('luminophore_shell.ui.launcher._notify_launch_failure') as notify:
            callbacks[1](False)
            notify.assert_called_once_with('other')
        self.assertEqual(failed, ['app'])

    def test_taskbar_uses_async_submission_and_reports_failure(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from luminophore_shell.ui.launcher import TaskbarWidget
        callbacks, labels = [], []
        taskbar = SimpleNamespace(catalog=SimpleNamespace(
            submit_launch=lambda operation, callback: callbacks.append(callback) or True),
            set_tooltip_text=labels.append)
        TaskbarWidget._submit_launch(taskbar, lambda: False, 'Example')
        self.assertEqual(labels, [])
        with patch('luminophore_shell.ui.launcher._notify_launch_failure') as notify:
            callbacks[0](False)
            notify.assert_called_once_with('Example')
        self.assertIn('Example', labels[0])


if __name__ == "__main__":
    unittest.main()
