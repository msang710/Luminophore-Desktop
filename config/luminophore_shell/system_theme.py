from __future__ import annotations

import ast
import base64
from dataclasses import dataclass, field, replace
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from typing import Callable, Mapping, Sequence

from .config import (
    ConfigConflictError,
    ConfigError,
    ShellConfig,
    config_digest,
    load_config,
    write_config_patch,
)
from .state import read_json, write_json_atomic
from .theme import Palette
from .appearance_types import AppearanceCompileRequest, AppearanceMode, AppearanceSource, AppearanceSourceKind, CompiledAppearance
from .appearance_compiler import AppearanceCompilerError
from .matugen import MatugenAppearanceCompiler, MatugenScheme
from .hyprland_settings import HyprlandPaletteSnapshot, HyprlandPaletteTransaction, SemanticPalette
from .cursor_theme import CompiledCursorTheme, CursorThemeError, CursorThemeTransaction


LOG = logging.getLogger("luminophore-shell")
SYSTEM_THEME_MODES = {"dark", "light"}
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class SystemThemeError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class SystemThemeSource:
    connector: str
    palette: Palette
    scheme: MatugenScheme | None = None


@dataclass(frozen=True)
class SemanticTheme:
    mode: str
    source_connector: str
    source_primary: str
    source_secondary: str
    tokens: dict[str, str]
    generation_id: str = ""


@dataclass(frozen=True)
class SystemThemePreview:
    preview_id: str
    config_digest: str
    semantic: SemanticTheme
    outputs: dict[str, bytes]
    target_ids: tuple[str, ...] = ()
    hyprland_settings: dict[str, str] = field(default_factory=dict)
    capability_digest: str = ""
    cursor_generation: str = ""


@dataclass(frozen=True)
class SystemThemeState:
    phase: str = "idle"
    message: str = ""
    error_category: str = ""
    preview: SystemThemePreview | None = None
    backup_available: bool = False
    drifted: bool = False


@dataclass(frozen=True)
class SystemThemeConfigSnapshot:
    payload: bytes
    mode: int
    before: ShellConfig
    after: ShellConfig


def _hex_rgb(value: str) -> tuple[float, float, float]:
    if not _HEX.fullmatch(value):
        raise SystemThemeError("validation", f"유효하지 않은 색상입니다: {value}")
    return tuple(int(value[index:index + 2], 16) / 255 for index in (1, 3, 5))  # type: ignore[return-value]


