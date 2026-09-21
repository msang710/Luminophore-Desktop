from __future__ import annotations

from dataclasses import dataclass
import os
import time
import uuid
from typing import Callable

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk, Gtk4LayerShell  # noqa: E402

from ..config import ShellConfig
from ..glow import FrameTimingProbe, GlowEdgeBudget
from ..transition import (
    MotionTokens,
    OverviewRevealTransition,
    TimedTransition,
    ease_in_cubic,
    ease_out_cubic,
    smoothstep,
)
from .effects import LuminophoreGlowContainer
from .projection import FrameProjectionHandshake, submit_surface_projection, forget_surface_projection


@dataclass(frozen=True)
class SurfacePlacement:
    side: str
    top_margin: int
    width: int
    height: int
    vertical: str = "top"


from ..panel_geometry import anchored_panel_bounds


def expanded_keyboard_mode(always_exclusive: bool, interactive: bool) -> Gtk4LayerShell.KeyboardMode:
    if always_exclusive:
        return Gtk4LayerShell.KeyboardMode.EXCLUSIVE
    if interactive:
        return Gtk4LayerShell.KeyboardMode.ON_DEMAND
    return Gtk4LayerShell.KeyboardMode.NONE


def transition_content_opacities(progress: float, expanding: bool, replace_collapsed: bool) -> tuple[float, float]:
    bounded = max(0.0, min(1.0, progress))

    def ramp(value: float, start: float, end: float) -> float:
        return smoothstep((value - start) / max(0.001, end - start))

    if expanding:
        expanded_opacity = ramp(bounded, 0.12, 0.68)
    else:
        expanded_opacity = 1.0 - ramp(1.0 - bounded, 0.0, 0.35)
    collapsed_opacity = 1.0 if not replace_collapsed else 1.0 - ramp(bounded, 0.0, 0.48)
    return collapsed_opacity, expanded_opacity


class ExpansionCoordinator:
    def __init__(self) -> None:
        self._open_by_monitor: dict[int, set["CornerSurface"]] = {}
        self._group_opening = False

    def request_open(self, surface: "CornerSurface") -> None:
        monitor_key = id(surface.monitor)
        existing = self._open_by_monitor.get(monitor_key, set()).copy()
        if not self._group_opening:
            for open_surface in existing:
                if open_surface is not surface:
                    open_surface.set_expanded(False)
            self._open_by_monitor[monitor_key] = {surface}
        else:
            self._open_by_monitor.setdefault(monitor_key, set()).add(surface)

    def open_group(self, surfaces: list["CornerSurface"]) -> None:
        self._group_opening = True
        try:
            for surface in surfaces:
                surface.set_expanded(True)
        finally:
            self._group_opening = False

    def closed(self, surface: "CornerSurface") -> None:
        monitor_key = id(surface.monitor)
        opened = self._open_by_monitor.get(monitor_key)
        if not opened:
            return
        opened.discard(surface)
        if not opened:
            self._open_by_monitor.pop(monitor_key, None)


