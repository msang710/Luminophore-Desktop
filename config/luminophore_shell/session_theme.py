from __future__ import annotations

from dataclasses import dataclass
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import tempfile
import time
import tomllib
from typing import Mapping, Sequence

from PIL import Image, ImageDraw, UnidentifiedImageError

from .hyprland import MonitorRecord
from .state import PaletteState
from .wallpaper_backends import STATIC_PROVIDERS, WallpaperSnapshot


LUMINOPHORE_SESSION_ROOT = "/etc/luminophore-shell"
LUMINOPHORE_THEME_PATH = f"{LUMINOPHORE_SESSION_ROOT}/greeter-theme.json"
LUMINOPHORE_LOCAL_CONFIG_PATH = f"{LUMINOPHORE_SESSION_ROOT}/greeter.json"
LUMINOPHORE_HYPRLAND_PATH = f"{LUMINOPHORE_SESSION_ROOT}/greeter-settings.toml"
LEGACY_LUMINOPHORE_HYPRLAND_PATH = f"{LUMINOPHORE_SESSION_ROOT}/greeter-hyprland.conf"
LUMINOPHORE_HYPRPAPER_PATH = f"{LUMINOPHORE_SESSION_ROOT}/greeter-hyprpaper.conf"
LUMINOPHORE_BACKGROUND_ROOT = f"{LUMINOPHORE_SESSION_ROOT}/greeter-backgrounds"
LUMINOPHORE_GREETER_PATH = "/usr/lib/luminophore-shell/luminophore-greeter"
LUMINOPHORE_GREETER_SESSION_PATH = "/usr/lib/luminophore-shell/luminophore-greeter-session"
LUMINOPHORE_PYTHON_ROOT = "/usr/lib/luminophore-shell/python"
GREETD_CONFIG_PATH = "/etc/greetd/greetd.conf"
GREETD_SESSION_COMMAND = "/usr/lib/luminophore/luminophore-greeter-session"
LUMINOPHORE_USER_SESSION_COMMAND = "/usr/bin/uwsm start -e -D Luminophore luminophore.desktop"
UWSM_HYPRLAND_DESKTOP = "/usr/share/wayland-sessions/luminophore.desktop"
GREETER_PACKAGE_FILES = (
    "__init__.py",
    "bootstrap.py",
    "glow.py",
    "greetd_client.py",
    "greeter_power.py",
    "greeter_supervisor.py",
    "luminophore_greeter.py",
    "transition.py",
    "ui/__init__.py",
    "ui/effects.py",
)
RELEASE_GREETER_ENTRIES = (
    "greeter_compositor", "greeter_control", "greeter_shell", "greeter_session",
)
_LOGIN_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


class SessionThemeError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def resolve_release_greeter(release_root: Path, manifest: Mapping[str, object]) -> dict[str, Path]:
    """Resolve every greeter component from one immutable release generation."""
    root = Path(release_root)
    if root.is_symlink() or not root.is_dir() or manifest.get("generation") != root.name:
        raise SessionThemeError("greeter_generation_mismatch")
    entries = manifest.get("entries")
    if not isinstance(entries, dict) or not set(RELEASE_GREETER_ENTRIES) <= set(entries):
        raise SessionThemeError("greeter_component_missing")
    resolved: dict[str, Path] = {}
    base = root.resolve()
    for name in RELEASE_GREETER_ENTRIES:
        entry = entries[name]
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise SessionThemeError("greeter_component_invalid")
        path = root / entry["path"]
        try:
            target = path.resolve(strict=True)
        except OSError as exc:
            raise SessionThemeError("greeter_component_missing") from exc
        if path.is_symlink() or not target.is_relative_to(base) or not target.is_file():
            raise SessionThemeError("greeter_component_unsafe")
        resolved[name] = target
    return resolved


