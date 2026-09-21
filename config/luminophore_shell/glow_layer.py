from __future__ import annotations

from dataclasses import dataclass, field
import math
import queue
from pathlib import Path
import subprocess
import threading
from typing import Callable, Mapping, Protocol


class GlowSurface(Protocol):
    monitor: object
    glow: object

    def global_bounds(self) -> tuple[int, int, int, int]: ...

    def external_glow_active(self) -> bool: ...

    def external_glow_layer(self) -> str: ...

    def external_glow_revision(self) -> int: ...

    def external_glow_reveal(self) -> tuple[float, float, float, float, float, float]: ...


@dataclass(frozen=True)
class GlowLayerStatus:
    running: bool
    pid: int
    rect_count: int
    error: str
    spectrum: dict[str, object]
    accepted_revision: int = 0
    rendered_revision: int = 0
    queued_payloads: int = 0
    superseded_payloads: int = 0
    geometry_revisions: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class SpectrumOutput:
    connector: str
    global_x: int
    width: int
    desktop_left: int
    desktop_width: int
    left_color: tuple[float, float, float]
    right_color: tuple[float, float, float]


class GlowLayerController:
    """Own the direct Wayland/EGL renderer and its bounded text protocol."""

    def __init__(
        self,
        executable: Path,
        failed: Callable[[str], None],
        spectrum_demo: bool = False,
        spectrum_enabled: bool = False,
        widget_bloom: bool = False,
        window_demo: bool = False,
    ) -> None:
        self.executable = executable
        self.failed = failed
        self.process: subprocess.Popen[str] | None = None
        self.error = ""
        self.rect_count = 0
        self._last_payload = ""
        self._failure_reported = False
        self._write_queue: queue.Queue[str | None] = queue.Queue(maxsize=1)
        self._writer: threading.Thread | None = None
        self._writer_error = ""
        self.spectrum_outputs: tuple[SpectrumOutput, ...] = ()
        self.spectrum_demo = bool(spectrum_demo)
        self.spectrum_enabled = bool(spectrum_enabled)
        self.widget_bloom = bool(widget_bloom)
        self.window_demo = bool(window_demo)
        self.spectrum_state = "disabled" if not spectrum_enabled else "starting"
        self.spectrum_sample_rate = 0
        self.spectrum_channels = 0
        self.spectrum_silence = True
        self.accepted_revision = 0
        self.rendered_revision = 0
        self.queued_payloads = 0
        self.superseded_payloads = 0
        self.geometry_revisions: dict[str, dict[str, int]] = {}

    def start(self) -> bool:
        if self.process and self.process.poll() is None:
            return True
        if not self.executable.is_file():
            self.error = "renderer executable is missing"
            return False
        try:
            self.process = subprocess.Popen(
                [
                    str(self.executable),
                    *(("--spectrum-demo",) if self.spectrum_demo else ()),
                    *(("--spectrum-live",) if self.spectrum_enabled else ()),
                    *(("--widget-bloom",) if self.widget_bloom else ()),
                    *(("--window-demo",) if self.window_demo else ()),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self.error = str(exc)
            return False
        self.error = ""
        self._writer_error = ""
        self._failure_reported = False
        self._write_queue = queue.Queue(maxsize=1)
        threading.Thread(target=self._read_status, name="luminophore-spectrum-status", daemon=True).start()
        self._writer = threading.Thread(
            target=self._write_payloads,
            name="luminophore-glow-writer",
            daemon=True,
        )
        self._writer.start()
        return True

    def _write_payloads(self) -> None:
        process = self.process
        if process is None or process.stdin is None:
            return
        while True:
            payload = self._write_queue.get()
            if payload is None:
                return
            try:
                process.stdin.write(payload)
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._writer_error = str(exc)
                return

    def _queue_latest(self, payload: str) -> None:
        try:
            self._write_queue.put_nowait(payload)
            self.queued_payloads += 1
            return
        except queue.Full:
            pass
        try:
            self._write_queue.get_nowait()
            self.superseded_payloads += 1
        except queue.Empty:
            pass
        try:
            self._write_queue.put_nowait(payload)
            self.queued_payloads += 1
        except queue.Full:
            # The writer won the race and already took a newer payload.
            pass

    def _read_status(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for raw in process.stdout:
            fields = raw.strip().split()
            if len(fields) == 4 and fields[0] == "A":
                self.spectrum_state = fields[1]
                try:
                    self.spectrum_sample_rate = int(fields[2])
                    self.spectrum_channels = int(fields[3])
                except ValueError:
                    continue
            elif len(fields) == 2 and fields[0] == "Q":
                self.spectrum_silence = fields[1] == "1"
            elif len(fields) == 4 and fields[0] == "G":
                try:
                    accepted = int(fields[2])
                    rendered = int(fields[3])
                except ValueError:
                    continue
                revisions = self.geometry_revisions.setdefault(
                    fields[1], {"accepted": 0, "rendered": 0}
                )
                revisions["accepted"] = max(revisions["accepted"], accepted)
                revisions["rendered"] = max(revisions["rendered"], rendered)
                self.accepted_revision = max(self.accepted_revision, accepted)
                self.rendered_revision = max(self.rendered_revision, rendered)

    @staticmethod
    def _finite(value: float) -> float:
        return min(100000.0, max(0.0, value if math.isfinite(value) else 100000.0))

    @staticmethod
    def _finite_signed(value: float) -> float:
        return min(1_000_000_000.0, max(-1_000_000_000.0, value if math.isfinite(value) else 0.0))

    @classmethod
    def payload(
        cls,
        surfaces: Mapping[str, GlowSurface],
        spectrum_outputs: tuple[SpectrumOutput, ...] = (),
    ) -> tuple[str, int]:
        lines = ["V 5", "B"]
        for output in spectrum_outputs:
            if (
                not output.connector
                or any(character.isspace() for character in output.connector)
                or output.width <= 0
                or output.desktop_width <= 0
            ):
                continue
            values = (
                output.connector, output.global_x, output.width,
                output.desktop_left, output.desktop_width,
                *output.left_color, *output.right_color,
            )
            lines.append("M " + " ".join(
                str(value) if isinstance(value, (str, int)) else f"{value:.6f}"
                for value in values
            ))
        count = 0
        for name, surface in sorted(surfaces.items()):
            active = getattr(surface, "external_glow_active", None)
            if callable(active) and not active():
                continue
            monitor = surface.monitor
            connector = monitor.get_connector()
            if not connector or any(character.isspace() for character in connector):
                continue
            monitor_geometry = monitor.get_geometry()
            global_x, global_y, width, height = surface.global_bounds()
            if width <= 0 or height <= 0:
                continue
            frame = surface.glow.external_glow_frame(width, height)
            local_x = global_x - monitor_geometry.x
            local_y = global_y - monitor_geometry.y
            edge = tuple(cls._finite(value) for value in frame.edge_budget)
            layer_provider = getattr(surface, "external_glow_layer", None)
            layer = layer_provider() if callable(layer_provider) else "bottom"
            if layer not in {"bottom", "top"}:
                continue
            revision_provider = getattr(surface, "external_glow_revision", None)
            revision = max(0, int(revision_provider())) if callable(revision_provider) else 0
            reveal_provider = getattr(surface, "external_glow_reveal", None)
            reveal = (
                tuple(cls._finite_signed(value) for value in reveal_provider())
                if callable(reveal_provider) else (1.0, 1.0, 0.0, 0.0, 0.0, 0.0)
            )
            if len(reveal) != 6:
                reveal = (1.0, 1.0, 0.0, 0.0, 0.0, 0.0)
            values = (
                connector, name, layer, revision, local_x, local_y, width, height,
                frame.corner_radius, frame.outline_width, frame.visible_extent,
                frame.core_energy / 0.16 if frame.core_energy > 0 else 0.0,
                frame.phase, *reveal,
                *frame.base_color, *frame.core_color, *edge,
            )
            lines.append("R " + " ".join(
                str(value) if isinstance(value, (str, int)) else f"{value:.6f}"
                for value in values
            ))
            count += 1
        lines.append("C")
        return "\n".join(lines) + "\n", count

    def sync(self, surfaces: Mapping[str, GlowSurface]) -> bool:
        process = self.process
        if process is None or process.poll() is not None or process.stdin is None:
            self.error = "renderer process exited"
            if not self._failure_reported:
                self._failure_reported = True
                self.failed(self.error)
            return False
        if self._writer_error:
            self.error = self._writer_error
            if not self._failure_reported:
                self._failure_reported = True
                self.failed(self.error)
            return False
        payload, count = self.payload(surfaces, self.spectrum_outputs)
        self.rect_count = count
        if payload == self._last_payload:
            return True
        self._last_payload = payload
        if self._writer is None:
            # Kept for deterministic unit fakes; a started runtime controller
            # always owns the asynchronous writer above.
            process.stdin.write(payload)
            process.stdin.flush()
        else:
            self._queue_latest(payload)
        return True

    def stop(self) -> None:
        process = self.process
        self.process = None
        if not process:
            return
        # Terminate first: closing a TextIO pipe while its writer is blocked can
        # wait on the same lock and freeze Shell shutdown.
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        try:
            self._write_queue.put_nowait(None)
        except queue.Full:
            try:
                self._write_queue.get_nowait()
            except queue.Empty:
                pass
            self._write_queue.put_nowait(None)
        if process.stdin:
            process.stdin.close()
        if self._writer is not None:
            self._writer.join(timeout=1)
            self._writer = None
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def status(self) -> GlowLayerStatus:
        process = self.process
        running = bool(process and process.poll() is None)
        return GlowLayerStatus(
            running, process.pid if running and process else 0, self.rect_count, self.error,
            {
                "enabled": self.spectrum_enabled,
                "demo": self.spectrum_demo,
                "state": self.spectrum_state,
                "sample_rate": self.spectrum_sample_rate,
                "channels": self.spectrum_channels,
                "silence": self.spectrum_silence,
            },
            self.accepted_revision,
            self.rendered_revision,
            self.queued_payloads,
            self.superseded_payloads,
            {name: dict(values) for name, values in self.geometry_revisions.items()},
        )
