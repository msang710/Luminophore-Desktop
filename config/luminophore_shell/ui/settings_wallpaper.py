from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from ..appearance_types import AppearanceErrorCategory, TargetCapability


class WallpaperUiPhase(StrEnum):
    IDLE = "idle"
    PREVIEWING = "previewing"
    READY = "ready"
    APPLYING = "applying"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class MonitorCard:
    connector: str
    display_name: str
    source_name: str = ""
    generation_id: str = ""

    def __post_init__(self) -> None:
        if not self.connector:
            raise ValueError("monitor connector must not be empty")
        if "/" in self.source_name or "\\" in self.source_name:
            raise ValueError("source_name must not expose a filesystem path")

    @classmethod
    def from_source_path(cls, connector: str, source: str, generation_id: str = "") -> "MonitorCard":
        return cls(connector, connector, Path(source).name, generation_id)


@dataclass(frozen=True, slots=True)
class WallpaperPageState:
    monitors: tuple[MonitorCard, ...] = ()
    targets: tuple[TargetCapability, ...] = ()
    phase: WallpaperUiPhase = WallpaperUiPhase.IDLE
    error: AppearanceErrorCategory | None = None
    message: str = ""
    online: bool = True

    @classmethod
    def build(
        cls,
        monitors: Iterable[MonitorCard],
        *,
        targets: Iterable[TargetCapability] = (),
        phase: WallpaperUiPhase = WallpaperUiPhase.IDLE,
        error: AppearanceErrorCategory | None = None,
        message: str = "",
        online: bool = True,
    ) -> "WallpaperPageState":
        ordered = tuple(sorted(monitors, key=lambda item: item.connector))
        if len({item.connector for item in ordered}) != len(ordered):
            raise ValueError("monitor connectors must be unique")
        return cls(ordered, tuple(sorted(targets, key=lambda item: item.target_id)), phase, error, message, online)

    @property
    def can_preview(self) -> bool:
        return self.online and bool(self.monitors) and self.phase not in {WallpaperUiPhase.PREVIEWING, WallpaperUiPhase.APPLYING}

    @property
    def can_apply(self) -> bool:
        return self.online and bool(self.monitors) and self.phase is WallpaperUiPhase.READY and self.error is None

    @property
    def system_theme_available(self) -> bool:
        return self.online and any(target.available for target in self.targets)

    @property
    def status_text(self) -> str:
        if not self.online:
            return "Shell이 실행 중일 때 배경화면과 테마를 적용할 수 있습니다"
        labels = {
            AppearanceErrorCategory.BUSY: "다른 외형 변경을 적용하는 중입니다",
            AppearanceErrorCategory.STALE_PREVIEW: "미리보기가 오래되었습니다. 다시 생성하세요",
            AppearanceErrorCategory.PROVIDER_MISSING: "선택한 배경화면 provider를 사용할 수 없습니다",
            AppearanceErrorCategory.PROVIDER_AMBIGUOUS: "배경화면 provider 상태가 모호합니다",
            AppearanceErrorCategory.COMPILER_MISSING: "Matugen compiler를 사용할 수 없습니다",
            AppearanceErrorCategory.COMPILE_TIMEOUT: "테마 색상 생성 시간이 초과되었습니다",
            AppearanceErrorCategory.APPLY_FAILED_ROLLED_BACK: "적용에 실패해 이전 상태로 복구했습니다",
            AppearanceErrorCategory.ROLLBACK_FAILED: "적용과 자동 복구가 모두 실패했습니다",
            AppearanceErrorCategory.COMPLETION_UNKNOWN: "적용 완료 여부를 확인하는 중입니다",
        }
        return labels.get(self.error, self.message)

