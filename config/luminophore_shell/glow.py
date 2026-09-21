from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import math
from typing import Any


GLOW_SLOW_PERIOD_SECONDS = 24.0
GLOW_DRIFT_PERIOD_SECONDS = 41.0
GLOW_RADIUS_PERIOD_SECONDS = 31.0
GLOW_INTENSITY_FLOOR = 0.64
GLOW_SLOW_AMPLITUDE = 0.24
GLOW_DRIFT_AMPLITUDE = 0.10
GLOW_RADIUS_AMPLITUDE = 0.14
GLOW_TIME_WRAP_SECONDS = 30_504.0
GPU_TIMELINE_MAX_DELTA_SECONDS = 0.050
CORE_ENERGY_FACTOR = 0.16
NEAR_ENERGY_FACTOR = 0.18
BLOOM_ENERGY_FACTOR = 0.035


@dataclass(frozen=True)
class GlowGeometryInput:
    panel_x: int
    panel_y: int
    panel_width: int
    panel_height: int
    envelope_x: int
    envelope_y: int
    envelope_width: int
    envelope_height: int
    revision: int


@dataclass(frozen=True)
class GlowRenderFrame:
    width: float
    height: float
    corner_radius: float
    outline_width: float
    visible_extent: float
    core_energy: float
    near_energy: float
    bloom_energy: float
    elapsed_seconds: float
    phase: float
    base_color: tuple[float, float, float]
    core_color: tuple[float, float, float]
    edge_budget: tuple[float, float, float, float] = (math.inf,) * 4


