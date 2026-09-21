from __future__ import annotations

import subprocess
import unittest
import threading
import queue
from pathlib import Path

from luminophore_shell.external_launch import UwsmApplicationLauncher


class RecordingRunner:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        return subprocess.CompletedProcess(argv, self.returncode, "", "")


class UwsmApplicationLauncherTests(unittest.TestCase):
    def test_external_application_cannot_inherit_private_runtime_paths(self):
        runner = RecordingRunner()
        launcher = UwsmApplicationLauncher(Path('/bin/true'), runner=runner,
            environment={'LUMINOPHORE_RELEASE_ROOT': '/private', 'LD_LIBRARY_PATH': '/private/lib',
                         'PYTHONHOME': '/private/python', 'QT_PLUGIN_PATH': '/private/qt',
                         'FONTCONFIG_FILE': '/private/fonts.conf', 'PATH': '/private/bin:/usr/bin',
                         'QT_QPA_PLATFORM_PLUGIN_PATH': '/old/qt', 'QML_IMPORT_PATH': '/old/qml',
                         'LUMINOPHORE_INSTANCE_SIGNATURE': 'session_123_456',
                         'WAYLAND_DISPLAY': 'wayland-2'})
        self.assertTrue(launcher.launch_argv(['/usr/bin/true']))
        env = runner.calls[0][1]['env']
        self.assertEqual(env, {'PATH': '/usr/bin:/bin', 'LUMINOPHORE_INSTANCE_SIGNATURE': 'session_123_456',
                              'WAYLAND_DISPLAY': 'wayland-2'})
    def launcher(self, runner: RecordingRunner) -> UwsmApplicationLauncher:
        return UwsmApplicationLauncher(
            Path("/bin/true"),
            Path("/bin/true"),
            runner=runner,
            environment={"DEBUG": "release", "KEEP": "yes"},
        )

    def test_desktop_and_action_use_transient_service(self) -> None:
        runner = RecordingRunner()
        launcher = self.launcher(runner)

        self.assertTrue(launcher.launch_desktop("example.desktop", "Settings"))

        argv, kwargs = runner.calls[0]
        self.assertEqual(
            argv,
            ("/bin/true", "app", "-t", "service", "example.desktop:Settings"),
        )
        self.assertEqual(kwargs["env"], {"KEEP": "yes"})

    def test_argv_is_passed_without_shell_reinterpretation(self) -> None:
        runner = RecordingRunner()
        launcher = self.launcher(runner)

        self.assertTrue(launcher.launch_argv(("terminal", "-e", "echo $HOME; exit"), app_name="command"))

        argv, _kwargs = runner.calls[0]
        self.assertEqual(
            argv,
            ("/bin/true", "app", "-t", "service", "-a", "command", "--", "terminal", "-e", "echo $HOME; exit"),
        )

    def test_uri_uses_gio_inside_service(self) -> None:
        runner = RecordingRunner()
        launcher = self.launcher(runner)

        self.assertTrue(launcher.open_uri("https://example.com/?q=hello"))

        argv, _kwargs = runner.calls[0]
        self.assertEqual(
            argv,
            ("/bin/true", "app", "-t", "service", "-a", "uri-handler", "--", "/bin/true", "open", "https://example.com/?q=hello"),
        )

    def test_failure_never_falls_back_to_direct_launch(self) -> None:
        runner = RecordingRunner(returncode=1)
        launcher = self.launcher(runner)

        with self.assertLogs("luminophore-shell", level="WARNING"):
            self.assertFalse(launcher.launch_desktop("broken.desktop"))

        self.assertEqual(len(runner.calls), 1)

    def test_direct_token_is_forwarded_as_unit_property_not_inherited_env(self):
        runner = RecordingRunner()
        launcher = self.launcher(runner)
        launcher.environment.update(LUMINOPHORE_COMPOSITOR="1", LUMINOPHORE_DIRECT_LAUNCH_TOKEN="stale")
        launcher._direct_launch_token = lambda: "12345678-1234-1234-1234-123456789abc"
        self.assertTrue(launcher.launch_desktop("example.desktop"))
        argv, kwargs = runner.calls[0]
        self.assertIn("Environment=LUMINOPHORE_DIRECT_LAUNCH_TOKEN=12345678-1234-1234-1234-123456789abc", argv)
        self.assertNotIn("LUMINOPHORE_DIRECT_LAUNCH_TOKEN", kwargs['env'])

    def test_missing_or_invalid_origin_still_launches_without_override(self):
        for token in ["", "not-a-token", "x\nEnvironment=bad"]:
            runner = RecordingRunner()
            launcher = self.launcher(runner)
            launcher.environment["LUMINOPHORE_COMPOSITOR"] = "1"
            launcher._direct_launch_token = lambda: token
            self.assertTrue(launcher.launch_argv(["app"]))
            self.assertNotIn("-p", runner.calls[0][0])

    def test_launch_cli_preserves_argument_boundaries(self):
        from unittest.mock import patch
        from luminophore_shell.__main__ import main
        with patch('luminophore_shell.external_launch.UwsmApplicationLauncher.launch_argv', return_value=True) as launch:
            self.assertEqual(main(['launch', '--', 'terminal', '-e', 'a b']), 0)
        launch.assert_called_once_with(['terminal', '-e', 'a b'])


