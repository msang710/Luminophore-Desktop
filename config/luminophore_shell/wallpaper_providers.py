from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Callable, Iterable, Mapping, Protocol, Sequence

from .appearance_types import (
    AppearanceErrorCategory,
    AppearanceSource,
    AppearanceSourceKind,
    MonitorAssignment,
    WallpaperProviderName,
    WallpaperSnapshot,
    canonical_json,
)
from .compositor_runtime import resolve_hyprctl


class WallpaperProviderError(RuntimeError):
    def __init__(self, category: AppearanceErrorCategory, message: str) -> None:
        super().__init__(message)
        self.category = category


Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WallpaperProviderError(AppearanceErrorCategory.PROVIDER_MISSING, "wallpaper provider unavailable") from exc


def topology_digest(connectors: Iterable[str]) -> str:
    names = tuple(sorted(str(name) for name in connectors))
    if not names or any(not name for name in names) or len(set(names)) != len(names):
        raise WallpaperProviderError(AppearanceErrorCategory.VALIDATION, "invalid monitor topology")
    return hashlib.sha256(canonical_json({"connectors": names})).hexdigest()


def _snapshot(provider: WallpaperProviderName, sources: Mapping[str, AppearanceSource], connectors: Iterable[str]) -> WallpaperSnapshot:
    expected = tuple(sorted(connectors))
    if tuple(sorted(sources)) != expected:
        raise WallpaperProviderError(AppearanceErrorCategory.PROVIDER_INCOMPLETE, "provider state is incomplete")
    assignments = tuple(MonitorAssignment(name, sources[name]) for name in expected)
    state = {item.connector: {"kind": item.source.kind, "value": item.source.value} for item in assignments}
    return WallpaperSnapshot(
        provider,
        assignments,
        topology_digest(expected),
        hashlib.sha256(canonical_json(state)).hexdigest(),
    )


class WallpaperProvider(Protocol):
    name: WallpaperProviderName

    def query(self, connectors: Iterable[str]) -> WallpaperSnapshot: ...
    def validate(self, assignments: Iterable[MonitorAssignment]) -> tuple[MonitorAssignment, ...]: ...
    def apply_batch(self, assignments: Iterable[MonitorAssignment], previous: WallpaperSnapshot) -> WallpaperSnapshot: ...
    def verify(self, expected: WallpaperSnapshot) -> None: ...
    def restore(self, snapshot: WallpaperSnapshot) -> None: ...


class _CommandProvider:
    name: WallpaperProviderName

    def __init__(self, runner: Runner = _run, timeout: float = 8.0, hyprctl: str | None = None) -> None:
        self.runner = runner
        self.timeout = timeout
        self.hyprctl = hyprctl or resolve_hyprctl()

    def validate(self, assignments: Iterable[MonitorAssignment]) -> tuple[MonitorAssignment, ...]:
        rows = tuple(sorted(assignments, key=lambda item: item.connector))
        if not rows or len({row.connector for row in rows}) != len(rows):
            raise WallpaperProviderError(AppearanceErrorCategory.VALIDATION, "assignments must cover unique monitors")
        validated: list[MonitorAssignment] = []
        for row in rows:
            if row.source.kind is not AppearanceSourceKind.IMAGE:
                raise WallpaperProviderError(AppearanceErrorCategory.INVALID_SOURCE, "only image wallpapers can be restored exactly")
            path = Path(row.source.value).expanduser().resolve()
            if not path.is_file():
                raise WallpaperProviderError(AppearanceErrorCategory.INVALID_SOURCE, "wallpaper image is unavailable")
            validated.append(MonitorAssignment(row.connector, AppearanceSource(AppearanceSourceKind.IMAGE, str(path))))
        return tuple(validated)

    def _apply_one(self, assignment: MonitorAssignment) -> None:
        result = self.runner(self._apply_argv(assignment), self.timeout)
        if result.returncode:
            raise WallpaperProviderError(AppearanceErrorCategory.APPLY_FAILED_ROLLED_BACK, "wallpaper provider apply failed")

    def apply_batch(self, assignments: Iterable[MonitorAssignment], previous: WallpaperSnapshot) -> WallpaperSnapshot:
        rows = self.validate(assignments)
        if previous.provider is not self.name or {row.connector for row in previous.assignments} != {row.connector for row in rows}:
            raise WallpaperProviderError(AppearanceErrorCategory.PROVIDER_INCOMPLETE, "complete provider pre-state is required")
        # A non-image pre-state cannot be compensated with the exact same provider command.
        old = self.validate(previous.assignments)
        old_by_connector = {row.connector: row for row in old}
        completed: list[str] = []
        try:
            for row in rows:
                self._apply_one(row)
                completed.append(row.connector)
        except WallpaperProviderError as exc:
            try:
                for connector in reversed(completed):
                    self._apply_one(old_by_connector[connector])
                self.verify(previous)
            except Exception as rollback_exc:
                raise WallpaperProviderError(AppearanceErrorCategory.ROLLBACK_FAILED, "wallpaper apply and compensation failed") from rollback_exc
            raise WallpaperProviderError(AppearanceErrorCategory.APPLY_FAILED_ROLLED_BACK, str(exc)) from exc
        expected = _snapshot(self.name, {row.connector: row.source for row in rows}, (row.connector for row in rows))
        self.verify(expected)
        return expected

    def verify(self, expected: WallpaperSnapshot) -> None:
        actual = self.query(row.connector for row in expected.assignments)
        if actual.provider is not expected.provider or actual.state_digest != expected.state_digest:
            raise WallpaperProviderError(AppearanceErrorCategory.COMPLETION_UNKNOWN, "wallpaper provider verification failed")

    def restore(self, snapshot: WallpaperSnapshot) -> None:
        if snapshot.provider is not self.name:
            raise WallpaperProviderError(AppearanceErrorCategory.VALIDATION, "snapshot provider mismatch")
        rows = self.validate(snapshot.assignments)
        for row in reversed(rows):
            self._apply_one(row)
        self.verify(snapshot)

    def _apply_argv(self, assignment: MonitorAssignment) -> tuple[str, ...]:
        raise NotImplementedError


