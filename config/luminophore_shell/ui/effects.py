from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import TYPE_CHECKING, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk  # noqa: E402

from ..glow import (
    BLOOM_ENERGY_FACTOR,
    CORE_ENERGY_FACTOR,
    FrameProfile,
    FrameTimingProbe,
    FrameTimingSnapshot,
    GlowEdgeBudget,
    GlowFrameCadence,
    GlowRenderFrame,
    GpuFrameTimeline,
    NEAR_ENERGY_FACTOR,
    frame_profile,
    parse_hex_rgb,
    surface_glow_phase,
)
from ..transition import MotionSpec, MotionTokens, TimedTransition, VanishingTransition, smoothstep

if TYPE_CHECKING:
    from ..applications import ApplicationRecord
    from ..app_icons import ApplicationIconProvider


@dataclass(frozen=True)
class IconEffectSpec:
    far_blur: float = 4.5
    far_alpha: float = 0.22
    near_blur: float = 1.75
    near_alpha: float = 0.62
    face_alpha: float = 1.0

    @property
    def passes(self) -> tuple[tuple[float, float], ...]:
        return (
            (self.far_blur, self.far_alpha),
            (self.near_blur, self.near_alpha),
            (0.0, self.face_alpha),
        )


@dataclass(frozen=True)
class RippleSample:
    radius: float
    alpha: float
    visible: bool


def ripple_sample(progress: float, width: float, height: float, x: float, y: float) -> RippleSample:
    bounded = max(0.0, min(1.0, progress))
    maximum = max(
        math.hypot(x, y),
        math.hypot(width - x, y),
        math.hypot(x, height - y),
        math.hypot(width - x, height - y),
    )
    return RippleSample(
        radius=max(1.0, maximum * bounded),
        alpha=0.24 * (1.0 - bounded),
        visible=bounded < 0.999,
    )


def animation_tick_required(mapped: bool, hover_active: bool, ripple_active: bool) -> bool:
    return mapped and (hover_active or ripple_active)


class AccentPulse:
    """Retargetable normal-accent-normal pulse with a shared 320 ms token."""

    def __init__(self, duration_ms: int = 320) -> None:
        self.duration_ms = max(0, int(duration_ms))
        self._half_ms = self.duration_ms // 2
        self._value = TimedTransition(0.0, self._half_ms, smoothstep)
        self._phase = "idle"

    @property
    def value(self) -> float:
        return self._value.value

    @property
    def active(self) -> bool:
        return self._phase != "idle" or self._value.active

    def trigger(self, now_ms: float) -> None:
        self.sample(now_ms)
        self._phase = "rising"
        self._value.set_motion(MotionSpec(self._half_ms, smoothstep))
        if not self._value.retarget(1.0, now_ms, span=1.0):
            self._phase = "falling"
            self._value.retarget(0.0, now_ms, span=1.0)

    def sample(self, now_ms: float) -> float:
        sample = self._value.sample(now_ms)
        if self._phase == "rising" and not sample.active and self._value.value >= 0.999:
            self._phase = "falling"
            self._value.set_motion(MotionSpec(self._half_ms, smoothstep))
            self._value.retarget(0.0, now_ms, span=1.0)
        elif self._phase == "falling" and not sample.active and self._value.value <= 0.001:
            self._phase = "idle"
            self._value.snap(0.0)
        return max(0.0, min(1.0, self._value.value))

    def reset(self) -> None:
        self._phase = "idle"
        self._value.snap(0.0)


