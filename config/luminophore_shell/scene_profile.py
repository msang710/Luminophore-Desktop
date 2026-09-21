from __future__ import annotations
from dataclasses import asdict, dataclass, field
import math
from .background_scene import SceneAsset, identifier, MAX_LAYERS


@dataclass
class ProfileLayer:
    name: str
    depth: float
    strokes: list = field(default_factory=list)
    feather: int = 3

    def validate(self):
        if (
            not isinstance(self.name, str)
            or not 1 <= len(self.name) <= 80
            or type(self.depth) not in (float, int)
            or not math.isfinite(self.depth)
            or not 0 <= self.depth <= 1
        ):
            raise ValueError("invalid_layer")
        if (
            type(self.feather) is not int
            or not 0 <= self.feather <= 32
            or len(self.strokes) > 10000
        ):
            raise ValueError("mask_budget")
        for stroke in self.strokes:
            if (
                set(stroke)
                not in (
                    {"x", "y", "radius", "include"},
                    {"x", "y", "radius", "include", "kind"},
                )
                or stroke.get("kind", "brush") not in {"brush", "fill"}
                or type(stroke["include"]) is not bool
            ):
                raise ValueError("invalid_stroke")
            if any(
                type(stroke[k]) not in (int, float)
                or not math.isfinite(stroke[k])
                or not 0 <= stroke[k] <= 1
                for k in ("x", "y", "radius")
            ):
                raise ValueError("invalid_stroke")


@dataclass
class SceneProfileDraft:
    id: str
    source: SceneAsset
    layers: list[ProfileLayer]
    revision: int = 1
    motion: float = 0.015

    def validate(self):
        identifier(self.id)
        if (
            type(self.revision) is not int
            or not 1 <= self.revision < 2**53
            or not 1 <= len(self.layers) <= MAX_LAYERS
        ):
            raise ValueError("draft_budget")
        if (
            type(self.motion) not in (int, float)
            or not math.isfinite(self.motion)
            or not 0 <= self.motion <= 0.05
        ):
            raise ValueError("invalid_motion")
        for layer in self.layers:
            layer.validate()
        if self.layers[0].depth != 0:
            raise ValueError("background_depth")

    def to_dict(self):
        self.validate()
        return {"schema": 1, **asdict(self)}

    @classmethod
    def parse(cls, data):
        if (
            set(data) != {"schema", "id", "source", "layers", "revision", "motion"}
            or data["schema"] != 1
        ):
            raise ValueError("draft_schema")
        result = cls(
            data["id"],
            SceneAsset(**data["source"]),
            [ProfileLayer(**row) for row in data["layers"]],
            data["revision"],
            data["motion"],
        )
        result.validate()
        return result
