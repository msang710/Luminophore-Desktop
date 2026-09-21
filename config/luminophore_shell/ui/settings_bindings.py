from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


def normalize_chord(chord: str) -> str:
    parts = [part.strip().upper() for part in chord.split("+") if part.strip()]
    modifiers = sorted(part for part in parts if part in {"ALT", "CTRL", "SHIFT", "SUPER"})
    keys = [part for part in parts if part not in {"ALT", "CTRL", "SHIFT", "SUPER"}]
    if len(keys) != 1:
        raise ValueError("binding chord must contain exactly one key")
    return "+".join((*modifiers, keys[0]))


@dataclass(frozen=True, slots=True)
class BindingRow:
    action_id: str
    label: str
    chord: str
    recovery: bool = False

    def __post_init__(self) -> None:
        if not self.action_id or not self.label:
            raise ValueError("binding requires an action id and label")
        object.__setattr__(self, "chord", normalize_chord(self.chord) if self.chord else "")


@dataclass(frozen=True, slots=True)
class BindingPageState:
    rows: tuple[BindingRow, ...]
    collisions: tuple[tuple[str, tuple[str, ...]], ...]
    recovery_available: bool

    @classmethod
    def build(cls, rows: Iterable[BindingRow]) -> "BindingPageState":
        ordered = tuple(sorted(rows, key=lambda item: item.action_id))
        if len({item.action_id for item in ordered}) != len(ordered):
            raise ValueError("binding action ids must be unique")
        by_chord: dict[str, list[str]] = {}
        for row in ordered:
            if row.chord:
                by_chord.setdefault(row.chord, []).append(row.action_id)
        collisions = tuple(
            (chord, tuple(actions))
            for chord, actions in sorted(by_chord.items())
            if len(actions) > 1
        )
        recovery_available = any(row.recovery and row.chord for row in ordered)
        return cls(ordered, collisions, recovery_available)

    @property
    def can_apply(self) -> bool:
        return not self.collisions and self.recovery_available

    @property
    def error_text(self) -> str:
        if self.collisions:
            return "같은 키 조합이 여러 동작에 지정되었습니다"
        if not self.recovery_available:
            return "설정 또는 터미널 복구 동작을 하나 이상 유지해야 합니다"
        return ""

