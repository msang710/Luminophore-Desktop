from __future__ import annotations

from dataclasses import dataclass, replace
import time
from typing import Callable

from .hyprland import HyprlandClient, WindowRecord, WorkspaceRef, normalize_address


@dataclass(frozen=True)
class MinimizedWindow:
    address: str
    app_class: str
    title: str
    monitor_name: str
    workspace: WorkspaceRef
    floating: bool
    at: tuple[int, int]
    size: tuple[int, int]
    minimized_at: float

    @classmethod
    def from_window(cls, window: WindowRecord) -> "MinimizedWindow":
        return cls(
            address=window.address,
            app_class=window.initial_class or window.app_class,
            title=window.title,
            monitor_name=window.monitor_name,
            workspace=window.workspace,
            floating=window.floating,
            at=window.at,
            size=window.size,
            minimized_at=time.time(),
        )

@dataclass(frozen=True)
class RestoreResult:
    restored: bool
    waiting_for_monitor: bool = False
    reason: str = ""


class MinimizeController:
    def __init__(
        self,
        client: HyprlandClient,
        changed: Callable[[], None] | None = None,
    ) -> None:
        self.client = client
        self.changed = changed or (lambda: None)
        self.records: dict[str, MinimizedWindow] = {}

    def restore(self, address: str) -> RestoreResult:
        normalized = normalize_address(address)
        record = self.records.get(normalized)
        if not record:
            return RestoreResult(False, reason="not minimized")
        connected = {monitor.name for monitor in self.client.monitors()}
        if record.monitor_name not in connected:
            return RestoreResult(False, waiting_for_monitor=True, reason="연결 대기")
        self.client.restore(record)
        self.records.pop(normalized, None)
        self.changed()
        return RestoreResult(True)

    def reconcile(self, windows: list[WindowRecord]) -> None:
        previous = self.records
        self.records = {
            window.address: MinimizedWindow.from_window(window)
            for window in windows
            if window.minimized
        }
        for address, record in tuple(self.records.items()):
            if address in previous:
                self.records[address] = replace(record, minimized_at=previous[address].minimized_at)
        if previous != self.records:
            self.changed()

    def ordered(self) -> list[MinimizedWindow]:
        return sorted(self.records.values(), key=lambda item: item.minimized_at, reverse=True)