class HyprpaperProvider(_CommandProvider):
    name = WallpaperProviderName.HYPRPAPER

    def query(self, connectors: Iterable[str]) -> WallpaperSnapshot:
        result = self.runner((self.hyprctl, "hyprpaper", "listactive"), self.timeout)
        if result.returncode:
            raise WallpaperProviderError(AppearanceErrorCategory.PROVIDER_MISSING, "hyprpaper is unavailable")
        sources: dict[str, AppearanceSource] = {}
        for raw in result.stdout.splitlines():
            if ": " not in raw:
                continue
            connector, value = (part.strip() for part in raw.split(": ", 1))
            if connector and value:
                sources[connector] = AppearanceSource(AppearanceSourceKind.IMAGE, str(Path(value).expanduser().resolve()))
        return _snapshot(self.name, sources, connectors)

    def _apply_argv(self, assignment: MonitorAssignment) -> tuple[str, ...]:
        return (self.hyprctl, "hyprpaper", "wallpaper", f"{assignment.connector},{assignment.source.value},cover")


class AwwwProvider(_CommandProvider):
    name = WallpaperProviderName.AWWW

    def query(self, connectors: Iterable[str]) -> WallpaperSnapshot:
        result = self.runner(("/usr/bin/awww", "query", "--all", "--json"), self.timeout)
        if result.returncode:
            raise WallpaperProviderError(AppearanceErrorCategory.PROVIDER_MISSING, "awww is unavailable")
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise WallpaperProviderError(AppearanceErrorCategory.COMPLETION_UNKNOWN, "invalid awww query response") from exc
        sources: dict[str, AppearanceSource] = {}
        if not isinstance(payload, dict):
            raise WallpaperProviderError(AppearanceErrorCategory.COMPLETION_UNKNOWN, "invalid awww query response")
        for namespace in payload.values():
            if not isinstance(namespace, list):
                continue
            for row in namespace:
                displaying = row.get("displaying") if isinstance(row, dict) else None
                name = row.get("name") if isinstance(row, dict) else None
                image = displaying.get("image") if isinstance(displaying, dict) else None
                if isinstance(name, str) and isinstance(image, str) and name and image:
                    source = AppearanceSource(AppearanceSourceKind.IMAGE, str(Path(image).expanduser().resolve()))
                    if name in sources and sources[name] != source:
                        raise WallpaperProviderError(AppearanceErrorCategory.PROVIDER_AMBIGUOUS, "conflicting awww namespaces")
                    sources[name] = source
        return _snapshot(self.name, sources, connectors)

    def _apply_argv(self, assignment: MonitorAssignment) -> tuple[str, ...]:
        return (
            "/usr/bin/awww", "img", "--outputs", assignment.connector,
            "--transition-type", "fade", "--transition-duration", "0.4", assignment.source.value,
        )


def provider_for(name: WallpaperProviderName | str, *, runner: Runner = _run, timeout: float = 8.0) -> WallpaperProvider:
    try:
        selected = WallpaperProviderName(name)
    except ValueError as exc:
        raise WallpaperProviderError(AppearanceErrorCategory.VALIDATION, "unsupported wallpaper provider") from exc
    if selected is WallpaperProviderName.HYPRPAPER:
        return HyprpaperProvider(runner, timeout)
    return AwwwProvider(runner, timeout)
