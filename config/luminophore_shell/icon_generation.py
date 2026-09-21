from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
from typing import Callable, Iterable, Literal
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops, ImageFilter, ImageOps, UnidentifiedImageError

from .applications import ApplicationRecord
from .app_icons import APP_ICON_THEME, safe_icon_name


class IconGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class IconSource:
    desktop_id: str
    path: Path
    media_type: Literal["image/svg+xml", "image/png"]
    sha256: str


@dataclass(frozen=True)
class IconDraft:
    draft_id: str
    desktop_id: str
    source_sha256: str
    strategy: Literal["silhouette", "edge", "combined", "manual"]
    svg_path: Path
    preview_path: Path


@dataclass(frozen=True)
class GeneratedIconEntry:
    desktop_id: str
    target_name: str
    source_sha256: str
    strategy: str
    object_sha256: str
    approved_at: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _media_type(path: Path) -> Literal["image/svg+xml", "image/png"]:
    try:
        prefix = path.read_bytes()[:512].lstrip()
    except OSError as exc:
        raise IconGenerationError(f"아이콘 원본을 읽지 못했습니다: {exc}") from exc
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if b"<svg" in prefix.lower():
        return "image/svg+xml"
    raise IconGenerationError("SVG 또는 PNG 원본만 선화로 만들 수 있습니다")


def inspect_icon_source(app: ApplicationRecord, path: Path) -> IconSource:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise IconGenerationError(f"아이콘 원본을 찾지 못했습니다: {exc}") from exc
    if not resolved.is_file():
        raise IconGenerationError("아이콘 원본은 local regular file이어야 합니다")
    if resolved.stat().st_size > 32_000_000:
        raise IconGenerationError("아이콘 원본 파일이 비정상적으로 큽니다")
    return IconSource(app.desktop_id, resolved, _media_type(resolved), _sha256(resolved))


def _rgba_source(source: IconSource, workspace: Path, size: int = 256) -> Image.Image:
    if source.media_type == "image/png":
        try:
            with Image.open(source.path) as opened:
                if opened.width * opened.height > 16_777_216:
                    raise IconGenerationError("PNG 해상도가 분석 한도를 초과했습니다")
                opened.load()
                image = opened.convert("RGBA")
        except (OSError, UnidentifiedImageError) as exc:
            raise IconGenerationError(f"PNG를 읽지 못했습니다: {exc}") from exc
        return ImageOps.contain(image, (size, size), Image.Resampling.LANCZOS)

    raw = source.path.read_bytes()
    _validate_source_svg(raw)
    isolated = workspace / "source.svg"
    isolated.write_bytes(raw)
    rendered = workspace / "source.png"
    try:
        subprocess.run(
            ["rsvg-convert", "-a", "-w", str(size), "-h", str(size), "-o", str(rendered), str(isolated)],
            check=True,
            capture_output=True,
            timeout=8,
        )
    except FileNotFoundError as exc:
        raise IconGenerationError("rsvg-convert가 설치되어 있지 않습니다") from exc
    except subprocess.TimeoutExpired as exc:
        raise IconGenerationError("SVG 분석 시간이 초과되었습니다") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.decode("utf-8", "replace").strip()
        raise IconGenerationError(f"SVG를 렌더링하지 못했습니다: {message}") from exc
    try:
        image = Image.open(rendered)
        image.load()
        return image.convert("RGBA")
    except (OSError, UnidentifiedImageError) as exc:
        raise IconGenerationError(f"SVG preview를 읽지 못했습니다: {exc}") from exc


