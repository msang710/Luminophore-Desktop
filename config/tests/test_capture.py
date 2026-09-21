from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from luminophore_shell.capture import CaptureBackend, CaptureError, run_capture


PNG = b"\x89PNG\r\n\x1a\nfixture"


class FakeProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.timeout_on_terminate = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if not self.timeout_on_terminate:
            self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        if self.terminated and self.timeout_on_terminate and not self.killed:
            raise subprocess.TimeoutExpired("hyprpicker", timeout)
        return self.returncode


class FakeCommands:
    def __init__(self, pictures: Path) -> None:
        self.pictures = pictures
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.selection = subprocess.CompletedProcess([], 0, "10,20 300x200\n", "")
        self.grim = subprocess.CompletedProcess([], 0, PNG, b"")
        self.clipboard = subprocess.CompletedProcess([], 0, b"", b"")
        self.freezer = FakeProcess()

    @staticmethod
    def which(name: str) -> str:
        return f"/usr/bin/{name}"

    def run(self, argv, **kwargs):
        command = Path(argv[0]).name
        self.calls.append((list(argv), dict(kwargs)))
        if command == "slurp":
            return self.selection
        if command == "grim":
            return self.grim
        if command == "wl-copy":
            return self.clipboard
        if command == "xdg-user-dir":
            return subprocess.CompletedProcess(argv, 0, f"{self.pictures}\n", "")
        raise AssertionError(f"unexpected command: {argv}")

    def popen(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        return self.freezer


class CaptureBackendTests(unittest.TestCase):
    def backend(self, commands: FakeCommands, runtime: Path) -> CaptureBackend:
        return CaptureBackend(
            runner=commands.run,
            popen=commands.popen,
            which=commands.which,
            sleep=lambda _delay: None,
            now=lambda: datetime(2026, 9, 4, 12, 34, 56),
            environ={"XDG_RUNTIME_DIR": str(runtime)},
        )

    def test_clipboard_capture_uses_one_png_and_stops_owned_freezer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = FakeCommands(root / "Pictures")
            result = self.backend(commands, root).capture_region(save=False, freeze=True)
        self.assertEqual(result.category, "copied")
        self.assertTrue(commands.freezer.terminated)
        self.assertFalse(commands.freezer.killed)
        self.assertEqual(commands.calls[0][0], ["/usr/bin/hyprpicker", "--render-inactive", "--no-zoom", "--quiet"])
        self.assertEqual(commands.calls[1][0], ["/usr/bin/slurp", "-d"])
        self.assertEqual(commands.calls[2][0], ["/usr/bin/grim", "-g", "10,20 300x200", "-t", "png", "-"])
        self.assertEqual(commands.calls[3][0], ["/usr/bin/wl-copy", "--type", "image/png"])
        self.assertEqual(commands.calls[3][1]["input"], PNG)

    def test_cancel_changes_neither_clipboard_nor_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = FakeCommands(root / "Pictures")
            commands.selection = subprocess.CompletedProcess([], 1, "", "")
            result = self.backend(commands, root).capture_region(save=True, freeze=True)
            self.assertFalse((root / "Pictures").exists())
        self.assertEqual(result.category, "cancelled")
        self.assertTrue(commands.freezer.terminated)
        self.assertNotIn("grim", [Path(call[0][0]).name for call in commands.calls])
        self.assertNotIn("wl-copy", [Path(call[0][0]).name for call in commands.calls])

    def test_freezer_is_killed_if_graceful_stop_times_out(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = FakeCommands(root / "Pictures")
            commands.freezer.timeout_on_terminate = True
            result = self.backend(commands, root).capture_region(save=False, freeze=True)
        self.assertEqual(result.category, "copied")
        self.assertTrue(commands.freezer.terminated)
        self.assertTrue(commands.freezer.killed)

    def test_save_and_clipboard_use_identical_png_and_collision_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pictures = root / "Pictures"
            pictures.mkdir()
            first = pictures / "2026-09-04-123456_hyprshot.png"
            first.write_bytes(b"existing")
            commands = FakeCommands(pictures)
            result = self.backend(commands, root).capture_region(save=True, freeze=False)
            self.assertEqual(result.saved_path, pictures / "2026-09-04-123456_hyprshot-1.png")
            self.assertEqual(result.saved_path.read_bytes(), PNG)
            self.assertEqual(first.read_bytes(), b"existing")
        clipboard = next(kwargs for argv, kwargs in commands.calls if Path(argv[0]).name == "wl-copy")
        self.assertEqual(clipboard["input"], PNG)
        self.assertEqual(clipboard["stdout"], subprocess.DEVNULL)
        self.assertEqual(clipboard["stderr"], subprocess.DEVNULL)
        self.assertNotIn("capture_output", clipboard)

    def test_invalid_geometry_fails_before_grim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = FakeCommands(root / "Pictures")
            commands.selection = subprocess.CompletedProcess([], 0, "not geometry\n", "")
            with self.assertRaisesRegex(CaptureError, "invalid geometry") as raised:
                self.backend(commands, root).capture_region(save=False, freeze=False)
        self.assertEqual(raised.exception.category, "selection_invalid")
        self.assertNotIn("grim", [Path(call[0][0]).name for call in commands.calls])

    def test_selector_error_is_not_reported_as_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = FakeCommands(root / "Pictures")
            commands.selection = subprocess.CompletedProcess([], 1, "", "protocol failed")
            with self.assertRaises(CaptureError) as raised:
                self.backend(commands, root).capture_region(save=False, freeze=False)
        self.assertEqual(raised.exception.category, "selection_failed")

    def test_missing_dependency_fails_before_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = FakeCommands(root / "Pictures")
            backend = self.backend(commands, root)
            backend.which = lambda name: None if name == "grim" else f"/usr/bin/{name}"
            with self.assertRaises(CaptureError) as raised:
                backend.capture_region(save=False, freeze=True)
        self.assertEqual(raised.exception.category, "dependency_missing")
        self.assertEqual(commands.calls, [])

    def test_clipboard_failure_after_save_preserves_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = FakeCommands(root / "Pictures")
            commands.clipboard = subprocess.CompletedProcess([], 1, b"", b"failed")
            with self.assertRaises(CaptureError) as raised:
                self.backend(commands, root).capture_region(save=True, freeze=False)
            self.assertEqual(raised.exception.category, "clipboard_failed_after_save")
            self.assertIsNotNone(raised.exception.saved_path)
            self.assertEqual(raised.exception.saved_path.read_bytes(), PNG)

    def test_concurrent_capture_fails_busy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = FakeCommands(root / "Pictures")
            first = self.backend(commands, root)
            second = self.backend(commands, root)
            with first._capture_lock():
                with self.assertRaises(CaptureError) as raised:
                    second.capture_region(save=False, freeze=False)
        self.assertEqual(raised.exception.category, "busy")

    def test_run_capture_maps_cancel_to_success_and_failure_to_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = FakeCommands(root / "Pictures")
            commands.selection = subprocess.CompletedProcess([], 1, "", "")
            self.assertEqual(run_capture(save=False, freeze=False, backend=self.backend(commands, root)), 0)
            failing = self.backend(FakeCommands(root / "Pictures"), root)
            failing.which = lambda _name: None
            self.assertEqual(run_capture(save=False, freeze=False, backend=failing), 1)


class CaptureCliTests(unittest.TestCase):
    def test_capture_cli_routes_without_ipc(self) -> None:
        from luminophore_shell.__main__ import main

        with patch("luminophore_shell.capture.run_capture", return_value=0) as capture:
            self.assertEqual(main(["capture", "region", "--clipboard-only", "--freeze"]), 0)
        capture.assert_called_once_with(save=False, freeze=True)


if __name__ == "__main__":
    unittest.main()
