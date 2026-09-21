from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class HyprlandControl:
    option_id: str
    label: str
    value: Any
    available: bool = True
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.option_id or not self.label:
            raise ValueError("Hyprland control requires an id and label")


@dataclass(frozen=True, slots=True)
class HyprlandPageState:
    controls: tuple[HyprlandControl, ...]
    online: bool
    busy: bool = False
    conflict: bool = False

    @classmethod
    def build(
        cls,
        values: Mapping[str, Any],
        labels: Mapping[str, str],
        *,
        unavailable: Iterable[str] = (),
        online: bool,
        busy: bool = False,
        conflict: bool = False,
    ) -> "HyprlandPageState":
        missing = frozenset(unavailable)
        controls = tuple(
            HyprlandControl(option, labels.get(option, option), value, option not in missing,
                            "현재 compositor에서 지원하지 않습니다" if option in missing else "")
            for option, value in sorted(values.items())
        )
        return cls(controls, online, busy, conflict)

    def enabled(self, option_id: str) -> bool:
        control = next(item for item in self.controls if item.option_id == option_id)
        return self.online and not self.busy and not self.conflict and control.available

    @property
    def banner(self) -> str:
        if self.conflict:
            return "설정이 외부에서 변경되었습니다. 다시 불러오세요"
        if self.busy:
            return "설정을 적용하는 중입니다"
        if not self.online:
            return "Shell이 꺼져 있어 변경 사항은 다음 시작 시 적용됩니다"
        return ""