class LuminophoreGlowContainer(Gtk.Widget):
    """Shared GSK luminophore frame used by Shell surfaces and the login Greeter."""

    __gtype_name__ = "LuminophoreGlowContainer"
    def __init__(
        self,
        child: Gtk.Widget,
        name: str,
        color: str,
        intensity: float,
        glow_radius: int,
        outline_width: int,
        corner_radius: int,
        color_transition_ms: int,
        core_color: str | None = None,
        glow_renderer: str = "gsk",
        frame_timing_probe: FrameTimingProbe | None = None,
        animate_glow: bool = True,
    ) -> None:
        super().__init__()
        self.child = child
        self.name = name
        self.phase = surface_glow_phase(name)
        self.intensity = max(0.0, float(intensity))
        self.glow_radius = max(1, int(glow_radius))
        self.outline_width = max(0, int(outline_width))
        self.corner_radius = max(0, int(corner_radius))
        self.color_transition_ms = max(0, int(color_transition_ms))
        self._color = parse_hex_rgb(color)
        self._color_from = self._color
        self._color_target = self._color
        self._color_started_at = time.monotonic()
        self._core_color = parse_hex_rgb(core_color or color)
        self._core_color_from = self._core_color
        self._core_color_target = self._core_color
        self._accent_color = self._color_target
        self._accent = AccentPulse(MotionTokens.from_config(180, 600).ripple.duration_ms)
        self._accent_level = 0.0
        self._tick_id = 0
        self._last_frame_seconds = time.monotonic()
        self._frame_cadence = GlowFrameCadence()
        self._gpu_timeline = GpuFrameTimeline()
        self._frame_timing_probe = frame_timing_probe or FrameTimingProbe()
        self._last_presentation_counter = -1
        self._geometry_key: tuple[int, int, int, int] | None = None
        self.edge_budget = GlowEdgeBudget()
        self._outline: Gsk.RoundedRect | None = None
        self._bloom_colors = (Gdk.RGBA(), Gdk.RGBA())
        if glow_renderer not in {"gsk", "layer"}:
            raise ValueError(f"unknown glow renderer: {glow_renderer}")
        self.requested_renderer = glow_renderer
        self.effective_renderer = glow_renderer
        self.renderer_error: str | None = None
        self.animate_glow = bool(animate_glow)
        self.set_overflow(Gtk.Overflow.VISIBLE)
        child.set_parent(self)

    def set_profile(
        self,
        color: str,
        intensity: float,
        glow_radius: int,
        outline_width: int,
        corner_radius: int,
        color_transition_ms: int,
        core_color: str | None = None,
    ) -> None:
        now = time.monotonic()
        target = parse_hex_rgb(color)
        core_target = parse_hex_rgb(core_color or color)
        current, current_core = self._sample_colors(now)
        if target != self._color_target:
            self._color_from = current
            self._color_target = target
            self._color_started_at = now
        if core_target != self._core_color_target:
            self._core_color_from = current_core
            self._core_color_target = core_target
            self._color_started_at = now
        self.intensity = max(0.0, float(intensity))
        next_glow_radius = max(1, int(glow_radius))
        self.glow_radius = next_glow_radius
        next_outline_width = max(0, int(outline_width))
        next_corner_radius = max(0, int(corner_radius))
        if next_outline_width != self.outline_width or next_corner_radius != self.corner_radius:
            self._invalidate_geometry()
        self.outline_width = next_outline_width
        self.corner_radius = next_corner_radius
        self.color_transition_ms = max(0, int(color_transition_ms))
        if self.color_transition_ms == 0:
            self._color = self._color_target
            self._color_from = self._color_target
            self._core_color = self._core_color_target
            self._core_color_from = self._core_color_target
        self._sync_animation()
        self.queue_draw()

    def set_edge_budget(self, budget: GlowEdgeBudget) -> None:
        if budget == self.edge_budget:
            return
        self.edge_budget = budget
        self.queue_draw()

    def set_animation_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.animate_glow:
            return
        self.animate_glow = enabled
        if not enabled:
            self._color = self._color_target
            self._color_from = self._color_target
            self._core_color = self._core_color_target
            self._core_color_from = self._core_color_target
            self._accent.reset()
            self._accent_level = 0.0
        self._sync_animation()
        self.queue_draw()

    def pulse_accent(self, color: str, now_ms: float | None = None) -> None:
        if not self.animate_glow:
            return
        self._accent_color = parse_hex_rgb(color)
        self._accent.trigger(time.monotonic() * 1000 if now_ms is None else now_ms)
        self._sync_animation()
        self.queue_draw()

    def _sample_colors(self, now: float) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        if not self.animate_glow:
            self._color = self._color_target
            self._core_color = self._core_color_target
            return self._color, self._core_color
        duration = self.color_transition_ms / 1000
        if duration <= 0:
            self._color = self._color_target
            self._core_color = self._core_color_target
            return self._color, self._core_color
        if self._color_from == self._color_target and self._core_color_from == self._core_color_target:
            return self._color, self._core_color
        progress = smoothstep((now - self._color_started_at) / duration)
        self._color = tuple(
            start + (target - start) * progress
            for start, target in zip(self._color_from, self._color_target, strict=True)
        )  # type: ignore[assignment]
        self._core_color = tuple(
            start + (target - start) * progress
            for start, target in zip(self._core_color_from, self._core_color_target, strict=True)
        )  # type: ignore[assignment]
        if progress >= 1:
            self._color_from = self._color_target
            self._core_color_from = self._core_color_target
        return self._color, self._core_color

    def _sample_frame_colors(self, now: float) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        base, core = self._sample_colors(now)
        if not self.animate_glow:
            self._accent_level = 0.0
            return base, core
        accent = self._accent.sample(now * 1000)
        self._accent_level = accent
        if accent <= 0:
            return base, core
        blended = tuple(start + (target - start) * accent for start, target in zip(base, self._accent_color, strict=True))
        blended_core = tuple(start + (target - start) * accent for start, target in zip(core, self._accent_color, strict=True))
        return blended, blended_core  # type: ignore[return-value]

    def _transition_pending(self) -> bool:
        return self._color_from != self._color_target or self._core_color_from != self._core_color_target

    def _needs_tick(self) -> bool:
        if getattr(self, "effective_renderer", "gsk") == "layer":
            return False
        return self.animate_glow and (
            self.intensity > 0 or self._transition_pending() or self._accent.active
        )

    def _sync_animation(self) -> None:
        if self.get_mapped() and self._needs_tick() and not self._tick_id:
            self._frame_cadence.reset()
            self._gpu_timeline.reset()
            self._tick_id = self.add_tick_callback(self._on_glow_frame)
        elif (not self.get_mapped() or not self._needs_tick()) and self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
            self._frame_cadence.reset()
            self._gpu_timeline.reset()

    def _on_glow_frame(self, _widget: Gtk.Widget, frame_clock: Gdk.FrameClock) -> bool:
        if not self.get_mapped() or not self._needs_tick():
            self._tick_id = 0
            self._frame_cadence.reset()
            self._gpu_timeline.reset()
            self._frame_timing_probe.reset()
            return False
        frame_time_us = frame_clock.get_frame_time()
        self._last_frame_seconds = frame_time_us / 1_000_000
        high_motion = self._transition_pending() or self._accent.active
        refresh_interval_us, _presentation_time_us = frame_clock.get_refresh_info(frame_time_us)
        self._frame_timing_probe.record(frame_time_us, refresh_interval_us)
        LuminophoreGlowContainer._record_completed_presentation(self, frame_clock)
        self._frame_timing_probe.event(
            "glow.tick",
            high_motion=high_motion,
            renderer=getattr(self, "effective_renderer", "unknown"),
        )
        if not self._frame_cadence.should_draw(refresh_interval_us, high_motion):
            return True
        self.queue_draw()
        if self._needs_tick():
            return True
        self._tick_id = 0
        self._frame_cadence.reset()
        self._gpu_timeline.reset()
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

    def do_map(self) -> None:
        Gtk.Widget.do_map(self)
        self._frame_cadence.reset()
        self._gpu_timeline.reset()
        self._sync_animation()

    def do_unmap(self) -> None:
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        self._frame_cadence.reset()
        self._gpu_timeline.reset()
        self._frame_timing_probe.reset()
        self._accent.reset()
        Gtk.Widget.do_unmap(self)

    def enable_frame_timing_probe(self, enabled: bool = True) -> None:
        self._frame_timing_probe.configure(enabled)
        self._last_presentation_counter = -1

    @property
    def frame_timing_enabled(self) -> bool:
        return self._frame_timing_probe.enabled

    def frame_timing_snapshot(self) -> FrameTimingSnapshot:
        return self._frame_timing_probe.snapshot()

    def external_glow_frame(self, width: int, height: int) -> GlowRenderFrame:
        frame = self._render_frame()
        return GlowRenderFrame(
            float(width), float(height), frame.corner_radius, frame.outline_width,
            frame.visible_extent, frame.core_energy, frame.near_energy,
            frame.bloom_energy, frame.elapsed_seconds, frame.phase,
            frame.base_color, frame.core_color, frame.edge_budget,
        )

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        started = time.perf_counter_ns()
        result = self.child.measure(orientation, for_size)
        self._frame_timing_probe.event("glow.measure", time.perf_counter_ns() - started)
        return result

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        started = time.perf_counter_ns()
        if self._geometry_key is not None and self._geometry_key[:2] != (width, height):
            self._invalidate_geometry()
        self.child.allocate(width, height, -1, None)
        self._frame_timing_probe.event(
            "glow.allocate",
            time.perf_counter_ns() - started,
            width=width,
            height=height,
        )

    def _render_frame(self) -> GlowRenderFrame:
        now = (
            self._last_frame_seconds if self._tick_id else time.monotonic()
        ) if self.animate_glow else 0.0
        base, core = self._sample_frame_colors(now)
        strength = max(0.0, self.intensity * (1.0 + self._accent_level * 0.45))
        return GlowRenderFrame(
            width=float(self.get_width()),
            height=float(self.get_height()),
            corner_radius=float(self.corner_radius),
            outline_width=float(self.outline_width),
            visible_extent=float(self.glow_radius),
            core_energy=strength * CORE_ENERGY_FACTOR,
            near_energy=strength * NEAR_ENERGY_FACTOR,
            bloom_energy=strength * BLOOM_ENERGY_FACTOR,
            elapsed_seconds=self._gpu_timeline.elapsed_seconds,
            phase=self.phase,
            base_color=base,
            core_color=core,
            edge_budget=self.edge_budget.as_tuple(),
        )

    def _invalidate_geometry(self) -> None:
        self._geometry_key = None
        self._outline = None

    def _ensure_geometry(self, width: int, height: int) -> Gsk.RoundedRect:
        key = (width, height, self.corner_radius, self.outline_width)
        if self._geometry_key == key and self._outline is not None:
            return self._outline
        bounds = Graphene.Rect()
        bounds.init(0, 0, width, height)
        outline = Gsk.RoundedRect()
        corner_radius = min(self.corner_radius, width / 2, height / 2)
        outline.init_from_rect(bounds, corner_radius)
        self._geometry_key = key
        self._outline = outline
        return outline

    def _snapshot_glow(
        self,
        snapshot: Gtk.Snapshot,
        outline: Gsk.RoundedRect,
        profile: FrameProfile,
        red: float,
        green: float,
        blue: float,
    ) -> None:
        for index, bloom_pass in enumerate(profile.bloom_passes):
            color = self._bloom_colors[index]
            color.red, color.green, color.blue, color.alpha = red, green, blue, bloom_pass.alpha
            snapshot.append_outset_shadow(outline, color, 0, 0, 0, bloom_pass.blur_radius)

    @staticmethod
    def _edge_mask_geometry(
        side: str,
        budget: float,
        width: float,
        height: float,
        extent: float,
    ) -> tuple[Graphene.Rect, Graphene.Point, Graphene.Point]:
        bounds = Graphene.Rect()
        bounds.init(-extent, -extent, width + extent * 2, height + extent * 2)
        start = Graphene.Point()
        end = Graphene.Point()
        if side == "left":
            start.init(-budget, 0)
            end.init(0, 0)
        elif side == "right":
            start.init(width, 0)
            end.init(width + budget, 0)
        elif side == "top":
            start.init(0, -budget)
            end.init(0, 0)
        else:
            start.init(0, height)
            end.init(0, height + budget)
        return bounds, start, end

    @staticmethod
    def _mask_stops(reverse: bool = False) -> list[Gsk.ColorStop]:
        stops: list[Gsk.ColorStop] = []
        for offset, alpha in ((0.0, 1.0 if reverse else 0.0), (1.0, 0.0 if reverse else 1.0)):
            stop = Gsk.ColorStop()
            stop.offset = offset
            stop.color = Gdk.RGBA(red=1.0, green=1.0, blue=1.0, alpha=alpha)
            stops.append(stop)
        return stops

    def _push_edge_masks(self, snapshot: Gtk.Snapshot, width: int, height: int, extent: float) -> int:
        constrained = self.edge_budget.constrained(extent)
        for side in constrained:
            budget = max(1.0, getattr(self.edge_budget, side))
            bounds, start, end = self._edge_mask_geometry(side, budget, width, height, extent)
            snapshot.push_mask(Gsk.MaskMode.ALPHA)
            snapshot.append_linear_gradient(
                bounds,
                start,
                end,
                self._mask_stops(side in {"right", "bottom"}),
            )
            snapshot.pop()
        return len(constrained)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        started = time.perf_counter_ns()
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        now = (
            self._last_frame_seconds if self._tick_id else time.monotonic()
        ) if self.animate_glow else 0.0
        (red, green, blue), (core_red, core_green, core_blue) = self._sample_frame_colors(now)
        outline = self._ensure_geometry(width, height)
        profile = frame_profile(
            self.glow_radius,
            self.intensity * (1.0 + self._accent_level * 0.45),
            self.outline_width,
            self.corner_radius,
            now,
            self.phase,
        )
        if self.effective_renderer == "layer":
            pass
        else:
            extent = max((item.blur_radius for item in profile.bloom_passes), default=float(self.glow_radius))
            mask_count = self._push_edge_masks(snapshot, width, height, extent)
            self._snapshot_glow(snapshot, outline, profile, red, green, blue)
            for _ in range(mask_count):
                snapshot.pop()
        self.snapshot_child(self.child, snapshot)
        self._frame_timing_probe.event(
            "glow.snapshot",
            time.perf_counter_ns() - started,
            renderer=self.effective_renderer,
            width=width,
            height=height,
        )

    def do_dispose(self) -> None:
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        self._frame_cadence.reset()
        self._gpu_timeline.reset()
        self._frame_timing_probe.reset()
        self._invalidate_geometry()
        if self.child.get_parent() is self:
            self.child.unparent()
        super().do_dispose()


