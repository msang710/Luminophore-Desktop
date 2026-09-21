from __future__ import annotations

import math
import os
import time
import uuid
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk, Gtk4LayerShell  # noqa: E402

from ..config import ShellConfig
from ..glow import GlowEdgeBudget, GlowGeometryInput
from .effects import LuminophoreGlowContainer
from .projection import FrameProjectionHandshake, submit_surface_projection, forget_surface_projection


OSD_KINDS = frozenset({"volume", "microphone", "brightness", "privacy"})
OSD_ICON_SIZE = 18
OSD_BAR_HEIGHT = 8
OSD_VALUE_WIDTH_CHARS = 4
OSD_VALUE_THEME_CLASS = "luminophore-key-secondary"
OSD_BOTTOM_MARGIN = 72
OSD_BLOOM_PADDING = 128


def osd_glow_layout(glow_radius: int, bottom_margin: int = OSD_BOTTOM_MARGIN) -> tuple[int, int, int]:
    """Return side/top padding, bottom padding, and layer margin for an unclipped glow."""
    extent = max(OSD_BLOOM_PADDING, math.ceil(max(1, glow_radius) * 1.35 + 6))
    bottom_padding = min(extent, max(0, bottom_margin))
    return extent, bottom_padding, max(0, bottom_margin - bottom_padding)


def validate_osd(kind: str, value: int) -> tuple[str, int]:
    if kind not in OSD_KINDS:
        raise ValueError(f"unsupported OSD kind: {kind}")
    return kind, max(0, min(100, value))


