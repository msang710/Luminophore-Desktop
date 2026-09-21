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
from typing import Callable, Mapping, Sequence

from .compositor_runtime import resolve_hyprctl


THEME_NAME = "Luminophore-Bibata"
CURSOR_SIZE = 24
XCURSOR_SIZES = (16, 20, 22, 24, 28, 32, 40, 48, 56, 64, 72, 80, 88, 96)
SOURCE_ROOT = Path(__file__).with_name("assets") / "cursors" / "bibata-modern-ice-source"
_HEX = re.compile(r"#[0-9A-Fa-f]{6}")
_SIZE = re.compile(r"^define_size\s*=\s*(\d+)\s*,\s*([^,\s]+)(?:\s*,\s*(\d+))?\s*$")
_OVERRIDE = re.compile(r"^define_override\s*=\s*(\S+)\s*$")
_HOTSPOT = re.compile(r"^hotspot_([xy])\s*=\s*([0-9.]+)\s*$")


class CursorThemeError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True, slots=True)
class CursorThemePalette:
    primary: str
    surface: str
    secondary: str
    tertiary: str
    error: str
    muted: str

    @classmethod
    def from_tokens(cls, tokens: Mapping[str, str]) -> "CursorThemePalette":
        def color(name: str, fallback: str = "") -> str:
            value = str(tokens.get(name, fallback)).upper()
            if not _HEX.fullmatch(value):
                raise CursorThemeError("validation", f"cursor token {name} is not #RRGGBB")
            return value

        return cls(
            color("primary"),
            color("surface"),
            color("secondary"),
            color("tertiary", tokens.get("secondary", "")),
            color("error"),
            color("on_surface_variant", tokens.get("outline", "")),
        )

    def replacements(self) -> dict[str, str]:
        return {
            "#FFFFFF": self.primary,
            "#000000": self.surface,
            "#606060": self.muted,
            "#F05024": self.error,
            "#F1613A": self.error,
            "#FE0000": self.error,
            "#F27400": self.tertiary,
            "#FCB813": self.tertiary,
            "#FDBE2A": self.tertiary,
            "#7EBA41": self.secondary,
            "#96C865": self.secondary,
            "#32A0DA": self.primary,
            "#179DD8": self.primary,
            "#4FADDF": self.primary,
            "#06B231": self.secondary,
            "#5F3BE4": self.secondary,
        }


@dataclass(frozen=True, slots=True)
class CursorFrame:
    source: str
    delay_ms: int = 0


@dataclass(frozen=True, slots=True)
class CursorShape:
    name: str
    hotspot_x: float
    hotspot_y: float
    frames: tuple[CursorFrame, ...]
    overrides: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompiledCursorTheme:
    generation_id: str
    root: Path
    theme_name: str = THEME_NAME
    size: int = CURSOR_SIZE
    digests: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class CursorThemePaths:
    config_home: Path
    data_home: Path
    gsettings_executable: Path = Path("/usr/bin/gsettings")

    @classmethod
    def current(cls) -> "CursorThemePaths":
        home = Path.home()
        return cls(
            Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")).expanduser(),
            Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")).expanduser(),
        )

    @property
    def active_theme(self) -> Path:
        return self.data_home / "icons" / THEME_NAME

    def selectors(self) -> tuple[Path, ...]:
        return (
            self.config_home / "uwsm/env",
            self.config_home / "gtk-3.0/settings.ini",
            self.config_home / "xsettingsd/xsettingsd.conf",
        )


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=30, check=False)


def _source_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != "LICENSE"):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def verify_source_integrity(root: Path = SOURCE_ROOT) -> None:
    manifest = root / "SOURCE.sha256"
    if not manifest.is_file():
        return
    for line in manifest.read_text(encoding="utf-8").splitlines():
        try:
            expected, relative = line.split("  ", 1)
        except ValueError as exc:
            raise CursorThemeError("source_invalid", "cursor source digest manifest is invalid") from exc
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise CursorThemeError("source_invalid", "cursor source digest escapes source root")
        path = root / relative_path
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise CursorThemeError("source_invalid", "cursor source digest escapes source root") from exc
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise CursorThemeError("source_invalid", f"cursor source digest mismatch: {relative}")