class VanishingSurfaceContent(Gtk.Widget):
    """Clip a child toward its top edge using the shared collapse token."""

    __gtype_name__ = "LuminophoreVanishingSurfaceContent"

    def __init__(self, child: Gtk.Widget, expansion_ms: int = 200) -> None:
        super().__init__()
        self.child = child
        self.transition = VanishingTransition(expansion_ms)
        self.backdrop_alpha = 1.0
        self._tick_id = 0
        self._vanishing = False
        self._completed: Callable[[], None] = lambda: None
        # Keep each shared frame's halo visible while the Greeter is idle.
        # The top-pivot clip is enabled only for the authenticated vanish.
        self.set_overflow(Gtk.Overflow.VISIBLE)
        child.set_parent(self)

    @staticmethod
    def _now_ms() -> float:
        return GLib.get_monotonic_time() / 1000

    @property
    def vanishing(self) -> bool:
        return self._vanishing

    @property
    def accepts_input(self) -> bool:
        return not self._vanishing

    def original_size(self) -> tuple[int, int]:
        minimum_width, natural_width, _min_baseline, _natural_baseline = self.child.measure(Gtk.Orientation.HORIZONTAL, -1)
        width = max(1, minimum_width, natural_width)
        minimum_height, natural_height, _min_baseline, _natural_baseline = self.child.measure(Gtk.Orientation.VERTICAL, width)
        return width, max(1, minimum_height, natural_height)

    def vanish(self, completed: Callable[[], None]) -> None:
        if self._vanishing:
            return
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self._vanishing = True
        self._completed = completed
        self.set_can_target(False)
        self.set_sensitive(False)
        self.transition.begin(self._now_ms())
        self._tick_id = self.add_tick_callback(self._on_tick)
        self.queue_resize()

    def _apply(self, now_ms: float) -> bool:
        sample = self.transition.sample(now_ms)
        self.backdrop_alpha = sample.backdrop_alpha
        self.child.set_opacity(sample.opacity)
        self.queue_resize()
        self.queue_draw()
        return sample.active

    def _on_tick(self, _widget: Gtk.Widget, frame_clock: Gdk.FrameClock) -> bool:
        active = self._apply(frame_clock.get_frame_time() / 1000)
        if active:
            return True
        self._tick_id = 0
        self._completed()
        return False

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        width, height = self.original_size()
        value = width if orientation == Gtk.Orientation.HORIZONTAL else height
        scale = self.transition.sample(self._now_ms()).scale if self._vanishing else 1.0
        measured = max(1, round(value * scale))
        return measured, measured, -1, -1

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        natural_width, natural_height = self.original_size()
        transform: Gsk.Transform | None = None
        offset_x = (width - natural_width) // 2
        if offset_x:
            point = Graphene.Point()
            point.init(float(offset_x), 0.0)
            transform = Gsk.Transform.new().translate(point)
        self.child.allocate(natural_width, natural_height, -1, transform)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        bounds = Graphene.Rect()
        bounds.init(0, 0, self.get_width(), self.get_height())
        snapshot.push_clip(bounds)
        self.snapshot_child(self.child, snapshot)
        snapshot.pop()

    def do_dispose(self) -> None:
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        if self.child.get_parent() is self:
            self.child.unparent()
        self._completed = lambda: None
        super().do_dispose()


