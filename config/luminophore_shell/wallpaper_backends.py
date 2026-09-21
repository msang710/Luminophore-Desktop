from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Callable, Iterable, Mapping, Sequence

from PIL import Image, UnidentifiedImageError

from .compositor_runtime import resolve_hyprctl
from .hyprland import MonitorRecord
from .theme import Palette, extract_palette_image
from .matugen import GeneratedPalette, MatugenError, MatugenPaletteBackend


AWWW_BINARY = Path("/usr/bin/awww")
STATIC_PROVIDERS = ("hyprpaper", "awww")
MAX_ANIMATION_FRAMES = 12
_HEX_COLOR = re.compile(r"^#?([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?$")


class WallpaperBackendError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class WallpaperSource:
    kind: str
    value: str


@dataclass(frozen=True)
class WallpaperSnapshot:
    provider: str
    sources: Mapping[str, WallpaperSource]


CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


def _run_command(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WallpaperBackendError("query_failed", "배경화면 provider 조회에 실패했습니다") from exc


def parse_hyprpaper_active(text: str) -> WallpaperSnapshot:
    sources: dict[str, WallpaperSource] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or ": " not in line:
            continue
        connector, value = line.split(": ", 1)
        connector = connector.strip()
        value = value.strip()
        if not connector or not value:
            continue
        source = WallpaperSource("image", value)
        previous = sources.get(connector)
        if previous is not None and previous != source:
            raise WallpaperBackendError("ambiguous_provider", "hyprpaper output 정보가 서로 충돌합니다")
        sources[connector] = source
    if not sources:
        raise WallpaperBackendError("no_provider", "실행 중인 hyprpaper 배경을 찾지 못했습니다")
    return WallpaperSnapshot("hyprpaper", sources)


def _awww_source(displaying: object) -> WallpaperSource:
    if not isinstance(displaying, dict):
        raise WallpaperBackendError("query_failed", "awww output 형식이 올바르지 않습니다")
    image = displaying.get("image")
    if isinstance(image, str) and image.strip():
        return WallpaperSource("image", image.strip())
    color = displaying.get("color")
    if isinstance(color, str):
        match = _HEX_COLOR.fullmatch(color.strip())
        if match:
            return WallpaperSource("color", f"#{match.group(1).upper()}")
    raise WallpaperBackendError("invalid_source", "awww가 분석할 수 있는 이미지나 색상을 표시하지 않습니다")


def parse_awww_query(payload: str | bytes | object) -> WallpaperSnapshot:
    if isinstance(payload, (str, bytes)):
        try:
            raw = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise WallpaperBackendError("query_failed", "awww query 결과를 읽지 못했습니다") from exc
    else:
        raw = payload
    if not isinstance(raw, dict):
        raise WallpaperBackendError("query_failed", "awww query 결과가 올바르지 않습니다")
    sources: dict[str, WallpaperSource] = {}
    for outputs in raw.values():
        if not isinstance(outputs, list):
            raise WallpaperBackendError("query_failed", "awww output 목록이 올바르지 않습니다")
        for row in outputs:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                raise WallpaperBackendError("query_failed", "awww output 항목이 올바르지 않습니다")
            connector = row["name"].strip()
            if not connector:
                raise WallpaperBackendError("query_failed", "awww output 이름이 비어 있습니다")
            source = _awww_source(row.get("displaying"))
            previous = sources.get(connector)
            if previous is not None and previous != source:
                raise WallpaperBackendError("ambiguous_provider", "여러 awww namespace가 같은 output에 다른 배경을 표시합니다")
            sources[connector] = source
    if not sources:
        raise WallpaperBackendError("no_provider", "실행 중인 awww 배경을 찾지 못했습니다")
    return WallpaperSnapshot("awww", sources)


def _query_provider(provider: str, runner: CommandRunner, timeout: float) -> WallpaperSnapshot:
    if provider == "hyprpaper":
        completed = runner((resolve_hyprctl(), "hyprpaper", "listactive"), timeout)
        if completed.returncode:
            raise WallpaperBackendError("no_provider", "실행 중인 hyprpaper 배경을 찾지 못했습니다")
        return parse_hyprpaper_active(completed.stdout)
    if provider == "awww":
        completed = runner((str(AWWW_BINARY), "query", "--all", "--json"), timeout)
        if completed.returncode:
            raise WallpaperBackendError("no_provider", "실행 중인 awww 배경을 찾지 못했습니다")
        return parse_awww_query(completed.stdout)
    raise WallpaperBackendError("no_provider", "지원하는 정적 배경 provider가 아닙니다")


def _require_monitor_sources(snapshot: WallpaperSnapshot, monitors: Iterable[MonitorRecord]) -> WallpaperSnapshot:
    missing = [monitor.name for monitor in monitors if monitor.name not in snapshot.sources]
    if missing:
        raise WallpaperBackendError("missing_output", "일부 모니터의 현재 배경을 확인하지 못했습니다")
    return snapshot


def discover_wallpaper_snapshot(
    monitors: Iterable[MonitorRecord],
    preferred: str = "",
    *,
    runner: CommandRunner = _run_command,
    timeout: float = 3.0,
) -> WallpaperSnapshot:
    monitor_rows = tuple(monitors)
    order = [preferred] if preferred in STATIC_PROVIDERS else []
    order.extend(provider for provider in STATIC_PROVIDERS if provider not in order)
    successes: list[WallpaperSnapshot] = []
    failures: list[WallpaperBackendError] = []
    for provider in order:
        try:
            snapshot = _require_monitor_sources(_query_provider(provider, runner, timeout), monitor_rows)
        except WallpaperBackendError as exc:
            failures.append(exc)
            continue
        if provider == preferred:
            return snapshot
        successes.append(snapshot)
    if len(successes) == 1:
        return successes[0]
    if len(successes) > 1:
        raise WallpaperBackendError("ambiguous_provider", "활성 배경 provider가 여러 개라 하나를 선택할 수 없습니다")
    preferred_failure = next(
        (failure for failure in failures if failure.category not in {"no_provider", "query_failed"}),
        None,
    )
    if preferred_failure is not None:
        raise preferred_failure
    raise WallpaperBackendError("no_provider", "실행 중인 hyprpaper 또는 awww 배경을 찾지 못했습니다")


def _frame_indices(frame_count: int, limit: int = MAX_ANIMATION_FRAMES) -> tuple[int, ...]:
    if frame_count <= 1:
        return (0,)
    count = min(frame_count, max(1, limit))
    if count == 1:
        return (0,)
    return tuple(round(index * (frame_count - 1) / (count - 1)) for index in range(count))


def _image_palette(
    path: Path,
    fallback: Palette,
    backend: MatugenPaletteBackend | None = None,
) -> Palette | GeneratedPalette:
    if not path.is_absolute() or not path.is_file():
        raise WallpaperBackendError("invalid_source", "배경 이미지 파일을 읽을 수 없습니다")
    try:
        with Image.open(path) as image:
            frame_count = max(1, int(getattr(image, "n_frames", 1)))
            indices = _frame_indices(frame_count)
            if len(indices) == 1:
                if backend is not None:
                    return backend.generate_image(path)
                image.seek(indices[0])
                return extract_palette_image(image, fallback)
            columns = math.ceil(math.sqrt(len(indices)))
            rows = math.ceil(len(indices) / columns)
            sheet = Image.new("RGB", (160 * columns, 160 * rows), "#000000")
            for position, frame_index in enumerate(indices):
                image.seek(frame_index)
                frame = image.convert("RGB")
                frame.thumbnail((160, 160))
                column = position % columns
                row = position // columns
                x = column * 160 + (160 - frame.width) // 2
                y = row * 160 + (160 - frame.height) // 2
                sheet.paste(frame, (x, y))
            if backend is None:
                return extract_palette_image(sheet, fallback)
            with tempfile.TemporaryDirectory(prefix="luminophore-matugen-animation-") as directory:
                sheet_path = Path(directory) / "representative.png"
                sheet.save(sheet_path, format="PNG")
                sheet_path.chmod(0o600)
                return backend.generate_image(sheet_path)
    except MatugenError as exc:
        raise WallpaperBackendError(exc.category, str(exc)) from exc
    except (OSError, ValueError, EOFError, UnidentifiedImageError) as exc:
        raise WallpaperBackendError("decode_failed", "배경 이미지를 분석하지 못했습니다") from exc


def extract_snapshot_palettes(
    snapshot: WallpaperSnapshot,
    monitors: Iterable[MonitorRecord],
    fallback: Palette,
    backend: MatugenPaletteBackend | None = None,
) -> dict[str, Palette | GeneratedPalette]:
    monitor_rows = tuple(monitors)
    _require_monitor_sources(snapshot, monitor_rows)
    palettes: dict[str, Palette | GeneratedPalette] = {}
    for monitor in monitor_rows:
        source = snapshot.sources[monitor.name]
        if source.kind == "image":
            palettes[monitor.name] = _image_palette(Path(source.value).expanduser(), fallback, backend)
        elif source.kind == "color":
            match = _HEX_COLOR.fullmatch(source.value)
            if not match:
                raise WallpaperBackendError("invalid_source", "awww 배경 색상 형식이 올바르지 않습니다")
            color = f"#{match.group(1)}"
            if backend is not None:
                try:
                    palettes[monitor.name] = backend.generate_color(color)
                except MatugenError as exc:
                    raise WallpaperBackendError(exc.category, str(exc)) from exc
            else:
                image = Image.new("RGB", (16, 16), color)
                palettes[monitor.name] = extract_palette_image(image, fallback)
        else:
            raise WallpaperBackendError("invalid_source", "지원하지 않는 배경 source입니다")
    return palettes
