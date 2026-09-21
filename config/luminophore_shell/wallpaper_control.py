from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Callable, Sequence

from PIL import Image, UnidentifiedImageError

from .appearance_types import AppearanceSource, AppearanceSourceKind, MonitorAssignment, WallpaperProviderName
from .wallpaper_providers import WallpaperProviderError, provider_for


class WallpaperControlError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=8)


def validate_wallpaper(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise WallpaperControlError("invalid_image")
    try:
        with Image.open(resolved) as image:
            image.verify()
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise WallpaperControlError("invalid_image") from exc
    return resolved


class WallpaperController:
    """Temporary compatibility facade for callers migrating to AppearanceService."""

    def __init__(self, provider: WallpaperProviderName | str = "", runner: Runner = _run) -> None:
        self.provider = provider
        self.runner = runner

    def apply(self, path: Path, connector: str) -> str:
        image = validate_wallpaper(path)
        if not connector:
            raise WallpaperControlError("missing_output")
        if self.provider not in {WallpaperProviderName.HYPRPAPER, WallpaperProviderName.AWWW, "hyprpaper", "awww"}:
            raise WallpaperControlError("provider_required")

        def runner(argv: Sequence[str], _timeout: float) -> subprocess.CompletedProcess[str]:
            return self.runner(argv)

        adapter = provider_for(self.provider, runner=runner)
        try:
            previous = adapter.query((connector,))
            adapter.apply_batch((MonitorAssignment(
                connector, AppearanceSource(AppearanceSourceKind.IMAGE, str(image)),
            ),), previous)
        except WallpaperProviderError as exc:
            raise WallpaperControlError(exc.category.value) from exc
        return str(adapter.name)