class LuminophoreIcon(Gtk.Widget):
    __gtype_name__ = "LuminophoreIcon"

    def __init__(self, source: Gtk.Image, size: int, role: str = "primary") -> None:
        super().__init__()
        self.source = source
        self.size = max(1, int(size))
        self.effect = IconEffectSpec()
        self._source_node: Gsk.RenderNode | None = None
        self._effect_node: Gsk.RenderNode | None = None
        self._effect_key: tuple[int, int, float, float, float, float] | None = None
        self.add_css_class(f"luminophore-symbol-{role}")
        self.set_overflow(Gtk.Overflow.VISIBLE)
        source.set_pixel_size(self.size)
        source.set_parent(self)

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        minimum, natural, minimum_baseline, natural_baseline = self.source.measure(orientation, for_size)
        extent = max(self.size, minimum, natural)
        return extent, extent, minimum_baseline, natural_baseline

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        if width != self.get_width() or height != self.get_height():
            self._source_node = None
            self._effect_node = None
            self._effect_key = None
        self.source.allocate(width, height, -1, None)

    @staticmethod
    def _with_alpha(color: Gdk.RGBA, alpha: float) -> Gdk.RGBA:
        result = Gdk.RGBA()
        result.red = color.red
        result.green = color.green
        result.blue = color.blue
        result.alpha = max(0.0, min(1.0, alpha))
        return result

    @staticmethod
    def _append_masked(
        snapshot: Gtk.Snapshot,
        node: Gsk.RenderNode,
        bounds: Graphene.Rect,
        color: Gdk.RGBA,
        blur: float,
    ) -> None:
        if blur > 0:
            snapshot.push_blur(blur)
        snapshot.push_mask(Gsk.MaskMode.ALPHA)
        snapshot.append_node(node)
        snapshot.pop()
        snapshot.append_color(color, bounds)
        snapshot.pop()
        if blur > 0:
            snapshot.pop()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self._source_node is None:
            source_snapshot = Gtk.Snapshot()
            self.snapshot_child(self.source, source_snapshot)
            self._source_node = source_snapshot.to_node()
        if self._source_node is None:
            self.snapshot_child(self.source, snapshot)
            return
        bounds = Graphene.Rect()
        bounds.init(0, 0, self.get_width(), self.get_height())
        base = self.get_style_context().get_color()
        key = (self.get_width(), self.get_height(), base.red, base.green, base.blue, base.alpha)
        if self._effect_node is None or self._effect_key != key:
            effect_snapshot = Gtk.Snapshot()
            for blur, alpha in self.effect.passes:
                self._append_masked(
                    effect_snapshot,
                    self._source_node,
                    bounds,
                    self._with_alpha(base, alpha),
                    blur,
                )
            self._effect_node = effect_snapshot.to_node()
            self._effect_key = key
        if self._effect_node is not None:
            snapshot.append_node(self._effect_node)
        else:
            self.snapshot_child(self.source, snapshot)

    def do_dispose(self) -> None:
        self._source_node = None
        self._effect_node = None
        self._effect_key = None
        if self.source.get_parent() is self:
            self.source.unparent()
        super().do_dispose()


