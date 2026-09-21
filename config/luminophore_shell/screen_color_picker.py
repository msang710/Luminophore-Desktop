from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import shutil
import subprocess
import threading
from typing import Callable


LOG = logging.getLogger("luminophore-shell")
_HEX_COLOR = re.compile(r"(?<![0-9A-Fa-f])#([0-9A-Fa-f]{6})(?![0-9A-Fa-f])")
_PLAIN_HEX_COLOR = re.compile(r"^\s*([0-9A-Fa-f]{6})\s*$")


@dataclass(frozen=True)
class ScreenColorPickResult:
    ok: bool
    color: str = ""
    message: str = ""
    error_category: str = ""


def parse_picked_color(output: str) -> str | None:
    match = _HEX_COLOR.search(output)
    if match:
        return f"#{match.group(1).upper()}"
    plain = _PLAIN_HEX_COLOR.match(output)
    return f"#{plain.group(1).upper()}" if plain else None


def hyprpicker_command(executable: str) -> list[str]:
    return [
        executable,
        "--format=hex",
        "--no-fancy",
        "--render-inactive",
    ]


class ScreenColorPicker:
    """Runs one Hyprland screen picker without blocking GTK's main loop."""

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable if executable is not None else shutil.which("hyprpicker")
        self._lock = threading.Lock()
        self._busy = False
        self._stopping = False
        self._process: subprocess.Popen[str] | None = None
        self._worker: threading.Thread | None = None

    @property
    def available(self) -> bool:
        return bool(self.executable)

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def pick(self, completed: Callable[[ScreenColorPickResult], None]) -> bool:
        with self._lock:
            if not self.executable or self._busy or self._stopping:
                return False
            self._busy = True
            worker = threading.Thread(
                target=self._pick_worker,
                args=(completed,),
                name="luminophore-screen-color-picker",
                daemon=True,
            )
            self._worker = worker
        worker.start()
        return True

    def _pick_worker(self, completed: Callable[[ScreenColorPickResult], None]) -> None:
        result: ScreenColorPickResult
        process: subprocess.Popen[str] | None = None
        try:
            assert self.executable is not None
            process = subprocess.Popen(
                hyprpicker_command(self.executable),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            with self._lock:
                self._process = process
                stopping = self._stopping
            if stopping:
                process.terminate()
            stdout, stderr = process.communicate()
            color = parse_picked_color(stdout)
            if process.returncode == 0 and color:
                result = ScreenColorPickResult(True, color, "화면에서 색상을 가져왔습니다")
            elif process.returncode != 0 and not stderr.strip():
                result = ScreenColorPickResult(False, message="색상 선택을 취소했습니다", error_category="cancelled")
            elif process.returncode != 0:
                result = ScreenColorPickResult(
                    False,
                    message="화면 색상 선택기를 실행하지 못했습니다",
                    error_category="picker_failed",
                )
            else:
                LOG.warning(
                    "screen color picker returned no readable color: stdout_bytes=%d stderr_bytes=%d",
                    len(stdout.encode("utf-8")),
                    len(stderr.encode("utf-8")),
                )
                result = ScreenColorPickResult(
                    False,
                    message="선택한 색상 값을 읽지 못했습니다",
                    error_category="invalid_output",
                )
        except (OSError, subprocess.SubprocessError):
            result = ScreenColorPickResult(
                False,
                message="화면 색상 선택기를 실행하지 못했습니다",
                error_category="picker_failed",
            )
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
                self._busy = False
                stopping = self._stopping
        if not stopping:
            completed(result)

    def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
            process = self._process
            worker = self._worker
        if process and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        if worker and worker.is_alive():
            worker.join(timeout=1.0)
        if process and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
            if worker and worker.is_alive():
                worker.join(timeout=1.0)