class AnimatedSurfaceContent(Gtk.Widget):
    __gtype_name__ = "LuminophoreAnimatedSurfaceContent"

    def __init__(
        self,
        collapsed: Gtk.Widget,
        expanded: Gtk.Widget,
        side: str,
        collapsed_outer_width: int,
        collapsed_outer_height: int,
        duration_ms: int,
        replace_collapsed: bool,
        completed: Callable[[bool], None],
        frame_timing_probe: FrameTimingProbe | None = None,
    ) -> None:
        super().__init__()
        self.collapsed = collapsed
        self.expanded = expanded
        self.side = side
        self.collapsed_outer_width = collapsed_outer_width
        self.collapsed_outer_height = collapsed_outer_height
        self.duration_ms = max(0, duration_ms)
        self.motion = MotionTokens.from_config(self.duration_ms, 0)
        self.replace_collapsed = replace_collapsed
        self.completed = completed
        self._frame_timing_probe = frame_timing_probe or FrameTimingProbe()
        self.desired_expanded = False
        self.height_override: int | None = None
        self._collapsed_target = (1, 1)
        self._expanded_target = (1, 1)
        self._targets_ready = False
        self._tick_id = 0
        self._completion_pending = False
        self._last_presentation_counter = -1
        self._openness = TimedTransition(0, self.duration_ms)
        self._width = TimedTransition(1, self.duration_ms)
        self._height = TimedTransition(1, self.duration_ms)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        collapsed.set_parent(self)
        expanded.set_parent(self)
        self._sync_content_state()

    @staticmethod
    def _now_ms() -> float:
        return GLib.get_monotonic_time() / 1000.0

    @staticmethod
    def _ramp(value: float, start: float, end: float) -> float:
        return smoothstep((value - start) / max(0.001, end - start))

    def _chrome(self) -> tuple[int, int]:
        parent = self.get_parent()
        if not parent:
            return 0, 0
        style = parent.get_style_context()
        padding = style.get_padding()
        border = style.get_border()
        return (
            padding.left + padding.right + border.left + border.right,
            padding.top + padding.bottom + border.top + border.bottom,
        )

    @staticmethod
    def _natural_size(widget: Gtk.Widget) -> tuple[int, int]:
        minimum_width, natural_width, _minimum_baseline, _natural_baseline = widget.measure(
            Gtk.Orientation.HORIZONTAL,
            -1,
        )
        width = max(1, minimum_width, natural_width)
        minimum_height, natural_height, _minimum_baseline, _natural_baseline = widget.measure(
            Gtk.Orientation.VERTICAL,
            width,
        )
        return width, max(1, minimum_height, natural_height)

    def _measure_targets(self) -> tuple[tuple[int, int], tuple[int, int]]:
        collapsed_width, collapsed_height = self._natural_size(self.collapsed)
        expanded_width, expanded_height = self._natural_size(self.expanded)
        chrome_width, chrome_height = self._chrome()
        width_floor = max(1, self.collapsed_outer_width - chrome_width)
        height_floor = max(1, self.collapsed_outer_height - chrome_height)
        collapsed_target = (
            max(width_floor, collapsed_width),
            max(height_floor, collapsed_height),
        )
        if self.replace_collapsed:
            target_width = max(width_floor, expanded_width)
            target_height = max(height_floor, expanded_height)
        else:
            target_width = max(width_floor, collapsed_width, expanded_width)
            target_height = max(height_floor, collapsed_height + expanded_height)
        if self.height_override is not None:
            target_height = max(target_height, self.height_override - chrome_height)
        return collapsed_target, (target_width, target_height)

    def _sync_targets(self, now_ms: float, force_retarget: bool = False) -> None:
        collapsed_target, expanded_target = self._measure_targets()
        targets_changed = (
            not self._targets_ready
            or collapsed_target != self._collapsed_target
            or expanded_target != self._expanded_target
        )
        self._collapsed_target = collapsed_target
        self._expanded_target = expanded_target
        if not self._targets_ready:
            self._width.snap(collapsed_target[0])
            self._height.snap(collapsed_target[1])
            self._targets_ready = True
        if not targets_changed and not force_retarget:
            return
        target = expanded_target if self.desired_expanded else collapsed_target
        motion = self.motion.expand if self.desired_expanded else self.motion.collapse
        self._width.set_motion(motion)
        self._height.set_motion(motion)
        width_span = max(1, abs(expanded_target[0] - collapsed_target[0]))
        height_span = max(1, abs(expanded_target[1] - collapsed_target[1]))
        width_active = self._width.retarget(target[0], now_ms, span=width_span)
        height_active = self._height.retarget(target[1], now_ms, span=height_span)
        if width_active or height_active:
            self._ensure_tick()

    def _sync_content_state(self) -> None:
        progress = max(0.0, min(1.0, self._openness.value))
        collapsed_opacity, expanded_opacity = transition_content_opacities(
            progress,
            self.desired_expanded,
            self.replace_collapsed,
        )
        self.collapsed.set_opacity(collapsed_opacity)
        self.expanded.set_opacity(expanded_opacity)
        if self.replace_collapsed:
            self.collapsed.set_can_target(not self.desired_expanded)
            self.collapsed.set_sensitive(not self.desired_expanded)
        else:
            self.collapsed.set_can_target(True)
            self.collapsed.set_sensitive(True)
        self.expanded.set_can_target(self.desired_expanded)
        self.expanded.set_sensitive(self.desired_expanded)

    def _is_active(self) -> bool:
        return self._openness.active or self._width.active or self._height.active

    def _sample(self, now_ms: float) -> None:
        self._openness.sample(now_ms)
        self._width.sample(now_ms)
        self._height.sample(now_ms)
        self._sync_content_state()

    def _queue_layout(self) -> None:
        self.queue_resize()
        self.queue_draw()
        parent = self.get_parent()
        if parent:
            parent.queue_allocate()
            parent.queue_draw()

    def _ensure_tick(self) -> None:
        if self._is_active():
            self._completion_pending = True
            if not self._tick_id:
                self._tick_id = self.add_tick_callback(self._on_tick)
            return
        if self._completion_pending:
            self._completion_pending = False
            GLib.idle_add(self._finish_transition)

    def _finish_transition(self) -> bool:
        if self._is_active():
            return False
        self._sync_content_state()
        self.completed(self.desired_expanded)
        return False

    def _record_completed_presentation(self, frame_clock: Gdk.FrameClock) -> None:
        if not self._frame_timing_probe.enabled:
            return
        try:
            counter = int(frame_clock.get_frame_counter()) - 1
            if counter <= self._last_presentation_counter:
                return
            timings = frame_clock.get_timings(counter)
            if timings is None or not timings.get_complete():
                return
            presentation_time_us = int(timings.get_presentation_time())
        except (AttributeError, TypeError, ValueError):
            return
        self._last_presentation_counter = counter
        self._frame_timing_probe.record_presentation(presentation_time_us)

    def _on_tick(self, _widget: Gtk.Widget, _frame_clock: Gdk.FrameClock) -> bool:
        frame_time_us = _frame_clock.get_frame_time()
        refresh_interval_us, _presentation_time_us = _frame_clock.get_refresh_info(frame_time_us)
        self._frame_timing_probe.record(frame_time_us, refresh_interval_us)
        self._record_completed_presentation(_frame_clock)
        now_ms = self._now_ms()
        self._frame_timing_probe.event(
            "surface.transition_tick", expanded=self.desired_expanded
        )
        started = time.perf_counter_ns()
        self._sync_targets(now_ms)
        self._frame_timing_probe.event(
            "surface.sync_targets", time.perf_counter_ns() - started
        )
        started = time.perf_counter_ns()
        self._sample(now_ms)
        self._frame_timing_probe.event("surface.sample", time.perf_counter_ns() - started)
        self._frame_timing_probe.event(
            "content.geometry.sampled",
            width=round(self._width.value),
            height=round(self._height.value),
            openness=round(self._openness.value, 4),
        )
        started = time.perf_counter_ns()
        self._queue_layout()
        self._frame_timing_probe.event(
            "surface.queue_layout", time.perf_counter_ns() - started
        )
        if self._is_active():
            return True
        self._tick_id = 0
        if self._completion_pending:
            self._completion_pending = False
            self.completed(self.desired_expanded)
        return False

    def set_expanded(self, expanded: bool) -> None:
        now_ms = self._now_ms()
        self._sample(now_ms)
        self.desired_expanded = expanded
        motion = self.motion.expand if expanded else self.motion.collapse
        self._openness.set_motion(motion)
        self._width.set_motion(motion)
        self._height.set_motion(motion)
        self._sync_targets(now_ms, force_retarget=True)
        self._openness.retarget(1.0 if expanded else 0.0, now_ms, span=1.0)
        self._sync_content_state()
        self._queue_layout()
        self._completion_pending = True
        self._ensure_tick()

    def set_height_override(self, height: int | None) -> None:
        if self.height_override == height:
            return
        now_ms = self._now_ms()
        self._sample(now_ms)
        self.height_override = height
        self._sync_targets(now_ms, force_retarget=True)
        self._queue_layout()
        self._ensure_tick()

    def is_settled(self, expanded: bool) -> bool:
        expected = 1.0 if expanded else 0.0
        return (
            self.desired_expanded == expanded
            and not self._is_active()
            and abs(self._openness.value - expected) < 0.001
        )

    @staticmethod
    def _transform(x: int, y: int) -> Gsk.Transform | None:
        if not x and not y:
            return None
        point = Graphene.Point()
        point.init(float(x), float(y))
        return Gsk.Transform.new().translate(point)

    def do_measure(
        self,
        orientation: Gtk.Orientation,
        _for_size: int,
    ) -> tuple[int, int, int, int]:
        started = time.perf_counter_ns()
        now_ms = self._now_ms()
        self._sync_targets(now_ms)
        self._sample(now_ms)
        value = self._width.value if orientation == Gtk.Orientation.HORIZONTAL else self._height.value
        measured = max(1, round(value))
        self._frame_timing_probe.event(
            "surface.measure", time.perf_counter_ns() - started
        )
        return measured, measured, -1, -1

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        started = time.perf_counter_ns()
        collapsed_width, collapsed_height = self._collapsed_target
        expanded_width, expanded_total_height = self._expanded_target
        collapsed_x = width - collapsed_width if self.side == "right" else 0
        self.collapsed.allocate(
            collapsed_width,
            collapsed_height,
            -1,
            self._transform(collapsed_x, 0),
        )
        expanded_y = 0 if self.replace_collapsed else collapsed_height
        expanded_height = max(1, expanded_total_height - expanded_y)
        expanded_x = width - expanded_width if self.side == "right" else 0
        self.expanded.allocate(
            expanded_width,
            expanded_height,
            -1,
            self._transform(expanded_x, expanded_y),
        )
        self._frame_timing_probe.event(
            "surface.allocate",
            time.perf_counter_ns() - started,
            width=width,
            height=height,
        )

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        started = time.perf_counter_ns()
        bounds = Graphene.Rect()
        bounds.init(0, 0, self.get_width(), self.get_height())
        snapshot.push_clip(bounds)
        self.snapshot_child(self.collapsed, snapshot)
        self.snapshot_child(self.expanded, snapshot)
        snapshot.pop()
        self._frame_timing_probe.event(
            "content.snapshot", time.perf_counter_ns() - started
        )

    def do_dispose(self) -> None:
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        if self.collapsed.get_parent() is self:
            self.collapsed.unparent()
        if self.expanded.get_parent() is self:
            self.expanded.unparent()
        self.completed = lambda _expanded: None
        super().do_dispose()


