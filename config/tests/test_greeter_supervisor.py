from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from luminophore_shell.greeter_supervisor import supervise, _wait_for_ui


class FakeProcess:
    def __init__(self, role, events):
        self.role = role
        self.events = events
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.events.append(("terminate", self.role))
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        self.events.append(("wait", self.role))
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class GreeterSupervisorTests(unittest.TestCase):
    def release(self, root):
        release = root / ("a" * 64)
        for relative in ("python/bin/python3", "shell/luminophore-shell"):
            path = release / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x")
        return release

    def test_supervisor_starts_compositor_then_shell_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            release = self.release(Path(directory))
            events = []
            def popen(argv, **kwargs):
                role = "compositor" if "greeter_compositor" in argv else "greeter"
                events.append(("start", role, tuple(argv), kwargs))
                return FakeProcess(role, events)
            with patch("luminophore_shell.greeter_supervisor._runtime_directory", return_value=Path(directory)), \
                 patch("luminophore_shell.greeter_supervisor._log_directory", return_value=Path(directory)), \
                 patch("luminophore_shell.greeter_supervisor.signal.signal", return_value=None):
                result = supervise("/theme", "/config", release_root=str(release),
                                   popen=popen, socket_waiter=lambda *_: "wayland-9",
                                   ready_waiter=lambda *_: None)
            self.assertEqual(result.greeter_returncode, 0)
            self.assertEqual([event[1] for event in events if event[0] == "start"],
                             ["compositor", "greeter"])
            self.assertIn("greeter_compositor", events[0][2])
            self.assertEqual(events[1][3]["env"]["WAYLAND_DISPLAY"], "wayland-9")
            self.assertNotIn("HYPRLAND_INSTANCE_SIGNATURE", events[1][3]["env"])
            self.assertEqual(events[1][3]["pass_fds"],
                             (int(events[1][3]["env"]["LUMINOPHORE_GREETER_READY_FD"]),))
            self.assertIn(("terminate", "compositor"), events)

    def test_missing_private_shell_fails_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            release = self.release(Path(directory))
            (release / "shell/luminophore-shell").unlink()
            with self.assertRaises(OSError):
                supervise("/theme", "/config", release_root=str(release))

    def test_missing_ready_signal_fails_instead_of_leaving_blank_screen(self):
        greeter = FakeProcess("greeter", [])
        compositor = FakeProcess("compositor", [])
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        try:
            with self.assertRaisesRegex(OSError, "did not report"):
                _wait_for_ui(greeter, compositor, read_fd, timeout=0.1)
        finally:
            os.close(read_fd)


if __name__ == "__main__":
    unittest.main()