@dataclass(frozen=True)
class GlowEdgeBudget:
    """Visible distance from a panel outline to each viewport edge."""

    left: float = math.inf
    top: float = math.inf
    right: float = math.inf
    bottom: float = math.inf

    @classmethod
    def from_bounds(
        cls,
        viewport_width: float,
        viewport_height: float,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> "GlowEdgeBudget":
        return cls(
            max(0.0, float(x)),
            max(0.0, float(y)),
            max(0.0, float(viewport_width) - float(x) - float(width)),
            max(0.0, float(viewport_height) - float(y) - float(height)),
        )

    def as_tuple(self) -> tuple[float, float, float, float]:
        return self.left, self.top, self.right, self.bottom

    def constrained(self, extent: float) -> tuple[str, ...]:
        radius = max(0.0, float(extent))
        return tuple(
            side
            for side, budget in zip(("left", "top", "right", "bottom"), self.as_tuple(), strict=True)
            if budget < radius
        )


@dataclass(frozen=True)
class GlowPass:
    blur_radius: float
    alpha: float


@dataclass(frozen=True)
class FrameProfile:
    outline_width: float
    corner_radius: float
    bloom_passes: tuple[GlowPass, ...]


@dataclass(frozen=True)
class GlowDynamics:
    intensity_factor: float
    radius_factor: float


@dataclass
class GpuFrameTimeline:
    """Bound frame-clock recovery while keeping shader time phase-stable."""

    last_frame_us: int | None = None
    elapsed_seconds: float = 0.0
    last_delta_seconds: float = 0.0

    def reset(self) -> None:
        self.last_frame_us = None
        self.elapsed_seconds = 0.0
        self.last_delta_seconds = 0.0

    def advance(self, frame_time_us: int | float) -> float:
        if not math.isfinite(frame_time_us):
            self.last_delta_seconds = 0.0
            return self.elapsed_seconds
        current = int(frame_time_us)
        if self.last_frame_us is None:
            self.last_frame_us = current
            self.last_delta_seconds = 0.0
            return self.elapsed_seconds
        delta = (current - self.last_frame_us) / 1_000_000.0
        if not math.isfinite(delta) or delta <= 0.0:
            self.last_delta_seconds = 0.0
            return self.elapsed_seconds
        self.last_frame_us = current
        self.last_delta_seconds = min(delta, GPU_TIMELINE_MAX_DELTA_SECONDS)
        self.elapsed_seconds = (
            self.elapsed_seconds + self.last_delta_seconds
        ) % GLOW_TIME_WRAP_SECONDS
        return self.elapsed_seconds


@dataclass(frozen=True)
class FrameTimingSnapshot:
    sample_count: int
    refresh_interval_us: int
    p50_us: float
    p95_us: float
    p99_us: float
    missed_frame_ratio: float
    max_interval_us: int = 0
    severe_stall_count: int = 0
    max_consecutive_missed: int = 0
    presentation_sample_count: int = 0
    presentation_p95_us: float = 0.0
    presentation_max_us: int = 0
    event_counts: dict[str, int] | None = None
    event_duration_us: dict[str, float] | None = None
    recent_stalls: tuple[dict[str, Any], ...] = ()


class FrameTimingProbe:
    """Opt-in bounded timing probe; disabled mode has no sample storage."""

    def __init__(self, enabled: bool = False, capacity: int = 1024) -> None:
        self.capacity = max(1, int(capacity))
        self.enabled = False
        self._intervals: deque[int] | None = None
        self._presentation_intervals: deque[int] | None = None
        self._recent_stalls: deque[dict[str, Any]] | None = None
        self._event_counts: dict[str, int] | None = None
        self._event_duration_ns: dict[str, int] | None = None
        self._pending_counts: dict[str, int] | None = None
        self._pending_duration_ns: dict[str, int] | None = None
        self._last_frame_us: int | None = None
        self._last_presentation_us: int | None = None
        self._refresh_interval_us = 0
        self.configure(enabled)

    def configure(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        if self.enabled:
            self._intervals = deque(maxlen=self.capacity)
            self._presentation_intervals = deque(maxlen=self.capacity)
            self._recent_stalls = deque(maxlen=16)
            self._event_counts = {}
            self._event_duration_ns = {}
            self._pending_counts = {}
            self._pending_duration_ns = {}
        else:
            self._intervals = None
            self._presentation_intervals = None
            self._recent_stalls = None
            self._event_counts = None
            self._event_duration_ns = None
            self._pending_counts = None
            self._pending_duration_ns = None
        self._last_frame_us = None
        self._last_presentation_us = None
        self._refresh_interval_us = 0

    def reset(self) -> None:
        self._last_frame_us = None
        self._last_presentation_us = None
        if self._intervals is not None:
            self._intervals.clear()
        if self._presentation_intervals is not None:
            self._presentation_intervals.clear()
        if self._recent_stalls is not None:
            self._recent_stalls.clear()
        for values in (
            self._event_counts,
            self._event_duration_ns,
            self._pending_counts,
            self._pending_duration_ns,
        ):
            if values is not None:
                values.clear()

    def event(self, name: str, duration_ns: int = 0, **details: int | float | bool | str) -> None:
        if self._event_counts is None or self._pending_counts is None:
            return
        self._event_counts[name] = self._event_counts.get(name, 0) + 1
        self._pending_counts[name] = self._pending_counts.get(name, 0) + 1
        bounded_duration = max(0, int(duration_ns))
        if bounded_duration and self._event_duration_ns is not None and self._pending_duration_ns is not None:
            self._event_duration_ns[name] = self._event_duration_ns.get(name, 0) + bounded_duration
            self._pending_duration_ns[name] = self._pending_duration_ns.get(name, 0) + bounded_duration
        for key, value in details.items():
            detail_name = f"{name}.{key}.{value}"
            self._pending_counts[detail_name] = self._pending_counts.get(detail_name, 0) + 1

    def record_presentation(self, presentation_time_us: int) -> None:
        if self._presentation_intervals is None or presentation_time_us <= 0:
            return
        current = int(presentation_time_us)
        if self._last_presentation_us is not None:
            interval = current - self._last_presentation_us
            if interval > 0:
                self._presentation_intervals.append(interval)
        self._last_presentation_us = current

    def record(self, frame_time_us: int, refresh_interval_us: int) -> None:
        if self._intervals is None:
            return
        if refresh_interval_us > 0:
            self._refresh_interval_us = int(refresh_interval_us)
        if self._last_frame_us is not None:
            interval = int(frame_time_us) - self._last_frame_us
            if interval > 0:
                self._intervals.append(interval)
                if (
                    self._recent_stalls is not None
                    and refresh_interval_us > 0
                    and interval > refresh_interval_us * 1.5
                ):
                    self._recent_stalls.append({
                        "frame_time_us": int(frame_time_us),
                        "interval_us": interval,
                        "refresh_interval_us": int(refresh_interval_us),
                        "severity": "severe" if interval > refresh_interval_us * 2.5 else "missed",
                        "events": dict(self._pending_counts or {}),
                        "event_duration_us": {
                            name: round(duration / 1000.0, 3)
                            for name, duration in (self._pending_duration_ns or {}).items()
                        },
                    })
                if self._pending_counts is not None:
                    self._pending_counts.clear()
                if self._pending_duration_ns is not None:
                    self._pending_duration_ns.clear()
        self._last_frame_us = int(frame_time_us)

    @staticmethod
    def _percentile(sorted_values: list[int], fraction: float) -> float:
        if not sorted_values:
            return 0.0
        position = (len(sorted_values) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return float(sorted_values[lower])
        weight = position - lower
        return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight

    def snapshot(self) -> FrameTimingSnapshot:
        values = sorted(self._intervals) if self._intervals is not None else []
        presentation_values = (
            sorted(self._presentation_intervals)
            if self._presentation_intervals is not None else []
        )
        refresh = self._refresh_interval_us
        missed = (
            sum(value > refresh * 1.5 for value in values) / len(values)
            if values and refresh > 0 else 0.0
        )
        consecutive = 0
        max_consecutive = 0
        if refresh > 0:
            for value in self._intervals or ():
                consecutive = consecutive + 1 if value > refresh * 1.5 else 0
                max_consecutive = max(max_consecutive, consecutive)
        return FrameTimingSnapshot(
            sample_count=len(values),
            refresh_interval_us=refresh,
            p50_us=self._percentile(values, 0.50),
            p95_us=self._percentile(values, 0.95),
            p99_us=self._percentile(values, 0.99),
            missed_frame_ratio=missed,
            max_interval_us=max(values, default=0),
            severe_stall_count=(
                sum(value > refresh * 2.5 for value in values) if refresh > 0 else 0
            ),
            max_consecutive_missed=max_consecutive,
            presentation_sample_count=len(presentation_values),
            presentation_p95_us=self._percentile(presentation_values, 0.95),
            presentation_max_us=max(presentation_values, default=0),
            event_counts=dict(self._event_counts or {}),
            event_duration_us={
                name: round(duration / 1000.0, 3)
                for name, duration in (self._event_duration_ns or {}).items()
            },
            recent_stalls=tuple(list(self._recent_stalls or ())[-8:]),
        )


@dataclass
class GlowFrameCadence:
    frames_until_draw: int = 0
    high_motion: bool = False

    def reset(self) -> None:
        self.frames_until_draw = 0
        self.high_motion = False

    def should_draw(self, refresh_interval_us: int, high_motion: bool) -> bool:
        if high_motion != self.high_motion:
            self.frames_until_draw = 0
        self.high_motion = high_motion
        if self.frames_until_draw > 0:
            self.frames_until_draw -= 1
            return False
        self.frames_until_draw = glow_frame_stride(refresh_interval_us, high_motion) - 1
        return True


def glow_frame_stride(
    refresh_interval_us: int,
    high_motion: bool,
    target_ambient_hz: float = 72.0,
) -> int:
    if high_motion or refresh_interval_us <= 0:
        return 1
    if not math.isfinite(target_ambient_hz) or target_ambient_hz <= 0:
        return 1
    refresh_hz = 1_000_000.0 / refresh_interval_us
    if not math.isfinite(refresh_hz) or refresh_hz <= 0:
        return 1
    return max(1, round(refresh_hz / target_ambient_hz))


def frame_profile(
    radius: float,
    intensity: float,
    outline_width: float,
    corner_radius: float,
    elapsed_seconds: float,
    phase: float = 0.0,
) -> FrameProfile:
    return FrameProfile(
        outline_width=max(0.0, outline_width),
        corner_radius=max(0.0, corner_radius),
        bloom_passes=glow_falloff(radius, intensity, elapsed_seconds, phase),
    )


def parse_hex_rgb(value: str) -> tuple[float, float, float]:
    normalized = value.removeprefix("#")
    if len(normalized) != 6:
        raise ValueError("glow color must use #RRGGBB")
    try:
        channels = tuple(int(normalized[index : index + 2], 16) / 255 for index in (0, 2, 4))
    except ValueError as exc:
        raise ValueError("glow color must use #RRGGBB") from exc
    return channels  # type: ignore[return-value]


def glow_falloff(
    radius: float,
    intensity: float,
    elapsed_seconds: float,
    phase: float = 0.0,
) -> tuple[GlowPass, ...]:
    """Return wide-to-near Gaussian bloom passes for one animation frame."""
    if intensity <= 0:
        return ()

    dynamics = glow_dynamics(elapsed_seconds, phase)
    bloom = max(1.0, radius * dynamics.radius_factor)
    strength = max(0.0, intensity * dynamics.intensity_factor)
    def bloom_pass(radius_factor: float, alpha_factor: float, alpha_limit: float) -> GlowPass:
        return GlowPass(
            blur_radius=max(1.0, bloom * radius_factor),
            alpha=min(alpha_limit, strength * alpha_factor),
        )

    # Both passes blur the same rounded-outline alpha mask. Their Gaussian
    # curves and their sum remain continuous; the direct border becomes the
    # bright tube instead of a stack of small, discrete shadow layers.
    return (
        bloom_pass(1.35, 0.035, 0.14),
        bloom_pass(0.34, 0.18, 0.52),
    )


def glow_dynamics(elapsed_seconds: float, phase: float = 0.0) -> GlowDynamics:
    elapsed = float(elapsed_seconds) % GLOW_TIME_WRAP_SECONDS
    slow = math.sin(math.tau * (elapsed / GLOW_SLOW_PERIOD_SECONDS + phase))
    drift = math.sin(
        math.tau * (elapsed / GLOW_DRIFT_PERIOD_SECONDS + phase * 1.71) + 0.83
    )
    radius_wave = math.sin(
        math.tau * (elapsed / GLOW_RADIUS_PERIOD_SECONDS + phase) + 1.19
    )
    return GlowDynamics(
        intensity_factor=max(
            GLOW_INTENSITY_FLOOR,
            1.0 + GLOW_SLOW_AMPLITUDE * slow + GLOW_DRIFT_AMPLITUDE * drift,
        ),
        radius_factor=1.0 + GLOW_RADIUS_AMPLITUDE * radius_wave,
    )


def surface_glow_phase(name: str) -> float:
    known = {
        "launcher": 0.03,
        "notifications": 0.29,
        "weather": 0.56,
        "system": 0.81,
    }
    if name in known:
        return known[name]
    checksum = sum((index + 1) * ord(character) for index, character in enumerate(name))
    return (checksum % 1000) / 1000