class AnchoredPanelViewport(Gtk.Widget):
    __gtype_name__ = "LuminophoreAnchoredPanelViewport"

    def __init__(
        self,
        panel: Gtk.Widget,
        side: str,
        width: int,
        height: int,
        edge_inset: int = 0,
        top_inset: int = 0,
        bottom_inset: int = 0,
        vertical: str = "top",
        geometry_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.panel = panel
        self.side = side
        self.viewport_width = max(1, width)
        self.viewport_height = max(1, height)
        self.edge_inset = max(0, edge_inset)
        self.top_inset = max(0, top_inset)
        self.bottom_inset = max(0, bottom_inset)
        self.vertical = vertical
        self.geometry_changed = geometry_changed
        self._panel_bounds = (0, 0, 1, 1)
        self._geometry_revision = 0
        self._reveal_opacity = 1.0
        self._reveal_offset = (0.0, 0.0)
        self._input_enabled = True
        self.set_overflow(Gtk.Overflow.HIDDEN)
        panel.set_parent(self)

    def panel_bounds(self) -> tuple[int, int, int, int]:
        return self._panel_bounds

    def geometry_revision(self) -> int:
        return self._geometry_revision

    def set_reveal(self, opacity: float, offset_x: float, offset_y: float) -> None:
        state = (
            max(0.0, min(1.0, float(opacity))),
            float(offset_x),
            float(offset_y),
        )
        if state == (self._reveal_opacity, *self._reveal_offset):
            return
        self._reveal_opacity = state[0]
        self._reveal_offset = state[1], state[2]
        self.queue_draw()

    def set_input_enabled(self, enabled: bool) -> None:
        self._input_enabled = bool(enabled)
        self.refresh_input_region()

    def do_measure(
        self,
        orientation: Gtk.Orientation,
        _for_size: int,
    ) -> tuple[int, int, int, int]:
        value = self.viewport_width if orientation == Gtk.Orientation.HORIZONTAL else self.viewport_height
        return value, value, -1, -1

    def _panel_size(self) -> tuple[int, int]:
        minimum_width, natural_width, _minimum_baseline, _natural_baseline = self.panel.measure(
            Gtk.Orientation.HORIZONTAL,
            -1,
        )
        available_width = max(1, self.viewport_width - 2 * self.edge_inset)
        width = min(available_width, max(1, minimum_width, natural_width))
        minimum_height, natural_height, _minimum_baseline, _natural_baseline = self.panel.measure(
            Gtk.Orientation.VERTICAL,
            width,
        )
        available_height = max(1, self.viewport_height - self.top_inset - self.bottom_inset)
        height = min(available_height, max(1, minimum_height, natural_height))
        return width, height

    def _set_input_region(self, x: int, y: int, width: int, height: int) -> None:
        native = self.get_native()
        surface = native.get_surface() if native else None
        if surface:
            surface.set_input_region(cairo.Region(cairo.RectangleInt(x, y, width, height)))

    def refresh_input_region(self) -> None:
        """Reapply the panel-only input region after a layer-shell role update."""
        if self._input_enabled:
            self._set_input_region(*self._panel_bounds)
        else:
            self._set_input_region(0, 0, 0, 0)

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        panel_width, panel_height = self._panel_size()
        previous_bounds = self._panel_bounds
        self._panel_bounds = anchored_panel_bounds(
            self.side,
            width,
            panel_width,
            panel_height,
            self.edge_inset,
            self.top_inset,
            height,
            self.bottom_inset,
            self.vertical,
        )
        x, y, panel_width, panel_height = self._panel_bounds
        if self._panel_bounds != previous_bounds:
            self._geometry_revision += 1
        if isinstance(self.panel, LuminophoreGlowContainer):
            self.panel.set_edge_budget(
                GlowEdgeBudget.from_bounds(width, height, x, y, panel_width, panel_height)
            )
        self.panel.allocate(panel_width, panel_height, -1, AnimatedSurfaceContent._transform(x, y))
        self.refresh_input_region()
        if self.geometry_changed is not None and self._panel_bounds != previous_bounds:
            self.geometry_changed()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        snapshot.save()
        point = Graphene.Point()
        point.init(*self._reveal_offset)
        transform = Gsk.Transform.new().translate(point)
        snapshot.transform(transform)
        snapshot.push_opacity(self._reveal_opacity)
        self.snapshot_child(self.panel, snapshot)
        snapshot.pop()
        snapshot.restore()

    def do_dispose(self) -> None:
        if self.panel.get_parent() is self:
            self.panel.unparent()
        super().do_dispose()


class CornerSurface:
    def __init__(
        self,
        application: Gtk.Application,
        monitor: Gdk.Monitor,
        name: str,
        placement: SurfacePlacement,
        config: ShellConfig,
        coordinator: ExpansionCoordinator,
        collapsed: Gtk.Widget,
        expanded: Gtk.Widget,
        keyboard: bool = False,
        replace_collapsed: bool = False,
        palette_index: int = 0,
        glow_color: str = "#78DCE8",
        glow_core_color: str | None = None,
        external_layer_glow: bool = False,
        external_glow_changed: Callable[[], object] | None = None,
        projection_changed: Callable[[str, bool, str, int, dict[str, float]], bool] | None = None,
    ) -> None:
        self.monitor = monitor
        self.name = name
        self.palette_index = palette_index
        self.placement = placement
        self.config = config
        self.coordinator = coordinator
        self.keyboard = keyboard
        self.keyboard_interactive = False
        self.replace_collapsed = replace_collapsed
        self.expanded = False
        self._external_glow_top = False
        self.external_layer_glow = bool(external_layer_glow)
        self.external_glow_changed = external_glow_changed
        self.projection_changed = projection_changed
        self._compositor_projected = False
        self._projection_initialized = False
        self._projection_content_revision = 0
        self._projection_geometry_revision = 0
        self._projection_pending_geometry_revision = 0
        self._transition_waiters: list[tuple[bool, Callable[[], None]]] = []
        hidden_x = -18.0 if placement.side == "left" else 18.0
        hidden_y = 18.0 if placement.vertical == "bottom" else 0.0
        if placement.vertical == "bottom":
            hidden_x = 0.0
        self._overview_reveal = OverviewRevealTransition(hidden_x, hidden_y)
        self._overview_reveal_tick = 0
        self._overview_reveal_owned = False
        self._overview_reveal_completed: Callable[[], None] | None = None
        self._overview_reveal_generation = 0
        self.overview_close_requested: Callable[[], None] | None = None
        self.frame_timing_probe = FrameTimingProbe()

        self.window = Gtk.ApplicationWindow(application=application)
        self.window.set_name(f"luminophore-{name}")
        self.window.add_css_class("luminophore-surface")
        self.window.add_css_class(f"palette-{palette_index}")
        self.window.set_decorated(False)
        self.window.set_resizable(False)

        Gtk4LayerShell.init_for_window(self.window)
        Gtk4LayerShell.set_namespace(self.window, f"luminophore-shell-{name}")
        Gtk4LayerShell.set_monitor(self.window, monitor)
        Gtk4LayerShell.set_layer(self.window, Gtk4LayerShell.Layer.BOTTOM)
        Gtk4LayerShell.set_exclusive_zone(self.window, 0)
        Gtk4LayerShell.set_anchor(self.window, Gtk4LayerShell.Edge.TOP, True)
        edge = Gtk4LayerShell.Edge.LEFT if placement.side == "left" else Gtk4LayerShell.Edge.RIGHT
        Gtk4LayerShell.set_anchor(self.window, edge, True)
        # The transparent layer surface spans the monitor so the snapshot glow
        # can paint into the configured visual margins. Input remains restricted
        # to the actual panel rectangle.
        Gtk4LayerShell.set_margin(self.window, Gtk4LayerShell.Edge.TOP, 0)
        Gtk4LayerShell.set_margin(self.window, edge, 0)
        Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.NONE)

        self.frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.frame.add_css_class("luminophore-panel")
        self.frame.add_css_class("gsk-luminophore-frame")
        self.collapsed = collapsed
        expanded.add_css_class("luminophore-expanded")
        if replace_collapsed:
            expanded.add_css_class("replace-collapsed")
        self.expanded_widget = expanded
        self.animation = AnimatedSurfaceContent(
            collapsed,
            expanded,
            placement.side,
            placement.width,
            placement.height,
            config.layout.expansion_ms,
            replace_collapsed,
            self._on_transition_finished,
            self.frame_timing_probe,
        )
        self.frame.append(self.animation)
        glow_renderer = config.theme.glow_renderer
        self.glow = LuminophoreGlowContainer(
            self.frame,
            name,
            glow_color,
            config.theme.outline_glow_intensity,
            config.theme.outline_glow_radius,
            config.theme.outline_width,
            config.layout.radius,
            config.theme.palette_transition_ms,
            glow_core_color,
            glow_renderer,
            self.frame_timing_probe,
            config.theme.animate_glow,
        )
        self._observe_label_updates(collapsed)
        self._observe_label_updates(expanded)
        geometry = monitor.get_geometry()
        viewport_width = max(1, geometry.width)
        viewport_height = max(1, geometry.height)
        self.viewport = AnchoredPanelViewport(
            self.glow,
            placement.side,
            viewport_width,
            viewport_height,
            config.layout.edge_margin,
            placement.top_margin,
            config.layout.edge_margin,
            placement.vertical,
            self._glow_geometry_changed,
        )
        self.window.set_child(self.viewport)
        self._projection_handshake = FrameProjectionHandshake(
            self.window,
            f"luminophore-shell-{name}",
            self._register_initial_projection,
            self.viewport.geometry_revision,
        )
        self.window.connect("map", self._on_window_mapped)
        click_outside = Gtk.EventControllerKey()
        click_outside.connect("key-pressed", self._on_key)
        self.window.add_controller(click_outside)

    def _observe_label_updates(self, root: Gtk.Widget) -> None:
        if isinstance(root, Gtk.Label):
            root.connect("notify::label", self._on_label_updated)
        child = root.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._observe_label_updates(child)
            child = next_child

    def _glow_geometry_changed(self) -> None:
        self.frame_timing_probe.event(
            "glow.geometry.submitted",
            revision=self.viewport.geometry_revision(),
        )
        if (
            self.external_layer_glow
            and not self._compositor_projected
            and self.external_glow_changed is not None
        ):
            self.external_glow_changed()
        self._projection_handshake.start()

    def _on_window_mapped(self, _window: Gtk.Widget) -> None:
        self._projection_handshake.start()

    def _register_initial_projection(self) -> bool:
        geometry_revision = self.viewport.geometry_revision()
        if geometry_revision <= 0:
            return False
        if geometry_revision == self._projection_geometry_revision:
            return True
        if geometry_revision != self._projection_pending_geometry_revision:
            self._projection_content_revision += 1
            self._projection_pending_geometry_revision = geometry_revision
        projected = self._set_surface_layer(self._external_glow_top, advance_revision=False)
        if projected:
            self._projection_geometry_revision = geometry_revision
            self._projection_pending_geometry_revision = 0
        return projected

    def _on_label_updated(self, _label: Gtk.Label, _parameter: object) -> None:
        self.frame_timing_probe.event("content.label_update")

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
        if self._projection_initialized:
            self._set_surface_layer(self._external_glow_top)

    def _on_key(self, _controller: Gtk.EventControllerKey, keyval: int, _keycode: int, _state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape and self.expanded:
            if self._overview_reveal_owned and self.overview_close_requested is not None:
                self.overview_close_requested()
            else:
                self.set_expanded(False)
            return True
        return False

    def _on_transition_finished(self, expanded: bool) -> None:
        if expanded != self.expanded:
            return
        if not expanded:
            self._set_surface_layer(False)
            self.coordinator.closed(self)
        remaining: list[tuple[bool, Callable[[], None]]] = []
        for target, callback in self._transition_waiters:
            if target == expanded:
                callback()
            else:
                remaining.append((target, callback))
        self._transition_waiters = remaining

    def when_transition_complete(self, expanded: bool, callback: Callable[[], None]) -> None:
        if self.animation.is_settled(expanded):
            GLib.idle_add(self._run_callback, callback)
            return
        self._transition_waiters.append((expanded, callback))

    @staticmethod
    def _run_callback(callback: Callable[[], None]) -> bool:
        callback()
        return False

    def is_fully_collapsed(self) -> bool:
        return self.animation.is_settled(False)

    def present(self) -> None:
        self.window.present()

    def global_bounds(self) -> tuple[int, int, int, int]:
        geometry = self.monitor.get_geometry()
        local_x, local_y, width, height = self.viewport.panel_bounds()
        return geometry.x + local_x, geometry.y + local_y, width, height

    def external_glow_active(self) -> bool:
        """Keep ambient glow alive; compositor layer order owns occlusion."""
        return (
            self.external_layer_glow
            and not self._compositor_projected
            and self.window.get_visible()
        )

    def external_glow_layer(self) -> str:
        return "top" if self._external_glow_top else "bottom"

    def external_glow_revision(self) -> int:
        return self.viewport.geometry_revision()

    def external_glow_reveal(self) -> tuple[float, float, float, float, float, float]:
        return self._overview_reveal.command()

    def overview_reveal_owned(self) -> bool:
        return self._overview_reveal_owned

    @staticmethod
    def _overview_now_ms() -> float:
        return GLib.get_monotonic_time() / 1000.0

    def _sample_overview_reveal(self) -> bool:
        sample = self._overview_reveal.sample(self._overview_now_ms())
        if self._compositor_projected:
            self.viewport.set_reveal(1.0, 0.0, 0.0)
        else:
            self.viewport.set_reveal(sample.opacity, sample.offset_x, sample.offset_y)
        if sample.active:
            return True
        self._overview_reveal_tick = 0
        completed = self._overview_reveal_completed
        self._overview_reveal_completed = None
        if completed is not None:
            completed()
        return False

    def _on_overview_reveal_tick(
        self,
        _widget: Gtk.Widget,
        _frame_clock: Gdk.FrameClock,
    ) -> bool:
        return self._sample_overview_reveal()

    def begin_overview_reveal(
        self,
        visible: bool,
        delay_ms: int = 0,
        completed: Callable[[], None] | None = None,
    ) -> None:
        self._overview_reveal_generation += 1
        now_ms = self._overview_now_ms()
        if visible and not self._overview_reveal_owned:
            self._overview_reveal.snap(False)
            hidden = self._overview_reveal.sample(now_ms)
            self.viewport.set_reveal(0.0, hidden.offset_x, hidden.offset_y)
        self._overview_reveal_owned = True
        self._overview_reveal_completed = completed
        from ..transition import OVERVIEW_ENTER_MS, OVERVIEW_EXIT_MS
        duration_ms = OVERVIEW_ENTER_MS if visible else OVERVIEW_EXIT_MS
        easing = ease_out_cubic if visible else ease_in_cubic
        self._overview_reveal.retarget(
            visible,
            now_ms,
            now_ms + max(0, delay_ms),
            duration_ms,
            easing,
        )
        self._sample_overview_reveal()
        if self._projection_initialized:
            self._set_surface_layer(visible or self.expanded or self._external_glow_top)
        if self._overview_reveal.active and not self._overview_reveal_tick:
            self._overview_reveal_tick = self.window.add_tick_callback(self._on_overview_reveal_tick)
        if visible:
            self.viewport.set_input_enabled(True)
        else:
            self.viewport.set_input_enabled(False)
        if self.external_glow_changed is not None:
            self.external_glow_changed()

    def release_overview_reveal(self) -> None:
        if self._overview_reveal_tick:
            self.window.remove_tick_callback(self._overview_reveal_tick)
            self._overview_reveal_tick = 0
        self._overview_reveal_completed = None
        self._overview_reveal_generation += 1
        self._overview_reveal_owned = False
        self._overview_reveal.snap(True)
        if self._projection_initialized:
            self._set_surface_layer(self.expanded)
        self.viewport.set_reveal(1.0, 0.0, 0.0)
        self.viewport.set_input_enabled(True)
        if self.external_glow_changed is not None:
            self.external_glow_changed()

    def set_overview_close_requested(self, callback: Callable[[], None]) -> None:
        self.overview_close_requested = callback

    def finish_overview_hide(self) -> None:
        generation = self._overview_reveal_generation
        self.set_expanded(False)
        self.when_transition_complete(
            False,
            lambda: self._release_overview_after_collapse(generation),
        )

    def _release_overview_after_collapse(self, generation: int) -> None:
        if generation == self._overview_reveal_generation:
            self.release_overview_reveal()

    def _refresh_input_region(self) -> bool:
        self.viewport.refresh_input_region()
        return False

    def _refresh_input_region_on_frame(
        self,
        _widget: Gtk.Widget,
        _frame_clock: Gdk.FrameClock,
    ) -> bool:
        self.viewport.refresh_input_region()
        return False

    def _set_surface_layer(self, overlay: bool, *, advance_revision: bool = True) -> bool:
        """Atomically route content, input and external glow to one layer state."""
        self._external_glow_top = overlay
        if advance_revision:
            self._projection_content_revision += 1
        generation = uuid.uuid4().hex
        _, _, width, height = self.global_bounds()
        frame = self.glow.external_glow_frame(width, height)
        reveal_from, reveal_to, reveal_started, reveal_duration, reveal_offset_x, reveal_offset_y = self._overview_reveal.command()
        projection_style = {
            "red": frame.base_color[0],
            "green": frame.base_color[1],
            "blue": frame.base_color[2],
            "radius": frame.corner_radius,
            "outline": frame.outline_width,
            "extent": frame.visible_extent,
            "intensity": frame.core_energy / 0.16 if frame.core_energy > 0 else 0.0,
            "glow_phase": frame.phase,
            "reveal_from": reveal_from,
            "reveal_to": reveal_to,
            "reveal_started": reveal_started,
            "reveal_duration": reveal_duration,
            "reveal_offset_x": reveal_offset_x,
            "reveal_offset_y": reveal_offset_y,
            # A zero-sized explicit panel selects the wl_surface's live input
            # region in the compositor.  GTK commits that region with the
            # animated allocation, so content and bloom consume one geometry
            # without spawning two hyprctl processes on every frame.
            "panel_x": -1.0,
            "panel_y": -1.0,
            "panel_width": 0.0,
            "panel_height": 0.0,
        }
        args = (self.name, overlay, generation, self._projection_content_revision, projection_style)
        return submit_surface_projection(self, self.projection_changed, args, lambda projected: self._projection_applied(projected, overlay))

    def _projection_applied(self, projected, overlay):
        self._compositor_projected = projected
        self._projection_initialized = projected
        # A transient LUMINOPHORE IPC rejection must not move the physical layer away
        # from BOTTOM: only physical BOTTOM surfaces can be projected.
        if not projected and os.getenv("LUMINOPHORE_COMPOSITOR") != "1":
            Gtk4LayerShell.set_layer(
                self.window,
                Gtk4LayerShell.Layer.OVERLAY if overlay else Gtk4LayerShell.Layer.BOTTOM,
            )
        # Layer-shell may replace or recommit the wl_surface.  The input region
        # belongs to that surface, so restore it both now and after GTK's next
        # main-loop/frame boundary instead of leaving a full-screen input plane.
        self.viewport.refresh_input_region()
        GLib.idle_add(self._refresh_input_region)
        self.window.add_tick_callback(self._refresh_input_region_on_frame)
        # Projection and the native fallback are mutually exclusive owners.
        # Always resync after the ownership result changes so a fallback frame
        # submitted during startup is explicitly cleared once projection wins.
        if self.external_layer_glow and self.external_glow_changed is not None:
            self.external_glow_changed()
        return projected

    def contains_global_point(self, x: int, y: int) -> bool:
        left, top, width, height = self.global_bounds()
        return left <= x < left + width and top <= y < top + height

    def toggle(self) -> None:
        self.set_expanded(not self.expanded)

    def set_expanded(self, expanded: bool) -> None:
        if self.expanded == expanded:
            return
        self.expanded = expanded
        if expanded:
            self.coordinator.request_open(self)
            self._set_surface_layer(True)
            mode = expanded_keyboard_mode(self.keyboard, self.keyboard_interactive)
            Gtk4LayerShell.set_keyboard_mode(self.window, mode)
            if mode != Gtk4LayerShell.KeyboardMode.NONE:
                self.window.present()
            self.animation.set_expanded(True)
        else:
            Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.NONE)
            self.animation.set_expanded(False)

    def set_keyboard_interactive(self, interactive: bool) -> None:
        if self.keyboard_interactive == interactive:
            return
        self.keyboard_interactive = interactive
        if not self.expanded:
            return
        mode = expanded_keyboard_mode(self.keyboard, interactive)
        Gtk4LayerShell.set_keyboard_mode(self.window, mode)
        if mode != Gtk4LayerShell.KeyboardMode.NONE:
            self.window.present()

    def keyboard_mode_name(self) -> str:
        mode = Gtk4LayerShell.get_keyboard_mode(self.window)
        return {
            Gtk4LayerShell.KeyboardMode.NONE: "none",
            Gtk4LayerShell.KeyboardMode.EXCLUSIVE: "exclusive",
            Gtk4LayerShell.KeyboardMode.ON_DEMAND: "on-demand",
        }.get(mode, str(int(mode)))

    def set_height_override(self, height: int | None) -> None:
        requested = None if height is None else max(self.placement.height, height)
        self.animation.set_height_override(requested)

    def destroy(self) -> None:
        forget_surface_projection(self)
        self.coordinator.closed(self)
        self._projection_handshake.cancel()
        self.window.set_visible(False)
        if self.external_layer_glow and self.external_glow_changed is not None:
            self.external_glow_changed()
        if self._overview_reveal_tick:
            self.window.remove_tick_callback(self._overview_reveal_tick)
            self._overview_reveal_tick = 0
        self.window.destroy()