def _validate_source_svg(raw: bytes) -> None:
    if len(raw) > 8_000_000:
        raise IconGenerationError("분석할 SVG가 비정상적으로 큽니다")
    lowered = raw.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise IconGenerationError("DOCTYPE 또는 entity가 포함된 SVG는 분석하지 않습니다")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise IconGenerationError(f"SVG 형식이 잘못되었습니다: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1].casefold() != "svg":
        raise IconGenerationError("SVG root element를 찾지 못했습니다")
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].casefold() == "script":
            raise IconGenerationError("script가 포함된 SVG는 분석하지 않습니다")
        for key, value in node.attrib.items():
            attribute = key.rsplit("}", 1)[-1].casefold()
            normalized = value.strip().casefold()
            if attribute == "href" and normalized and not normalized.startswith("#"):
                raise IconGenerationError("외부 참조가 포함된 SVG는 분석하지 않습니다")
            for match in re.findall(r"url\(([^)]+)\)", normalized):
                reference = match.strip(" \t\r\n\"'")
                if reference and not reference.startswith("#"):
                    raise IconGenerationError("외부 참조가 포함된 SVG는 분석하지 않습니다")
            if "javascript:" in normalized or "file://" in normalized:
                raise IconGenerationError("실행 또는 local file 참조가 포함된 SVG는 분석하지 않습니다")


Point = tuple[float, float]
Segment = tuple[Point, Point]


_MARCHING: dict[int, tuple[tuple[int, int], ...]] = {
    0: (), 1: ((3, 0),), 2: ((0, 1),), 3: ((3, 1),),
    4: ((1, 2),), 5: ((3, 2), (0, 1)), 6: ((0, 2),), 7: ((3, 2),),
    8: ((2, 3),), 9: ((0, 2),), 10: ((0, 3), (1, 2)), 11: ((1, 2),),
    12: ((1, 3),), 13: ((0, 1),), 14: ((3, 0),), 15: (),
}


def _marching_segments(mask: Image.Image) -> list[Segment]:
    width, height = mask.size
    pixels = mask.load()
    segments: list[Segment] = []
    edge_point = {
        0: lambda x, y: (x + 0.5, y),
        1: lambda x, y: (x + 1.0, y + 0.5),
        2: lambda x, y: (x + 0.5, y + 1.0),
        3: lambda x, y: (x, y + 0.5),
    }
    for y in range(height - 1):
        for x in range(width - 1):
            code = (
                (1 if pixels[x, y] else 0)
                | (2 if pixels[x + 1, y] else 0)
                | (4 if pixels[x + 1, y + 1] else 0)
                | (8 if pixels[x, y + 1] else 0)
            )
            for left, right in _MARCHING[code]:
                segments.append((edge_point[left](x, y), edge_point[right](x, y)))
    return segments


def _segments_to_lines(segments: Iterable[Segment]) -> list[list[Point]]:
    remaining = list(segments)
    adjacency: dict[Point, list[Point]] = {}
    for left, right in remaining:
        adjacency.setdefault(left, []).append(right)
        adjacency.setdefault(right, []).append(left)
    used: set[tuple[Point, Point]] = set()
    lines: list[list[Point]] = []

    def key(left: Point, right: Point) -> tuple[Point, Point]:
        return (left, right) if left <= right else (right, left)

    starts = sorted(adjacency, key=lambda point: (len(adjacency[point]) == 2, point[1], point[0]))
    for start in starts:
        for first in adjacency[start]:
            if key(start, first) in used:
                continue
            line = [start]
            previous: Point | None = None
            current = start
            following = first
            while True:
                used.add(key(current, following))
                line.append(following)
                previous, current = current, following
                candidates = [point for point in adjacency[current] if point != previous and key(current, point) not in used]
                if not candidates:
                    break
                following = sorted(candidates)[0]
                if following == line[0]:
                    used.add(key(current, following))
                    line.append(following)
                    break
            if len(line) >= 4:
                lines.append(line)
    return lines


def _distance(point: Point, start: Point, end: Point) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == 0 and dy == 0:
        return math.dist(point, start)
    return abs(dy * point[0] - dx * point[1] + end[0] * start[1] - end[1] * start[0]) / math.hypot(dx, dy)


