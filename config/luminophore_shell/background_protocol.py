from pathlib import Path
from .background_scene import SceneAsset
from .scene_profile_compiler import validate_package


def asset_spec(asset: SceneAsset, *, recovery=False):
    asset.validate()
    path = Path(asset.path)
    if path.suffix.lower() == ".json":
        package = validate_package(path, check_source=not recovery)
        layers = [
            {"path": str(path.parent / row["file"]), "depth": row["depth"]}
            for row in package["layers"]
        ]
        motion = package["motion"]
    else:
        layers, motion = [{"path": asset.path, "depth": 0}], 0
    return {
        "layers": layers,
        "motion": motion,
        "fit": asset.fit,
        "focal_x": asset.focal_x,
        "focal_y": asset.focal_y,
    }


def topology_key(monitors):
    return tuple(sorted((m.name, m.x, m.y, m.width, m.height) for m in monitors))