@dataclass(frozen=True)
class SessionThemeGeneration:
    generation_id: str
    mode: str
    primary: str
    secondary: str
    surface: str
    on_surface: str
    error: str

    def validate(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{16,64}", self.generation_id):
            raise SessionThemeError("invalid_generation")
        if self.mode not in {"dark", "light"}:
            raise SessionThemeError("invalid_mode")
        for value in (self.primary, self.secondary, self.surface, self.on_surface, self.error):
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                raise SessionThemeError("invalid_color")


@dataclass(frozen=True)
class RenderedTheme:
    root: Path
    generation_id: str
    files: Mapping[str, str]


def _atomic_write(path: Path, payload: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _checked_regular(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise SessionThemeError("unsafe_source")
    return path


def _stage_png(source: Path, destination: Path) -> str:
    _checked_regular(source)
    try:
        with Image.open(source) as image:
            if image.width * image.height > 40_000_000:
                raise SessionThemeError("image_too_large")
            image.seek(0)
            frame = image.convert("RGB")
            destination.parent.mkdir(parents=True, exist_ok=True)
            frame.save(destination, format="PNG", optimize=True)
    except SessionThemeError:
        raise
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise SessionThemeError("invalid_image") from exc
    return hashlib.sha256(destination.read_bytes()).hexdigest()


class LuminophoreGreeterRenderer:
    @staticmethod
    def _launcher_payload(python_root: str) -> bytes:
        return f'''#!/usr/bin/python3
import os
import sys
sys.path.insert(0, {python_root!r})
from luminophore_shell.bootstrap import LAYER_SHELL, preload_entries, with_preload, without_preload
if os.path.isfile(LAYER_SHELL):
    preload = preload_entries(os.environ.get("LD_PRELOAD"))
    if LAYER_SHELL not in preload:
        environment = dict(os.environ)
        environment["LD_PRELOAD"] = with_preload(preload, LAYER_SHELL)
        os.execve(sys.executable, [sys.executable, __file__, *sys.argv[1:]], environment)
    child_preload = without_preload(preload, LAYER_SHELL)
    if child_preload is None:
        os.environ.pop("LD_PRELOAD", None)
    else:
        os.environ["LD_PRELOAD"] = child_preload
from luminophore_shell.luminophore_greeter import main
raise SystemExit(main())
'''.encode()

    def render(
        self,
        destination: Path,
        generation: SessionThemeGeneration,
        connectors: Sequence[str],
        primary_connector: str,
        wallpapers: Mapping[str, Path],
    ) -> RenderedTheme:
        generation.validate()
        ordered = tuple(connectors)
        if not ordered or len(ordered) > 2 or len(set(ordered)) != len(ordered):
            raise SessionThemeError("unsupported_monitor_layout")
        if primary_connector not in ordered or set(wallpapers) != set(ordered):
            raise SessionThemeError("invalid_monitor_mapping")
        destination.mkdir(parents=True, exist_ok=False)
        names: dict[str, str] = {}
        files: dict[str, str] = {}
        for connector in ordered:
            name = f"{hashlib.sha256(connector.encode()).hexdigest()[:12]}.png"
            names[connector] = name
            relative = f"backgrounds/{name}"
            files[relative] = _stage_png(wallpapers[connector], destination / relative)

        theme = {
            "version": 1,
            "primary_connector": primary_connector,
            "colors": {
                "primary": generation.primary.upper(),
                "secondary": generation.secondary.upper(),
                "surface": generation.surface.upper(),
                "on_surface": generation.on_surface.upper(),
            },
            "placement": {
                "center_from_right": [2, 3],
                "center_from_top": [2, 3],
            },
            "layout": {
                "entry_width": 360,
                "control_size": 48,
                "spacing": 10,
                "radius": 12,
                "glow_intensity": 2.3,
                "glow_radius": 64,
                "outline_width": 5,
                "palette_transition_ms": 594,
                "expansion_ms": 200,
                "backdrop_opacity": 0.4,
            },
        }
        theme_bytes = json.dumps(theme, ensure_ascii=False, indent=2, sort_keys=True).encode() + b"\n"
        _atomic_write(destination / "greeter-theme.json", theme_bytes)
        files["greeter-theme.json"] = hashlib.sha256(theme_bytes).hexdigest()

        hyprpaper_lines = ["splash = false", ""]
        for connector in ordered:
            hyprpaper_lines.extend((
                "wallpaper {",
                f"    monitor = {connector}",
                f"    path = {LUMINOPHORE_BACKGROUND_ROOT}/{names[connector]}",
                "    fit_mode = cover",
                "}",
                "",
            ))
        hyprpaper = "\n".join(hyprpaper_lines).encode()
        _atomic_write(destination / "greeter-hyprpaper.conf", hyprpaper)
        files["greeter-hyprpaper.conf"] = hashlib.sha256(hyprpaper).hexdigest()

        background = generation.surface.removeprefix("#")
        from .desktop_defaults import greeter_defaults
        from .settings_migration import _render
        settings = greeter_defaults()
        settings['compositor']['background_color'] = 'ff'+background
        hyprland = _render(settings).encode()
        _atomic_write(destination / "greeter-settings.toml", hyprland)
        files["greeter-settings.toml"] = hashlib.sha256(hyprland).hexdigest()

        launcher = self._launcher_payload(LUMINOPHORE_PYTHON_ROOT)
        supervisor = f'''#!/usr/bin/python3
import sys
sys.path.insert(0, {LUMINOPHORE_PYTHON_ROOT!r})
from luminophore_shell.greeter_supervisor import main
raise SystemExit(main())
'''.encode()
        _atomic_write(destination / "luminophore-greeter", launcher, 0o755)
        _atomic_write(destination / "luminophore-greeter-session", supervisor, 0o755)
        files["luminophore-greeter"] = hashlib.sha256(launcher).hexdigest()
        files["luminophore-greeter-session"] = hashlib.sha256(supervisor).hexdigest()

        source_root = Path(__file__).parent
        for relative in GREETER_PACKAGE_FILES:
            source = _checked_regular(source_root / relative)
            target_relative = f"python/luminophore_shell/{relative}"
            payload = source.read_bytes()
            _atomic_write(destination / target_relative, payload)
            files[target_relative] = hashlib.sha256(payload).hexdigest()

        manifest = {
            "version": 1,
            "generation_id": generation.generation_id,
            "connectors": [hashlib.sha256(item.encode()).hexdigest()[:12] for item in ordered],
            "primary_connector_digest": hashlib.sha256(primary_connector.encode()).hexdigest(),
            "files": files,
        }
        _atomic_write(
            destination / "manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n",
            0o600,
        )
        return RenderedTheme(destination, generation.generation_id, files)

    @staticmethod
    def preview_argv(rendered: RenderedTheme) -> tuple[str, ...]:
        validate_rendered(rendered)
        if rendered.root.is_symlink():
            raise SessionThemeError("unsafe_source")
        python_root = rendered.root / "python"
        launcher = rendered.root / "luminophore-greeter-preview"
        payload = LuminophoreGreeterRenderer._launcher_payload(str(python_root))
        _atomic_write(launcher, payload, 0o700)
        return (
            "/usr/bin/python3",
            str(launcher),
            "--theme",
            str(rendered.root / "greeter-theme.json"),
            "--test-mode",
            "--test-script",
            "failure,success",
        )


def stage_session_theme(
    output_root: Path,
    mode: str,
    monitors: Sequence[MonitorRecord],
    snapshot: WallpaperSnapshot,
    palette_state: PaletteState,
    *,
    nonce: int | None = None,
) -> RenderedTheme:
    ordered_monitors = tuple(sorted(monitors, key=lambda item: (item.x, item.y, item.id, item.name)))
    if not ordered_monitors or len(ordered_monitors) > 2:
        raise SessionThemeError("unsupported_monitor_layout")
    if len({monitor.name for monitor in ordered_monitors}) != len(ordered_monitors):
        raise SessionThemeError("invalid_monitor_mapping")
    primary_rows = tuple(monitor for monitor in ordered_monitors if monitor.x == 0)
    if len(primary_rows) != 1:
        raise SessionThemeError("invalid_primary_monitor")
    primary = primary_rows[0]
    if palette_state.provider in STATIC_PROVIDERS and palette_state.provider != snapshot.provider:
        raise SessionThemeError("palette_provider_mismatch")
    entry = palette_state.entries.get(primary.name)
    if entry is None or entry.scheme is None or mode not in entry.scheme.modes:
        raise SessionThemeError("missing_palette_scheme")
    tokens = entry.scheme.modes[mode].colors
    try:
        generation_colors = {name: tokens[name] for name in ("primary", "secondary", "surface", "on_surface", "error")}
    except KeyError as exc:
        raise SessionThemeError("missing_palette_token") from exc
    connectors = tuple(monitor.name for monitor in ordered_monitors)
    if any(connector not in snapshot.sources for connector in connectors):
        raise SessionThemeError("missing_wallpaper_output")

    fingerprint = {
        "palette_generation": entry.scheme.generation_id,
        "mode": mode,
        "connectors": connectors,
        "provider": snapshot.provider,
        "sources": {
            connector: {
                "kind": snapshot.sources[connector].kind,
                "value": snapshot.sources[connector].value,
            }
            for connector in connectors
        },
        "nonce": time.time_ns() if nonce is None else nonce,
    }
    generation_id = hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    generation = SessionThemeGeneration(
        generation_id,
        mode,
        generation_colors["primary"],
        generation_colors["secondary"],
        generation_colors["surface"],
        generation_colors["on_surface"],
        generation_colors["error"],
    )

    output_root = output_root.expanduser()
    if output_root.is_symlink():
        raise SessionThemeError("unsafe_target")
    output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = output_root / generation_id
    if destination.exists() or destination.is_symlink():
        raise SessionThemeError("stage_exists")
    with tempfile.TemporaryDirectory(prefix=".session-sources-", dir=output_root) as directory:
        source_root = Path(directory)
        wallpapers: dict[str, Path] = {}
        for connector in connectors:
            source = snapshot.sources[connector]
            if source.kind == "image":
                path = Path(source.value).expanduser()
                if not path.is_absolute():
                    raise SessionThemeError("invalid_wallpaper_source")
                wallpapers[connector] = path
            elif source.kind == "color" and re.fullmatch(r"#[0-9A-Fa-f]{6}", source.value):
                path = source_root / f"{hashlib.sha256(connector.encode()).hexdigest()[:12]}.png"
                Image.new("RGB", (16, 16), source.value).save(path, format="PNG")
                wallpapers[connector] = path
            else:
                raise SessionThemeError("invalid_wallpaper_source")
        try:
            return LuminophoreGreeterRenderer().render(destination, generation, connectors, primary.name, wallpapers)
        except Exception:
            if destination.exists() and not destination.is_symlink():
                shutil.rmtree(destination)
            raise


class PlymouthRenderer:
    def render(self, destination: Path, generation: SessionThemeGeneration) -> RenderedTheme:
        generation.validate()
        destination.mkdir(parents=True, exist_ok=False)
        theme = '''[Plymouth Theme]
Name=Luminophore Rice
Description=Luminophore Shell semantic boot theme
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/luminophore-rice
ScriptFile=/usr/share/plymouth/themes/luminophore-rice/luminophore-rice.script
'''.encode()
        rgb = [int(generation.surface[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        primary = [int(generation.primary[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        script = f'''Window.SetBackgroundTopColor({rgb[0]:.4f}, {rgb[1]:.4f}, {rgb[2]:.4f});
Window.SetBackgroundBottomColor({rgb[0]:.4f}, {rgb[1]:.4f}, {rgb[2]:.4f});
logo_image = Image("logo.png"); logo = Sprite(logo_image);
progress_image = Image("progress.png"); progress = Sprite();
bullet_image = Image("bullet.png"); message = Sprite();
fun refresh_callback () {{
  logo.SetPosition(Window.GetX() + Window.GetWidth()/2 - logo_image.GetWidth()/2, Window.GetY() + Window.GetHeight()/2 - logo_image.GetHeight()/2, 10);
}}
fun progress_callback (duration, value) {{
  image = progress_image.Scale(progress_image.GetWidth() * value, progress_image.GetHeight());
  progress.SetImage(image); progress.SetPosition(Window.GetX() + Window.GetWidth()/2 - progress_image.GetWidth()/2, Window.GetY() + Window.GetHeight()*0.72, 20);
}}
fun password_callback (prompt, bullets) {{
  for (index = 0; password_bullet[index] || index < bullets; index++) {{
    if (!password_bullet[index]) {{ password_bullet[index] = Sprite(bullet_image); }}
    password_bullet[index].SetPosition(Window.GetX() + Window.GetWidth()/2 - bullets*8 + index*16, Window.GetY() + Window.GetHeight()*0.62, 100);
    if (index < bullets) password_bullet[index].SetOpacity(1); else password_bullet[index].SetOpacity(0);
  }}
}}
fun question_callback (prompt, entry) {{ message.SetImage(Image.Text(prompt + entry, 1, 1, 1)); }}
fun message_callback (text) {{ message.SetImage(Image.Text(text, 1, 1, 1)); }}
fun hide_message_callback (text) {{ message.SetImage(Image.Text("", 1, 1, 1)); }}
fun display_normal_callback () {{ for (index = 0; password_bullet[index]; index++) password_bullet[index].SetOpacity(0); }}
fun quit_callback () {{ logo.SetOpacity(1); }}
Plymouth.SetRefreshFunction(refresh_callback);
Plymouth.SetBootProgressFunction(progress_callback);
Plymouth.SetDisplayPasswordFunction(password_callback);
Plymouth.SetDisplayQuestionFunction(question_callback);
Plymouth.SetDisplayMessageFunction(message_callback);
Plymouth.SetHideMessageFunction(hide_message_callback);
Plymouth.SetDisplayNormalFunction(display_normal_callback);
Plymouth.SetQuitFunction(quit_callback);
'''.encode()
        source_root = Path(__file__).parent.parent / "boot-login-theme/plymouth"
        source_theme = source_root / "luminophore-rice.plymouth"
        source_script = source_root / "luminophore-rice.script.in"
        if source_theme.is_file() and source_script.is_file():
            theme = source_theme.read_bytes()
            replacements = {
                "@SURFACE_R@": f"{rgb[0]:.4f}", "@SURFACE_G@": f"{rgb[1]:.4f}", "@SURFACE_B@": f"{rgb[2]:.4f}",
            }
            script_text = source_script.read_text(encoding="utf-8")
            for marker, value in replacements.items():
                script_text = script_text.replace(marker, value)
            if "@" in script_text:
                raise SessionThemeError("invalid_plymouth_template")
            script = script_text.encode()
        _atomic_write(destination / "luminophore-rice.plymouth", theme)
        _atomic_write(destination / "luminophore-rice.script", script)
        logo = Image.new("RGBA", (360, 120), generation.surface)
        draw = ImageDraw.Draw(logo)
        draw.rounded_rectangle((4, 4, 355, 115), radius=26, outline=generation.primary, width=5)
        draw.text((132, 48), "RICE", fill=generation.on_surface)
        logo.save(destination / "logo.png")
        progress = Image.new("RGBA", (420, 8), generation.primary)
        progress.save(destination / "progress.png")
        bullet = Image.new("RGBA", (12, 12), (0, 0, 0, 0))
        ImageDraw.Draw(bullet).ellipse((1, 1, 10, 10), fill=generation.primary)
        bullet.save(destination / "bullet.png")
        files = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in destination.iterdir() if path.is_file()
        }
        manifest = {"version": 1, "generation_id": generation.generation_id, "files": files}
        _atomic_write(destination / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n", 0o600)
        return RenderedTheme(destination, generation.generation_id, files)


def validate_rendered(rendered: RenderedTheme) -> None:
    raw = json.loads(_checked_regular(rendered.root / "manifest.json").read_text(encoding="utf-8"))
    if raw.get("version") != 1 or raw.get("generation_id") != rendered.generation_id:
        raise SessionThemeError("invalid_manifest")
    files = raw.get("files")
    if not isinstance(files, dict):
        raise SessionThemeError("invalid_manifest")
    for relative, digest in files.items():
        relative_path = Path(str(relative))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise SessionThemeError("invalid_manifest")
        path = rendered.root / relative_path
        if not isinstance(digest, str) or not path.is_file() or path.is_symlink():
            raise SessionThemeError("invalid_manifest")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise SessionThemeError("checksum_mismatch")


def _snapshot_path(path: Path, backup: Path, key: str) -> dict[str, object]:
    if path.is_symlink():
        raise SessionThemeError("unsafe_target")
    row: dict[str, object] = {"path": str(path), "existed": path.exists()}
    if path.is_file():
        payload = path.read_bytes()
        name = f"{key}.bin"
        _atomic_write(backup / name, payload, 0o600)
        stat = path.stat()
        row.update({
            "backup": name,
            "mode": stat.st_mode & 0o777,
            "uid": stat.st_uid,
            "gid": stat.st_gid,
            "digest": hashlib.sha256(payload).hexdigest(),
        })
    return row


def _assert_safe_target(root: Path, path: Path) -> None:
    if root != Path("/") and root not in path.parents:
        raise SessionThemeError("unsafe_target")
    current = path.parent
    while current != root and current != current.parent:
        if current.is_symlink():
            raise SessionThemeError("unsafe_target")
        current = current.parent
    if path.is_symlink():
        raise SessionThemeError("unsafe_target")


def _assert_live_root_owner(root: Path, paths: Sequence[Path]) -> None:
    if root != Path("/"):
        return
    for path in paths:
        candidate = path if path.exists() else path.parent
        while not candidate.exists() and candidate != candidate.parent:
            candidate = candidate.parent
        if candidate.stat().st_uid != 0:
            raise SessionThemeError("unexpected_owner")


def _restore_rows(root: Path, backup: Path, rows: Mapping[str, object]) -> None:
    for row in rows.values():
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise SessionThemeError("invalid_backup")
        path = Path(row["path"])
        if root != Path("/") and root not in path.parents:
            raise SessionThemeError("invalid_backup")
        if row.get("existed"):
            source = backup / str(row.get("backup", ""))
            payload = _checked_regular(source).read_bytes()
            if hashlib.sha256(payload).hexdigest() != row.get("digest"):
                raise SessionThemeError("backup_checksum_mismatch")
            _atomic_write(path, payload, int(row.get("mode", 0o644)))
            if root == Path("/") and os.geteuid() == 0:
                os.chown(path, int(row.get("uid", 0)), int(row.get("gid", 0)))
        else:
            path.unlink(missing_ok=True)


class SessionStackInstaller:
    def __init__(self, root: Path, login_user: str) -> None:
        self.root = root
        self.login_user = login_user

    def _target_for(self, relative: str) -> Path:
        if relative == "greeter-theme.json":
            return self.root / LUMINOPHORE_THEME_PATH.removeprefix("/")
        if relative == "greeter-hyprpaper.conf":
            return self.root / LUMINOPHORE_HYPRPAPER_PATH.removeprefix("/")
        if relative == "greeter-settings.toml":
            return self.root / LUMINOPHORE_HYPRLAND_PATH.removeprefix("/")
        if relative == "luminophore-greeter":
            return self.root / LUMINOPHORE_GREETER_PATH.removeprefix("/")
        if relative == "luminophore-greeter-session":
            return self.root / LUMINOPHORE_GREETER_SESSION_PATH.removeprefix("/")
        if relative.startswith("backgrounds/"):
            return self.root / LUMINOPHORE_BACKGROUND_ROOT.removeprefix("/") / Path(relative).name
        if relative.startswith("python/luminophore_shell/"):
            package_relative = relative.removeprefix("python/")
            return self.root / LUMINOPHORE_PYTHON_ROOT.removeprefix("/") / package_relative
        raise SessionThemeError("invalid_manifest")

    def _write_local_config(self, path: Path) -> str:
        local = json.dumps(
            {"version": 1, "login_user": self.login_user},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode() + b"\n"
        _atomic_write(path, local, 0o640)
        if self.root == Path("/"):
            try:
                greeter_gid = grp.getgrnam("greeter").gr_gid
            except KeyError as exc:
                raise SessionThemeError("missing_greeter_group") from exc
            os.chown(path, 0, greeter_gid)
        return hashlib.sha256(local).hexdigest()

    def check(self, rendered: RenderedTheme) -> None:
        if self.root.is_symlink() or rendered.root.is_symlink():
            raise SessionThemeError("unsafe_target")
        if not _LOGIN_USER.fullmatch(self.login_user):
            raise SessionThemeError("invalid_login_user")
        validate_rendered(rendered)
        required_files = {
            "greeter-theme.json",
            "greeter-hyprpaper.conf",
            "greeter-settings.toml",
            "luminophore-greeter",
            "luminophore-greeter-session",
            *(f"python/luminophore_shell/{relative}" for relative in GREETER_PACKAGE_FILES),
        }
        if not required_files.issubset(rendered.files) or not any(
            str(relative).startswith("backgrounds/") for relative in rendered.files
        ):
            raise SessionThemeError("invalid_manifest")
        theme = json.loads((rendered.root / "greeter-theme.json").read_text(encoding="utf-8"))
        if (
            not isinstance(theme, dict)
            or theme.get("version") != 1
            or set(theme) != {"version", "primary_connector", "colors", "placement", "layout"}
            or not isinstance(theme.get("primary_connector"), str)
            or not isinstance(theme.get("colors"), dict)
            or set(theme["colors"]) != {"primary", "secondary", "surface", "on_surface"}
            or not isinstance(theme.get("layout"), dict)
            or set(theme["layout"]) != {
                "entry_width", "control_size", "spacing", "radius", "glow_intensity",
                "glow_radius", "outline_width", "palette_transition_ms", "expansion_ms",
                "backdrop_opacity",
            }
            or theme.get("placement") != {"center_from_right": [2, 3], "center_from_top": [2, 3]}
        ):
            raise SessionThemeError("invalid_greeter_theme")
        compositor = _checked_regular(rendered.root / "greeter-settings.toml").read_text(encoding="utf-8")
        from .native_domains import decode_native
        try:
            settings = tomllib.loads(compositor)
            if settings.get('schema_version') != 1 or settings.get('native', {}).get('profile') != 'greeter':
                raise ValueError('greeter profile required')
            decode_native(settings['native'])
            if settings['motion']['enabled'] is not False:
                raise ValueError('greeter animations must be disabled')
        except (ValueError, KeyError, TypeError) as error:
            raise SessionThemeError('invalid_compositor_config') from error
        if self.root == Path("/"):
            try:
                pwd.getpwnam(self.login_user)
                grp.getgrnam("greeter")
            except KeyError as exc:
                raise SessionThemeError("missing_login_identity") from exc
        else:
            passwd = self.root / "etc/passwd"
            if passwd.is_symlink() or not passwd.is_file():
                raise SessionThemeError("missing_login_identity")
            try:
                users = {
                    line.split(":", 1)[0]
                    for line in passwd.read_text(encoding="utf-8", errors="strict").splitlines()
                    if ":" in line
                }
            except UnicodeDecodeError as exc:
                raise SessionThemeError("missing_login_identity") from exc
            if self.login_user not in users:
                raise SessionThemeError("missing_login_identity")
        for executable in (
            "usr/bin/greetd",
            "usr/bin/uwsm",
            "usr/bin/systemctl",
            "usr/lib/luminophore/luminophore-greeter-session",
        ):
            path = self.root / executable
            if not path.is_file() or not os.access(path, os.X_OK):
                raise SessionThemeError("missing_dependency")
        for relative in (
            "usr/lib/girepository-1.0/Gtk-4.0.typelib",
            "usr/lib/girepository-1.0/Gtk4LayerShell-1.0.typelib",
        ):
            typelib = self.root / relative
            if typelib.is_symlink() or not typelib.is_file():
                raise SessionThemeError("missing_dependency")
        desktop_entry = self.root / UWSM_HYPRLAND_DESKTOP.removeprefix("/")
        if desktop_entry.is_symlink() or not desktop_entry.is_file():
            raise SessionThemeError("missing_dependency")
        if not any(line.startswith("Exec=") for line in desktop_entry.read_text(encoding="utf-8").splitlines()):
            raise SessionThemeError("missing_dependency")

        staged_targets = {relative: self._target_for(relative) for relative in rendered.files}
        local_config = self.root / LUMINOPHORE_LOCAL_CONFIG_PATH.removeprefix("/")
        greetd = self.root / GREETD_CONFIG_PATH.removeprefix("/")
        legacy_compositor = self.root / LEGACY_LUMINOPHORE_HYPRLAND_PATH.removeprefix("/")
        targets = {
            **staged_targets,
            "local-config": local_config,
            "greetd": greetd,
            "legacy-compositor": legacy_compositor,
        }
        backup = self.root / "var/lib/luminophore-shell/backups/session" / rendered.generation_id
        _assert_safe_target(self.root, backup)
        _assert_live_root_owner(self.root, (*targets.values(), backup))
        for path in targets.values():
            _assert_safe_target(self.root, path)

    def install(self, rendered: RenderedTheme) -> Path:
        self.check(rendered)
        staged_targets = {relative: self._target_for(relative) for relative in rendered.files}
        local_config = self.root / LUMINOPHORE_LOCAL_CONFIG_PATH.removeprefix("/")
        greetd = self.root / GREETD_CONFIG_PATH.removeprefix("/")
        legacy_compositor = self.root / LEGACY_LUMINOPHORE_HYPRLAND_PATH.removeprefix("/")
        targets = {
            **staged_targets,
            "local-config": local_config,
            "greetd": greetd,
            "legacy-compositor": legacy_compositor,
        }
        backup = self.root / "var/lib/luminophore-shell/backups/session" / rendered.generation_id
        backup.mkdir(parents=True, exist_ok=False, mode=0o700)
        rows = {
            name: _snapshot_path(path, backup, hashlib.sha256(name.encode()).hexdigest()[:20])
            for name, path in targets.items()
        }
        try:
            for relative, target in staged_targets.items():
                source = _checked_regular(rendered.root / relative)
                mode = 0o755 if relative in {"luminophore-greeter", "luminophore-greeter-session"} else 0o644
                _atomic_write(target, source.read_bytes(), mode)
            legacy_compositor.unlink(missing_ok=True)
            local_config_digest = self._write_local_config(local_config)
            greetd_text = (
                '[terminal]\nvt = 1\n\n[default_session]\n'
                f'command = "{GREETD_SESSION_COMMAND}"\nuser = "greeter"\n'
            )
            tomllib.loads(greetd_text)
            _atomic_write(greetd, greetd_text.encode(), 0o600)
            manifest = {
                "version": 1,
                "generation_id": rendered.generation_id,
                "local_config_digest": local_config_digest,
                "rows": rows,
            }
            _atomic_write(
                backup / "rollback.json",
                json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n",
                0o600,
            )
        except Exception:
            _restore_rows(self.root, backup, rows)
            raise
        return backup

    def rollback(self, backup: Path) -> None:
        raw = json.loads(_checked_regular(backup / "rollback.json").read_text(encoding="utf-8"))
        if raw.get("version") != 1 or not isinstance(raw.get("rows"), dict):
            raise SessionThemeError("invalid_backup")
        _restore_rows(self.root, backup, raw["rows"])


class RetainSplashInstaller:
    UNIT_PATH = "/usr/lib/systemd/system/plymouth-quit.service"
    DROPIN_PATH = "/etc/systemd/system/plymouth-quit.service.d/10-luminophore-retain-splash.conf"
    DROPIN = b"[Service]\nExecStart=\nExecStart=-/usr/bin/plymouth quit --retain-splash\n"

    def __init__(self, root: Path, runner) -> None:
        self.root = root
        self.runner = runner

    def check(self) -> None:
        unit = self.root / self.UNIT_PATH.removeprefix("/")
        if unit.is_symlink() or not unit.is_file():
            raise SessionThemeError("missing_plymouth_quit_unit")
        text = unit.read_text(encoding="utf-8")
        if not re.search(r"(?m)^ExecStart=-?/usr/bin/plymouth quit$", text):
            raise SessionThemeError("unsupported_plymouth_quit_unit")
        dropin = self.root / self.DROPIN_PATH.removeprefix("/")
        _assert_safe_target(self.root, dropin)

    def install(self) -> Path:
        self.check()
        dropin = self.root / self.DROPIN_PATH.removeprefix("/")
        generation = hashlib.sha256(f"{time.time_ns()}:{dropin}".encode()).hexdigest()
        backup = self.root / "var/lib/luminophore-shell/backups/handoff" / generation
        _assert_safe_target(self.root, backup)
        _assert_live_root_owner(self.root, (dropin, backup))
        backup.mkdir(parents=True, exist_ok=False, mode=0o700)
        row = _snapshot_path(dropin, backup, "retain-splash")
        rows = {"retain-splash": row}
        try:
            _atomic_write(dropin, self.DROPIN, 0o644)
            result = self.runner(("/usr/bin/systemctl", "daemon-reload"))
            if result.returncode != 0:
                raise SessionThemeError("daemon_reload_failed")
            _atomic_write(
                backup / "rollback.json",
                json.dumps({"version": 1, "rows": rows}, indent=2, sort_keys=True).encode() + b"\n",
                0o600,
            )
        except Exception:
            _restore_rows(self.root, backup, rows)
            self.runner(("/usr/bin/systemctl", "daemon-reload"))
            raise
        return backup

    def rollback(self, backup: Path) -> None:
        raw = json.loads(_checked_regular(backup / "rollback.json").read_text(encoding="utf-8"))
        if raw.get("version") != 1 or not isinstance(raw.get("rows"), dict):
            raise SessionThemeError("invalid_backup")
        _restore_rows(self.root, backup, raw["rows"])
        result = self.runner(("/usr/bin/systemctl", "daemon-reload"))
        if result.returncode != 0:
            raise SessionThemeError("rollback_daemon_reload_failed")


class PlymouthInstaller:
    def __init__(self, root: Path, runner) -> None:
        self.root = root
        self.runner = runner

    def install(self, rendered: RenderedTheme) -> Path:
        validate_rendered(rendered)
        theme_root = self.root / "usr/share/plymouth/themes/luminophore-rice"
        config = self.root / "etc/plymouth/plymouthd.conf"
        backup = self.root / "var/lib/luminophore-shell/backups/boot" / rendered.generation_id
        _assert_safe_target(self.root, backup)
        _assert_live_root_owner(self.root, (theme_root, config, backup))
        backup.mkdir(parents=True, exist_ok=False, mode=0o700)
        _assert_safe_target(self.root, config)
        rows: dict[str, object] = {"config": _snapshot_path(config, backup, "config")}
        for source in rendered.root.iterdir():
            if source.name == "manifest.json":
                continue
            _assert_safe_target(self.root, theme_root / source.name)
            rows[f"theme:{source.name}"] = _snapshot_path(theme_root / source.name, backup, f"theme-{source.name}")
        try:
            for source in rendered.root.iterdir():
                if source.name != "manifest.json":
                    _atomic_write(theme_root / source.name, _checked_regular(source).read_bytes())
            current = config.read_text(encoding="utf-8") if config.is_file() else "[Daemon]\n"
            if re.search(r"(?m)^Theme=", current):
                current = re.sub(r"(?m)^Theme=.*$", "Theme=luminophore-rice", current, count=1)
            else:
                current += ("" if current.endswith("\n") else "\n") + "Theme=luminophore-rice\n"
            _atomic_write(config, current.encode(), 0o644)
            manifest = {"version": 1, "generation_id": rendered.generation_id, "rows": rows}
            _atomic_write(backup / "rollback.json", json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n", 0o600)
            result = self.runner(("/usr/bin/limine-mkinitcpio",))
            if result.returncode != 0:
                raise SessionThemeError("rebuild_failed")
        except Exception:
            _restore_rows(self.root, backup, rows)
            self.runner(("/usr/bin/limine-mkinitcpio",))
            raise
        return backup

    def rollback(self, backup: Path) -> None:
        raw = json.loads(_checked_regular(backup / "rollback.json").read_text(encoding="utf-8"))
        if raw.get("version") != 1 or not isinstance(raw.get("rows"), dict):
            raise SessionThemeError("invalid_backup")
        _restore_rows(self.root, backup, raw["rows"])
        result = self.runner(("/usr/bin/limine-mkinitcpio",))
        if result.returncode != 0:
            raise SessionThemeError("rollback_rebuild_failed")