def _file_digests(root: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        (path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    )


def _parse_shape(directory: Path) -> CursorShape:
    meta = directory / "meta.hl"
    if not meta.is_file():
        raise CursorThemeError("source_invalid", f"cursor source has no meta.hl: {directory.name}")
    hotspot = {"x": math.nan, "y": math.nan}
    frames: list[CursorFrame] = []
    overrides: list[str] = []
    for raw in meta.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if match := _HOTSPOT.fullmatch(line):
            hotspot[match.group(1)] = float(match.group(2))
        elif match := _SIZE.fullmatch(line):
            frames.append(CursorFrame(match.group(2), int(match.group(3) or 0)))
        elif match := _OVERRIDE.fullmatch(line):
            overrides.append(match.group(1))
    if not frames or any(math.isnan(value) or not 0 <= value <= 1 for value in hotspot.values()):
        raise CursorThemeError("source_invalid", f"invalid cursor metadata: {directory.name}")
    if any(not (directory / frame.source).is_file() for frame in frames):
        raise CursorThemeError("source_invalid", f"cursor frame is missing: {directory.name}")
    return CursorShape(directory.name, hotspot["x"], hotspot["y"], tuple(frames), tuple(overrides))


def source_inventory(root: Path = SOURCE_ROOT) -> tuple[CursorShape, ...]:
    verify_source_integrity(root)
    cursor_root = root / "hyprcursors"
    if not (root / "manifest.hl").is_file() or not cursor_root.is_dir():
        raise CursorThemeError("source_invalid", "Bibata cursor source is incomplete")
    shapes = tuple(_parse_shape(path) for path in sorted(cursor_root.iterdir()) if path.is_dir())
    if not shapes or len({shape.name for shape in shapes}) != len(shapes):
        raise CursorThemeError("source_invalid", "Bibata cursor inventory is invalid")
    return shapes


class CursorThemeCompiler:
    def __init__(
        self,
        source_root: Path = SOURCE_ROOT,
        cache_root: Path | None = None,
        hyprcursor_util: Path = Path("/usr/bin/hyprcursor-util"),
        rsvg_convert: Path = Path("/usr/bin/rsvg-convert"),
        xcursorgen: Path = Path("/usr/bin/xcursorgen"),
        runner: CommandRunner = _run,
    ) -> None:
        self.source_root = source_root
        self.cache_root = cache_root or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "luminophore-shell/cursors"
        self.hyprcursor_util = hyprcursor_util
        self.rsvg_convert = rsvg_convert
        self.xcursorgen = xcursorgen
        self.runner = runner

    def missing_dependencies(self) -> tuple[str, ...]:
        return tuple(str(path) for path in (self.hyprcursor_util, self.rsvg_convert, self.xcursorgen) if not path.is_file())

    def available(self) -> bool:
        try:
            source_inventory(self.source_root)
        except CursorThemeError:
            return False
        return not self.missing_dependencies()

    @staticmethod
    def _check(result: subprocess.CompletedProcess[str], category: str, action: str) -> None:
        if result.returncode:
            detail = " ".join((result.stderr or result.stdout).split())[:240]
            raise CursorThemeError(category, f"{action} failed: {detail or result.returncode}")

    @staticmethod
    def _recolor_svg(path: Path, replacements: Mapping[str, str]) -> None:
        source = path.read_text(encoding="utf-8")
        rendered = _HEX.sub(lambda match: replacements.get(match.group(0).upper(), match.group(0).upper()), source)
        path.write_text(rendered, encoding="utf-8")

    def _prepare_source(self, destination: Path, palette: CursorThemePalette) -> tuple[CursorShape, ...]:
        shapes = source_inventory(self.source_root)
        shutil.copytree(self.source_root, destination)
        manifest = destination / "manifest.hl"
        text = manifest.read_text(encoding="utf-8")
        text = re.sub(r"(?m)^name\s*=.*$", f"name = {THEME_NAME}", text)
        text = re.sub(r"(?m)^description\s*=.*$", "description = LUMINOPHORE palette Bibata cursors", text)
        manifest.write_text(text, encoding="utf-8")
        index = destination / "index.theme"
        if index.exists():
            index.write_text(
                "[Icon Theme]\nName=Luminophore-Bibata\nComment=LUMINOPHORE palette Bibata cursors\nInherits=hicolor\n",
                encoding="utf-8",
            )
        replacements = palette.replacements()
        for svg in destination.rglob("*.svg"):
            self._recolor_svg(svg, replacements)
        return shapes

    def _build_hyprcursor(self, source: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        result = self.runner((str(self.hyprcursor_util), "--create", str(source), "--output", str(destination)))
        self._check(result, "compile_failed", "hyprcursor build")
        generated = destination / f"theme_{THEME_NAME}"
        if not generated.is_dir():
            raise CursorThemeError("compile_failed", "hyprcursor output directory is missing")
        shutil.copytree(generated / "hyprcursors", destination.parent / "theme" / "hyprcursors")

    def _render_png(self, svg: Path, png: Path, size: int) -> None:
        result = self.runner((str(self.rsvg_convert), "-a", "-f", "png", "-w", str(size), "-h", str(size), "-o", str(png), str(svg)))
        self._check(result, "compile_failed", "cursor SVG rasterization")

    def _build_xcursor(self, source: Path, shapes: Sequence[CursorShape], theme: Path) -> None:
        cursors = theme / "cursors"
        work = theme.parent / "xcursor-work"
        cursors.mkdir(parents=True)
        work.mkdir()
        canonical = {shape.name for shape in shapes}
        for shape in shapes:
            shape_work = work / shape.name
            shape_work.mkdir()
            config: list[str] = []
            for size in XCURSOR_SIZES:
                for index, frame in enumerate(shape.frames):
                    name = f"{shape.name}-{size}-{index:02}.png"
                    png = shape_work / name
                    self._render_png(source / "hyprcursors" / shape.name / frame.source, png, size)
                    row = f"{size} {int(size * shape.hotspot_x)} {int(size * shape.hotspot_y)} {name}"
                    if frame.delay_ms:
                        row += f" {frame.delay_ms}"
                    config.append(row)
            config_path = shape_work / "cursor.conf"
            config_path.write_text("\n".join(config) + "\n", encoding="utf-8")
            output = cursors / shape.name
            result = self.runner((str(self.xcursorgen), "-p", str(shape_work), str(config_path), str(output)))
            self._check(result, "compile_failed", f"XCursor build for {shape.name}")
            if not output.is_file() or not output.stat().st_size:
                raise CursorThemeError("compile_failed", f"XCursor output is missing: {shape.name}")
        for shape in shapes:
            for alias in shape.overrides:
                if alias in canonical or (cursors / alias).exists():
                    continue
                (cursors / alias).symlink_to(shape.name)

    def compile(self, tokens: Mapping[str, str]) -> CompiledCursorTheme:
        missing = self.missing_dependencies()
        if missing:
            raise CursorThemeError("compiler_missing", f"cursor compiler is missing: {', '.join(missing)}")
        palette = CursorThemePalette.from_tokens(tokens)
        fingerprint = hashlib.sha256()
        fingerprint.update(_source_digest(self.source_root).encode())
        fingerprint.update(json.dumps(palette.__dict__ if hasattr(palette, "__dict__") else {
            "primary": palette.primary, "surface": palette.surface, "secondary": palette.secondary,
            "tertiary": palette.tertiary, "error": palette.error, "muted": palette.muted,
        }, sort_keys=True).encode())
        fingerprint.update(str(XCURSOR_SIZES).encode())
        for executable in (self.hyprcursor_util, self.rsvg_convert, self.xcursorgen):
            fingerprint.update(executable.read_bytes())
        generation = fingerprint.hexdigest()
        cached = self.cache_root / generation / THEME_NAME
        manifest = cached.parent / "digests.json"
        if cached.is_dir() and manifest.is_file():
            digests = tuple((str(name), str(value)) for name, value in json.loads(manifest.read_text()).items())
            if digests == _file_digests(cached):
                return CompiledCursorTheme(generation, cached, digests=digests)
        with tempfile.TemporaryDirectory(prefix="luminophore-cursor-build-") as directory:
            root = Path(directory)
            source = root / "source"
            shapes = self._prepare_source(source, palette)
            theme = root / "theme"
            theme.mkdir()
            shutil.copy2(source / "manifest.hl", theme / "manifest.hl")
            shutil.copy2(source / "index.theme", theme / "index.theme")
            self._build_hyprcursor(source, root / "hypr-output")
            self._build_xcursor(source, shapes, theme)
            built_names = {path.name for path in (theme / "hyprcursors").glob("*.hlc")}
            if built_names != {f"{shape.name}.hlc" for shape in shapes}:
                raise CursorThemeError("verification_failed", "Hyprcursor shape set does not match source")
            if not all((theme / "cursors" / shape.name).is_file() for shape in shapes):
                raise CursorThemeError("verification_failed", "XCursor shape set does not match source")
            destination = self.cache_root / generation
            destination.mkdir(parents=True, exist_ok=False)
            shutil.copytree(theme, destination / THEME_NAME, symlinks=True)
            digests = _file_digests(destination / THEME_NAME)
            (destination / "digests.json").write_text(json.dumps(dict(digests), sort_keys=True), encoding="utf-8")
        return CompiledCursorTheme(generation, cached, digests=digests)


def _patch_selector(path: Path, source: str, theme_name: str, size: int) -> str:
    if path.name == "env":
        values = {
            "HYPRCURSOR_THEME": f'export HYPRCURSOR_THEME="{theme_name}"',
            "XCURSOR_THEME": f'export XCURSOR_THEME="{theme_name}"',
            "HYPRCURSOR_SIZE": f"export HYPRCURSOR_SIZE={size}",
            "XCURSOR_SIZE": f"export XCURSOR_SIZE={size}",
        }
        lines = source.splitlines()
        found: set[str] = set()
        for index, line in enumerate(lines):
            match = re.match(r"^\s*export\s+(HYPRCURSOR_THEME|XCURSOR_THEME|HYPRCURSOR_SIZE|XCURSOR_SIZE)=", line)
            if match:
                key = match.group(1)
                lines[index] = values[key]
                found.add(key)
        lines.extend(values[key] for key in values if key not in found)
        return "\n".join(lines) + "\n"
    if path.name == "settings.ini":
        lines = source.splitlines()
        replacements = {
            "gtk-cursor-theme-name": f"gtk-cursor-theme-name={theme_name}",
            "gtk-cursor-theme-size": f"gtk-cursor-theme-size={size}",
        }
        found: set[str] = set()
        for index, line in enumerate(lines):
            key = line.split("=", 1)[0].strip()
            if key in replacements:
                lines[index] = replacements[key]
                found.add(key)
        if "[Settings]" not in lines:
            lines.insert(0, "[Settings]")
        lines.extend(replacements[key] for key in replacements if key not in found)
        return "\n".join(lines) + "\n"
    lines = source.splitlines()
    replacements = {
        "Gtk/CursorThemeName": f'Gtk/CursorThemeName "{theme_name}"',
        "Gtk/CursorThemeSize": f"Gtk/CursorThemeSize {size}",
    }
    found: set[str] = set()
    for index, line in enumerate(lines):
        key = line.split(None, 1)[0] if line.strip() else ""
        if key in replacements:
            lines[index] = replacements[key]
            found.add(key)
    lines.extend(replacements[key] for key in replacements if key not in found)
    return "\n".join(lines) + "\n"


class CursorThemeTransaction:
    def __init__(
        self,
        compiler: CursorThemeCompiler | None = None,
        paths: CursorThemePaths | None = None,
        runner: CommandRunner = _run,
        hyprctl: str | None = None,
    ) -> None:
        self.paths = paths or CursorThemePaths.current()
        self.compiler = compiler or CursorThemeCompiler()
        self.runner = runner
        self.hyprctl = hyprctl or resolve_hyprctl()

    def available(self) -> bool:
        return self.compiler.available()

    def prepare(self, tokens: Mapping[str, str]) -> CompiledCursorTheme:
        return self.compiler.compile(tokens)

    def snapshot(self, destination: Path) -> dict[str, object]:
        destination.mkdir(parents=True, exist_ok=False)
        active = self.paths.active_theme
        active_existed = active.is_dir()
        if active_existed:
            shutil.copytree(active, destination / "active", symlinks=True)
        files: list[dict[str, object]] = []
        for index, path in enumerate(self.paths.selectors()):
            if path.is_symlink():
                raise CursorThemeError("backup_failed", f"cursor selector must not be a symlink: {path}")
            row: dict[str, object] = {"path": str(path), "existed": path.is_file()}
            if path.is_file():
                payload = path.read_bytes()
                name = f"selector-{index}.bin"
                (destination / name).write_bytes(payload)
                row.update({"backup": name, "digest": hashlib.sha256(payload).hexdigest(), "mode": path.stat().st_mode & 0o777})
            files.append(row)
        previous_name = os.environ.get("HYPRCURSOR_THEME", "Bibata-Modern-Ice")
        previous_size = int(os.environ.get("HYPRCURSOR_SIZE", str(CURSOR_SIZE)))
        gsettings: dict[str, str] = {}
        if self.paths.gsettings_executable.is_file():
            for key in ("cursor-theme", "cursor-size"):
                result = self.runner((str(self.paths.gsettings_executable), "get", "org.gnome.desktop.interface", key))
                if result.returncode:
                    raise CursorThemeError("backup_failed", f"cursor gsettings snapshot failed: {key}")
                gsettings[key] = result.stdout.strip()
        return {
            "active_path": str(active),
            "active_existed": active_existed,
            "backup": "cursor/active" if active_existed else "",
            "selectors": files,
            "live_name": previous_name,
            "live_size": previous_size,
            "gsettings": gsettings,
        }

    @staticmethod
    def _write_atomic(path: Path, payload: bytes, mode: int = 0o644) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.luminophore-next")
        temporary.write_bytes(payload)
        os.chmod(temporary, mode)
        os.replace(temporary, path)

    def _activate(self, name: str, size: int) -> None:
        result = self.runner((self.hyprctl, "setcursor", name, str(size)))
        if result.returncode:
            detail = " ".join((result.stderr or result.stdout).split())[:240]
            raise CursorThemeError("activation_failed", f"cursor activation failed: {detail or result.returncode}")

    def _apply_gsettings(self, name: str, size: int) -> None:
        if not self.paths.gsettings_executable.is_file():
            return
        schema = "org.gnome.desktop.interface"
        for key, value in (("cursor-theme", name), ("cursor-size", str(size))):
            result = self.runner((str(self.paths.gsettings_executable), "set", schema, key, value))
            if result.returncode:
                raise CursorThemeError("activation_failed", f"cursor gsettings apply failed: {key}")

    def apply(self, compiled: CompiledCursorTheme) -> None:
        if compiled.theme_name != THEME_NAME or compiled.size != CURSOR_SIZE:
            raise CursorThemeError("verification_failed", "cursor artifact identity is invalid")
        if _file_digests(compiled.root) != compiled.digests:
            raise CursorThemeError("verification_failed", "cursor artifact digest mismatch")
        active = self.paths.active_theme
        active.parent.mkdir(parents=True, exist_ok=True)
        incoming = active.with_name(f".{active.name}.luminophore-next")
        displaced = active.with_name(f".{active.name}.luminophore-previous")
        if incoming.exists() or displaced.exists():
            raise CursorThemeError("apply_failed", "stale cursor transaction directory exists")
        shutil.copytree(compiled.root, incoming, symlinks=True)
        if active.exists():
            os.replace(active, displaced)
        try:
            os.replace(incoming, active)
            for path in self.paths.selectors():
                source = path.read_text(encoding="utf-8") if path.is_file() else ""
                mode = path.stat().st_mode & 0o777 if path.is_file() else 0o644
                self._write_atomic(path, _patch_selector(path, source, THEME_NAME, CURSOR_SIZE).encode(), mode)
            self._apply_gsettings(THEME_NAME, CURSOR_SIZE)
            self._activate(THEME_NAME, CURSOR_SIZE)
        except Exception:
            if active.exists():
                shutil.rmtree(active)
            if displaced.exists():
                os.replace(displaced, active)
            if incoming.exists():
                shutil.rmtree(incoming)
            raise
        if displaced.exists():
            shutil.rmtree(displaced)

    def verify(self, compiled: CompiledCursorTheme) -> None:
        active = self.paths.active_theme
        if _file_digests(active) != compiled.digests:
            raise CursorThemeError("verification_failed", "active cursor digest mismatch")
        for path in self.paths.selectors():
            text = path.read_text(encoding="utf-8")
            if THEME_NAME not in text or str(CURSOR_SIZE) not in text:
                raise CursorThemeError("verification_failed", f"cursor selector mismatch: {path.name}")
        if self.paths.gsettings_executable.is_file():
            for key, expected in (("cursor-theme", THEME_NAME), ("cursor-size", str(CURSOR_SIZE))):
                result = self.runner((str(self.paths.gsettings_executable), "get", "org.gnome.desktop.interface", key))
                if result.returncode or expected not in result.stdout:
                    raise CursorThemeError("verification_failed", f"cursor gsettings mismatch: {key}")

    def restore(self, snapshot: Mapping[str, object], backup_root: Path) -> None:
        if Path(str(snapshot.get("active_path", ""))) != self.paths.active_theme:
            raise CursorThemeError("rollback_failed", "cursor backup path mismatch")
        active = self.paths.active_theme
        if active.exists():
            shutil.rmtree(active)
        if snapshot.get("active_existed"):
            source = backup_root / "cursor/active"
            if not source.is_dir():
                raise CursorThemeError("rollback_failed", "cursor directory backup is missing")
            shutil.copytree(source, active, symlinks=True)
        rows = snapshot.get("selectors")
        if not isinstance(rows, list):
            raise CursorThemeError("rollback_failed", "cursor selector backup is invalid")
        expected_paths = self.paths.selectors()
        for row in rows:
            if not isinstance(row, dict):
                raise CursorThemeError("rollback_failed", "cursor selector row is invalid")
            path = Path(str(row.get("path", "")))
            if path not in expected_paths:
                raise CursorThemeError("rollback_failed", "cursor selector path mismatch")
            if row.get("existed"):
                name = row.get("backup")
                if not isinstance(name, str):
                    raise CursorThemeError("rollback_failed", "cursor selector payload is missing")
                payload = (backup_root / "cursor" / name).read_bytes()
                if hashlib.sha256(payload).hexdigest() != row.get("digest"):
                    raise CursorThemeError("rollback_failed", "cursor selector digest mismatch")
                self._write_atomic(path, payload, int(row.get("mode", 0o644)))
            else:
                path.unlink(missing_ok=True)
        gsettings = snapshot.get("gsettings")
        if isinstance(gsettings, dict) and self.paths.gsettings_executable.is_file():
            for key, value in gsettings.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    raise CursorThemeError("rollback_failed", "cursor gsettings backup is invalid")
                result = self.runner((str(self.paths.gsettings_executable), "set", "org.gnome.desktop.interface", key, value))
                if result.returncode:
                    raise CursorThemeError("rollback_failed", f"cursor gsettings restore failed: {key}")
        self._activate(str(snapshot.get("live_name", "Bibata-Modern-Ice")), int(snapshot.get("live_size", CURSOR_SIZE)))
