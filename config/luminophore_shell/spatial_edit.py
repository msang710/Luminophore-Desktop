from __future__ import annotations

from dataclasses import dataclass
import re
import json


@dataclass(frozen=True)
class SpatialEditRequest:
    action: str
    revision: int
    topology_revision: int
    output_id: int
    x: int
    y: int
    window: str = ""
    columns: int = 0
    rows: int = 0

    def arguments(self) -> tuple[str, ...]:
        if self.action not in {"move-window", "move-view", "resize-view"}:
            raise ValueError("unknown spatial edit action")
        for value in (self.revision, self.topology_revision, self.output_id, self.x, self.y, self.columns, self.rows):
            if type(value) is not int:
                raise ValueError("spatial edit fields must be integers")
        if self.revision < 0 or self.topology_revision < 0 or self.output_id <= 0:
            raise ValueError("invalid spatial edit revision or output")
        if not all(-(2**63) <= value < 2**63 for value in (self.x,self.y)):
            raise ValueError("coordinate overflow")
        arguments = [self.action, str(self.revision), str(self.topology_revision), str(self.output_id)]
        if self.action == "move-window":
            if not re.fullmatch(r"0x[0-9a-fA-F]+", self.window) or int(self.window, 16) == 0:
                raise ValueError("invalid spatial window address")
            arguments.append(self.window)
        arguments.extend((str(self.x), str(self.y)))
        if self.action == "resize-view":
            arguments.extend((str(self.columns), str(self.rows)))
        return tuple(arguments)

@dataclass(frozen=True)
class SpatialEditPreview:
    status: str
    revision: int
    topology_revision: int
    windows: tuple[tuple[str, int, int, str], ...]
    output_views: tuple[tuple[int, int, int, int, int], ...]
    boards: tuple[tuple[str, int], ...] = ()

    @property
    def accepted(self) -> bool:
        return self.status in {"applied", "no-change"}


def parse_edit_preview(data: object) -> SpatialEditPreview:
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("invalid spatial preview schema")
    status = data.get("status")
    if status not in {"applied", "no-change", "stale-revision", "stale-topology", "invalid-command", "no-capacity", "commit-failed"}:
        raise ValueError("invalid spatial preview status")
    for name in ("revision", "topologyRevision"):
        if type(data.get(name)) is not int or data[name] < 0:
            raise ValueError("invalid spatial preview revision")
    if not isinstance(data.get("windows"), list) or not isinstance(data.get("outputViews"), list):
        raise ValueError("invalid spatial preview arrays")
    windows = []
    occupied = set()
    for window in data["windows"]:
        if not isinstance(window, dict) or not isinstance(window.get("address"), str) or window.get("mode") not in {"tiled", "floating"}:
            raise ValueError("invalid spatial preview window")
        if any(type(window.get(name)) is not int or not -(2**63) <= window[name] < 2**63 for name in ("x", "y")):
            raise ValueError("invalid spatial preview coordinate")
        if "board" in window and (type(window["board"]) is not int or not 0 <= window["board"] < 2**64):
            raise ValueError("invalid spatial preview board")
        if window.get("board", 0) > 0:
            point = (window["board"], window["x"], window["y"])
            if point in occupied:
                raise ValueError("duplicate spatial preview coordinate")
            occupied.add(point)
        windows.append((window["address"], window["x"], window["y"], window["mode"]))
    views = []
    for view in data["outputViews"]:
        if not isinstance(view, dict) or any(type(view.get(name)) is not int for name in ("output", "x", "y", "columns", "rows")):
            raise ValueError("invalid spatial preview output view")
        views.append((view["output"], view["x"], view["y"], view["columns"], view["rows"]))
    return SpatialEditPreview(status, data["revision"], data["topologyRevision"], tuple(windows), tuple(views),tuple((w["address"],int(w.get("board",0))) for w in data["windows"]))


@dataclass(frozen=True)
class SpatialGrabLayout:
    generation: int
    revision: int
    topology_revision: int
    frame_generation: str
    frame_revision: int
    # Logical column/row followed by window-local x/y/width/height.
    cells: tuple[tuple[int, int, float, float, float, float], ...]
    target_output_id: int = 0
    target_epoch: int = 0

    def payload(self) -> dict:
        import math
        identifiers = (self.generation, self.revision, self.topology_revision, self.frame_revision, self.target_epoch)
        if any(type(value) is not int or not 0 <= value < 2**64 for value in identifiers) or not self.generation or not self.frame_revision:
            raise ValueError("invalid grab layout identity")
        if not isinstance(self.frame_generation, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", self.frame_generation):
            raise ValueError("invalid grab layout frame")
        if len(self.cells) > 4096:
            raise ValueError("too many grab layout cells")
        if type(self.target_output_id) is not int or not 0 <= self.target_output_id < 2**64:
            raise ValueError("invalid board output")
        cells = []
        seen = set()
        for cell in self.cells:
            if len(cell) != 6 or any(type(value) is not int or not -(2**63) <= value < 2**63 for value in cell[:2]):
                raise ValueError("invalid grab layout cell coordinate")
            if cell[:2] in seen:
                raise ValueError("duplicate grab layout cell")
            seen.add(cell[:2])
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in cell[2:]):
                raise ValueError("invalid grab layout cell rectangle")
            if cell[4] <= 0 or cell[5] <= 0:
                raise ValueError('invalid grab layout rectangle extent')
            cells.append(dict(zip(('column', 'row', 'x', 'y', 'width', 'height'), cell), output=str(self.target_output_id)))
        return dict(generation=str(self.generation), revision=str(self.revision), topology=str(self.topology_revision),
                    frame_generation=self.frame_generation, frame_revision=str(self.frame_revision),
                    target_epoch=str(self.target_epoch), cells=cells)