class AsyncLaunchControllerTests(unittest.TestCase):
    def test_slow_launch_returns_immediately_and_completes_on_dispatch_thread(self):
        from luminophore_shell.external_launch import AsyncLaunchController
        callbacks = queue.Queue()
        controller = AsyncLaunchController(callbacks.put)
        entered, release = threading.Event(), threading.Event()
        completed = []
        caller = threading.get_ident()
        def operation():
            self.assertNotEqual(threading.get_ident(), caller)
            entered.set()
            release.wait(2)
            return True
        try:
            self.assertTrue(controller.submit(operation, lambda ok: completed.append((ok, threading.get_ident()))))
            self.assertTrue(entered.wait(1))
            self.assertFalse(controller.submit(lambda: False, completed.append))
            self.assertEqual(completed, [])
            release.set()
            finish = callbacks.get(timeout=2)
            self.assertFalse(controller.submit(lambda: False, completed.append))
            self.assertFalse(finish())
            self.assertEqual(completed, [(True, caller)])
            self.assertTrue(controller.submit(lambda: False, completed.append))
            callbacks.get(timeout=2)()
            self.assertEqual(completed[-1], False)
        finally:
            release.set()

    def test_failure_is_delivered_once_and_next_request_can_run(self):
        from luminophore_shell.external_launch import AsyncLaunchController
        callbacks = queue.Queue()
        controller = AsyncLaunchController(callbacks.put)
        completed = []
        def broken():
            raise OSError("worker failure")
        self.assertTrue(controller.submit(broken, completed.append))
        callbacks.get(timeout=2)()
        self.assertEqual(completed, [False])
        self.assertTrue(controller.submit(lambda: True, completed.append))
        callbacks.get(timeout=2)()
        self.assertEqual(completed, [False, True])

    def test_external_process_timeout_is_bounded_and_delivered_as_failure(self):
        from luminophore_shell.external_launch import AsyncLaunchController
        callbacks, completed, timeouts = queue.Queue(), [], []
        def slow_runner(argv, **kwargs):
            timeouts.append(kwargs['timeout'])
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        launcher = UwsmApplicationLauncher(Path('/bin/true'), runner=slow_runner, environment={})
        controller = AsyncLaunchController(callbacks.put)
        self.assertTrue(controller.submit(lambda: launcher.launch_argv(['app']), completed.append))
        callbacks.get(timeout=2)()
        self.assertEqual(timeouts, [5.0])
        self.assertEqual(completed, [False])

    def test_thread_start_failure_keeps_slot_until_ui_delivery(self):
        from unittest.mock import patch
        from luminophore_shell.external_launch import AsyncLaunchController
        callbacks, completed = queue.Queue(), []
        controller = AsyncLaunchController(callbacks.put)
        with patch('threading.Thread.start', side_effect=RuntimeError('no threads')):
            self.assertTrue(controller.submit(lambda: True, completed.append))
            self.assertFalse(controller.submit(lambda: True, completed.append))
        callbacks.get(timeout=2)()
        self.assertEqual(completed, [False])


if __name__ == "__main__":
    unittest.main()
