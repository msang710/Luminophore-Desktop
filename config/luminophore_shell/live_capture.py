"""Capture and Live PiP share one selection gesture and one capture lock."""
import json
import os
from pathlib import Path
import subprocess
import sys
from .bootstrap import internal_python_environment
from .capture import CaptureBackend, CaptureError, CaptureResult
from .config import load_config
from .hyprland import HyprlandError
from .live_pip import LivePipClient, LivePipError, initial_placement

class LiveCaptureBackend(CaptureBackend):
    def __init__(self, pip=None, **kwargs):
        super().__init__(**kwargs)
        self.pip = pip or LivePipClient()

    def _preflight(self, *, save, freeze):
        names = ['grim', 'wl-copy'] + (['hyprpicker'] if freeze else []) + (['xdg-user-dir'] if save else [])
        tools = {name: self.which(name) for name in names}
        if any(not path for path in tools.values()):
            raise CaptureError('dependency_missing', 'required capture tool is unavailable')
        return tools

    def select(self):
        try:
            options = {'env': internal_python_environment()} if os.environ.get('LUMINOPHORE_RELEASE_ROOT') else {}
            result = self.runner([sys.executable, '-m', 'luminophore_shell.capture_selector'], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=False, timeout=155, **options)
            if result.returncode:
                raise ValueError('selector failed')
            selected = json.loads(result.stdout)
            if selected is None:
                return None
            rect = selected['rect']
            if selected['mode'] not in ('pip', 'screenshot') or len(rect) != 4 or any(type(v) is not int or abs(v) > 1000000 for v in rect) or min(rect[2:]) <= 1:
                raise ValueError('invalid selection')
            return selected
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as exc:
            raise CaptureError('selection_failed', '영역 선택을 완료하지 못했습니다') from exc

    def capture_region(self, *, save, freeze):
        tools = self._preflight(save=save, freeze=freeze)
        with self._capture_lock():
            freezer = None
            try:
                self.pip.begin()
                if freeze:
                    freezer = self._start_freezer(tools['hyprpicker'])
                selected = self.select()
                if selected is None:
                    return CaptureResult('cancelled')
                x, y, width, height = selected['rect']
                if selected['mode'] == 'pip':
                    crop = self.pip.resolve(selected['rect'])
                    if not self._stop_freezer(freezer):
                        raise CaptureError('freeze_failed', '화면 고정을 해제하지 못했습니다')
                    freezer = None
                    placement = initial_placement(self.pip.monitors(), crop, load_config().layout.edge_margin)
                    self.pip.create(placement)
                    return CaptureResult('pip_created')
                png = self._render_png(tools['grim'], f'{x},{y} {width}x{height}')
                saved_path = self._save_png(png, tools['xdg-user-dir']) if save else None
                try:
                    self._copy_png(tools['wl-copy'], png)
                except CaptureError as exc:
                    if saved_path is not None:
                        raise CaptureError('clipboard_failed_after_save', f'screenshot saved but clipboard copy failed: {saved_path}', saved_path) from exc
                    raise
                return CaptureResult('saved' if save else 'copied', saved_path)
            except (LivePipError, HyprlandError) as exc:
                raise CaptureError('pip_failed', str(exc)) from exc
            finally:
                if not self._stop_freezer(freezer):
                    print('luminophore-shell capture: screen freeze process could not be stopped', file=sys.stderr)
                self.pip.cancel()
