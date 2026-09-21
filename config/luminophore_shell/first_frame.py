from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Callable, Mapping

from .compositor_runtime import CompositorRuntimeError, compositor_instance

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402


def runtime_signal_path(uid: int | None = None) -> Path:
    owner = os.getuid() if uid is None else uid
    return Path(f"/run/user/{owner}/luminophore-shell/first-frame-ready.json")


class FirstFrameSignalWriter:
    def __init__(
        self,
        path: Path | None = None,
        boot_id_path: Path = Path("/proc/sys/kernel/random/boot_id"),
        process_id: int | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.path = path or runtime_signal_path()
        self.boot_id_path = boot_id_path
        self.process_id = os.getpid() if process_id is None else process_id
        self.monotonic_ns = monotonic_ns
        self.environment = os.environ if environment is None else environment

    def payload(self) -> dict[str, object]:
        try:
            namespace, _ = compositor_instance(self.environment)
        except CompositorRuntimeError as error:
            raise RuntimeError("Hyprland compositor identity is unavailable") from error
        if not self.environment.get("WAYLAND_DISPLAY"):
            raise RuntimeError("Wayland display identity is unavailable")
        return {
            "boot_id": self.boot_id_path.read_text().strip(),
            "kernel_release": os.uname().release,
            "compositor": "Luminophore" if namespace == "luminophore" else "Hyprland",
            "shell": "luminophore-shell",
            "process_id": self.process_id,
            "signal_monotonic_ns": self.monotonic_ns(),
            "surface_committed": True,
            "frame_callback": True,
        }

    def write(self) -> Path:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".first-frame-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(self.payload(), stream, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            return self.path
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class GtkFirstFrameProbe:
    """Write readiness only after GDK reports an actually presented frame."""

    def __init__(self, writer: FirstFrameSignalWriter | None = None) -> None:
        self.writer = writer or FirstFrameSignalWriter()
        self._widget: Gtk.Widget | None = None
        self._clock: Gdk.FrameClock | None = None
        self._tick_handler = 0
        self._baseline_counter = 0
        self._completed = False

    def arm(self, widget: Gtk.Widget) -> None:
        if self._widget is not None or self._completed:
            return
        self._widget = widget
        clock = widget.get_frame_clock()
        if clock is None:
            widget.connect("realize", self._on_realize)
            return
        self._attach_clock(clock)

    def _on_realize(self, widget: Gtk.Widget) -> None:
        clock = widget.get_frame_clock()
        if clock is not None:
            self._attach_clock(clock)

    def _attach_clock(self, clock: Gdk.FrameClock) -> None:
        if self._clock is not None:
            return
        self._clock = clock
        self._baseline_counter = max(0, clock.get_frame_counter())
        if self._widget is None:
            return
        self._tick_handler = self._widget.add_tick_callback(self._on_later_tick)

    def _on_later_tick(self, _widget: Gtk.Widget, clock: Gdk.FrameClock) -> bool:
        if self._completed:
            return GLib.SOURCE_REMOVE
        current = clock.get_frame_counter()
        for counter in range(self._baseline_counter, current):
            timings = clock.get_timings(counter)
            if timings is not None and timings.get_complete() and timings.get_presentation_time() > 0:
                self.writer.write()
                self._completed = True
                self._tick_handler = 0
                return GLib.SOURCE_REMOVE
        self._baseline_counter = max(self._baseline_counter, current - 8)
        return GLib.SOURCE_CONTINUE