def app_icon(
    app: ApplicationRecord | None,
    size: int = 22,
    role: str = "primary",
    provider: ApplicationIconProvider | None = None,
    catalog_revision: int = 0,
) -> Gtk.Widget:
    resolution = provider.resolve(app, size, 1, catalog_revision) if provider else None
    if resolution and resolution.themed and resolution.source:
        source = Gtk.Image.new_from_paintable(resolution.source)
        source.set_pixel_size(size)
        return LuminophoreIcon(source, size, role)
    original = resolution.source if resolution else app.icon if app else None
    if isinstance(original, Gdk.Paintable):
        image = Gtk.Image.new_from_paintable(original)
    elif isinstance(original, Gio.Icon):
        image = Gtk.Image.new_from_gicon(original)
    else:
        image = Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
        image.add_css_class(f"luminophore-symbol-{role}")
    image.set_pixel_size(size)
    return image


class LuminophoreStateLayer(Gtk.Widget):
    __gtype_name__ = "LuminophoreStateLayer"

    def __init__(self, radius: int = 8) -> None:
        super().__init__()
        motions = MotionTokens.from_config(180, 600)
        self.radius = max(0, int(radius))
        self._hover = TimedTransition(0.0, motions.hover.duration_ms, motions.hover.easing)
        self._ripple = TimedTransition(1.0, motions.ripple.duration_ms, motions.ripple.easing)
        self._ripple_x = 0.0
        self._ripple_y = 0.0
        self._tick_id = 0
        self.add_css_class("luminophore-symbol-secondary")
        self.set_can_target(False)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.FILL)
        self.set_overflow(Gtk.Overflow.HIDDEN)

    @staticmethod
    def _rgba(color: Gdk.RGBA, alpha: float) -> Gdk.RGBA:
        result = Gdk.RGBA()
        result.red, result.green, result.blue = color.red, color.green, color.blue
        result.alpha = max(0.0, min(1.0, alpha))
        return result

    def _active(self) -> bool:
        return self._hover.active or self._ripple.active

    def _ensure_tick(self) -> None:
        if animation_tick_required(self.get_mapped(), self._hover.active, self._ripple.active) and not self._tick_id:
            self._tick_id = self.add_tick_callback(self._on_tick)

    def set_hovered(self, hovered: bool, now_ms: float) -> None:
        self._hover.retarget(1.0 if hovered else 0.0, now_ms, span=1.0)
        self._ensure_tick()
        self.queue_draw()

    def press(self, x: float, y: float, now_ms: float) -> None:
        self._ripple_x = float(x)
        self._ripple_y = float(y)
        self._ripple.snap(0.0)
        self._ripple.retarget(1.0, now_ms, span=1.0)
        self._ensure_tick()
        self.queue_draw()

    def release(self, now_ms: float) -> None:
        self._ripple.sample(now_ms)
        self._ensure_tick()

    def _on_tick(self, _widget: Gtk.Widget, frame_clock: Gdk.FrameClock) -> bool:
        now_ms = frame_clock.get_frame_time() / 1000
        self._hover.sample(now_ms)
        self._ripple.sample(now_ms)
        self.queue_draw()
        if self._active() and self.get_mapped():
            return True
        self._tick_id = 0
        return False

    def do_map(self) -> None:
        Gtk.Widget.do_map(self)
        self._ensure_tick()

    def do_unmap(self) -> None:
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        self._hover.snap(0.0)
        self._ripple.snap(1.0)
        Gtk.Widget.do_unmap(self)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return
        bounds = Graphene.Rect()
        bounds.init(0, 0, width, height)
        rounded = Gsk.RoundedRect()
        rounded.init_from_rect(bounds, min(self.radius, width / 2, height / 2))
        color = self.get_style_context().get_color()
        snapshot.push_rounded_clip(rounded)
        hover_alpha = 0.13 * max(0.0, min(1.0, self._hover.value))
        if hover_alpha > 0.001:
            snapshot.append_color(self._rgba(color, hover_alpha), bounds)
        progress = max(0.0, min(1.0, self._ripple.value))
        ripple = ripple_sample(progress, width, height, self._ripple_x, self._ripple_y)
        if ripple.visible:
            center = Graphene.Point()
            center.init(self._ripple_x, self._ripple_y)
            stops: list[Gsk.ColorStop] = []
            for offset, factor in ((0.0, 1.0), (0.68, 0.42), (1.0, 0.0)):
                stop = Gsk.ColorStop()
                stop.offset = offset
                stop.color = self._rgba(color, ripple.alpha * factor)
                stops.append(stop)
            snapshot.append_radial_gradient(
                bounds,
                center,
                ripple.radius,
                ripple.radius,
                0.0,
                1.0,
                stops,
            )
        snapshot.pop()

    def do_dispose(self) -> None:
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        super().do_dispose()


