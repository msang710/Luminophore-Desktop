from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import fcntl
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Callable, Iterator, Mapping


_GEOMETRY = re.compile(r"^-?\d+,-?\d+ [1-9]\d*x[1-9]\d*$")


class CaptureError(RuntimeError):
    def __init__(self, category: str, message: str, saved_path: Path | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.saved_path = saved_path


@dataclass(frozen=True)
class CaptureResult:
    category: str
    saved_path: Path | None = None


RunCommand = Callable[..., subprocess.CompletedProcess]
PopenCommand = Callable[..., subprocess.Popen]
WhichCommand = Callable[[str], str | None]


class CaptureBackend:
    """Owns LUMINOPHORE's region-capture transaction without requiring the Shell daemon."""

    def __init__(
        self,
        *,
        runner: RunCommand = subprocess.run,
        popen: PopenCommand = subprocess.Popen,
        which: WhichCommand = shutil.which,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = datetime.now,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.runner = runner
        self.popen = popen
        self.which = which
        self.sleep = sleep
        self.now = now
        self.environ = os.environ if environ is None else environ

    def capture_region(self, *, save: bool, freeze: bool) -> CaptureResult:
        tools = self._preflight(save=save, freeze=freeze)
        with self._capture_lock():
            freezer: subprocess.Popen | None = None
            try:
                if freeze:
                    freezer = self._start_freezer(tools["hyprpicker"])
                geometry = self._select_region(tools["slurp"])
                if geometry is None:
                    return CaptureResult("cancelled")
                png = self._render_png(tools["grim"], geometry)
                saved_path = self._save_png(png, tools["xdg-user-dir"]) if save else None
                try:
                    self._copy_png(tools["wl-copy"], png)
                except CaptureError as exc:
                    if saved_path is not None:
                        raise CaptureError(
                            "clipboard_failed_after_save",
                            f"screenshot saved but clipboard copy failed: {saved_path}",
                            saved_path,
                        ) from exc
                    raise
                return CaptureResult("saved" if save else "copied", saved_path)
            finally:
                if not self._stop_freezer(freezer):
                    print("luminophore-shell capture: screen freeze process could not be stopped", file=sys.stderr)

    def _preflight(self, *, save: bool, freeze: bool) -> dict[str, str]:
        required = ["slurp", "grim", "wl-copy"]
        if freeze:
            required.append("hyprpicker")
        if save:
            required.append("xdg-user-dir")
        resolved: dict[str, str] = {}
        for name in required:
            executable = self.which(name)
            if not executable:
                raise CaptureError("dependency_missing", f"required capture tool is unavailable: {name}")
            resolved[name] = executable
        return resolved

    @contextmanager
    def _capture_lock(self) -> Iterator[None]:
        runtime = self.environ.get("XDG_RUNTIME_DIR")
        if not runtime:
            raise CaptureError("runtime_unavailable", "XDG_RUNTIME_DIR is unavailable")
        lock_path = Path(runtime) / "luminophore-shell-capture.lock"
        try:
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        except OSError as exc:
            raise CaptureError("runtime_unavailable", "capture lock could not be created") from exc
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CaptureError("busy", "another capture transaction is active") from exc
            yield
        finally:
            os.close(descriptor)

    def _start_freezer(self, executable: str) -> subprocess.Popen:
        try:
            process = self.popen(
                [executable, "--render-inactive", "--no-zoom", "--quiet"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise CaptureError("freeze_failed", "screen freeze could not be started") from exc
        self.sleep(0.2)
        if process.poll() is not None:
            raise CaptureError("freeze_failed", "screen freeze stopped before selection")
        return process

    def _select_region(self, executable: str) -> str | None:
        try:
            result = self.runner(
                [executable, "-d"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise CaptureError("selection_failed", "region selector could not be started") from exc
        geometry = result.stdout.strip()
        if result.returncode != 0 and result.stderr.strip():
            raise CaptureError("selection_failed", "region selector failed")
        if result.returncode != 0 or not geometry:
            return None
        if not _GEOMETRY.fullmatch(geometry):
            raise CaptureError("selection_invalid", "region selector returned invalid geometry")
        return geometry

    def _render_png(self, executable: str, geometry: str) -> bytes:
        try:
            result = self.runner(
                [executable, "-g", geometry, "-t", "png", "-"],
                check=False,
                capture_output=True,
            )
        except OSError as exc:
            raise CaptureError("capture_failed", "selected region could not be captured") from exc
        if result.returncode != 0 or not result.stdout:
            raise CaptureError("capture_failed", "selected region could not be captured")
        return bytes(result.stdout)

    def _pictures_directory(self, executable: str) -> Path:
        try:
            result = self.runner(
                [executable, "PICTURES"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise CaptureError("pictures_unavailable", "Pictures directory could not be resolved") from exc
        value = result.stdout.strip()
        if result.returncode != 0 or not value or not Path(value).is_absolute():
            raise CaptureError("pictures_unavailable", "Pictures directory could not be resolved")
        return Path(value)

    def _save_png(self, png: bytes, xdg_user_dir: str) -> Path:
        directory = self._pictures_directory(xdg_user_dir)
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CaptureError("save_failed", "Pictures directory could not be created") from exc
        stamp = self.now().strftime("%Y-%m-%d-%H%M%S")
        descriptor, temporary = tempfile.mkstemp(prefix=".luminophore-capture-", dir=directory)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(png)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_path, 0o600)
            for suffix in ("", *(f"-{index}" for index in range(1, 1000))):
                target = directory / f"{stamp}_hyprshot{suffix}.png"
                try:
                    os.link(temporary_path, target)
                except FileExistsError:
                    continue
                temporary_path.unlink()
                return target
            raise CaptureError("save_failed", "no collision-free screenshot filename is available")
        except CaptureError:
            raise
        except OSError as exc:
            raise CaptureError("save_failed", "screenshot could not be saved") from exc
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    def _copy_png(self, executable: str, png: bytes) -> None:
        try:
            result = self.runner(
                [executable, "--type", "image/png"],
                input=png,
                check=False,
                # wl-copy forks a background clipboard owner by default.  A
                # captured stdout/stderr pipe is inherited by that child, so
                # subprocess.run() waits forever for EOF and keeps the global
                # capture lock held.  Do not create pipes for this command.
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise CaptureError("clipboard_failed", "screenshot could not be copied to the clipboard") from exc
        if result.returncode != 0:
            raise CaptureError("clipboard_failed", "screenshot could not be copied to the clipboard")

    @staticmethod
    def _stop_freezer(process: subprocess.Popen | None) -> bool:
        if process is None or process.poll() is not None:
            return True
        try:
            process.terminate()
            process.wait(timeout=1.0)
            return True
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
                process.wait(timeout=1.0)
                return True
            except (OSError, subprocess.TimeoutExpired):
                return False


def run_capture(*, save: bool, freeze: bool, backend: CaptureBackend | None = None) -> int:
    try:
        if backend is None:
            from .live_capture import LiveCaptureBackend
            candidate = LiveCaptureBackend()
            backend = candidate if candidate.pip.supported() else CaptureBackend()
        result = backend.capture_region(save=save, freeze=freeze)
    except CaptureError as exc:
        print(f"luminophore-shell capture: {exc}", file=sys.stderr)
        return 1
    if result.category == "cancelled":
        return 0
    return 0


__all__ = ["CaptureBackend", "CaptureError", "CaptureResult", "run_capture"]
