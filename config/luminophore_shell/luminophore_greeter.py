from __future__ import annotations

import argparse
import cairo
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import pwd
import re
import subprocess
from typing import Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk, Gtk4LayerShell  # noqa: E402

from .greetd_client import AuthResult, GreetdClient
from .greeter_power import GreeterPowerController, PowerResult
from .glow import GlowEdgeBudget
from .ui.effects import LuminophoreGlowContainer, VanishingSurfaceContent, attach_luminophore_state


LOG = logging.getLogger("luminophore-greeter")
DEFAULT_THEME_PATH = Path("/etc/luminophore-shell/greeter-theme.json")
DEFAULT_CONFIG_PATH = Path("/etc/luminophore-shell/greeter.json")
VISIBLE_CONTROLS = ("password", "reboot", "poweroff", "firmware")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_LOGIN_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


class GreeterConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class GreeterTheme:
    connector: str
    primary: str
    secondary: str
    surface: str
    on_surface: str
    entry_width: int = 360
    control_size: int = 48
    spacing: int = 10
    radius: int = 12
    glow_intensity: float = 2.3
    glow_radius: int = 64
    outline_width: int = 5
    palette_transition_ms: int = 594
    expansion_ms: int = 200
    backdrop_opacity: float = 0.4

    @classmethod
    def read(cls, path: Path) -> "GreeterTheme":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GreeterConfigurationError("invalid_theme") from exc
        if (
            not isinstance(raw, dict)
            or raw.get("version") != 1
            or set(raw) != {"version", "primary_connector", "colors", "placement", "layout"}
        ):
            raise GreeterConfigurationError("invalid_theme")
        colors = raw.get("colors")
        layout = raw.get("layout")
        placement = raw.get("placement")
        connector = raw.get("primary_connector")
        if (
            not isinstance(colors, dict)
            or set(colors) != {"primary", "secondary", "surface", "on_surface"}
            or not isinstance(layout, dict)
            or set(layout) != {
                "entry_width", "control_size", "spacing", "radius", "glow_intensity",
                "glow_radius", "outline_width", "palette_transition_ms", "expansion_ms",
                "backdrop_opacity",
            }
            or not isinstance(placement, dict)
        ):
            raise GreeterConfigurationError("invalid_theme")
        if placement != {"center_from_right": [2, 3], "center_from_top": [2, 3]}:
            raise GreeterConfigurationError("invalid_placement")
        values = {name: colors.get(name) for name in ("primary", "secondary", "surface", "on_surface")}
        if not isinstance(connector, str) or not connector or any(
            not isinstance(value, str) or not _HEX.fullmatch(value) for value in values.values()
        ):
            raise GreeterConfigurationError("invalid_theme")
        try:
            theme = cls(
                connector=connector,
                primary=str(values["primary"]).upper(),
                secondary=str(values["secondary"]).upper(),
                surface=str(values["surface"]).upper(),
                on_surface=str(values["on_surface"]).upper(),
                entry_width=int(layout["entry_width"]),
                control_size=int(layout["control_size"]),
                spacing=int(layout["spacing"]),
                radius=int(layout["radius"]),
                glow_intensity=float(layout["glow_intensity"]),
                glow_radius=int(layout["glow_radius"]),
                outline_width=int(layout["outline_width"]),
                palette_transition_ms=int(layout["palette_transition_ms"]),
                expansion_ms=int(layout["expansion_ms"]),
                backdrop_opacity=float(layout["backdrop_opacity"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GreeterConfigurationError("invalid_theme") from exc
        if (
            min(theme.entry_width, theme.control_size, theme.radius, theme.glow_radius, theme.outline_width) < 1
            or theme.spacing < 0
            or theme.expansion_ms < 0
            or not 0.2 <= theme.backdrop_opacity <= 0.95
        ):
            raise GreeterConfigurationError("invalid_theme")
        return theme


@dataclass(frozen=True)
class GreeterLocalConfig:
    login_user: str

    @classmethod
    def read(cls, path: Path) -> "GreeterLocalConfig":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            if path != DEFAULT_CONFIG_PATH:
                raise GreeterConfigurationError("invalid_local_config") from exc
            users = [user.pw_name for user in pwd.getpwall()
                     if 1000 <= user.pw_uid < 60000
                     and _LOGIN_USER.fullmatch(user.pw_name)
                     and Path(user.pw_shell).name not in {"nologin", "false"}]
            if len(users) != 1:
                raise GreeterConfigurationError("ambiguous_login_user") from exc
            return cls(users[0])
        except (OSError, json.JSONDecodeError) as exc:
            raise GreeterConfigurationError("invalid_local_config") from exc
        login_user = (
            raw.get("login_user")
            if isinstance(raw, dict) and raw.get("version") == 1 and set(raw) == {"version", "login_user"}
            else None
        )
        if not isinstance(login_user, str) or not _LOGIN_USER.fullmatch(login_user):
            raise GreeterConfigurationError("invalid_local_config")
        return cls(login_user)


def positioned_row_bounds(
    viewport_width: int,
    viewport_height: int,
    row_width: int,
    row_height: int,
    original_height: int | None = None,
) -> tuple[int, int, int, int]:
    width = max(1, min(viewport_width, row_width))
    height = max(1, min(viewport_height, row_height))
    full_height = max(height, original_height or height)
    center_x = round(viewport_width / 3)
    center_y = round(viewport_height * 2 / 3)
    x = max(0, min(viewport_width - width, center_x - width // 2))
    y = max(0, min(viewport_height - full_height, center_y - full_height // 2))
    return x, y, width, height


class PositionedSurfaceViewport(Gtk.Widget):
    __gtype_name__ = "LuminophoreGreeterPositionedViewport"

    def __init__(self, content: Gtk.Widget, width: int, height: int) -> None:
        super().__init__()
        self.content = content
        self.viewport_width = max(1, width)
        self.viewport_height = max(1, height)
        self._content_bounds = (0, 0, 1, 1)
        self.set_overflow(Gtk.Overflow.VISIBLE)
        content.set_parent(self)

    def content_bounds(self) -> tuple[int, int, int, int]:
        return self._content_bounds

    def do_measure(self, orientation: Gtk.Orientation, _for_size: int) -> tuple[int, int, int, int]:
        value = self.viewport_width if orientation == Gtk.Orientation.HORIZONTAL else self.viewport_height
        return value, value, -1, -1

    def _content_size(self) -> tuple[int, int, int]:
        minimum_width, natural_width, _min_baseline, _natural_baseline = self.content.measure(
            Gtk.Orientation.HORIZONTAL,
            -1,
        )
        width = max(1, minimum_width, natural_width)
        minimum_height, natural_height, _min_baseline, _natural_baseline = self.content.measure(
            Gtk.Orientation.VERTICAL,
            width,
        )
        height = max(1, minimum_height, natural_height)
        original_height = height
        if isinstance(self.content, VanishingSurfaceContent):
            _original_width, original_height = self.content.original_size()
        return width, height, original_height

    def _set_input_region(self, bounds: tuple[int, int, int, int]) -> None:
        native = self.get_native()
        surface = native.get_surface() if native else None
        if not surface:
            return
        if isinstance(self.content, VanishingSurfaceContent) and not self.content.accepts_input:
            surface.set_input_region(cairo.Region())
            return
        x, y, width, height = bounds
        surface.set_input_region(cairo.Region(cairo.RectangleInt(x, y, width, height)))

    def do_size_allocate(self, width: int, height: int, _baseline: int) -> None:
        content_width, content_height, original_height = self._content_size()
        self._content_bounds = positioned_row_bounds(width, height, content_width, content_height, original_height)
        x, y, content_width, content_height = self._content_bounds
        if isinstance(self.content, LuminophoreGlowContainer):
            self.content.set_edge_budget(
                GlowEdgeBudget.from_bounds(width, height, x, y, content_width, content_height)
            )
        transform = None
        if x or y:
            point = Graphene.Point()
            point.init(float(x), float(y))
            transform = Gsk.Transform.new().translate(point)
        self.content.allocate(content_width, content_height, -1, transform)
        self._set_input_region(self._content_bounds)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        self.snapshot_child(self.content, snapshot)

    def do_dispose(self) -> None:
        if self.content.get_parent() is self:
            self.content.unparent()
        super().do_dispose()


def _icon_button(icon_name: str, size: int, accessible_name: str) -> Gtk.Button:
    image = Gtk.Image.new_from_icon_name(icon_name)
    image.set_pixel_size(max(16, round(size * 0.46)))
    button = Gtk.Button(child=image)
    button.add_css_class("luminophore-login-control")
    button.set_size_request(size, size)
    button.update_property([Gtk.AccessibleProperty.LABEL], [accessible_name])
    attach_luminophore_state(button, max(1, round(size * 0.25)))
    return button


class LuminophoreGreeterApplication(Gtk.Application):
    def __init__(
        self,
        theme: GreeterTheme,
        local_config: GreeterLocalConfig,
        *,
        test_outcomes: Sequence[str] = (),
    ) -> None:
        super().__init__(
            application_id="io.github.luminophoreshell.Greeter",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )
        self.theme = theme
        self.local_config = local_config
        self.test_outcomes = list(test_outcomes)
        self.test_mode = bool(test_outcomes)
        self.failed = False
        self.window: Gtk.ApplicationWindow | None = None
        self.entry: Gtk.Entry | None = None
        self.entry_frame: LuminophoreGlowContainer | None = None
        self.power_frames: dict[str, LuminophoreGlowContainer] = {}
        self.vanishing: VanishingSurfaceContent | None = None
        self._power_pending = False
        self.auth_client = GreetdClient()
        test_runner = None
        if self.test_mode:
            test_runner = lambda argv: subprocess.CompletedProcess(argv, 1, "", "")
        self.power = GreeterPowerController(runner=test_runner) if test_runner else GreeterPowerController()
        self.auth_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="luminophore-greeter-auth")
        self.power_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="luminophore-greeter-power")

    def _frame(self, child: Gtk.Widget, name: str) -> LuminophoreGlowContainer:
        return LuminophoreGlowContainer(
            child,
            name,
            self.theme.primary,
            self.theme.glow_intensity,
            self.theme.glow_radius,
            self.theme.outline_width,
            self.theme.radius,
            self.theme.palette_transition_ms,
            self.theme.on_surface,
        )

    def _install_css(self, display: Gdk.Display) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_string(
            f"""
            window {{ background: transparent; }}
            .luminophore-login-control {{
                color: {self.theme.on_surface};
                background: alpha({self.theme.surface}, {self.theme.backdrop_opacity:.3f});
                border: 0;
                border-radius: {self.theme.radius}px;
                box-shadow: none;
            }}
            entry.luminophore-login-control {{ padding: 0 16px; min-height: {self.theme.control_size}px; }}
            button.luminophore-login-control {{ padding: 0; min-width: {self.theme.control_size}px; min-height: {self.theme.control_size}px; }}
            entry.luminophore-login-control:focus, button.luminophore-login-control:focus {{ outline: none; box-shadow: none; }}
            """
        )
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _find_monitor(self, display: Gdk.Display) -> Gdk.Monitor:
        model = display.get_monitors()
        matches: list[Gdk.Monitor] = []
        for index in range(model.get_n_items()):
            monitor = model.get_item(index)
            if isinstance(monitor, Gdk.Monitor) and (
                self.theme.connector == "auto" or monitor.get_connector() == self.theme.connector
            ):
                matches.append(monitor)
        if self.theme.connector == "auto" and matches:
            return matches[0]
        if len(matches) != 1:
            raise GreeterConfigurationError("primary_connector_unavailable")
        return matches[0]

    def do_activate(self) -> None:
        if self.window is not None:
            self.window.present()
            return
        display = Gdk.Display.get_default()
        if display is None:
            self.failed = True
            self.quit()
            return
        try:
            monitor = self._find_monitor(display)
        except GreeterConfigurationError:
            LOG.error("category=primary_connector_unavailable")
            self.failed = True
            self.quit()
            return
        self._install_css(display)
        geometry = monitor.get_geometry()

        entry = Gtk.Entry()
        entry.set_visibility(False)
        entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        entry.set_size_request(self.theme.entry_width, self.theme.control_size)
        entry.add_css_class("luminophore-login-control")
        entry.connect("activate", self._submit_password)
        entry_frame = self._frame(entry, "login-password")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=self.theme.spacing)
        row.append(entry_frame)
        button_specs = (
            ("reboot", "system-reboot-symbolic", "재부팅"),
            ("poweroff", "system-shutdown-symbolic", "종료"),
            ("firmware", "preferences-system-symbolic", "BIOS 진입"),
        )
        for action, icon_name, accessible_name in button_specs:
            button = _icon_button(icon_name, self.theme.control_size, accessible_name)
            button.connect("clicked", self._request_power, action)
            frame = self._frame(button, f"login-{action}")
            self.power_frames[action] = frame
            row.append(frame)

        vanishing = VanishingSurfaceContent(row, self.theme.expansion_ms)
        viewport = PositionedSurfaceViewport(vanishing, geometry.width, geometry.height)
        window = Gtk.ApplicationWindow(application=self)
        window.set_name("luminophore-login")
        window.set_decorated(False)
        window.set_resizable(False)
        Gtk4LayerShell.init_for_window(window)
        Gtk4LayerShell.set_namespace(window, "luminophore-shell-login")
        Gtk4LayerShell.set_monitor(window, monitor)
        Gtk4LayerShell.set_layer(window, Gtk4LayerShell.Layer.OVERLAY)
        Gtk4LayerShell.set_exclusive_zone(window, 0)
        for edge in (
            Gtk4LayerShell.Edge.TOP,
            Gtk4LayerShell.Edge.BOTTOM,
            Gtk4LayerShell.Edge.LEFT,
            Gtk4LayerShell.Edge.RIGHT,
        ):
            Gtk4LayerShell.set_anchor(window, edge, True)
        Gtk4LayerShell.set_keyboard_mode(window, Gtk4LayerShell.KeyboardMode.EXCLUSIVE)
        window.set_child(viewport)
        self.window = window
        self.entry = entry
        self.entry_frame = entry_frame
        self.vanishing = vanishing
        window.connect("map", self._report_mapped)
        window.present()

        GLib.idle_add(self._focus_password)

    def _report_mapped(self, _window: Gtk.Widget) -> None:
        ready_fd = os.environ.get("LUMINOPHORE_GREETER_READY_FD")
        if ready_fd is not None:
            try:
                descriptor = int(ready_fd)
                os.write(descriptor, b"1")
                os.close(descriptor)
                os.environ.pop("LUMINOPHORE_GREETER_READY_FD", None)
            except (OSError, ValueError) as exc:
                LOG.error("category=greeter_ready_signal_failed: %s", exc)
                self.failed = True
                self.quit()

    def _focus_password(self) -> bool:
        if self.entry is not None:
            self.entry.grab_focus()
        return False

    def _test_authenticate(self) -> AuthResult:
        outcome = self.test_outcomes.pop(0) if self.test_outcomes else "failure"
        return AuthResult(outcome == "success", "" if outcome == "success" else "auth_error")

    def _submit_password(self, _entry: Gtk.Entry) -> None:
        if self.entry is None or not self.entry.get_sensitive() or self.vanishing is None or self.vanishing.vanishing:
            return
        password = self.entry.get_text()
        self.entry.set_text("")
        self.entry.set_sensitive(False)
        if self.test_mode:
            future = self.auth_executor.submit(self._test_authenticate)
        else:
            future = self.auth_executor.submit(self.auth_client.authenticate, self.local_config.login_user, password)
        future.add_done_callback(lambda completed: GLib.idle_add(self._auth_finished, completed))

    def _auth_finished(self, future: Future[AuthResult]) -> bool:
        try:
            result = future.result()
        except Exception:
            result = AuthResult(False, "internal_error")
        if result.success and self.vanishing is not None:
            self.vanishing.vanish(self._finish_success)
            return False
        LOG.warning("category=%s", result.category or "auth_failed")
        if self.entry is not None:
            self.entry.set_text("")
            self.entry.set_sensitive(True)
            self.entry.grab_focus()
        if self.entry_frame is not None:
            self.entry_frame.pulse_accent(self.theme.primary)
        return False

    def _finish_success(self) -> None:
        if self.window is not None:
            self.window.set_visible(False)
        self.quit()

    def _request_power(self, _button: Gtk.Button, action: str) -> None:
        if self._power_pending or (self.vanishing is not None and self.vanishing.vanishing):
            return
        self._power_pending = True
        try:
            future = self.power_executor.submit(self.power.request, action)
        except RuntimeError:
            self._power_pending = False
            return
        future.add_done_callback(lambda completed: GLib.idle_add(self._power_finished, action, completed))

    def _power_finished(self, action: str, future: Future[PowerResult]) -> bool:
        try:
            result = future.result()
        except Exception:
            result = PowerResult(False, "internal_error")
        if not result.success:
            self._power_pending = False
            LOG.warning("power_category=%s", result.category or "failed")
            frame = self.power_frames.get(action)
            if frame is not None:
                frame.pulse_accent(self.theme.primary)
        return False

    def do_shutdown(self) -> None:
        self.auth_executor.shutdown(wait=False, cancel_futures=True)
        self.power_executor.shutdown(wait=False, cancel_futures=True)
        Gtk.Application.do_shutdown(self)


def main(argv: Sequence[str] | None = None) -> int:
    # A login form never needs file, settings, screenshot, or screencast
    # portals. This must run before Gtk.Application initializes; otherwise the
    # short-lived greeter can start a full portal stack that outlives Hyprland.
    Gtk.disable_portals()
    parser = argparse.ArgumentParser(prog="luminophore-greeter")
    parser.add_argument("--theme", type=Path, default=DEFAULT_THEME_PATH)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument("--test-script", default="failure,success")
    args = parser.parse_args(argv)
    try:
        theme_path = args.theme
        if theme_path == DEFAULT_THEME_PATH and not theme_path.exists() and os.environ.get("LUMINOPHORE_RELEASE_ROOT"):
            theme_path = Path(os.environ["LUMINOPHORE_RELEASE_ROOT"]) / "config/greeter-theme.json"
        theme = GreeterTheme.read(theme_path)
        local_config = GreeterLocalConfig("test") if args.test_mode else GreeterLocalConfig.read(args.config)
        outcomes = tuple(item.strip() for item in args.test_script.split(",") if item.strip()) if args.test_mode else ()
        if any(item not in {"failure", "success"} for item in outcomes):
            raise GreeterConfigurationError("invalid_test_script")
    except GreeterConfigurationError as exc:
        LOG.error("category=%s", exc)
        return 1
    application = LuminophoreGreeterApplication(theme, local_config, test_outcomes=outcomes)
    # argparse already consumed our options; do not let GTK parse sys.argv again.
    result = application.run(["luminophore-greeter"])
    return 1 if application.failed else result


if __name__ == "__main__":
    raise SystemExit(main())
