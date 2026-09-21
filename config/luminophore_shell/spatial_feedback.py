from __future__ import annotations

from dataclasses import dataclass
import json


ACTIONS = frozenset({"focus", "window-move", "view-move", "view-resize", "wide", "desktop"})


@dataclass(frozen=True)
class SpatialFeedback:
    action: str
    applied: bool
    reason: str
    direction: str
    visible: bool
    revision: int
    topology_revision: int
    connector: str
    window: str
    from_point: tuple[int, ...] | None
    to_point: tuple[int, ...] | None
    from_rect: tuple[int, ...] | None
    to_rect: tuple[int, ...] | None
    display_origin: tuple[int, ...]

    def label(self, app_name: str = "") -> str:
        if not self.applied:
            return {
                "invalid-target": "조작할 대상을 찾을 수 없습니다",
                "stale-revision": "공간 상태가 변경되었습니다",
                "stale-topology": "화면 구성이 변경되었습니다",
                "commit-failed": "공간 조작을 적용하지 못했습니다",
            }.get(self.reason, "이 방향으로 더 이동할 수 없습니다")
        if self.action in {"focus", "window-move"} and self.to_point:
            x, y = (self.to_point[i] - self.display_origin[i] for i in range(2))
            verb = "선택" if self.action == "focus" else "이동"
            suffix = " · 화면 밖" if self.action == "window-move" and not self.visible else ""
            return f"{app_name or '창'} · {verb} ({x:+d}, {y:+d}){suffix}"
        if self.action in {"view-move", "view-resize"} and self.to_rect:
            x, y, width, height = self.to_rect
            x, y = x - self.display_origin[0], y - self.display_origin[1]
            return f"{self.connector} · 뷰 ({x:+d}, {y:+d}) · {width}×{height}"
        return "바탕화면 전환" if self.action == "desktop" else "와이드 뷰 전환"


def parse_spatial_feedback(payload: str) -> SpatialFeedback | None:
    try:
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get("schema") != 1 or data.get("action") not in ACTIONS:
            return None
        if data.get("direction") not in {"left", "right", "up", "down"}:
            return None
        if type(data.get("visible")) is not bool or type(data.get("applied")) is not bool:
            return None
        for name in ("revision", "topologyRevision"):
            if type(data.get(name)) is not int or data[name] < 0:
                return None
        for name in ("reason", "connector", "window"):
            if not isinstance(data.get(name), str):
                return None

        def vector(name: str, size: int, optional: bool = True) -> tuple[int, ...] | None:
            value = data.get(name)
            if value is None and optional:
                return None
            if not isinstance(value, list) or len(value) != size or any(type(n) is not int for n in value):
                raise ValueError(name)
            return tuple(value)

        origin = vector("displayOrigin", 2, False)
        assert origin is not None
        return SpatialFeedback(data["action"], data["applied"], data["reason"], data["direction"], data["visible"], data["revision"], data["topologyRevision"],
                               data["connector"], data["window"], vector("fromPoint", 2), vector("toPoint", 2),
                               vector("fromRect", 4), vector("toRect", 4), origin)
    except (ValueError, TypeError):
        return None


class SpatialFeedbackQueue:
    """Keep the latest action per output during one short presentation interval."""

    def __init__(self) -> None:
        self.pending: dict[str, SpatialFeedback] = {}

    def push(self, event: SpatialFeedback) -> None:
        revision = (event.topology_revision, event.revision)
        previous = self.pending.get(event.connector)
        if previous and revision < (previous.topology_revision, previous.revision):
            return
        self.pending[event.connector] = event

    def drain(self) -> tuple[SpatialFeedback, ...]:
        result = tuple(self.pending.values())
        self.pending.clear()
        return result


def parse_fullscreen_feedback(payload: str) -> tuple[str, str, str] | None:
    try:
        data = json.loads(payload)
        if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
            return None
        if any(not isinstance(data.get(key), str) or not data[key] for key in ("connector", "window")):
            return None
        if any(type(data.get(key)) is not bool for key in ("enabled", "applied")):
            return None
        label = ("전체화면" if data["enabled"] else "전체화면 해제") if data["applied"] else "전체화면 전환을 적용하지 못했습니다"
        return data["connector"], data["window"], label
    except (ValueError, TypeError):
        return None
