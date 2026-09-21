from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


def smoothstep(value: float) -> float:
    bounded = max(0.0, min(1.0, value))
    return bounded * bounded * (3.0 - 2.0 * bounded)


def ease_out_cubic(value: float) -> float:
    bounded = max(0.0, min(1.0, value))
    return 1.0 - (1.0 - bounded) ** 3


def ease_in_cubic(value: float) -> float:
    bounded = max(0.0, min(1.0, value))
    return bounded ** 3


@dataclass(frozen=True)
class MotionSpec:
    duration_ms: int
    easing: Callable[[float], float] = smoothstep


@dataclass(frozen=True)
class MotionTokens:
    expand: MotionSpec
    collapse: MotionSpec
    palette: MotionSpec
    hover: MotionSpec
    ripple: MotionSpec

    @classmethod
    def from_config(cls, expansion_ms: int, palette_transition_ms: int) -> "MotionTokens":
        expansion = max(0, expansion_ms)
        return cls(
            expand=MotionSpec(expansion, smoothstep),
            collapse=MotionSpec(max(0, round(expansion * 0.90)), smoothstep),
            palette=MotionSpec(max(0, palette_transition_ms), smoothstep),
            hover=MotionSpec(180, ease_out_cubic),
            ripple=MotionSpec(320, smoothstep),
        )


@dataclass(frozen=True)
class TransitionSample:
    value: float
    active: bool


@dataclass(frozen=True)
class VanishingSample:
    """One frame of the shared focus-out disappearance motion."""

    scale: float
    opacity: float
    backdrop_alpha: float
    active: bool


@dataclass(frozen=True)
class OverviewRevealSample:
    opacity: float
    offset_x: float
    offset_y: float
    active: bool


class OverviewRevealTransition:
    """Reversible whole-surface reveal driven from one monotonic timeline."""

    def __init__(self, hidden_offset_x: float, hidden_offset_y: float) -> None:
        self.hidden_offset_x = float(hidden_offset_x)
        self.hidden_offset_y = float(hidden_offset_y)
        self._visibility = TimedTransition(1.0, 160, ease_out_cubic)

    @property
    def active(self) -> bool:
        return self._visibility.active

    @property
    def value(self) -> float:
        return self._visibility.value

    def snap(self, visible: bool) -> None:
        self._visibility.snap(1.0 if visible else 0.0)

    def retarget(
        self,
        visible: bool,
        now_ms: float,
        started_ms: float,
        duration_ms: int,
        easing: Callable[[float], float],
    ) -> bool:
        self._visibility.sample(now_ms)
        target = 1.0 if visible else 0.0
        if abs(target - self._visibility.value) < 0.001:
            self._visibility.snap(target)
            return False
        self._visibility.start_value = self._visibility.value
        self._visibility.target_value = target
        self._visibility.started_ms = started_ms
        self._visibility.running_ms = max(0, duration_ms) * abs(target - self._visibility.value)
        self._visibility._running_easing = easing
        self._visibility.active = self._visibility.running_ms > 0
        if not self._visibility.active:
            self._visibility.snap(target)
        return self._visibility.active

    def command(self) -> tuple[float, float, float, float, float, float]:
        return (
            self._visibility.start_value,
            self._visibility.target_value,
            self._visibility.started_ms / 1000.0,
            self._visibility.running_ms / 1000.0,
            self.hidden_offset_x,
            self.hidden_offset_y,
        )

    def sample(self, now_ms: float) -> OverviewRevealSample:
        sampled = self._visibility.sample(now_ms)
        opacity = max(0.0, min(1.0, sampled.value))
        hidden = 1.0 - opacity
        return OverviewRevealSample(
            opacity,
            self.hidden_offset_x * hidden,
            self.hidden_offset_y * hidden,
            sampled.active,
        )


class TimedTransition:
    """A deterministic scalar transition that can be retargeted mid-flight."""

    def __init__(self, value: float, duration_ms: int, easing: Callable[[float], float] = smoothstep) -> None:
        self.duration_ms = max(0, duration_ms)
        self.easing = easing
        self._running_easing = easing
        self.value = float(value)
        self.start_value = float(value)
        self.target_value = float(value)
        self.started_ms = 0.0
        self.running_ms = 0.0
        self.active = False

    def sample(self, now_ms: float) -> TransitionSample:
        if not self.active:
            return TransitionSample(self.value, False)
        if self.running_ms <= 0:
            self.snap(self.target_value)
            return TransitionSample(self.value, False)
        fraction = max(0.0, min(1.0, (now_ms - self.started_ms) / self.running_ms))
        eased = self._running_easing(fraction)
        self.value = self.start_value + (self.target_value - self.start_value) * eased
        if fraction >= 1.0:
            self.snap(self.target_value)
        return TransitionSample(self.value, self.active)

    def retarget(self, target: float, now_ms: float, span: float | None = None) -> bool:
        self.sample(now_ms)
        target = float(target)
        if abs(target - self.value) < 0.001:
            self.snap(target)
            return False
        self.start_value = self.value
        self.target_value = target
        self.started_ms = now_ms
        self._running_easing = self.easing
        if span is None or span <= 0:
            factor = 1.0
        else:
            factor = min(1.0, abs(target - self.value) / span)
        self.running_ms = self.duration_ms * factor
        if self.running_ms <= 0:
            self.snap(target)
            return False
        self.active = True
        return True

    def set_motion(self, spec: MotionSpec) -> None:
        self.duration_ms = max(0, spec.duration_ms)
        self.easing = spec.easing

    def snap(self, value: float) -> None:
        self.value = float(value)
        self.start_value = self.value
        self.target_value = self.value
        self.running_ms = 0.0
        self.active = False


class VanishingTransition:
    """Deterministic top-pivot collapse shared by Shell and Greeter views."""

    def __init__(self, expansion_ms: int = 200) -> None:
        self.motion = MotionTokens.from_config(expansion_ms, 0).collapse
        self._visibility = TimedTransition(1.0, self.motion.duration_ms, self.motion.easing)

    @property
    def duration_ms(self) -> int:
        return self.motion.duration_ms

    @property
    def active(self) -> bool:
        return self._visibility.active

    def begin(self, now_ms: float) -> bool:
        self._visibility.set_motion(self.motion)
        return self._visibility.retarget(0.0, now_ms, span=1.0)

    def reset(self) -> None:
        self._visibility.snap(1.0)

    def sample(self, now_ms: float) -> VanishingSample:
        sampled = self._visibility.sample(now_ms)
        value = max(0.0, min(1.0, sampled.value))
        return VanishingSample(value, value, value, sampled.active)


# Shared overview/editor arrival cadence.
OVERVIEW_ENTER_MS = 320
OVERVIEW_EXIT_MS = 240