def _simplify(points: list[Point], tolerance: float = 1.15) -> list[Point]:
    if len(points) <= 2:
        return points
    start, end = points[0], points[-1]
    distances = [_distance(point, start, end) for point in points[1:-1]]
    if not distances:
        return points
    maximum = max(distances)
    if maximum <= tolerance:
        return [start, end]
    index = distances.index(maximum) + 1
    left = _simplify(points[:index + 1], tolerance)
    right = _simplify(points[index:], tolerance)
    return left[:-1] + right


def _normalize_lines(lines: Iterable[list[Point]], source_size: tuple[int, int]) -> list[list[Point]]:
    usable = [_simplify(line) for line in lines if len(line) >= 4]
    usable = [line for line in usable if sum(math.dist(a, b) for a, b in zip(line, line[1:])) >= 6]
    if not usable:
        return []
    points = [point for line in usable for point in line]
    min_x, max_x = min(point[0] for point in points), max(point[0] for point in points)
    min_y, max_y = min(point[1] for point in points), max(point[1] for point in points)
    extent = max(max_x - min_x, max_y - min_y, 1)
    scale = 40 / extent
    offset_x = 24 - (min_x + max_x) * scale / 2
    offset_y = 24 - (min_y + max_y) * scale / 2
    return [[(x * scale + offset_x, y * scale + offset_y) for x, y in line] for line in usable]


def _svg(lines: Iterable[list[Point]]) -> str:
    paths: list[str] = []
    for line in lines:
        if len(line) < 2:
            continue
        commands = [f"M {line[0][0]:.2f} {line[0][1]:.2f}"]
        commands.extend(f"L {x:.2f} {y:.2f}" for x, y in line[1:])
        if math.dist(line[0], line[-1]) < 0.75:
            commands.append("Z")
        paths.append(f'  <path d="{" ".join(commands)}"/>')
    if not paths:
        raise IconGenerationError("식별 가능한 contour를 만들지 못했습니다")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="48" height="48">\n'
        ' <g fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">\n'
        + "\n".join(paths)
        + "\n </g>\n</svg>\n"
    )