def _relative_luminance(value: str) -> float:
    def channel(raw: float) -> float:
        return raw / 12.92 if raw <= 0.04045 else ((raw + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(item) for item in _hex_rgb(value))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    first, second = _relative_luminance(foreground), _relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


class MatugenThemeBackend:
    REQUIRED_TOKENS = (
        "primary", "on_primary", "secondary", "on_secondary", "surface",
        "on_surface", "surface_container", "error", "on_error", "outline",
    )
    OUTPUT_NAMES = ("alacritty", "btop", "ghostty", "gtk3", "gtk4", "kde", "kitty", "qt")

    def __init__(
        self,
        executable: Path = Path("/usr/bin/matugen"),
        template_root: Path | None = None,
        timeout_seconds: float = 10.0,
        palette_backend: object | None = None,
        compiler: MatugenAppearanceCompiler | None = None,
    ) -> None:
        self.executable = executable
        self.template_root = template_root or Path(__file__).with_name("templates") / "matugen"
        self.timeout_seconds = timeout_seconds
        # palette_backend remains accepted for source compatibility only.
        self.compiler = compiler or MatugenAppearanceCompiler(executable, template_root, timeout_seconds)

    def shutdown(self) -> None:
        self.compiler.shutdown()

    def preview_compiled(
        self, compiled: CompiledAppearance, connector: str, expected_config_digest: str, mode: str,
    ) -> SystemThemePreview:
        tokens = dict(compiled.scheme)
        palette = dict(compiled.palette)
        outputs = dict(compiled.outputs)
        missing = set(self.OUTPUT_NAMES) - set(outputs)
        if missing:
            raise SystemThemeError("backend_incompatible", f"compiled appearance 출력이 없습니다: {', '.join(sorted(missing))}")
        primary = tokens.get("primary") or palette.get("primary", "")
        secondary = tokens.get("secondary") or palette.get("secondary", "")
        hyprland_settings = {
            "primary": primary,
            "surface_container": tokens.get("surface_container", ""),
            "secondary": secondary,
            "error": tokens.get("error", ""),
        }
        for value in hyprland_settings.values():
            _hex_rgb(value)
        fingerprint = hashlib.sha256()
        for value in (expected_config_digest, mode, connector, compiled.generation_id):
            fingerprint.update(value.encode())
        return SystemThemePreview(
            fingerprint.hexdigest(), expected_config_digest,
            SemanticTheme(mode, connector, primary.upper(), secondary.upper(), tokens, compiled.generation_id),
            {name: outputs[name] for name in self.OUTPUT_NAMES},
            (),
            hyprland_settings,
        )

    def preview(
        self,
        primary: str,
        secondary: str,
        mode: str,
        connector: str,
        expected_config_digest: str,
        scheme: MatugenScheme | None = None,
    ) -> SystemThemePreview:
        if mode not in SYSTEM_THEME_MODES:
            raise SystemThemeError("validation", "시스템 밝기는 dark 또는 light여야 합니다")
        _hex_rgb(primary)
        _hex_rgb(secondary)
        try:
            if scheme is not None:
                compiled = self.compiler.compile_scheme(scheme, AppearanceMode(mode), self.OUTPUT_NAMES)
            else:
                compiled = self.compiler.compile(AppearanceCompileRequest(
                    AppearanceSource(AppearanceSourceKind.COLOR, primary), AppearanceMode(mode), self.OUTPUT_NAMES,
                ))
            result = self.preview_compiled(compiled, connector, expected_config_digest, mode)
            if scheme is not None:
                result = replace(result, semantic=replace(result.semantic, generation_id=scheme.generation_id))
            return result
        except AppearanceCompilerError as exc:
            raise SystemThemeError(exc.category, str(exc)) from exc


@dataclass(frozen=True)
class SystemThemePaths:
    config_home: Path
    data_home: Path
    state_home: Path
    plasma_executable: Path = Path("/usr/bin/plasma-apply-colorscheme")
    gsettings_executable: Path = Path("/usr/bin/gsettings")

    @classmethod
    def current(cls) -> SystemThemePaths:
        home = Path.home()
        return cls(
            Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")).expanduser(),
            Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")).expanduser(),
            Path(os.environ.get("XDG_STATE_HOME", home / ".local/state")).expanduser(),
        )

    @property
    def state_root(self) -> Path:
        return self.state_home / "luminophore-shell"

    @property
    def backup_root(self) -> Path:
        return self.state_root / "system-theme-backups"

    @property
    def metadata_path(self) -> Path:
        return self.state_root / "system-theme.json"

    def targets(self) -> dict[str, Path]:
        ghostty_root = self.config_home / "ghostty"
        return {
            "gtk3_palette": self.config_home / "gtk-3.0/matugen.css",
            "gtk4_palette": self.config_home / "gtk-4.0/matugen.css",
            "gtk3_entry": self.config_home / "gtk-3.0/gtk.css",
            "gtk4_entry": self.config_home / "gtk-4.0/gtk.css",
            "qt5_palette": self.config_home / "qt5ct/colors/matugen.conf",
            "qt6_palette": self.config_home / "qt6ct/colors/matugen.conf",
            "qt5_config": self.config_home / "qt5ct/qt5ct.conf",
            "qt6_config": self.config_home / "qt6ct/qt6ct.conf",
            "kde_palette": self.data_home / "color-schemes/matugen.colors",
            "kdeglobals": self.config_home / "kdeglobals",
            "kitty_palette": self.config_home / "kitty/themes/matugen.conf",
            "kitty_config": self.config_home / "kitty/kitty.conf",
            "alacritty_palette": self.config_home / "alacritty/themes/matugen.toml",
            "alacritty_config": self.config_home / "alacritty/alacritty.toml",
            "btop_palette": self.config_home / "btop/themes/matugen.theme",
            "btop_config": self.config_home / "btop/btop.conf",
            "ghostty_palette": ghostty_root / "themes/matugen",
            "ghostty_config": ghostty_root / "config.ghostty",
        }


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _default_command_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    environment = None
    if Path(argv[0]).name == "plasma-apply-colorscheme":
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = "offscreen"
        environment["QT_QPA_PLATFORMTHEME"] = ""
    try:
        result = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=8.0,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SystemThemeError("apply_failed", f"명령을 실행하지 못했습니다: {Path(argv[0]).name}") from exc
    if result.returncode:
        detail = " ".join((result.stderr or result.stdout).strip().split())[:240]
        message = f"명령이 실패했습니다: {Path(argv[0]).name}"
        raise SystemThemeError("apply_failed", f"{message}: {detail}" if detail else message)
    return result


class GSettingsAdapter:
    SCHEMA = "org.gnome.desktop.interface"

    def __init__(self, executable: Path, runner: CommandRunner = _default_command_runner) -> None:
        self.executable = executable
        self.runner = runner

    def _get(self, key: str) -> str:
        result = self.runner((str(self.executable), "get", self.SCHEMA, key))
        value = result.stdout.strip()
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            parsed = value
        if not isinstance(parsed, str):
            raise SystemThemeError("apply_failed", f"GNOME {key} 값을 읽지 못했습니다")
        return parsed

    def _set(self, key: str, value: str) -> None:
        self.runner((str(self.executable), "set", self.SCHEMA, key, value))
        if self._get(key) != value:
            raise SystemThemeError("apply_failed", f"GNOME {key} 적용을 확인하지 못했습니다")

    def snapshot(self) -> dict[str, str]:
        return {"color_scheme": self._get("color-scheme"), "gtk_theme": self._get("gtk-theme")}

    def apply(self, mode: str) -> None:
        self._set("color-scheme", f"prefer-{mode}")
        self._set("gtk-theme", "adw-gtk3-dark" if mode == "dark" else "adw-gtk3")

    def restore(self, values: Mapping[str, str]) -> None:
        self._set("color-scheme", values["color_scheme"])
        self._set("gtk-theme", values["gtk_theme"])


def _atomic_write(path: Path, payload: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode if mode is not None else 0o600)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _ensure_gtk_import(text: str) -> str:
    replacement = '@import url("matugen.css");\n'
    lines = text.splitlines(keepends=True)
    theme_import = re.compile(r'''^\s*@import\s+url\(\s*["'][^"']+\.css["']\s*\)\s*;\s*$''')
    matches = [index for index, line in enumerate(lines) if theme_import.match(line.strip())]
    if len(matches) > 1:
        raise SystemThemeError("apply_failed", "GTK theme import가 하나가 아닙니다")
    if matches:
        lines[matches[0]] = replacement
        return "".join(lines)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + replacement


def _replace_theme_line(text: str, pattern: str, replacement: str, missing_message: str) -> str:
    lines = text.splitlines(keepends=True)
    regex = re.compile(pattern)
    matches = [index for index, line in enumerate(lines) if regex.match(line.strip())]
    if len(matches) != 1:
        raise SystemThemeError("apply_failed", missing_message)
    suffix = "\n" if lines[matches[0]].endswith("\n") else ""
    lines[matches[0]] = replacement + suffix
    return "".join(lines)


def _patch_alacritty_import(text: str) -> str:
    current = '"~/.config/alacritty/themes/matugen.toml"'
    if current in text:
        return text
    lines = text.splitlines(keepends=True)
    import_line = re.compile(r"^\s*import\s*=")
    theme_path = re.compile(r'''(?P<quote>["'])[^"']*/themes/[^"']+\.toml(?P=quote)''')
    matches = [
        index
        for index, line in enumerate(lines)
        if import_line.match(line) and len(theme_path.findall(line)) == 1
    ]
    if len(matches) != 1:
        raise SystemThemeError("apply_failed", "Alacritty theme import를 하나로 식별하지 못했습니다")
    lines[matches[0]] = theme_path.sub(current, lines[matches[0]], count=1)
    return "".join(lines)


def _patch_ini_values(text: str, section: str, values: Mapping[str, str]) -> str:
    lines = text.splitlines(keepends=True)
    header = re.compile(r"^\s*\[([^]]+)]\s*$")
    start = next((index for index, line in enumerate(lines) if header.match(line.strip()) and header.match(line.strip()).group(1) == section), None)  # type: ignore[union-attr]
    if start is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.append(f"[{section}]\n")
        start = len(lines) - 1
        end = len(lines)
    else:
        end = next((index for index in range(start + 1, len(lines)) if header.match(lines[index].strip())), len(lines))
    for key, value in values.items():
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        matches = [index for index in range(start + 1, end) if pattern.match(lines[index])]
        replacement = f"{key}={value}\n"
        if matches:
            lines[matches[0]] = replacement
            for duplicate in reversed(matches[1:]):
                lines.pop(duplicate)
                end -= 1
        else:
            lines.insert(end, replacement)
            end += 1
    return "".join(lines)


def _kde_scheme(text: str) -> str:
    in_general = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_general = stripped == "[General]"
        elif in_general and stripped.startswith("ColorScheme="):
            return stripped.split("=", 1)[1].strip()
    return ""


class SystemThemeTargetManager:
    OPTIONAL_TARGETS = {
        "kitty": ("kitty_palette", "kitty_config"),
        "alacritty": ("alacritty_palette", "alacritty_config"),
        "btop": ("btop_palette", "btop_config"),
        "ghostty": ("ghostty_palette", "ghostty_config"),
    }
    TARGET_FILES = {
        "gtk3": ("gtk3_palette", "gtk3_entry"),
        "gtk4": ("gtk4_palette", "gtk4_entry"),
        "qt5": ("qt5_palette", "qt5_config"),
        "qt6": ("qt6_palette", "qt6_config"),
        "kde": ("kde_palette", "kdeglobals"),
        "kitty": ("kitty_palette", "kitty_config"),
        "alacritty": ("alacritty_palette", "alacritty_config"),
        "btop": ("btop_palette", "btop_config"),
        "ghostty": ("ghostty_palette", "ghostty_config"),
        "hyprland": (),
        "cursor": (),
    }

    def __init__(
        self,
        paths: SystemThemePaths | None = None,
        settings: GSettingsAdapter | None = None,
        command_runner: CommandRunner = _default_command_runner,
        hyprland: HyprlandPaletteTransaction | None = None,
        cursor: CursorThemeTransaction | None = None,
    ) -> None:
        self.paths = paths or SystemThemePaths.current()
        self.command_runner = command_runner
        self.settings = settings or GSettingsAdapter(self.paths.gsettings_executable, command_runner)
        self.hyprland = hyprland
        self.cursor = cursor
        self._pending_hyprland_snapshot: HyprlandPaletteSnapshot | None = None
        self._pending_cursor: CompiledCursorTheme | None = None
        self._config_path: Path | None = None
        self._config_saved: Callable[[ShellConfig, ShellConfig], None] | None = None

    def configure_config_transaction(
        self, path: Path, saved: Callable[[ShellConfig, ShellConfig], None],
    ) -> None:
        self._config_path = path
        self._config_saved = saved

    def _pointer_path(self, kind: str) -> Path:
        return self.paths.backup_root / f"{kind}.json"

    def availability(self) -> dict[str, bool]:
        targets = self.paths.targets()
        return {
            "gtk3": self.paths.gsettings_executable.is_file() and targets["gtk3_entry"].is_file(),
            "gtk4": self.paths.gsettings_executable.is_file() and targets["gtk4_entry"].is_file(),
            "qt5": targets["qt5_config"].is_file(),
            "qt6": targets["qt6_config"].is_file(),
            "kde": self.paths.plasma_executable.is_file() and targets["kdeglobals"].is_file(),
            "hyprland": self.hyprland is not None and self.hyprland.available(),
            "cursor": self.cursor is not None and self.cursor.available(),
            **{
                app: targets[config_name].is_file()
                for app, (_palette_name, config_name) in self.OPTIONAL_TARGETS.items()
            },
        }

    def selected_targets(self) -> tuple[str, ...]:
        return tuple(sorted(name for name, available in self.availability().items() if available))

    def bind_preview(self, preview: SystemThemePreview) -> SystemThemePreview:
        selected = self.selected_targets()
        cursor_generation = ""
        if "cursor" in selected:
            if self.cursor is None:
                raise SystemThemeError("compiler_missing", "cursor compiler가 준비되지 않았습니다")
            try:
                self._pending_cursor = self.cursor.prepare(preview.semantic.tokens)
            except CursorThemeError as exc:
                raise SystemThemeError(exc.category, str(exc)) from exc
            cursor_generation = self._pending_cursor.generation_id
        digest = hashlib.sha256("\0".join((*selected, cursor_generation)).encode()).hexdigest()
        return replace(
            preview,
            target_ids=selected,
            capability_digest=digest,
            cursor_generation=cursor_generation,
        )

    def _selected_files(self, target_ids: Sequence[str]) -> tuple[str, ...]:
        try:
            names = {name for target in target_ids for name in self.TARGET_FILES[target]}
        except KeyError as exc:
            raise SystemThemeError("stale_preview", "지원 대상 구성이 바뀌었습니다") from exc
        return tuple(sorted(names))

    def _read_pointer(self, kind: str) -> str:
        raw = read_json(self._pointer_path(kind), {})
        generation = raw.get("generation") if isinstance(raw, dict) else None
        if isinstance(generation, str) and re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", generation):
            return generation
        return ""

    def _write_pointer(self, kind: str, generation: str) -> None:
        write_json_atomic(self._pointer_path(kind), {"version": 1, "generation": generation})

    def backup_available(self) -> bool:
        return bool(self._read_pointer("pending") or self._read_pointer("current"))

    def drifted(self) -> bool:
        raw = read_json(self.paths.metadata_path, {})
        if not isinstance(raw, dict) or raw.get("version") != 1 or not raw.get("applied"):
            return False
        expected = raw.get("target_digests")
        if not isinstance(expected, dict):
            return False
        targets = self.paths.targets()
        return any(
            not isinstance(name, str)
            or not isinstance(digest, str)
            or name not in targets
            or _file_digest(targets[name]) != digest
            for name, digest in expected.items()
        )

    def _snapshot(
        self, target_ids: Sequence[str], config_snapshot: SystemThemeConfigSnapshot | None = None,
    ) -> tuple[str, dict[str, object]]:
        generation = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        directory = self.paths.backup_root / generation
        files_directory = directory / "files"
        files_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        files: dict[str, object] = {}
        for name in self._selected_files(target_ids):
            path = self.paths.targets()[name]
            if path.is_symlink():
                raise SystemThemeError("apply_failed", f"symbolic link target은 안전하게 변경할 수 없습니다: {name}")
            if path.exists():
                payload = path.read_bytes()
                backup_name = f"{name}.bin"
                _atomic_write(files_directory / backup_name, payload, 0o600)
                files[name] = {
                    "path": str(path),
                    "existed": True,
                    "mode": path.stat().st_mode & 0o777,
                    "digest": hashlib.sha256(payload).hexdigest(),
                    "backup": f"files/{backup_name}",
                }
            else:
                files[name] = {"path": str(path), "existed": False}
        kde_text = ""
        if "kde" in target_ids:
            try:
                kde_text = self.paths.targets()["kdeglobals"].read_text(encoding="utf-8")
            except OSError:
                pass
        manifest: dict[str, object] = {
            "version": 1,
            "generation": generation,
            "created_at": time.time(),
            "files": files,
            "settings": self.settings.snapshot() if set(target_ids) & {"gtk3", "gtk4"} else None,
            "kde_scheme": _kde_scheme(kde_text) if "kde" in target_ids else None,
            "target_ids": list(target_ids),
        }
        if "hyprland" in target_ids and self.hyprland is not None:
            snapshot = self.hyprland.snapshot()
            self._pending_hyprland_snapshot = snapshot
            manifest["hyprland"] = {
                "live_values": dict(snapshot.live_values),
                "file_existed": snapshot.file_existed,
                "file_payload": base64.b64encode(snapshot.file_payload).decode("ascii"),
            }
        if "cursor" in target_ids:
            if self.cursor is None or self._pending_cursor is None:
                raise SystemThemeError("backup_failed", "cursor transaction이 준비되지 않았습니다")
            try:
                manifest["cursor"] = self.cursor.snapshot(directory / "cursor")
            except CursorThemeError as exc:
                raise SystemThemeError(exc.category, str(exc)) from exc
        if config_snapshot is not None:
            manifest["config"] = {
                "payload": base64.b64encode(config_snapshot.payload).decode("ascii"),
                "mode": config_snapshot.mode,
                "digest": hashlib.sha256(config_snapshot.payload).hexdigest(),
                "before_mode": config_snapshot.before.system_theme.mode,
                "after_mode": config_snapshot.after.system_theme.mode,
            }
        write_json_atomic(directory / "manifest.json", manifest)
        self._write_pointer("pending", generation)
        return generation, manifest

    def _write_payload(self, path: Path, payload: bytes) -> None:
        mode = (path.stat().st_mode & 0o777) if path.exists() else 0o644
        _atomic_write(path, payload, mode)

    def _install(self, preview: SystemThemePreview) -> None:
        targets = self.paths.targets()
        selected = set(preview.target_ids)
        output_mapping: dict[str, bytes] = {}
        for target, output, file_name in (
            ("gtk3", "gtk3", "gtk3_palette"), ("gtk4", "gtk4", "gtk4_palette"),
            ("qt5", "qt", "qt5_palette"), ("qt6", "qt", "qt6_palette"),
            ("kde", "kde", "kde_palette"),
        ):
            if target in selected:
                output_mapping[file_name] = preview.outputs[output]
        for app, (target_name, _config_name) in self.OPTIONAL_TARGETS.items():
            if app in selected:
                output_mapping[target_name] = preview.outputs[app]
        for name, payload in output_mapping.items():
            self._write_payload(targets[name], payload)
        for name in tuple(name for target, name in (("gtk3", "gtk3_entry"), ("gtk4", "gtk4_entry")) if target in selected):
            try:
                current = targets[name].read_text(encoding="utf-8")
            except OSError:
                current = ""
            self._write_payload(targets[name], _ensure_gtk_import(current).encode())
        qt_configs = tuple(name for target, name in (("qt5", "qt5_config"), ("qt6", "qt6_config")) if target in selected)
        for name in qt_configs:
            try:
                current = targets[name].read_text(encoding="utf-8")
            except OSError:
                current = ""
            palette_name = "qt6ct" if name == "qt6_config" else "qt5ct"
            palette_path = self.paths.config_home / f"{palette_name}/colors/matugen.conf"
            patched = _patch_ini_values(
                current,
                "Appearance",
                {"color_scheme_path": str(palette_path), "custom_palette": "true"},
            )
            self._write_payload(targets[name], patched.encode())
        if "kitty" in selected:
            current = targets["kitty_config"].read_text(encoding="utf-8")
            patched = _replace_theme_line(
                current,
                r"^include\s+themes/[^\s]+\.conf$",
                "include themes/matugen.conf",
                "Kitty theme include를 찾지 못했습니다",
            )
            self._write_payload(targets["kitty_config"], patched.encode())
        if "alacritty" in selected:
            current = targets["alacritty_config"].read_text(encoding="utf-8")
            self._write_payload(targets["alacritty_config"], _patch_alacritty_import(current).encode())
        if "btop" in selected:
            current = targets["btop_config"].read_text(encoding="utf-8")
            patched = _replace_theme_line(
                current,
                r'^color_theme\s*=\s*"[^"]*"$',
                'color_theme = "matugen"',
                "Btop color_theme 선택을 찾지 못했습니다",
            )
            self._write_payload(targets["btop_config"], patched.encode())
        if "ghostty" in selected:
            current = targets["ghostty_config"].read_text(encoding="utf-8")
            patched = _replace_theme_line(
                current,
                r"^theme\s*=.*$",
                "theme = matugen",
                "Ghostty theme 선택을 찾지 못했습니다",
            )
            self._write_payload(targets["ghostty_config"], patched.encode())
        if selected & {"gtk3", "gtk4"}:
            self.settings.apply(preview.semantic.mode)
        if "kde" in selected:
            self.command_runner((str(self.paths.plasma_executable), "matugen"))
            current = targets["kdeglobals"].read_text(encoding="utf-8")
            patched = _patch_ini_values(current, "General", {"ColorScheme": "matugen", "Name": "matugen"})
            self._write_payload(targets["kdeglobals"], patched.encode())
        if "hyprland" in selected:
            if self.hyprland is None or self._pending_hyprland_snapshot is None:
                raise SystemThemeError("apply_failed", "Hyprland semantic palette transaction이 없습니다")
            try:
                palette = SemanticPalette.from_scheme(preview.semantic.generation_id, preview.hyprland_settings)
                self.hyprland.apply_with_snapshot(palette, self._pending_hyprland_snapshot)
            except Exception as exc:
                if isinstance(exc, SystemThemeError):
                    raise
                raise SystemThemeError(getattr(exc, "category", "apply_failed"), str(exc)) from exc
        if "cursor" in selected:
            if self.cursor is None or self._pending_cursor is None:
                raise SystemThemeError("apply_failed", "cursor transaction이 준비되지 않았습니다")
            if self._pending_cursor.generation_id != preview.cursor_generation:
                raise SystemThemeError("stale_preview", "cursor generation이 바뀌었습니다")
            try:
                self.cursor.apply(self._pending_cursor)
            except CursorThemeError as exc:
                raise SystemThemeError(exc.category, str(exc)) from exc
        self._verify(preview)

    def _verify(self, preview: SystemThemePreview) -> None:
        targets = self.paths.targets()
        selected = set(preview.target_ids)
        expected: dict[str, bytes] = {}
        for target, output, file_name in (("gtk3", "gtk3", "gtk3_palette"), ("gtk4", "gtk4", "gtk4_palette"), ("qt5", "qt", "qt5_palette"), ("qt6", "qt", "qt6_palette"), ("kde", "kde", "kde_palette")):
            if target in selected:
                expected[file_name] = preview.outputs[output]
        for app, (target_name, _config_name) in self.OPTIONAL_TARGETS.items():
            if app in selected:
                expected[target_name] = preview.outputs[app]
        for name, payload in expected.items():
            if _file_digest(targets[name]) != hashlib.sha256(payload).hexdigest():
                raise SystemThemeError("apply_failed", f"{name} 적용을 확인하지 못했습니다")
        for name in tuple(name for target, name in (("gtk3", "gtk3_entry"), ("gtk4", "gtk4_entry")) if target in selected):
            try:
                text = targets[name].read_text(encoding="utf-8")
            except OSError as exc:
                raise SystemThemeError("apply_failed", f"{name} import를 확인하지 못했습니다") from exc
            if "matugen.css" not in text or "@import" not in text:
                raise SystemThemeError("apply_failed", f"{name} import를 확인하지 못했습니다")
        for target, name in (("qt5", "qt5_config"), ("qt6", "qt6_config")):
            if target in selected:
                text = targets[name].read_text(encoding="utf-8")
                if "custom_palette=true" not in text or "colors/matugen.conf" not in text:
                    raise SystemThemeError("apply_failed", f"{target} palette 선택을 확인하지 못했습니다")
        if "kde" in selected and _kde_scheme(targets["kdeglobals"].read_text(encoding="utf-8")).casefold() != "matugen":
            raise SystemThemeError("apply_failed", "KDE palette 선택을 확인하지 못했습니다")
        if "kitty" in selected:
            if "include themes/matugen.conf" not in targets["kitty_config"].read_text(encoding="utf-8"):
                raise SystemThemeError("apply_failed", "Kitty theme 선택을 확인하지 못했습니다")
        if "alacritty" in selected:
            if "themes/matugen.toml" not in targets["alacritty_config"].read_text(encoding="utf-8"):
                raise SystemThemeError("apply_failed", "Alacritty theme 선택을 확인하지 못했습니다")
        if "btop" in selected:
            if 'color_theme = "matugen"' not in targets["btop_config"].read_text(encoding="utf-8"):
                raise SystemThemeError("apply_failed", "Btop theme 선택을 확인하지 못했습니다")
        if "ghostty" in selected:
            if "theme = matugen" not in targets["ghostty_config"].read_text(encoding="utf-8"):
                raise SystemThemeError("apply_failed", "Ghostty theme 선택을 확인하지 못했습니다")
        if "hyprland" in selected:
            if self.hyprland is None:
                raise SystemThemeError("apply_failed", "Hyprland semantic palette transaction이 없습니다")
            expected_palette = SemanticPalette.from_scheme(preview.semantic.generation_id, preview.hyprland_settings)
            observed = dict(self.hyprland.runtime.read_options(tuple(sorted(dict(expected_palette.values)))))
            if observed != dict(expected_palette.values):
                raise SystemThemeError("apply_failed", "Hyprland semantic palette 적용을 확인하지 못했습니다")
        if "cursor" in selected:
            if self.cursor is None or self._pending_cursor is None:
                raise SystemThemeError("apply_failed", "cursor transaction이 준비되지 않았습니다")
            try:
                self.cursor.verify(self._pending_cursor)
            except CursorThemeError as exc:
                raise SystemThemeError(exc.category, str(exc)) from exc

    def _restore_generation(self, generation: str) -> None:
        if not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", generation):
            raise SystemThemeError("rollback_failed", "시스템 팔레트 backup 식별자가 잘못됐습니다")
        directory = self.paths.backup_root / generation
        manifest = read_json(directory / "manifest.json", {})
        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise SystemThemeError("rollback_failed", "시스템 팔레트 backup manifest가 손상됐습니다")
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise SystemThemeError("rollback_failed", "시스템 팔레트 backup 파일 목록이 없습니다")
        targets = self.paths.targets()
        for name, row in files.items():
            if not isinstance(name, str) or not isinstance(row, dict):
                raise SystemThemeError("rollback_failed", "시스템 팔레트 backup 항목이 잘못됐습니다")
            path_value = row.get("path")
            if not isinstance(path_value, str) or name not in targets or Path(path_value) != targets[name]:
                raise SystemThemeError("rollback_failed", "시스템 팔레트 backup 경로가 잘못됐습니다")
            path = Path(path_value)
            if row.get("existed"):
                backup_value = row.get("backup")
                if not isinstance(backup_value, str):
                    raise SystemThemeError("rollback_failed", f"{name} backup 내용이 없습니다")
                payload = (directory / backup_value).read_bytes()
                expected = row.get("digest")
                if not isinstance(expected, str) or hashlib.sha256(payload).hexdigest() != expected:
                    raise SystemThemeError("rollback_failed", f"{name} backup checksum이 다릅니다")
                mode_value = row.get("mode")
                _atomic_write(path, payload, int(mode_value) if isinstance(mode_value, int) else 0o600)
            else:
                path.unlink(missing_ok=True)
        settings = manifest.get("settings")
        if settings is not None:
            if not isinstance(settings, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in settings.items()):
                raise SystemThemeError("rollback_failed", "GNOME appearance backup이 잘못됐습니다")
            self.settings.restore(settings)
        hyprland = manifest.get("hyprland")
        if hyprland is not None:
            if self.hyprland is None or not isinstance(hyprland, dict):
                raise SystemThemeError("rollback_failed", "Hyprland semantic palette backup이 잘못됐습니다")
            live_values = hyprland.get("live_values")
            encoded = hyprland.get("file_payload")
            if not isinstance(live_values, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in live_values.items()) or not isinstance(encoded, str):
                raise SystemThemeError("rollback_failed", "Hyprland semantic palette backup이 손상됐습니다")
            try:
                payload = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise SystemThemeError("rollback_failed", "Hyprland semantic palette backup payload가 손상됐습니다") from exc
            snapshot = HyprlandPaletteSnapshot(tuple(sorted(live_values.items())), bool(hyprland.get("file_existed")), payload)
            try:
                self.hyprland.restore(snapshot)
            except Exception as exc:
                raise SystemThemeError("rollback_failed", str(exc)) from exc
        cursor = manifest.get("cursor")
        if cursor is not None:
            if self.cursor is None or not isinstance(cursor, dict):
                raise SystemThemeError("rollback_failed", "cursor backup이 잘못됐습니다")
            try:
                self.cursor.restore(cursor, directory)
            except CursorThemeError as exc:
                raise SystemThemeError("rollback_failed", str(exc)) from exc
        previous_scheme = manifest.get("kde_scheme")
        if isinstance(previous_scheme, str) and previous_scheme and self.paths.plasma_executable.is_file():
            self.command_runner((str(self.paths.plasma_executable), previous_scheme))
            kde_row = files.get("kdeglobals")
            if isinstance(kde_row, dict) and kde_row.get("existed"):
                backup_value = kde_row.get("backup")
                if isinstance(backup_value, str):
                    payload = (directory / backup_value).read_bytes()
                    mode_value = kde_row.get("mode")
                    _atomic_write(
                        Path(str(kde_row["path"])),
                        payload,
                        int(mode_value) if isinstance(mode_value, int) else 0o600,
                    )
        for name, row in files.items():
            path = Path(row["path"])
            if row.get("existed"):
                if _file_digest(path) != row.get("digest"):
                    raise SystemThemeError("rollback_failed", f"{name} 복구를 확인하지 못했습니다")
            elif path.exists():
                raise SystemThemeError("rollback_failed", f"{name} 신규 파일을 되돌리지 못했습니다")
        config = manifest.get("config")
        if config is not None:
            if not isinstance(config, dict) or self._config_path is None or self._config_saved is None:
                raise SystemThemeError("rollback_failed", "시스템 테마 config backup을 복구할 수 없습니다")
            encoded = config.get("payload")
            digest = config.get("digest")
            mode = config.get("mode")
            before_mode = config.get("before_mode")
            after_mode = config.get("after_mode")
            if not isinstance(encoded, str) or not isinstance(digest, str) or not isinstance(mode, int) or before_mode not in SYSTEM_THEME_MODES or after_mode not in SYSTEM_THEME_MODES:
                raise SystemThemeError("rollback_failed", "시스템 테마 config backup이 손상됐습니다")
            try:
                payload = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise SystemThemeError("rollback_failed", "시스템 테마 config backup payload가 손상됐습니다") from exc
            if hashlib.sha256(payload).hexdigest() != digest:
                raise SystemThemeError("rollback_failed", "시스템 테마 config backup checksum이 다릅니다")
            try:
                current = load_config(self._config_path)
                if current.system_theme.mode != after_mode:
                    raise ValueError("config changed after theme apply")
                from .config import _native_path
                if _native_path(self._config_path):
                    restored = write_config_patch(self._config_path, {'system_theme.mode': before_mode}, config_digest(self._config_path))
                else:
                    _atomic_write(self._config_path, payload, mode)
                    restored = load_config(self._config_path)
                    if self._config_path.read_bytes() != payload:
                        raise ValueError("config readback mismatch")
                if restored.system_theme.mode != before_mode:
                    raise ValueError("config mode mismatch")
                self._config_saved(current, restored)
            except Exception as exc:
                raise SystemThemeError("rollback_failed", "시스템 테마 config backup을 복구하지 못했습니다") from exc

    def apply(
        self, preview: SystemThemePreview, config_snapshot: SystemThemeConfigSnapshot | None = None,
    ) -> str:
        if not preview.capability_digest:
            preview = self.bind_preview(preview)
        current = self.bind_preview(preview)
        if preview.target_ids != current.target_ids or preview.capability_digest != current.capability_digest:
            raise SystemThemeError("stale_preview", "설치된 시스템 테마 대상이 바뀌었습니다. 미리보기를 다시 생성하세요")
        try:
            generation, _manifest = self._snapshot(preview.target_ids, config_snapshot)
        except SystemThemeError:
            raise
        except Exception as exc:
            raise SystemThemeError("backup_failed", "기존 시스템 팔레트를 backup하지 못했습니다") from exc
        try:
            self._install(preview)
            targets = self.paths.targets()
            digest_names = tuple(name for name in self._selected_files(preview.target_ids) if targets[name].exists())
            write_json_atomic(
                self.paths.metadata_path,
                {
                    "version": 1,
                    "applied": True,
                    "applied_at": time.time(),
                    "mode": preview.semantic.mode,
                    "source_connector": preview.semantic.source_connector,
                    "source_primary": preview.semantic.source_primary,
                    "target_digests": {name: _file_digest(targets[name]) for name in digest_names},
                    "backup_generation": generation,
                },
            )
            self._write_pointer("current", generation)
            self._write_pointer("pending", "")
            self._pending_hyprland_snapshot = None
            self._pending_cursor = None
        except Exception as exc:
            try:
                self._restore_generation(generation)
                self._write_pointer("pending", "")
                self._write_pointer("current", "")
                self._pending_hyprland_snapshot = None
                self._pending_cursor = None
            except Exception as rollback_exc:
                raise SystemThemeError("rollback_failed", "시스템 팔레트 적용과 자동 복구가 모두 실패했습니다") from rollback_exc
            message = str(exc) if isinstance(exc, SystemThemeError) else "시스템 팔레트 적용 중 오류가 발생했습니다"
            raise SystemThemeError("apply_failed_rolled_back", f"{message} · 이전 색상으로 복구했습니다") from exc
        return generation

    def rollback(self) -> str:
        generation = self._read_pointer("pending") or self._read_pointer("current")
        if not generation:
            raise SystemThemeError("no_backup", "복구할 이전 시스템 팔레트가 없습니다")
        self._restore_generation(generation)
        self._write_pointer("pending", "")
        self._write_pointer("current", "")
        write_json_atomic(
            self.paths.metadata_path,
            {"version": 1, "applied": False, "rolled_back_at": time.time(), "restored_generation": generation},
        )
        return generation


class SystemThemeController:
    BUSY_PHASES = {"generating", "applying", "rolling_back"}

    def __init__(
        self,
        config_path: Path,
        source: Callable[[], SystemThemeSource | None],
        changed: Callable[[SystemThemeState], None],
        config_saved: Callable[[ShellConfig, ShellConfig], None],
        backend: MatugenThemeBackend | None = None,
        targets: SystemThemeTargetManager | None = None,
    ) -> None:
        self.config_path = config_path
        self.source = source
        self.changed = changed
        self.config_saved = config_saved
        self.backend = backend or MatugenThemeBackend()
        self.targets = targets or SystemThemeTargetManager()
        self.state = SystemThemeState()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._config_rollback: SystemThemeConfigSnapshot | None = None
        self._targets_persist_config = hasattr(self.targets, "configure_config_transaction")
        if self._targets_persist_config:
            self.targets.configure_config_transaction(self.config_path, self.config_saved)

    def _restore_config(self, snapshot: SystemThemeConfigSnapshot) -> None:
        try:
            from .config import _native_path
            if _native_path(self.config_path):
                current = load_config(self.config_path)
                if current.system_theme.mode != snapshot.after.system_theme.mode:
                    raise ConfigConflictError('system theme changed after apply')
                restored = write_config_patch(self.config_path, {'system_theme.mode': snapshot.before.system_theme.mode}, config_digest(self.config_path))
                self.config_saved(current, restored)
                return
            _atomic_write(self.config_path, snapshot.payload, snapshot.mode)
            if self.config_path.read_bytes() != snapshot.payload:
                raise OSError("config readback mismatch")
            self.config_saved(snapshot.after, snapshot.before)
        except Exception as exc:
            raise SystemThemeError("rollback_failed", "시스템 테마 설정을 이전 상태로 복구하지 못했습니다") from exc

    def _set_state(self, state: SystemThemeState) -> None:
        with self._lock:
            self.state = state
        self.changed(state)

    def _flags(self) -> tuple[bool, bool]:
        return self.targets.backup_available(), self.targets.drifted()

    def open(self) -> SystemThemeState:
        backup, drifted = self._flags()
        with self._lock:
            current = self.state
        message = current.message or ("다른 도구가 시스템 팔레트를 변경했습니다" if drifted else "")
        state = SystemThemeState(
            current.phase,
            message,
            current.error_category,
            current.preview,
            backup,
            drifted,
        )
        self._set_state(state)
        return state

    def _fail(self, category: str, message: str, preview: SystemThemePreview | None = None) -> None:
        backup, drifted = self._flags()
        self._set_state(SystemThemeState("error", message, category, preview, backup, drifted))

    def preview(self, mode: str) -> None:
        with self._lock:
            if self.state.phase in self.BUSY_PHASES:
                return
        source = self.source()
        if source is None:
            self._fail("main_output_missing", "왼쪽 주 작업 모니터의 팔레트를 찾을 수 없습니다")
            return
        if mode not in SYSTEM_THEME_MODES:
            self._fail("validation", "시스템 밝기는 dark 또는 light여야 합니다")
            return
        digest = config_digest(self.config_path)
        backup, drifted = self._flags()
        self._set_state(SystemThemeState("generating", "시스템 팔레트를 만드는 중…", "", None, backup, drifted))
        self._thread = threading.Thread(
            target=self._preview_worker,
            args=(source, mode, digest),
            name="luminophore-system-theme-preview",
            daemon=True,
        )
        self._thread.start()

    def _preview_worker(self, source: SystemThemeSource, mode: str, digest: str) -> None:
        try:
            preview = self.backend.preview(
                source.palette.primary,
                source.palette.secondary,
                mode,
                source.connector,
                digest,
                source.scheme,
            )
        except SystemThemeError as exc:
            self._fail(exc.category, str(exc))
            return
        except Exception:
            LOG.exception("system theme preview failed unexpectedly")
            self._fail("generation_failed", "시스템 팔레트 미리보기 중 오류가 발생했습니다")
            return
        preview = self.targets.bind_preview(preview)
        backup, drifted = self._flags()
        self._set_state(SystemThemeState("preview", "시스템 적용 전 미리보기입니다", "", preview, backup, drifted))

    def apply(self, preview_id: str) -> None:
        with self._lock:
            state = self.state
            if state.phase in self.BUSY_PHASES:
                return
            preview = state.preview
        if not preview or preview.preview_id != preview_id:
            self._fail("stale_preview", "미리보기를 다시 생성해야 합니다")
            return
        source = self.source()
        if (
            source is None
            or source.connector != preview.semantic.source_connector
            or source.palette.primary.upper() != preview.semantic.source_primary
            or source.palette.secondary.upper() != preview.semantic.source_secondary
            or (
                preview.semantic.generation_id
                and source.scheme is not None
                and source.scheme.generation_id != preview.semantic.generation_id
            )
            or config_digest(self.config_path) != preview.config_digest
        ):
            self._fail("stale_preview", "설정 또는 왼쪽 모니터 색이 바뀌었습니다. 미리보기를 다시 생성하세요", preview)
            return
        backup, drifted = self._flags()
        self._set_state(SystemThemeState("applying", "시스템 팔레트를 적용하는 중…", "", preview, backup, drifted))
        self._thread = threading.Thread(
            target=self._apply_worker,
            args=(preview,),
            name="luminophore-system-theme-apply",
            daemon=True,
        )
        self._thread.start()

    def _apply_worker(self, preview: SystemThemePreview) -> None:
        snapshot: SystemThemeConfigSnapshot | None = None
        try:
            old_config = load_config(self.config_path)
            old_payload = self.config_path.read_bytes()
            old_mode = self.config_path.stat().st_mode & 0o777
            new_config = write_config_patch(
                self.config_path,
                {"system_theme.mode": preview.semantic.mode},
                preview.config_digest,
            )
            snapshot = SystemThemeConfigSnapshot(old_payload, old_mode, old_config, new_config)
            self.config_saved(old_config, new_config)
            if self._targets_persist_config:
                self.targets.apply(preview, snapshot)
            else:
                self.targets.apply(preview)
        except ConfigConflictError as exc:
            self._fail("conflict", str(exc), preview)
            return
        except ConfigError as exc:
            self._fail("config_write_failed", str(exc), preview)
            return
        except SystemThemeError as exc:
            if snapshot is not None and not self._targets_persist_config:
                try:
                    self._restore_config(snapshot)
                except SystemThemeError as rollback_exc:
                    self._fail(rollback_exc.category, str(rollback_exc), preview)
                    return
            self._fail(exc.category, str(exc), preview)
            return
        except Exception:
            if snapshot is not None:
                try:
                    self._restore_config(snapshot)
                except SystemThemeError as rollback_exc:
                    self._fail(rollback_exc.category, str(rollback_exc), preview)
                    return
            LOG.exception("system theme apply failed unexpectedly")
            self._fail("apply_failed", "시스템 팔레트 적용 중 오류가 발생했습니다", preview)
            return
        self._config_rollback = snapshot
        backup, drifted = self._flags()
        self._set_state(
            SystemThemeState(
                "applied",
                "시스템 팔레트를 적용했습니다 · Kitty/Btop과 실행 중인 Qt 앱은 reload 또는 재시작이 필요할 수 있습니다",
                "",
                None,
                backup,
                drifted,
            )
        )

    def rollback(self) -> None:
        with self._lock:
            if self.state.phase in self.BUSY_PHASES:
                return
        backup, drifted = self._flags()
        if not backup:
            self._fail("no_backup", "복구할 이전 시스템 팔레트가 없습니다")
            return
        self._set_state(SystemThemeState("rolling_back", "이전 시스템 팔레트를 복구하는 중…", "", None, backup, drifted))
        self._thread = threading.Thread(target=self._rollback_worker, name="luminophore-system-theme-rollback", daemon=True)
        self._thread.start()

    def _rollback_worker(self) -> None:
        target_error: SystemThemeError | None = None
        try:
            self.targets.rollback()
        except SystemThemeError as exc:
            target_error = exc
        except Exception:
            LOG.exception("system theme rollback failed unexpectedly")
            target_error = SystemThemeError("rollback_failed", "이전 시스템 팔레트 복구 중 오류가 발생했습니다")
        if target_error is not None and self._config_rollback is not None:
            try:
                self._restore_config(self._config_rollback)
                self._config_rollback = None
            except SystemThemeError as exc:
                target_error = exc
        elif target_error is None:
            self._config_rollback = None
        if target_error:
            error = target_error
            assert error is not None
            self._fail("rollback_failed", str(error))
            return
        backup, drifted = self._flags()
        self._set_state(SystemThemeState("idle", "이전 시스템 팔레트를 복구했습니다", "", None, backup, drifted))

    def shutdown(self) -> None:
        self.backend.shutdown()
