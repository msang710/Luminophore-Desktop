"""Offline deterministic compiler; no network, inference, or desktop mutation."""

from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from PIL import Image, ImageOps
from .background_scene import MAX_TEXTURE_BYTES, canonical, digest
from .background_store import atomic_json
from .scene_profile import SceneProfileDraft


def compile_profile(
    draft: SceneProfileDraft, destination: Path, temporary_prefix=".compile-"
) -> Path:
    import cv2
    import numpy as np

    cv2.setNumThreads(1)
    cv2.setRNGSeed(0)
    draft.validate()
    draft.source.validate()
    with Image.open(draft.source.path) as image:
        if image.format not in {"PNG", "JPEG"}:
            raise ValueError("unsupported_source_format")
        if image.width * image.height > 8_000_000:
            raise ValueError("profile_pixel_budget")
        image = ImageOps.exif_transpose(image).convert("RGBA")
        rgba = np.array(image)
    height, width = rgba.shape[:2]
    if width * height * 4 * len(draft.layers) > MAX_TEXTURE_BYTES // 4:
        raise ValueError("profile_texture_budget")
    compiler = f"opencv-{cv2.__version__}/luminophore-profile-2"
    identity = hashlib.sha256(
        canonical({"draft": draft.to_dict(), "compiler": compiler})
    ).hexdigest()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / identity
    if target.exists():
        validate_package(target / "scene-package.json")
        return target / "scene-package.json"
    if (
        sum(p.stat().st_size for p in destination.rglob("*") if p.is_file())
        > 2 * 1024**3
    ):
        raise ValueError("profile_disk_budget")
    temporary = Path(tempfile.mkdtemp(prefix=temporary_prefix, dir=destination))
    try:
        bgr = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)

        def apply_seed(target_mask, stroke, value):
            point = (
                round(stroke["x"] * (width - 1)),
                round(stroke["y"] * (height - 1)),
            )
            if stroke.get("kind") == "fill":
                region = np.zeros((height + 2, width + 2), np.uint8)
                cv2.floodFill(
                    bgr,
                    region,
                    point,
                    (0, 0, 0),
                    (16, 16, 16),
                    (16, 16, 16),
                    4
                    | cv2.FLOODFILL_FIXED_RANGE
                    | cv2.FLOODFILL_MASK_ONLY
                    | (255 << 8),
                )
                target_mask[region[1:-1, 1:-1] != 0] = value
            else:
                cv2.circle(
                    target_mask,
                    point,
                    max(1, round(stroke["radius"] * min(width, height))),
                    value,
                    -1,
                )

        masks = []
        for layer in draft.layers[1:]:
            mask = np.full((height, width), cv2.GC_PR_BGD, np.uint8)
            mask[:2] = mask[-2:] = cv2.GC_BGD
            mask[:, :2] = mask[:, -2:] = cv2.GC_BGD
            for stroke in layer.strokes:
                apply_seed(
                    mask, stroke, cv2.GC_FGD if stroke["include"] else cv2.GC_BGD
                )
            if not np.any(mask == cv2.GC_FGD):
                masks.append(np.zeros((height, width), np.uint8))
                continue
            if not np.any(mask == cv2.GC_BGD):
                raise ValueError("background_seed_required")
            cv2.grabCut(
                bgr,
                mask,
                None,
                np.zeros((1, 65), np.float64),
                np.zeros((1, 65), np.float64),
                4,
                cv2.GC_INIT_WITH_MASK,
            )
            binary = np.where(
                (mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0
            ).astype(np.uint8)
            # Keep thin foreground seeds even when cleaning isolated pixel noise.
            binary = cv2.morphologyEx(
                binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)
            )
            for stroke in layer.strokes:
                apply_seed(binary, stroke, 255 if stroke["include"] else 0)
            if layer.feather:
                binary = cv2.GaussianBlur(binary, (layer.feather * 2 + 1,) * 2, 0)
            masks.append(binary)
        covered = (
            np.maximum.reduce(masks) if masks else np.zeros((height, width), np.uint8)
        )
        fill_mask = cv2.dilate(covered, np.ones((3, 3), np.uint8))
        background = (
            cv2.inpaint(bgr, fill_mask, 3, cv2.INPAINT_TELEA)
            if np.any(fill_mask)
            else bgr
        )
        base = cv2.cvtColor(background, cv2.COLOR_BGR2BGRA)
        base[:, :, 3] = rgba[:, :, 3]
        layers = []
        for index, layer in enumerate(draft.layers):
            pixels = (
                base.copy() if index == 0 else cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
            )
            if index:
                pixels[:, :, 3] = np.minimum(rgba[:, :, 3], masks[index - 1])
            filename = f"layer-{index}.png"
            if not cv2.imwrite(
                str(temporary / filename), pixels, [cv2.IMWRITE_PNG_COMPRESSION, 9]
            ):
                raise ValueError("encode_failed")
            layers.append(
                {
                    "file": filename,
                    "sha256": digest(temporary / filename),
                    "depth": layer.depth,
                }
            )
        mesh = {
            "schema": 1,
            "columns": 16,
            "rows": 16,
            "vertices": [[x / 16, y / 16] for y in range(17) for x in range(17)],
        }
        atomic_json(temporary / "mesh.json", mesh)
        package = {
            "schema": 1,
            "compiler": compiler,
            "draft_hash": identity,
            "source": draft.source.path,
            "source_hash": draft.source.sha256,
            "draft": draft.to_dict(),
            "width": width,
            "height": height,
            "motion": draft.motion,
            "layers": layers,
            "mesh": {"file": "mesh.json", "sha256": digest(temporary / "mesh.json")},
        }
        atomic_json(temporary / "scene-package.json", package)
        # Atomic directory publication; originals and saved draft are never touched.
        os.rename(temporary, target)
        return target / "scene-package.json"
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def validate_package(path: Path, check_source=True):
    from .background_scene import MAX_LAYERS, MAX_PIXELS

    path = Path(path)
    data = json.loads(path.read_text())
    import math

    if (
        type(data.get("schema")) is not int
        or data["schema"] != 1
        or any(
            type(data.get(k)) is not int or not 1 <= data[k] <= 16384
            for k in ("width", "height")
        )
        or data["width"] * data["height"] > MAX_PIXELS
    ):
        raise ValueError("package_schema")
    if (
        not isinstance(data.get("layers"), list)
        or not 1 <= len(data["layers"]) <= MAX_LAYERS
    ):
        raise ValueError("layer_budget")
    if (
        data["width"] * data["height"] * 4 * len(data["layers"])
        > MAX_TEXTURE_BYTES // 4
    ):
        raise ValueError("profile_texture_budget")
    if (
        type(data.get("motion")) not in (int, float)
        or not math.isfinite(data["motion"])
        or not 0 <= data["motion"] <= 0.05
    ):
        raise ValueError("invalid_motion")
    if check_source and digest(Path(data["source"])) != data["source_hash"]:
        raise ValueError("source_changed")
    for row in [*data["layers"], data["mesh"]]:
        if (
            not isinstance(row.get("file"), str)
            or Path(row["file"]).name != row["file"]
            or row["file"] in {".", ".."}
            or digest(path.parent / row["file"]) != row["sha256"]
        ):
            raise ValueError("package_hash")
    for index, row in enumerate(data["layers"]):
        depth = row.get("depth")
        if (
            type(depth) not in (int, float)
            or not math.isfinite(depth)
            or not 0 <= depth <= 1
            or index == 0
            and depth != 0
        ):
            raise ValueError("invalid_depth")
        with Image.open(path.parent / row["file"]) as image:
            if image.size != (data["width"], data["height"]) or image.format != "PNG":
                raise ValueError("layer_dimensions")
    mesh = json.loads((path.parent / data["mesh"]["file"]).read_text())
    expected = {
        "schema": 1,
        "columns": 16,
        "rows": 16,
        "vertices": [[x / 16, y / 16] for y in range(17) for x in range(17)],
    }
    if mesh != expected:
        raise ValueError("mesh_schema")
    return data
