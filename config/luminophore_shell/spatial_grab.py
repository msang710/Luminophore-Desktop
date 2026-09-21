from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re

from .spatial_edit import SpatialEditPreview, parse_edit_preview


@dataclass(frozen=True)
class SpatialGrabEvent:
    source: str
    phase: str
    generation: int
    revision: int
    topology_revision: int
    output_id: int
    window: str
    floating: bool
    result: SpatialEditPreview | None
    pointer: tuple[float,float] | None = None
    target_output_id: int | None = None
    target_epoch: int = 0
    editor_origin: bool = False
    settled_revision: int | None = None
    settled_topology_revision: int | None = None
    settled_committed: bool | None = None


def parse_spatial_grab(payload: str) -> SpatialGrabEvent | None:
    try:
        data = json.loads(payload)
        if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] not in (1, 2):
            return None
        if data.get("phase") not in {"begin", "update", "end", "cancel"}:
            return None
        source = data.get("source")
        if not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", source):
            return None
        for name in ("generation", "revision", "topologyRevision", "output"):
            if type(data.get(name)) is not int or not 0 <= data[name] < 2**64:
                return None
        if not data["generation"] or not data["output"] or type(data.get("floating")) is not bool:
            return None
        window = data.get("window")
        if not isinstance(window, str) or not re.fullmatch(r"0x[0-9a-fA-F]{1,16}", window) or int(window, 16) == 0:
            return None
        pointer=data.get("pointer")
        if pointer is not None and (not isinstance(pointer,list) or len(pointer)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in pointer)):
            return None
        if data["schema"] == 2 and not {"targetOutput", "targetEpoch"} <= data.keys():
            return None
        target = data.get("targetOutput", data["output"])
        epoch = data.get("targetEpoch", 0)
        if any(type(v) is not int or not 0 <= v < 2**64 for v in (target, epoch)):
            return None
        if type(data.get("editorOrigin", False)) is not bool:
            return None
        settled = (data.get("settledRevision"), data.get("settledTopologyRevision"), data.get("settledCommitted"))
        if any(name in data for name in ("settledRevision", "settledTopologyRevision", "settledCommitted")):
            if data["phase"] not in {"end", "cancel"} or any(type(v) is not int or not 0 <= v < 2**64 for v in settled[:2]) or type(settled[2]) is not bool:
                return None
        result = parse_edit_preview(data["result"]) if data["result"] is not None else None
        return SpatialGrabEvent(source, data["phase"], data["generation"], data["revision"], data["topologyRevision"],
                                data["output"], window.lower(), data["floating"], result,tuple(pointer) if pointer is not None else None,target,epoch,data.get("editorOrigin", False), *settled)
    except (ValueError, TypeError, KeyError):
        return None
