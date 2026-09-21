from __future__ import annotations

from dataclasses import dataclass, field
import logging
from pathlib import Path
import threading
import time
from typing import Callable

from .config import ConfigError, write_theme_source
from .hyprland import MonitorRecord
from .state import write_palette_state
from .theme import Palette
from .matugen import GeneratedPalette, MatugenPaletteBackend, MatugenScheme
from .wallpaper_backends import (
    STATIC_PROVIDERS,
    WallpaperBackendError,
    WallpaperSnapshot,
    discover_wallpaper_snapshot,
    extract_snapshot_palettes,
)


LOG = logging.getLogger("luminophore-shell")
_BUSY_PHASES = {"discovering", "rendering", "applying"}


@dataclass(frozen=True)
class PaletteSettingsState:
    phase: str = "idle"
    message: str = ""
    error_category: str = ""
    preview: dict[str, Palette] = field(default_factory=dict)
    captured_at: float | None = None
    provider: str = ""
    schemes: dict[str, MatugenScheme] = field(default_factory=dict)


class PaletteExtractionController:
    def __init__(
        self,
        config_path: Path,
        monitors: Callable[[], list[MonitorRecord]],
        fallback: Callable[[], Palette],
        changed: Callable[[PaletteSettingsState], None],
        applied: Callable[[], None],
        backend: MatugenPaletteBackend | None = None,
        provider: str = "",
    ) -> None:
        self.config_path = config_path
        self._monitors = monitors
        self._fallback = fallback
        self._changed = changed
        self._applied = applied
        self._backend = backend or MatugenPaletteBackend()
        self._provider = provider
        self._lock = threading.Lock()
        self._state = PaletteSettingsState()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    @property
    def state(self) -> PaletteSettingsState:
        with self._lock:
            state = self._state
            return PaletteSettingsState(
                phase=state.phase,
                message=state.message,
                error_category=state.error_category,
                preview=dict(state.preview),
                captured_at=state.captured_at,
                provider=state.provider,
                schemes=dict(state.schemes),
            )

    def _set_state(self, state: PaletteSettingsState) -> None:
        with self._lock:
            self._state = state
        self._changed(state)

    def set_provider(self, provider: str) -> bool:
        """Adopt an explicit static provider between extraction transactions."""
        if provider not in STATIC_PROVIDERS:
            return False
        with self._lock:
            if self._state.phase in _BUSY_PHASES:
                return False
            self._provider = provider
        return True

    def extract(self) -> bool:
        with self._lock:
            if self._state.phase in _BUSY_PHASES:
                return False
            self._state = PaletteSettingsState("discovering", "실행 중인 작품을 확인하는 중…")
            state = self._state
        self._changed(state)
        self._stop.clear()
        self._worker = threading.Thread(target=self._extract_worker, name="luminophore-palette", daemon=True)
        self._worker.start()
        return True

    def _extract_worker(self) -> None:
        started = time.monotonic()
        try:
            monitors = self._monitors()
            if not monitors:
                raise WallpaperBackendError("no_monitors", "활성 모니터를 확인할 수 없습니다")
            provider, source = self._discover_source(monitors)
            provider_name = "Hyprpaper" if provider == "hyprpaper" else "Awww"
            self._set_state(
                PaletteSettingsState(
                    "rendering",
                    f"{provider_name} 원본에서 모니터별 색상을 추출하는 중…",
                    provider=provider,
                )
            )
            extracted = extract_snapshot_palettes(source, monitors, self._fallback(), self._backend)
            palettes: dict[str, Palette] = {}
            schemes: dict[str, MatugenScheme] = {}
            for connector, value in extracted.items():
                if isinstance(value, GeneratedPalette):
                    palettes[connector] = value.palette
                    schemes[connector] = value.scheme
                else:
                    palettes[connector] = value
            captured_at = time.time()
            LOG.info(
                "palette extraction succeeded: provider=%s outputs=%d elapsed=%.2fs",
                provider,
                len(palettes),
                time.monotonic() - started,
            )
            self._set_state(
                PaletteSettingsState(
                    "preview",
                    "추출 결과를 확인한 뒤 적용하세요",
                    preview=palettes,
                    captured_at=captured_at,
                    provider=provider,
                    schemes=schemes,
                )
            )
        except WallpaperBackendError as exc:
            LOG.warning("palette extraction failed: category=%s", exc.category)
            self._set_state(PaletteSettingsState("error", str(exc), exc.category))
        except Exception:
            LOG.exception("palette extraction failed unexpectedly")
            self._set_state(PaletteSettingsState("error", "색상 추출 중 예상하지 못한 오류가 발생했습니다", "unexpected"))

    def _discover_source(
        self,
        monitors: list[MonitorRecord],
    ) -> tuple[str, WallpaperSnapshot]:
        if self._provider not in STATIC_PROVIDERS:
            raise WallpaperBackendError("provider_required", "Luminophore이 선택한 wallpaper provider가 필요합니다")
        snapshot = discover_wallpaper_snapshot(monitors, self._provider)
        return snapshot.provider, snapshot

    def apply(self) -> bool:
        with self._lock:
            if self._state.phase != "preview" or not self._state.preview or self._state.captured_at is None:
                return False
            preview = dict(self._state.preview)
            captured_at = self._state.captured_at
            provider = self._state.provider
            schemes = dict(self._state.schemes)
            if provider not in STATIC_PROVIDERS:
                return False
            self._state = PaletteSettingsState(
                "applying",
                "추출 색상을 저장하는 중…",
                preview=preview,
                captured_at=captured_at,
                provider=provider,
                schemes=schemes,
            )
            state = self._state
        self._changed(state)
        try:
            if schemes:
                write_palette_state(preview, captured_at, schemes=schemes, provider=provider)
            else:
                write_palette_state(preview, captured_at)
            write_theme_source(self.config_path, provider)
        except (OSError, ConfigError) as exc:
            LOG.warning("palette apply failed: %s", exc)
            self._set_state(
                PaletteSettingsState(
                    "error",
                    "색상 설정을 저장하지 못했습니다",
                    "write_failed",
                    preview,
                    captured_at,
                    provider,
                    schemes,
                )
            )
            return False
        LOG.info("palette source changed: %s", provider)
        self._set_state(PaletteSettingsState("idle", "추출 색상을 적용했습니다", captured_at=captured_at, provider=provider))
        self._applied()
        return True

    def shutdown(self) -> None:
        self._stop.set()
        self._backend.shutdown()
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=3.0)
