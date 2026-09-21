from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from .glow import CORE_ENERGY_FACTOR, GlowRenderFrame
from .glow_layer import GlowLayerController
from .hyprland import HyprlandClient, HyprlandError, HyprlandEventListener, WindowRecord


DEMO_CLASS = "luminophore-window-glow-demo"
DEMO_TITLE = "LUMINOPHORE Window Glow Demo"


@dataclass(frozen=True)
class WindowGlowProfile:
    corner_radius: float = 12.0
    outline_width: float = 2.0
    extent: float = 42.0
    intensity: float = 0.72
    color: tuple[float, float, float] = (0.38, 0.76, 1.0)

    def frame(self, width: int, height: int) -> GlowRenderFrame:
        return GlowRenderFrame(
            float(width),
            float(height),
            self.corner_radius,
            self.outline_width,
            self.extent,
            self.intensity * CORE_ENERGY_FACTOR,
            0.0,
            0.0,
            0.0,
            0.0,
            self.color,
            self.color,
            (math.inf,) * 4,
        )


def demo_window(windows: list[WindowRecord], pid: int) -> WindowRecord | None:
    return next(
        (
            window
            for window in windows
            if window.pid == pid
            or window.app_class.casefold() == DEMO_CLASS
            or window.initial_class.casefold() == DEMO_CLASS
            or window.title == DEMO_TITLE
        ),
        None,
    )


class _MonitorAdapter:
    def __init__(self, monitor: object, connector: str, x: int, y: int) -> None:
        self._monitor = monitor
        self._connector = connector
        self._geometry = SimpleNamespace(x=x, y=y)

    def get_connector(self) -> str:
        return self._connector

    def get_geometry(self) -> object:
        return self._geometry


class _WindowGlow:
    name = "window-glow-demo"

    def __init__(self, monitor: _MonitorAdapter, window: WindowRecord, profile: WindowGlowProfile) -> None:
        self.monitor = monitor
        self.window = window
        self.glow = self
        self.profile = profile
        self.revision = 1

    def update(self, monitor: _MonitorAdapter, window: WindowRecord) -> None:
        if (self.window.at, self.window.size, self.window.monitor_name) != (
            window.at,
            window.size,
            window.monitor_name,
        ):
            self.revision += 1
        self.monitor = monitor
        self.window = window

    def global_bounds(self) -> tuple[int, int, int, int]:
        return (*self.window.at, *self.window.size)

    def external_glow_frame(self, width: int, height: int) -> GlowRenderFrame:
        return self.profile.frame(width, height)

    def external_glow_active(self) -> bool:
        return self.window.mapped and not self.window.hidden and not self.window.minimized

    def external_glow_layer(self) -> str:
        return "top"

    def external_glow_revision(self) -> int:
        return self.revision

    def external_glow_reveal(self) -> tuple[float, float, float, float, float, float]:
        return (1.0, 1.0, 0.0, 0.0, 0.0, 0.0)


def _gdk_monitors() -> dict[str, object]:
    display = Gdk.Display.get_default()
    if display is None:
        return {}
    model = display.get_monitors()
    return {
        connector: monitor
        for index in range(model.get_n_items())
        if (monitor := model.get_item(index)) is not None
        if (connector := monitor.get_connector())
    }


def _launch_blank_window() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-m", "luminophore_shell.window_glow_demo", "--blank-child"]
    )


def run_blank_window() -> int:
    application = Gtk.Application(application_id="io.github.msang710.LuminophoreWindowGlowDemo")

    def activate(app: Gtk.Application) -> None:
        window = Gtk.ApplicationWindow(application=app, title=DEMO_TITLE)
        window.set_default_size(720, 480)
        window.add_css_class("window-glow-demo")
        child = Gtk.Box()
        child.set_hexpand(True)
        child.set_vexpand(True)
        window.set_child(child)
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"window.window-glow-demo { background: rgba(12, 16, 24, 0.96); }"
        )
        Gtk.StyleContext.add_provider_for_display(
            window.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        window.present()

    application.connect("activate", activate)
    return application.run([])


def run_demo() -> int:
    executable = Path(__file__).with_name("luminophore-glow-layer")
    controller = GlowLayerController(
        executable,
        lambda reason: print(f"renderer: {reason}"),
        widget_bloom=True,
        window_demo=True,
    )
    if not controller.start():
        raise RuntimeError(controller.error)
    demo_process = _launch_blank_window()
    hyprland = HyprlandClient()
    changed = threading.Event()
    listener = HyprlandEventListener(lambda _name, _data: changed.set())
    listener.start()
    surface: _WindowGlow | None = None
    try:
        while demo_process.poll() is None:
            changed.wait(0.25)
            changed.clear()
            try:
                monitors = hyprland.monitors()
                window = demo_window(hyprland.windows(monitors), demo_process.pid)
            except HyprlandError:
                continue
            if window is None:
                controller.sync({})
                continue
            monitor_record = next((item for item in monitors if item.name == window.monitor_name), None)
            gdk_monitor = _gdk_monitors().get(window.monitor_name)
            if monitor_record is None or gdk_monitor is None:
                controller.sync({})
                continue
            adapter = _MonitorAdapter(gdk_monitor, monitor_record.name, monitor_record.x, monitor_record.y)
            if surface is None:
                surface = _WindowGlow(adapter, window, WindowGlowProfile())
            else:
                surface.update(adapter, window)
            controller.sync({surface.name: surface})
        return 0
    finally:
        listener.stop()
        controller.stop()
        if demo_process.poll() is None:
            demo_process.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(description="Show isolated LUMINOPHORE bloom around a disposable blank window")
    parser.add_argument("--blank-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.blank_child:
        return run_blank_window()
    return run_demo()


if __name__ == "__main__":
    raise SystemExit(main())