def attach_luminophore_state(button: Gtk.Button, radius: int = 8) -> LuminophoreStateLayer | None:
    existing = getattr(button, "_luminophore_state_layer", None)
    if isinstance(existing, LuminophoreStateLayer):
        return existing
    child = button.get_child()
    if child is None:
        return None
    button.set_child(None)
    overlay = Gtk.Overlay()
    overlay.set_child(child)
    layer = LuminophoreStateLayer(radius)
    overlay.add_overlay(layer)
    button.set_child(overlay)

    motion = Gtk.EventControllerMotion()
    motion.connect("enter", lambda _controller, _x, _y: layer.set_hovered(True, _event_time_ms(button)))
    motion.connect("leave", lambda _controller: layer.set_hovered(False, _event_time_ms(button)))
    button.add_controller(motion)

    click = Gtk.GestureClick(button=1)
    click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    click.set_exclusive(False)
    click.connect("pressed", lambda _gesture, _count, x, y: layer.press(x, y, _event_time_ms(button)))
    click.connect("released", lambda _gesture, _count, _x, _y: layer.release(_event_time_ms(button)))
    button.add_controller(click)
    button._luminophore_state_layer = layer  # type: ignore[attr-defined]
    return layer


def _event_time_ms(widget: Gtk.Widget) -> float:
    clock = widget.get_frame_clock()
    return clock.get_frame_time() / 1000 if clock else 0.0