def _binary_masks(image: Image.Image) -> dict[str, Image.Image]:
    working = ImageOps.contain(image, (128, 128), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    canvas.alpha_composite(working, ((128 - working.width) // 2, (128 - working.height) // 2))
    alpha = canvas.getchannel("A")
    silhouette = alpha.point(lambda value: 255 if value >= 28 else 0, "1")
    opaque_rgb = Image.new("RGB", canvas.size, "white")
    opaque_rgb.paste(canvas.convert("RGB"), mask=alpha)
    gray = ImageOps.grayscale(opaque_rgb)
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_mask = edges.point(lambda value: 255 if value >= 32 else 0, "1")
    edge_mask = ImageChops.logical_and(edge_mask, silhouette)
    combined = ImageChops.logical_or(silhouette.filter(ImageFilter.FIND_EDGES).convert("1"), edge_mask)
    return {"silhouette": silhouette, "edge": edge_mask, "combined": combined}


def validate_generated_svg(data: bytes) -> None:
    if len(data) > 2_000_000:
        raise IconGenerationError("생성 SVG가 비정상적으로 큽니다")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise IconGenerationError(f"생성 SVG 형식이 잘못되었습니다: {exc}") from exc
    if not root.tag.endswith("svg") or root.attrib.get("viewBox") != "0 0 48 48":
        raise IconGenerationError("생성 SVG는 48×48 viewBox여야 합니다")
    allowed = {"svg", "g", "path"}
    for node in root.iter():
        local = node.tag.rsplit("}", 1)[-1]
        if local not in allowed:
            raise IconGenerationError(f"생성 SVG에 허용되지 않은 element가 있습니다: {local}")
        if any(key.rsplit("}", 1)[-1] in {"href", "filter", "style"} for key in node.attrib):
            raise IconGenerationError("생성 SVG에 외부 참조 또는 filter가 있습니다")


class IconDraftGenerator:
    def __init__(
        self,
        source_resolver: Callable[[ApplicationRecord], Path | None],
        cache_root: Path | None = None,
    ) -> None:
        self.source_resolver = source_resolver
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")).expanduser()
        self.cache_root = cache_root or base / "luminophore-shell/icon-drafts"

    def generate(self, app: ApplicationRecord) -> tuple[IconDraft, ...]:
        path = self.source_resolver(app)
        if path is None:
            raise IconGenerationError("분석할 local SVG/PNG 아이콘이 없습니다")
        source = inspect_icon_source(app, path)
        draft_id = f"{safe_icon_name(app.desktop_id.removesuffix('.desktop'))}-{source.sha256[:12]}"
        workspace = self.cache_root / draft_id
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True, mode=0o700)
        image = _rgba_source(source, workspace)
        masks = _binary_masks(image)
        drafts: list[IconDraft] = []
        for strategy in ("silhouette", "edge", "combined"):
            lines = _segments_to_lines(_marching_segments(masks[strategy]))
            normalized = _normalize_lines(lines, masks[strategy].size)
            try:
                svg = _svg(normalized)
            except IconGenerationError:
                continue
            svg_path = workspace / f"{strategy}.svg"
            _atomic_bytes(svg_path, svg.encode("utf-8"))
            validate_generated_svg(svg_path.read_bytes())
            preview_path = workspace / f"{strategy}.png"
            try:
                subprocess.run(
                    ["rsvg-convert", "-w", "192", "-h", "192", "-o", str(preview_path), str(svg_path)],
                    check=True,
                    capture_output=True,
                    timeout=4,
                )
            except (OSError, subprocess.SubprocessError):
                image.resize((192, 192), Image.Resampling.LANCZOS).save(preview_path)
            drafts.append(IconDraft(draft_id, app.desktop_id, source.sha256, strategy, svg_path, preview_path))
        if not drafts:
            raise IconGenerationError("선화 후보를 만들지 못했습니다")
        return tuple(drafts)

    @staticmethod
    def discard(drafts: Iterable[IconDraft]) -> None:
        parents = {draft.svg_path.parent for draft in drafts}
        for parent in parents:
            shutil.rmtree(parent, ignore_errors=True)


class GeneratedIconStore:
    def __init__(self, data_home: Path | None = None, changed: Callable[[], None] | None = None) -> None:
        root = data_home or Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")).expanduser()
        self.data_home = root
        self.app_data = root / "luminophore-shell"
        self.object_root = self.app_data / "icon-objects"
        self.manifest_path = self.app_data / "generated-icons.toml"
        self.journal_path = self.app_data / "generated-icons.pending.json"
        self.theme_root = root / "icons" / APP_ICON_THEME
        self.active_root = self.theme_root / "scalable" / "apps"
        self.changed = changed or (lambda: None)
        self.entries: dict[str, GeneratedIconEntry] = {}
        self._load()
        self.reconcile()

    def ensure_overlay(self) -> None:
        index = (
            "[Icon Theme]\n"
            "Name=Luminophore Shell Arcticons\n"
            "Comment=Approved local line icons over Arcticons\n"
            "Inherits=arcticons-dark,hicolor\n"
            "Directories=scalable/apps\n\n"
            "[scalable/apps]\n"
            "Size=48\nType=Scalable\nMinSize=16\nMaxSize=512\nContext=Applications\n"
        )
        current = self.theme_root / "index.theme"
        if not current.exists():
            _atomic_bytes(current, index.encode("utf-8"), 0o644)
        self.active_root.mkdir(parents=True, exist_ok=True, mode=0o755)

    def _load(self) -> None:
        try:
            raw = tomllib.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            self.entries = {}
            return
        rows = raw.get("icons", {})
        result: dict[str, GeneratedIconEntry] = {}
        if isinstance(rows, dict):
            for desktop_id, item in rows.items():
                if not isinstance(desktop_id, str) or not isinstance(item, dict):
                    continue
                try:
                    entry = GeneratedIconEntry(
                        desktop_id,
                        safe_icon_name(str(item["target_name"])),
                        str(item["source_sha256"]),
                        str(item["strategy"]),
                        str(item["object_sha256"]),
                        int(item["approved_at"]),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                result[desktop_id.casefold()] = entry
        self.entries = result

    def _manifest_bytes(self, entries: dict[str, GeneratedIconEntry]) -> bytes:
        lines = ["version = 1", ""]
        for entry in sorted(entries.values(), key=lambda item: item.desktop_id.casefold()):
            lines.append(f"[icons.{json.dumps(entry.desktop_id, ensure_ascii=False)}]")
            for key, value in (
                ("target_name", entry.target_name),
                ("source_sha256", entry.source_sha256),
                ("strategy", entry.strategy),
                ("object_sha256", entry.object_sha256),
            ):
                lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
            lines.append(f"approved_at = {entry.approved_at}")
            lines.append("")
        return ("\n".join(lines).rstrip() + "\n").encode("utf-8")

    def _activate(self, entry: GeneratedIconEntry) -> None:
        object_path = self.object_root / f"{entry.object_sha256}.svg"
        if not object_path.is_file() or _sha256(object_path) != entry.object_sha256:
            raise IconGenerationError("승인 SVG object가 없거나 손상됐습니다")
        self.ensure_overlay()
        _atomic_bytes(self.active_root / f"{entry.target_name}.svg", object_path.read_bytes(), 0o644)

    def approve(self, draft: IconDraft, target_name: str) -> GeneratedIconEntry:
        target = safe_icon_name(target_name)
        data = draft.svg_path.read_bytes()
        validate_generated_svg(data)
        object_sha = hashlib.sha256(data).hexdigest()
        object_path = self.object_root / f"{object_sha}.svg"
        if not object_path.exists():
            _atomic_bytes(object_path, data)
        entry = GeneratedIconEntry(
            draft.desktop_id,
            target,
            draft.source_sha256,
            draft.strategy,
            object_sha,
            int(time.time()),
        )
        pending = {"desktop_id": entry.desktop_id, "object_sha256": object_sha, "target_name": target}
        _atomic_bytes(self.journal_path, json.dumps(pending).encode("utf-8"))
        updated = dict(self.entries)
        updated[entry.desktop_id.casefold()] = entry
        _atomic_bytes(self.manifest_path, self._manifest_bytes(updated))
        self._activate(entry)
        self.entries = updated
        self.journal_path.unlink(missing_ok=True)
        self.changed()
        return entry

    def reconcile(self) -> None:
        if not self.journal_path.exists():
            return
        try:
            raw = json.loads(self.journal_path.read_text(encoding="utf-8"))
            entry = self.entries.get(str(raw.get("desktop_id", "")).casefold())
            if entry and entry.object_sha256 == raw.get("object_sha256"):
                self._activate(entry)
            self.journal_path.unlink(missing_ok=True)
        except (OSError, ValueError, IconGenerationError):
            return

    def entry_for(self, desktop_id: str) -> GeneratedIconEntry | None:
        return self.entries.get(desktop_id.casefold())

    def status(self, app: ApplicationRecord, source_path: Path | None = None) -> str:
        entry = self.entry_for(app.desktop_id)
        if entry is None:
            return "없음"
        if source_path and source_path.is_file() and _sha256(source_path) != entry.source_sha256:
            return "업데이트 필요"
        return "적용됨"

    def remove(self, desktop_id: str) -> bool:
        key = desktop_id.casefold()
        entry = self.entries.get(key)
        if entry is None:
            return False
        active = self.active_root / f"{entry.target_name}.svg"
        if active.is_file() and _sha256(active) == entry.object_sha256:
            active.unlink()
        updated = dict(self.entries)
        updated.pop(key, None)
        _atomic_bytes(self.manifest_path, self._manifest_bytes(updated))
        self.entries = updated
        self.changed()
        return True
