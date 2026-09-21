from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Protocol

from .appearance_types import (
    AppearanceCompileRequest,
    AppearanceErrorCategory,
    AppearanceMode,
    AppearancePreviewToken,
    AppearanceSource,
    CompiledAppearance,
    MonitorAssignment,
    WallpaperProviderName,
    canonical_json,
)
from .wallpaper_providers import WallpaperProvider, WallpaperProviderError, topology_digest


class AppearanceServiceError(RuntimeError):
    def __init__(self, category: AppearanceErrorCategory, message: str) -> None:
        super().__init__(message)
        self.category = category


class AppearanceCompiler(Protocol):
    def compile(self, request: AppearanceCompileRequest) -> CompiledAppearance: ...


class AppearanceStateStore(Protocol):
    def snapshot(self) -> object: ...
    def apply(self, compiled: Mapping[str, CompiledAppearance], provider: WallpaperProviderName) -> None: ...
    def restore(self, snapshot: object) -> None: ...


@dataclass(frozen=True, slots=True)
class AppearancePreview:
    token: AppearancePreviewToken
    assignments: tuple[MonitorAssignment, ...]
    compiled: tuple[tuple[str, CompiledAppearance], ...]


class AppearanceService:
    def __init__(
        self,
        providers: Mapping[WallpaperProviderName, WallpaperProvider],
        compiler: AppearanceCompiler,
        state_store: AppearanceStateStore,
        connectors: Callable[[], Iterable[str]],
        config_digest: Callable[[], str],
        required_outputs: Callable[[], Iterable[str]],
    ) -> None:
        self._providers = dict(providers)
        self._compiler = compiler
        self._store = state_store
        self._connectors = connectors
        self._config_digest = config_digest
        self._required_outputs = required_outputs
        self._lock = threading.Lock()
        self._preview_lock = threading.Lock()
        self._previews: dict[str, AppearancePreview] = {}

    def discard(self, preview_id: str) -> bool:
        """Forget an abandoned compile-only preview without mutating live state."""
        with self._preview_lock:
            return self._previews.pop(preview_id, None) is not None

    def preview(
        self,
        provider: WallpaperProviderName | str,
        sources: Mapping[str, AppearanceSource],
        mode: AppearanceMode,
    ) -> AppearancePreview:
        try:
            selected = WallpaperProviderName(provider)
        except ValueError as exc:
            raise AppearanceServiceError(AppearanceErrorCategory.VALIDATION, "unsupported wallpaper provider") from exc
        adapter = self._providers.get(selected)
        if adapter is None:
            raise AppearanceServiceError(AppearanceErrorCategory.PROVIDER_MISSING, "wallpaper provider is unavailable")
        connectors = tuple(sorted(self._connectors()))
        if not connectors or tuple(sorted(sources)) != connectors:
            raise AppearanceServiceError(AppearanceErrorCategory.PROVIDER_INCOMPLETE, "every monitor requires a wallpaper")
        assignments = adapter.validate(MonitorAssignment(name, sources[name]) for name in connectors)
        outputs = tuple(sorted(set(self._required_outputs())))
        compiled: list[tuple[str, CompiledAppearance]] = []
        try:
            for assignment in assignments:
                compiled.append((assignment.connector, self._compiler.compile(AppearanceCompileRequest(assignment.source, mode, outputs))))
        except Exception as exc:
            category = getattr(exc, "category", AppearanceErrorCategory.COMPILER_INCOMPATIBLE)
            try:
                normalized = AppearanceErrorCategory(category)
            except ValueError:
                normalized = AppearanceErrorCategory.COMPILER_INCOMPATIBLE
            raise AppearanceServiceError(normalized, str(exc)) from exc
        config = self._config_digest()
        topology = topology_digest(connectors)
        generation_ids = tuple((name, value.generation_id) for name, value in compiled)
        source_pairs = tuple((row.connector, row.source.value) for row in assignments)
        preview_id = hashlib.sha256(canonical_json({
            "provider": selected,
            "sources": source_pairs,
            "topology": topology,
            "config": config,
            "generations": generation_ids,
        })).hexdigest()
        token = AppearancePreviewToken(preview_id, selected, source_pairs, topology, config, generation_ids)
        result = AppearancePreview(token, assignments, tuple(compiled))
        with self._preview_lock:
            self._previews[preview_id] = result
        return result

    def apply(self, token: AppearancePreviewToken) -> None:
        if not self._lock.acquire(blocking=False):
            raise AppearanceServiceError(AppearanceErrorCategory.BUSY, "another appearance transaction is active")
        try:
            with self._preview_lock:
                preview = self._previews.pop(token.preview_id, None)
            if preview is None or preview.token != token:
                raise AppearanceServiceError(AppearanceErrorCategory.STALE_PREVIEW, "appearance preview is stale")
            current_topology = topology_digest(self._connectors())
            if token.topology_digest != current_topology or token.config_digest != self._config_digest():
                raise AppearanceServiceError(AppearanceErrorCategory.STALE_PREVIEW, "appearance preview is stale")
            provider = self._providers.get(token.provider)
            if provider is None:
                raise AppearanceServiceError(AppearanceErrorCategory.PROVIDER_MISSING, "wallpaper provider is unavailable")
            connectors = tuple(row.connector for row in preview.assignments)
            try:
                before_wallpaper = provider.query(connectors)
            except WallpaperProviderError as exc:
                raise AppearanceServiceError(exc.category, str(exc)) from exc
            before_state = self._store.snapshot()
            wallpaper_changed = False
            state_changed = False
            try:
                provider.apply_batch(preview.assignments, before_wallpaper)
                wallpaper_changed = True
                self._store.apply(dict(preview.compiled), token.provider)
                state_changed = True
            except Exception as exc:
                category = getattr(exc, "category", AppearanceErrorCategory.APPLY_FAILED_ROLLED_BACK)
                # apply_batch performs its own exact compensation when it fails.
                if wallpaper_changed:
                    try:
                        if state_changed:
                            self._store.restore(before_state)
                        else:
                            # A store may partially mutate before raising.
                            self._store.restore(before_state)
                        provider.restore(before_wallpaper)
                    except Exception as rollback_exc:
                        raise AppearanceServiceError(AppearanceErrorCategory.ROLLBACK_FAILED, "appearance apply and rollback failed") from rollback_exc
                try:
                    normalized = AppearanceErrorCategory(category)
                except ValueError:
                    normalized = AppearanceErrorCategory.APPLY_FAILED_ROLLED_BACK
                if normalized is AppearanceErrorCategory.ROLLBACK_FAILED:
                    raise AppearanceServiceError(normalized, str(exc)) from exc
                raise AppearanceServiceError(AppearanceErrorCategory.APPLY_FAILED_ROLLED_BACK, str(exc)) from exc
        finally:
            self._lock.release()