class OsdGlowViewport(Gtk.Widget):
    """Allocate the OSD panel and publish the exact GTK allocation to native glow."""

    def __init__(
        self,
        child: Gtk.Widget,
        extent: int,
        bottom_padding: int,
        geometry_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.child = child
        self.extent = max(0, extent)
        self.bottom_padding = max(0, bottom_padding)
        self._panel_bounds = (self.extent, self.extent, 1, 1)
        self._revision = 0
        self.geometry_changed = geometry_changed
        child.set_parent(self)

    def do_measure(
        self, orientation: Gtk.Orientation, for_size: int
    ) -> tuple[int, int, int, int]:
        extra = self.extent * 2 if orientation == Gtk.Orientation.HORIZONTAL else self.extent + self.bottom_padding
        child_for_size = max(-1, for_size - extra) if for_size >= 0 else -1
        minimum, natural, minimum_baseline, natural_baseline = self.child.measure(
            orientation, child_for_size
        )
        return minimum + extra, natural + extra, minimum_baseline, natural_baseline

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        child_width = max(1, width - self.extent * 2)
        child_height = max(1, height - self.extent - self.bottom_padding)
        next_bounds = (self.extent, self.extent, child_width, child_height)
        changed = next_bounds != self._panel_bounds
        if changed:
            self._panel_bounds = next_bounds
            self._revision += 1
        point = Graphene.Point()
        point.init(float(self.extent), float(self.extent))
        self.child.allocate(
            child_width, child_height, -1, Gsk.Transform.new().translate(point)
        )
        if changed and self.geometry_changed is not None:
            self.geometry_changed()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        self.snapshot_child(self.child, snapshot)

    def glow_geometry(self) -> GlowGeometryInput:
        x, y, width, height = self._panel_bounds
        return GlowGeometryInput(x, y, width, height, x, y, width, height, self._revision)

    def do_dispose(self) -> None:
        if self.child.get_parent() is self:
            self.child.unparent()
        super().do_dispose()


class OsdLayer:
    def __init__(
        self,
        application: Gtk.Application,
        monitor: Gdk.Monitor,
        config: ShellConfig,
        palette_index: int = 0,
        glow_color: str = "#7dd3fc",
        glow_core_color: str = "#e0f2fe",
        external_glow_changed: Callable[[], object] | None = None,
        projection_changed: Callable[[str, bool, str, int, dict[str, float]], bool] | None = None,
    ) -> None:
        self._spatial_feedback_shown = False
        self.timer = 0
        self.hide_timer = 0
        self.monitor = monitor
        self.palette_index = palette_index
        self.external_layer_glow = True
        self.external_glow_changed = external_glow_changed
        self.projection_changed = projection_changed
        self._compositor_projected = False
        self._projection_revision = 0
        self._shown = False
        self._reveal_from = 1.0
        self._reveal_to = 1.0
        self._reveal_started = 0.0
        self._reveal_duration = 0.0
        self.window = Gtk.ApplicationWindow(application=application)
        self.window.set_decorated(False)
        self.window.add_css_class("luminophore-surface")
        self.window.add_css_class(f"palette-{palette_index}")
        Gtk4LayerShell.init_for_window(self.window)
        connector = monitor.get_connector() or f"monitor-{palette_index}"
        self.projection_name = f"osd-{connector}"
        Gtk4LayerShell.set_namespace(self.window, f"luminophore-shell-{self.projection_name}")
        Gtk4LayerShell.set_monitor(self.window, monitor)
        Gtk4LayerShell.set_layer(self.window, Gtk4LayerShell.Layer.BOTTOM)
        Gtk4LayerShell.set_anchor(self.window, Gtk4LayerShell.Edge.BOTTOM, True)
        glow_extent, glow_bottom_padding, layer_bottom_margin = osd_glow_layout(
            config.theme.outline_glow_radius
        )
        Gtk4LayerShell.set_margin(self.window, Gtk4LayerShell.Edge.BOTTOM, layer_bottom_margin)
        self.layer_bottom_margin = layer_bottom_margin
        Gtk4LayerShell.set_exclusive_zone(self.window, 0)
        Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.NONE)
        panel = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        panel.add_css_class("luminophore-panel")
        panel.add_css_class("gsk-luminophore-frame")
        panel.add_css_class("luminophore-osd")
        panel.set_size_request(280, 48)
        self.icon = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
        self.icon.add_css_class("luminophore-symbol-primary")
        self.icon.add_css_class("osd-icon")
        self.icon.set_pixel_size(OSD_ICON_SIZE)
        self.icon.set_size_request(OSD_ICON_SIZE, OSD_ICON_SIZE)
        self.icon.set_valign(Gtk.Align.CENTER)
        self.progress = Gtk.ProgressBar()
        self.progress.add_css_class("osd-progress")
        self.progress.set_hexpand(True)
        self.progress.set_valign(Gtk.Align.CENTER)
        self.progress.set_size_request(-1, OSD_BAR_HEIGHT)
        self.label = Gtk.Label(label="0%")
        self.label.add_css_class("numeric")
        self.label.add_css_class("osd-value")
        self.label.add_css_class(OSD_VALUE_THEME_CLASS)
        self.label.set_width_chars(OSD_VALUE_WIDTH_CHARS)
        self.label.set_xalign(1.0)
        self.label.set_valign(Gtk.Align.CENTER)
        panel.append(self.icon)
        panel.append(self.progress)
        panel.append(self.label)
        glow_renderer = config.theme.glow_renderer
        self.glow = LuminophoreGlowContainer(
            panel,
            f"osd-{connector}",
            glow_color,
            config.theme.outline_glow_intensity,
            config.theme.outline_glow_radius,
            config.theme.outline_width,
            config.layout.radius,
            config.theme.palette_transition_ms,
            glow_core_color,
            glow_renderer,
            None,
            config.theme.animate_glow,
        )
        self.glow.set_edge_budget(
            GlowEdgeBudget(glow_extent, glow_extent, glow_extent, glow_bottom_padding)
        )
        self.stage = OsdGlowViewport(
            self.glow, glow_extent, glow_bottom_padding, self._glow_geometry_changed
        )
        self.window.set_child(self.stage)
        self._projection_handshake = FrameProjectionHandshake(
            self.window,
            f"luminophore-shell-{self.projection_name}",
            self._submit_projection,
            lambda: self.stage._revision,
        )
        # Keep the layer surface mapped for the whole Shell lifetime.  Native
        # bloom allocation is therefore ready before the first transient OSD
        # reveal instead of racing a map/allocation/projection handshake.
        self.window.set_can_target(False)
        self.window.set_opacity(0.0)
        self._reveal_from = 0.0
        self._reveal_to = 0.0
        self.window.present()
        self._projection_handshake.start()

    def _glow_geometry_changed(self) -> None:
        self._projection_revision += 1
        if self.external_glow_changed is not None:
            self.external_glow_changed()
        if self.window.get_visible():
            self._projection_handshake.start()

    def _sync_external_glow_once(self) -> bool:
        """Run the external glow update without retaining a GLib idle source."""
        if self.external_glow_changed is not None:
            self.external_glow_changed()
        return False

    def global_bounds(self) -> tuple[int, int, int, int]:
        geometry = self.monitor.get_geometry()
        panel = self.stage.glow_geometry()
        window_width = max(1, self.window.get_width())
        window_height = max(1, self.window.get_height())
        window_x = geometry.x + (geometry.width - window_width) // 2
        window_y = geometry.y + geometry.height - self.layer_bottom_margin - window_height
        return (
            window_x + panel.panel_x,
            window_y + panel.panel_y,
            panel.panel_width,
            panel.panel_height,
        )

    def external_glow_active(self) -> bool:
        return self._shown and self.window.get_visible() and not self._compositor_projected

    @staticmethod
    def external_glow_layer() -> str:
        return "top"

    def set_glow_profile(self, color: str, core_color: str, config: ShellConfig) -> None:
        self.glow.set_animation_enabled(config.theme.animate_glow)
        self.glow.set_profile(
            color,
            config.theme.outline_glow_intensity,
            config.theme.outline_glow_radius,
            config.theme.outline_width,
            config.layout.radius,
            config.theme.palette_transition_ms,
            core_color,
        )
        if self._compositor_projected:
            self._projection_revision += 1
            self._submit_projection()

    def _set_reveal(self, reveal_from: float, reveal_to: float, duration: float) -> None:
        self._reveal_from = reveal_from
        self._reveal_to = reveal_to
        self._reveal_started = time.monotonic()
        self._reveal_duration = duration

    def _submit_projection(self) -> bool:
        if self.projection_changed is None or self.stage._revision <= 0:
            return False
        geometry = self.stage.glow_geometry()
        frame = self.glow.external_glow_frame(geometry.panel_width, geometry.panel_height)
        args = (
            self.projection_name,
            True,
            uuid.uuid4().hex,
            self._projection_revision,
            {
                "red": frame.base_color[0],
                "green": frame.base_color[1],
                "blue": frame.base_color[2],
                "radius": frame.corner_radius,
                "outline": frame.outline_width,
                "extent": frame.visible_extent,
                "intensity": frame.core_energy / 0.16 if frame.core_energy > 0 else 0.0,
                "glow_phase": frame.phase,
                "reveal_from": self._reveal_from,
                "reveal_to": self._reveal_to,
                "reveal_started": self._reveal_started,
                "reveal_duration": self._reveal_duration,
                "reveal_offset_x": 0.0,
                "reveal_offset_y": 8.0,
                "panel_x": geometry.panel_x,
                "panel_y": geometry.panel_y,
                "panel_width": geometry.panel_width,
                "panel_height": geometry.panel_height,
            },
        )
        return submit_surface_projection(self, self.projection_changed, args, self._projection_applied)

    def _projection_applied(self, projected):
        self._compositor_projected = projected
        if not projected and os.getenv("LUMINOPHORE_COMPOSITOR") != "1":
            Gtk4LayerShell.set_layer(self.window, Gtk4LayerShell.Layer.OVERLAY)
        if self.external_glow_changed is not None:
            self.external_glow_changed()

    def show(self, kind: str, value: int, *, muted: bool = False, connector: str = "", text: str = "") -> None:
        self._spatial_feedback_shown = False
        kind, value = validate_osd(kind, value)
        icons = {
            "volume": "audio-volume-muted-symbolic" if muted else "audio-volume-high-symbolic",
            "microphone": "microphone-sensitivity-muted-symbolic" if muted else "audio-input-microphone-symbolic",
            "brightness": "display-brightness-symbolic",
            "privacy": "security-high-symbolic",
        }
        self.icon.set_from_icon_name(icons[kind])
        self.progress.set_fraction(value / 100)
        self.progress.set_visible(kind != "privacy")
        prefix = f"{connector} " if kind == "brightness" and connector else ""
        self.label.set_label(text if kind == "privacy" and text else f"{prefix}{'음소거' if muted else f'{value}%'}")
        self.label.set_width_chars(OSD_VALUE_WIDTH_CHARS)
        self._present()

    def show_spatial(self, text: str, icon: object | None = None) -> None:
        self._spatial_feedback_shown = True
        if icon is not None:
            self.icon.set_from_gicon(icon)
        else:
            self.icon.set_from_icon_name("view-grid-symbolic")
        self.progress.set_visible(False)
        self.label.set_width_chars(-1)
        self.label.set_label(text)
        self._present()

    def dismiss_spatial(self) -> None:
        if getattr(self, "_spatial_feedback_shown", False):
            self._spatial_feedback_shown = False
            if self.timer:
                GLib.source_remove(self.timer)
                self.timer = 0
            self._hide()

    def _present(self) -> None:
        self._osd_requested_at = time.monotonic()
        self._osd_waiting = True
        self._projection_rejections = 0
        if self.timer:
            GLib.source_remove(self.timer)
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = 0
        was_shown = self._shown
        self._shown = True
        self.window.set_opacity(1.0)
        self._projection_revision += 1
        self._set_reveal(1.0 if was_shown else 0.0, 1.0, 0.0 if was_shown else 0.16)
        self.window.present()
        if self.stage._revision > 0:
            self._submit_projection()
        else:
            self._projection_handshake.start()
        if self.external_glow_changed is not None:
            GLib.idle_add(self._sync_external_glow_once)
        self.timer = GLib.timeout_add(1200, self._hide)

    def _hide(self) -> bool:
        self.timer = 0
        self._projection_revision += 1
        self._set_reveal(1.0 if self._compositor_projected else 0.0, 0.0, 0.16 if self._compositor_projected else 0.0)
        self._submit_projection()
        self.hide_timer = GLib.timeout_add(170, self._finish_hide)
        return False

    def _finish_hide(self) -> bool:
        self.hide_timer = 0
        self._shown = False
        self.window.set_opacity(0.0)
        if self.external_glow_changed is not None:
            self.external_glow_changed()
        return False

    def stop(self) -> None:
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = 0

    def destroy(self) -> None:
        forget_surface_projection(self)
        self.stop()
        self._projection_handshake.cancel()
        self.window.set_visible(False)
        if self.external_glow_changed is not None:
            self.external_glow_changed()
        self.window.destroy()
