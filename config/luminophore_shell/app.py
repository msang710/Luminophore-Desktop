from __future__ import annotations

import colorsys
import hashlib
import json
from uuid import uuid4
import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

import hashlib
from .config import ConfigError, ShellConfig, config_digest, config_mtime_ns, load_config, load_config_text, write_config_patch, write_taskbar_pins
from .ui_dispatch import UiDispatchRequest
from .window_snapshot import WindowSnapshotReader
from .applications import ApplicationCatalog
from .spatial_feedback import SpatialFeedbackQueue, parse_spatial_feedback, parse_fullscreen_feedback
from .spatial_grab import parse_spatial_grab
from .app_icons import ApplicationIconProvider
from .bootstrap import LAYER_SHELL, preload_entries
from .clipboard import ClipboardHistory
from .hyprland import HyprlandClient, HyprlandError, HyprlandEventListener, MonitorRecord, WindowRecord, normalize_address
from .icon_generation import GeneratedIconStore, IconDraftGenerator
from .ipc import IpcError, IpcServer
from .minimize import MinimizeController
from .metrics import MetricSnapshot, MetricsProvider
from .notifications import Notification, NotificationManager, notification_target_connector
from .palette_controller import PaletteExtractionController, PaletteSettingsState
from .screen_color_picker import ScreenColorPickResult, ScreenColorPicker
from .settings_controller import SettingsController, SettingsRuntimeApplyError
from .settings_generation import fixture_enabled, boot_config
from .settings_service import SettingsServiceHost
from .settings_contract import SettingsApplyRequest, SettingsCompletionUnknown
from .settings_schema import settings_values, RETIRED_VISUAL_PATHS
from .state import read_palette_state_details
from .matugen import MatugenScheme
from .system_theme import SystemThemeController, SystemThemeSource, SystemThemeState, SystemThemeTargetManager
from .cursor_theme import CursorThemeTransaction
from .hyprland_settings import HyprctlSettingsRuntime, HyprlandPaletteTransaction
from .theme import Palette, build_css, fallback_palette, resolve_palettes
from .visual_tokens import LuminophoreVisualTokens, derive_visual_tokens
from .tray import StatusNotifierHost
from .ui.launcher import LauncherPanel, TaskbarWidget
from .ui.notifications import NotificationView, ToastLayer
from .ui.surface import CornerSurface, ExpansionCoordinator, SurfacePlacement
from .ui.spatial_editor import SpatialEditorSurface
from .ui.system import SystemView
from .ui.weather import WeatherView
from .weather import WeatherProvider, WeatherSnapshot
from .spotify import SpotifyProvider, SpotifySnapshot
from .ui.spotify import SpotifyView
from .network_policy import OfflinePolicy
from .location import CityResult, LocationProvider, city_config_changes
from .session_actions import SessionActionController, SessionActionError
from .hardware_controls import AudioController, DdcBrightnessController, DdcBrightnessQueue, DdcTarget, HardwareControlError, MediaController, discover_ddc_targets, read_audio_quick_state, read_brightness_quick_state
from .ui.osd import OsdLayer
from .system_controls import BluetoothDevice, PairingDecision, PairingPrompt, SystemControlError, SystemControlSnapshot, SystemControls, WifiNetwork
from .bluez_agent import BluezAgent
from .calendar import CalendarAccount, CalendarError, CalendarSnapshot, GoogleCalendarProvider, GoogleOAuthFlow, calendar_error_message, install_oauth_client, load_oauth_client, oauth_client_path
from .privacy import PrivacyProvider, PrivacySnapshot
from .first_frame import GtkFirstFrameProbe
from .glow_layer import GlowLayerController, SpectrumOutput
from .appearance_service import AppearanceService, AppearanceServiceError
from .appearance_state import FileAppearanceStateStore
from .appearance_types import AppearanceErrorCategory, AppearanceMode, AppearanceSource, AppearanceSourceKind, WallpaperProviderName
from .matugen import MatugenAppearanceCompiler
from .wallpaper_providers import provider_for
from .binding_registry import BindingFlags
from .ui.widgets import (
    launcher_collapsed,
)


LOG = logging.getLogger("luminophore-shell")


def overview_occupied_monitor_names(
    monitors: list[MonitorRecord],
    windows: list[WindowRecord],
) -> frozenset[str]:
    """Return monitors whose currently visible workspace owns a tiled client."""
    visible_workspaces = {
        monitor.name: {
            workspace
            for workspace in (monitor.active_workspace, monitor.special_workspace)
            if workspace.name
        }
        for monitor in monitors
        if monitor.name
    }
    return frozenset(
        window.monitor_name
        for window in windows
        if (
            window.monitor_name in visible_workspaces
            and window.workspace in visible_workspaces[window.monitor_name]
            and window.mapped
            and not window.hidden
            and not window.minimized
            and not window.floating
        )
    )


def _surface_connector(surface: object) -> str:
    monitor = getattr(surface, "monitor", None)
    get_connector = getattr(monitor, "get_connector", None)
    return str(get_connector() or "") if callable(get_connector) else ""


class LuminophoreShellApplication(Gtk.Application):
    def __init__(self, config: ShellConfig) -> None:
        super().__init__(application_id="io.github.msang710.LuminophoreShell", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.config = config
        self.config_mtime = config_mtime_ns(config.path)
        self.css_provider = Gtk.CssProvider()
        self.wallpapers: dict[str, Path] = {}
        self.current_palettes: dict[int, Palette] = {}
        self.current_visual_tokens: dict[int, LuminophoreVisualTokens] = {}
        self.current_palette_by_connector: dict[str, Palette] = {}
        self.current_scheme_by_connector: dict[str, MatugenScheme] = {}
        self.palette_captured_at: float | None = None
        self.coordinator = ExpansionCoordinator()
        self.surfaces: dict[str, CornerSurface] = {}
        self.hyprland = HyprlandClient()
        self.catalog: ApplicationCatalog | None = None
        self.app_icon_provider: ApplicationIconProvider | None = None
        self.icon_generator: IconDraftGenerator | None = None
        self.generated_icon_store: GeneratedIconStore | None = None
        self.clipboard: ClipboardHistory | None = None
        self.minimize: MinimizeController | None = None
        self.weather_provider: WeatherProvider | None = None
        self.location_provider: LocationProvider | None = None
        self.metrics_provider: MetricsProvider | None = None
        self.notification_manager: NotificationManager | None = None
        self.tray_host: StatusNotifierHost | None = None
        self.weather_view: WeatherView | None = None
        self.system_view: SystemView | None = None
        self.spotify_view: SpotifyView | None = None
        self.notification_view: NotificationView | None = None
        self.toast_layers: dict[str, ToastLayer] = {}
        self.osd_layers: dict[str, OsdLayer] = {}
        self.spatial_editor_surface: SpatialEditorSurface | None = None
        self.audio_controller = AudioController()
        self.media_controller = MediaController()
        self.spotify_provider = SpotifyProvider(self._spotify_changed)
        self.brightness_controller = DdcBrightnessController()
        self.brightness_queue = DdcBrightnessQueue(self.brightness_controller)
        self._hardware_locks: dict[str, threading.Lock] = {"audio": threading.Lock(), "media": threading.Lock()}
        self._hardware_refresh_locks = {"audio": threading.Lock(), "brightness": threading.Lock()}
        self._ddc_targets: dict[str, DdcTarget] = {}
        self._brightness_values: dict[str, int] = {}
        self._monitor_powered: dict[str, bool] = {}
        self._brightness_key_intents: dict[str, int] = {}
        self._brightness_key_lock = threading.Lock()
        self.system_controls = SystemControls()
        self.bluez_agent = BluezAgent(self._pairing_prompted)
        self.system_control_snapshot: SystemControlSnapshot | None = None
        self.calendar_account = CalendarAccount("google-primary", "Google Calendar")
        self.calendar_provider: GoogleCalendarProvider | None = None
        self.privacy_provider = PrivacyProvider(self._privacy_changed)
        self.first_frame_probe = GtkFirstFrameProbe()
        self.glow_layer: GlowLayerController | None = None
        self._glow_layer_source = 0
        self._privacy_active: frozenset[str] = frozenset()
        self.latest_metrics: MetricSnapshot | None = None
        self.launcher_panel: LauncherPanel | None = None
        self.taskbar: TaskbarWidget | None = None
        self.windows: list[WindowRecord] = []
        self._spatial_feedback = SpatialFeedbackQueue()
        self._spatial_feedback_timer = 0
        self.hypr_monitors: list[MonitorRecord] = []
        self._hypr_events: HyprlandEventListener | None = None
        self._refresh_source = 0
        self._window_reader = None
        self._window_refresh_closed = False
        self._monitor_model: Gio.ListModel | None = None
        self._ipc: IpcServer | None = None
        self._screen_pick_pending = False
        self._last_screen_color_pick: ScreenColorPickResult | None = None
        self._outside_click_suppressed_until = 0.0
        self._overview_surface_names: set[str] = set()
        self._overview_reveal_names: set[str] = set()
        self._overview_phase = "hidden"
        self._overview_pending = 0
        self.palette_controller = PaletteExtractionController(
            config.path,
            self._palette_monitors,
            lambda: fallback_palette(self.config.theme),
            self._palette_state_changed,
            self._palette_applied,
            provider=config.appearance.wallpaper_provider,
        )
        self.screen_color_picker = ScreenColorPicker()
        self._settings_fixture_host = None
        self._joint_settings_enabled = True
        self.settings_controller = SettingsController(
            config.path,
            self._settings_applied,
            transaction=lambda values, digest: self._dispatch({"command": "settings-apply",
                "changes": dict(values), "expected_digest": digest, "request_id": f"embedded-{uuid4().hex}"}),
            status=lambda request_id, recover: self._settings_status(request_id, recover=recover),
            read_snapshot=(lambda: self._settings_fixture_host.controller_snapshot()),
        )
        self.appearance_compiler = MatugenAppearanceCompiler()
        selected_provider = WallpaperProviderName(config.appearance.wallpaper_provider)
        self.appearance_service = AppearanceService(
            {selected_provider: provider_for(selected_provider)},
            self.appearance_compiler,
            FileAppearanceStateStore(config.path, self._appearance_runtime_reload),
            lambda: tuple(sorted(monitor.name for monitor in self._palette_monitors() if monitor.name)),
            lambda: config_digest(self.config.path),
            lambda: (),
        )
        self._appearance_job_lock = threading.Lock()
        self._appearance_jobs: dict[str, dict[str, Any]] = {}
        self._appearance_job_fingerprints: dict[str, str] = {}
        self._appearance_job_deadlines: dict[str, float] = {}
        self._appearance_job_finished: dict[str, float] = {}
        self._appearance_tokens: dict[str, Any] = {}
        self._appearance_token_created: dict[str, float] = {}
        self._appearance_token_reservations: dict[str, str] = {}
        self.system_theme_controller = SystemThemeController(
            config.path,
            self._system_theme_source,
            self._system_theme_state_changed,
            self._system_theme_config_saved,
            targets=SystemThemeTargetManager(
                hyprland=HyprlandPaletteTransaction(
                    HyprctlSettingsRuntime(),
                    config.path,
                ),
                cursor=CursorThemeTransaction(),
            ),
        )
        self.connect("activate", self._activate)

    def _activate(self, _application: Gtk.Application) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            raise RuntimeError("Wayland display is unavailable")
        Gtk.StyleContext.add_provider_for_display(display, self.css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.catalog = ApplicationCatalog(self.config.launcher.preferred_actions)
        self.app_icon_provider = ApplicationIconProvider(
            display,
            self.config.theme.app_icon_theme,
            self.config.theme.app_icon_aliases,
            lambda: GLib.idle_add(self._app_icons_changed),
        )
        self.icon_generator = IconDraftGenerator(self.app_icon_provider.source_file)
        self.generated_icon_store = GeneratedIconStore(
            changed=lambda: GLib.idle_add(self._generated_icons_changed),
        )
        self.clipboard = ClipboardHistory(
            self.config.launcher.clipboard_limit,
            lambda: GLib.idle_add(self._refresh_launcher),
            retention_hours=self.config.launcher.clipboard_retention_hours,
        )
        self.clipboard.start()
        self.minimize = MinimizeController(
            self.hyprland,
            changed=self._queue_window_refresh,
        )
        network_policy = OfflinePolicy(self.config.network.offline)
        self.location_provider = LocationProvider(self.config.location, network_policy)
        self.weather_provider = WeatherProvider(
            self.config.weather,
            self._weather_changed,
            self.location_provider.resolve,
            network_policy,
        )
        self.metrics_provider = MetricsProvider(self.config.metrics, self._metrics_changed, self._hardware_danger)
        self.notification_manager = NotificationManager(
            self.config.notifications,
            lambda: GLib.idle_add(self._notifications_changed),
            self._notification_popup,
        )
        self.tray_host = StatusNotifierHost(lambda: GLib.idle_add(self._tray_changed))
        self._apply_css()
        self._build_surfaces(display)
        self._start_glow_layer()
        self._settings_fixture_host = SettingsServiceHost(self.hyprland, GLib.idle_add,
            lambda candidate: self._adopt_config(self.config, candidate), self._verify_joint_shell_settings)
        self._refresh_hardware_quick_controls("all")
        try:
            self.bluez_agent.start()
        except (SystemControlError, Exception) as exc:
            LOG.warning("BlueZ pairing agent unavailable: %s", exc)
        self._start_ipc()
        self._hypr_events = HyprlandEventListener(self._hypr_event)
        self._hypr_events.start()
        self._monitor_model = display.get_monitors()
        self._monitor_model.connect("items-changed", self._gdk_monitors_changed)
        self.privacy_provider.start()
        self._refresh_windows()
        if not self.notification_manager.start():
            LOG.warning("notification daemon unavailable: %s", self.notification_manager.start_error)
        if not self.tray_host.start():
            LOG.warning("StatusNotifier host unavailable: %s", self.tray_host.start_error)
        self.weather_provider.start()
        self.metrics_provider.start()
        self.spotify_provider.start()
        # Native ownership is restored only when a previously presented scene exists.
        from .background_store import BackgroundStore
        if (BackgroundStore().state / "background-state.json").exists():
            self._background_controller().restore()
        GLib.timeout_add(250, self._watch_config)

    def _apply_css(self) -> None:
        display = Gdk.Display.get_default()
        connectors: list[str] = []
        if display:
            for index, monitor in enumerate(self._monitors(display)):
                connectors.append(monitor.get_connector() or f"monitor-{index}")
        palette_state = read_palette_state_details()
        captured_at = palette_state.captured_at
        engine_palettes = {
            connector: entry.palette
            for connector, entry in palette_state.entries.items()
        }
        palettes = resolve_palettes(self.config.theme, connectors, self.wallpapers, engine_palettes)
        if not palettes:
            fallback = fallback_palette(self.config.theme)
            palettes = {0: fallback, 1: fallback, 2: fallback}
        self.current_palettes = palettes
        self.current_visual_tokens = {
            index: derive_visual_tokens(palette, self.config.theme.backdrop_opacity)
            for index, palette in palettes.items()
        }
        self.current_palette_by_connector = {
            connector: palettes[index]
            for index, connector in enumerate(connectors)
            if index in palettes
        }
        self.current_scheme_by_connector = {
            connector: entry.scheme
            for connector, entry in palette_state.entries.items()
            if (
                entry.scheme is not None
                and self.config.theme.palette_source in {"hyprpaper", "awww"}
                and self.current_palette_by_connector.get(connector) == entry.palette
            )
        }
        self.palette_captured_at = captured_at
        css = build_css(
            self.config.theme,
            palettes,
            self.config.layout.radius,
            self.current_visual_tokens,
        )
        self.css_provider.load_from_string(css)
        fallback = fallback_palette(self.config.theme)
        for surface in self.surfaces.values():
            palette = palettes.get(surface.palette_index, fallback)
            tokens = self.current_visual_tokens.get(surface.palette_index) or derive_visual_tokens(
                palette,
                self.config.theme.backdrop_opacity,
            )
            surface.set_glow_profile(tokens.raw_primary, tokens.face_primary, self.config)
        for osd in self.osd_layers.values():
            palette = palettes.get(osd.palette_index, fallback)
            tokens = self.current_visual_tokens.get(osd.palette_index) or derive_visual_tokens(
                palette,
                self.config.theme.backdrop_opacity,
            )
            osd.set_glow_profile(tokens.raw_primary, tokens.face_primary, self.config)
        self._sync_glow_layer()
        if self.system_view:
            palette = palettes[max(palettes)]
            self.system_view.set_colors(palette.primary, palette.secondary)
            self._sync_palette_view()

    def _palette_monitors(self) -> list[MonitorRecord]:
        if self.hypr_monitors:
            return list(self.hypr_monitors)
        try:
            return self.hyprland.monitors()
        except HyprlandError:
            return []

    def _palette_state_changed(self, _state: PaletteSettingsState) -> None:
        GLib.idle_add(self._sync_palette_view)

    def _sync_palette_view(self) -> bool:
        if self.system_view:
            self.system_view.update_palette_settings(
                self.palette_controller.state,
                self.config.theme.palette_source,
                self.current_palette_by_connector,
            )
        return False

    def _system_theme_source(self) -> SystemThemeSource | None:
        display = Gdk.Display.get_default()
        if display is None:
            return None
        main_monitors = [monitor for monitor in self._monitors(display) if monitor.get_geometry().x == 0]
        if len(main_monitors) != 1:
            return None
        monitor = main_monitors[0]
        connector = monitor.get_connector() or ""
        palette = self.current_palette_by_connector.get(connector)
        if not connector or palette is None:
            return None
        return SystemThemeSource(connector, palette, self.current_scheme_by_connector.get(connector))

    def _system_theme_state_changed(self, state: SystemThemeState) -> None:
        GLib.idle_add(self._sync_system_theme_view, state)

    @staticmethod
    def _system_theme_wire(state: SystemThemeState) -> dict[str, Any]:
        preview = state.preview
        return {
            "phase": state.phase,
            "message": state.message,
            "error_category": state.error_category,
            "backup_available": state.backup_available,
            "drifted": state.drifted,
            "preview": None if preview is None else {
                "preview_id": preview.preview_id,
                "mode": preview.semantic.mode,
                "generation_id": preview.semantic.generation_id,
                "tokens": dict(preview.semantic.tokens),
                "target_ids": list(preview.target_ids),
                "hyprland_settings": dict(preview.hyprland_settings),
            },
        }

    def _sync_system_theme_view(self, state: SystemThemeState) -> bool:
        if self.system_view:
            self.system_view.update_system_theme(state)
        return False

    def _system_theme_config_saved(self, old_config: ShellConfig, new_config: ShellConfig) -> None:
        GLib.idle_add(self._finish_system_theme_config_saved, old_config, new_config)

    def _finish_system_theme_config_saved(self, old_config: ShellConfig, new_config: ShellConfig) -> bool:
        self._adopt_config(old_config, new_config)
        return False

    def _palette_applied(self) -> None:
        GLib.idle_add(self._reload_after_palette_apply)

    def _reload_after_palette_apply(self) -> bool:
        try:
            self._reload_config()
        except (ConfigError, SettingsCompletionUnknown) as exc:
            LOG.error("palette configuration reload rejected: %s", exc)
        return False

    def _settings_applied(
        self,
        old_config: ShellConfig,
        new_config: ShellConfig,
        changed_paths: frozenset[str],
    ) -> None:
        self.config_mtime = config_mtime_ns(new_config.path)
        surface = self.surfaces.get("system")
        if surface:
            surface.when_transition_complete(
                False,
                lambda: self._finish_settings_apply(old_config, new_config, changed_paths),
            )
            surface.set_expanded(False)
            return
        GLib.idle_add(self._finish_settings_apply, old_config, new_config, changed_paths)

    def _finish_settings_apply(
        self,
        old_config: ShellConfig,
        new_config: ShellConfig,
        changed_paths: frozenset[str],
    ) -> bool:
        self._adopt_config(old_config, new_config)
        LOG.info("settings runtime apply completed: paths=%s", ",".join(sorted(changed_paths)))
        return False

    def _monitors(self, display: Gdk.Display) -> list[Gdk.Monitor]:
        model = display.get_monitors()
        monitors = [model.get_item(index) for index in range(model.get_n_items())]
        return sorted((monitor for monitor in monitors if monitor is not None), key=lambda item: item.get_geometry().x)

    def _build_surfaces(self, display: Gdk.Display) -> None:
        def retire(surface):
            # GTK's application removal callback needs a native surface even
            # for layer windows that have never been shown. Realization does
            # not map the window; it makes this destruction path well-defined.
            if not surface.window.get_realized():
                surface.window.realize()
            surface.destroy()

        if self.spatial_editor_surface:
            retire(self.spatial_editor_surface)
            self.spatial_editor_surface = None
        for surface in self.surfaces.values():
            retire(surface)
        self.surfaces.clear()
        for toast in self.toast_layers.values():
            retire(toast)
        self.toast_layers.clear()
        for osd in self.osd_layers.values():
            retire(osd)
        self.osd_layers.clear()
        monitors = self._monitors(display)
        if not monitors:
            raise RuntimeError("no monitors are available")
        left = monitors[0]
        right = monitors[-1]
        left_index = 0
        right_index = len(monitors) - 1
        single = left is right
        margin = self.config.layout.edge_margin
        stacked_top = margin + self.config.layout.panel_height + 12
        fallback = fallback_palette(self.config.theme)

        def visual_colors(index: int) -> tuple[str, str]:
            palette = self.current_palettes.get(index, fallback)
            tokens = self.current_visual_tokens.get(index) or derive_visual_tokens(
                palette,
                self.config.theme.backdrop_opacity,
            )
            return tokens.raw_primary, tokens.face_primary

        if not self.catalog or not self.clipboard or not self.minimize:
            raise RuntimeError("providers are not initialized")
        launcher_holder: dict[str, CornerSurface] = {}
        self.taskbar = TaskbarWidget(
            self.catalog,
            self.config.taskbar,
            self.hyprland,
            self.minimize,
            self._open_launcher,
            self._set_pinned,
            self.app_icon_provider,
        )
        self.launcher_panel = LauncherPanel(
            self.config.launcher,
            self.catalog,
            self.clipboard,
            self.hyprland,
            self.minimize,
            lambda: launcher_holder["surface"].set_expanded(False),
            lambda active: launcher_holder["surface"].set_height_override(
                max(self.config.layout.panel_height, left.get_geometry().height - 2 * margin) if active else None
            ),
            self._choose_wallpaper,
            self.app_icon_provider,
        )
        launcher = CornerSurface(
            self,
            left,
            "launcher",
            SurfacePlacement("left", margin, 360, self.config.layout.panel_height),
            self.config,
            self.coordinator,
            launcher_collapsed(lambda: self._open_launcher(""), self.taskbar),
            self.launcher_panel,
            keyboard=True,
            replace_collapsed=True,
            palette_index=left_index,
            glow_color=visual_colors(left_index)[0],
            glow_core_color=visual_colors(left_index)[1],
            external_layer_glow=True,
            external_glow_changed=self._sync_glow_layer,
            projection_changed=self.hyprland.shell_projection,
        )
        launcher_holder["surface"] = launcher
        self.surfaces["launcher"] = launcher

        if not self.notification_manager or not self.tray_host:
            raise RuntimeError("notification provider is not initialized")
        notification_holder: dict[str, CornerSurface] = {}
        self.notification_view = NotificationView(
            self.notification_manager,
            lambda: self._open_notification_mode("history"),
            lambda: self._open_notification_mode("power"),
            lambda: self._power("lock"),
            lambda: self._power("suspend"),
            lambda: self._power("logout"),
            lambda: self._power("reboot"),
            lambda: self._power("poweroff"),
            lambda: self._power("firmware-setup"),
            self.tray_host,
            self._refresh_hardware_quick_controls,
            self._set_quick_volume,
            self._toggle_quick_volume_mute,
            self._set_quick_brightness,
            self._set_monitor_power,
            self._set_quick_application_volume,
            self._set_quick_application_muted,
            self.catalog,
            self.app_icon_provider,
        )
        notification = CornerSurface(
            self,
            left,
            "notifications",
            SurfacePlacement("right", margin, 276, self.config.layout.panel_height),
            self.config,
            self.coordinator,
            self.notification_view.collapsed,
            self.notification_view.expanded,
            palette_index=left_index,
            glow_color=visual_colors(left_index)[0],
            glow_core_color=visual_colors(left_index)[1],
            external_layer_glow=True,
            external_glow_changed=self._sync_glow_layer,
            projection_changed=self.hyprland.shell_projection,
        )
        notification_holder["surface"] = notification
        self.surfaces["notifications"] = notification
        for index, monitor in enumerate(monitors):
            connector = monitor.get_connector() or f"monitor-{index}"
            self.toast_layers[connector] = ToastLayer(
                self,
                monitor,
                self.config.notifications,
                self.notification_manager,
                palette_index=index,
                catalog=self.catalog,
                icon_provider=self.app_icon_provider,
            )
            glow_color, glow_core_color = visual_colors(index)
            self.osd_layers[connector] = OsdLayer(
                self,
                monitor,
                self.config,
                index,
                glow_color,
                glow_core_color,
                self._sync_glow_layer,
                self.hyprland.shell_projection,
            )

        weather_holder: dict[str, CornerSurface] = {}
        self.weather_view = WeatherView(
            lambda: weather_holder["surface"].toggle(),
            self._refresh_weather,
        )
        weather = CornerSurface(
            self,
            right,
            "weather",
            SurfacePlacement("left", stacked_top if single else margin, self.config.layout.weather_width, self.config.layout.weather_height),
            self.config,
            self.coordinator,
            self.weather_view.collapsed,
            self.weather_view.expanded,
            palette_index=right_index,
            glow_color=visual_colors(right_index)[0],
            glow_core_color=visual_colors(right_index)[1],
            external_layer_glow=True,
            external_glow_changed=self._sync_glow_layer,
            projection_changed=self.hyprland.shell_projection,
        )
        weather_holder["surface"] = weather
        self.surfaces["weather"] = weather

        system_holder: dict[str, CornerSurface] = {}

        def set_system_settings_mode(active: bool) -> None:
            surface = system_holder.get("surface")
            if surface is None:
                return
            surface.set_height_override(
                max(self.config.layout.panel_height, right.get_geometry().height - 2 * margin)
                if active else None
            )
            surface.set_keyboard_interactive(active)

        self.system_view = SystemView(
            lambda: system_holder["surface"].toggle(),
            self.palette_controller.extract,
            self.palette_controller.apply,
            self.settings_controller,
            self.system_theme_controller,
            lambda: self.config.system_theme.mode,
            self.catalog,
            set_system_settings_mode,
            lambda: self.screen_color_picker.available,
            self._screen_color_pick_busy,
            self._begin_screen_color_pick,
            self._refresh_system_controls,
            lambda enabled: self._change_system_control("wifi", enabled),
            lambda network, password: self._change_system_control("wifi-connect", network, password),
            lambda enabled: self._change_system_control("bluetooth", enabled),
            lambda device: self._change_system_control("bluetooth-connect", device),
            self._pair_bluetooth,
            lambda profile, available: self._change_system_control("power-profile", profile, available),
            self._resolve_location_for_ui,
            self._search_city,
            self._select_city,
            self._connect_calendar,
            self._refresh_calendar,
            self._disconnect_calendar,
            self.app_icon_provider,
            self.icon_generator,
            self.generated_icon_store,
        )
        history_count = max(2, round(self.config.metrics.graph_seconds / self.config.metrics.sample_seconds))
        self.system_view.set_history_capacity(history_count, self.config.metrics.graph_seconds)
        if self.current_palettes:
            palette = self.current_palettes[max(self.current_palettes)]
            self.system_view.set_colors(palette.primary, palette.secondary)
        system = CornerSurface(
            self,
            right,
            "system",
            SurfacePlacement(
                "right",
                stacked_top if single else margin,
                self.config.layout.system_width,
                self.config.layout.system_height,
            ),
            self.config,
            self.coordinator,
            self.system_view.collapsed,
            self.system_view.expanded,
            palette_index=right_index,
            glow_color=visual_colors(right_index)[0],
            glow_core_color=visual_colors(right_index)[1],
            external_layer_glow=True,
            external_glow_changed=self._sync_glow_layer,
            projection_changed=self.hyprland.shell_projection,
        )
        system_holder["surface"] = system
        self.surfaces["system"] = system

        spotify_holder: dict[str, CornerSurface] = {}
        self.spotify_view = SpotifyView(
            lambda: spotify_holder["surface"].toggle(),
            lambda: self.spotify_provider.control("Previous"),
            lambda: self.spotify_provider.control("PlayPause"),
            lambda: self.spotify_provider.control("Next"),
        )
        self.spotify_view.update(self.spotify_provider.snapshot)
        spotify = CornerSurface(
            self,
            right,
            "spotify",
            SurfacePlacement("left", 0, 112, self.config.layout.panel_height, "bottom"),
            self.config,
            self.coordinator,
            self.spotify_view.collapsed,
            self.spotify_view.expanded,
            replace_collapsed=True,
            palette_index=right_index,
            glow_color=visual_colors(right_index)[0],
            glow_core_color=visual_colors(right_index)[1],
            external_layer_glow=True,
            external_glow_changed=self._sync_glow_layer,
            projection_changed=self.hyprland.shell_projection,
        )
        spotify_holder["surface"] = spotify
        self.surfaces["spotify"] = spotify
        self.spatial_editor_surface = SpatialEditorSurface(
            self, monitors[0], self.hyprland, self.catalog, self.app_icon_provider, 0,
            palette_indices={m.get_connector():i for i,m in enumerate(monitors)},
        )
        for surface in self.surfaces.values():
            surface.set_overview_close_requested(self._hide_overview)
            surface.present()
        primary_surface = self.surfaces.get("launcher")
        if primary_surface is not None:
            self.first_frame_probe.arm(primary_surface.window)
        if self.windows:
            self._update_window_widgets()
        if self.weather_view and self.weather_provider:
            self.weather_view.update(self.weather_provider.snapshot, self.weather_provider.error)
        if self.system_view and self.latest_metrics:
            self.system_view.update(self.latest_metrics)
        if self.system_view and self.system_control_snapshot:
            self.system_view.update_controls(self.system_control_snapshot)
        self._sync_palette_view()
        self._sync_system_theme_view(self.system_theme_controller.state)

    def _start_glow_layer(self) -> None:
        if self.config.theme.glow_renderer != "layer":
            return
        controller = GlowLayerController(
            Path(__file__).with_name("luminophore-glow-layer"),
            lambda reason: GLib.idle_add(self._glow_layer_failed, reason),
            os.environ.get("LUMINOPHORE_SPECTRUM_DEMO") == "1",
            self.config.theme.audio_spectrum_enabled,
            widget_bloom=os.environ.get("LUMINOPHORE_COMPOSITOR") != "1",
        )
        controller.spectrum_outputs = self._spectrum_outputs()
        if not controller.start():
            self.glow_layer = controller
            self._glow_layer_failed(controller.error)
            return
        self.glow_layer = controller
        # The process now owns only the spectrum.  Widget geometry is pushed by
        # each GTK surface allocation, so a 16 ms application-level poll would
        # be both redundant and a source of frame-phase drift.
        self._sync_glow_layer()

    @staticmethod
    def _rgb_float(color: str) -> tuple[float, float, float]:
        value = color.removeprefix("#")
        return tuple(int(value[index:index + 2], 16) / 255 for index in (0, 2, 4))  # type: ignore[return-value]

    @classmethod
    def _spectrum_color(cls, color: str) -> tuple[float, float, float]:
        red, green, blue = cls._rgb_float(color)
        hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
        saturation = max(0.72, min(0.95, saturation * 1.25))
        value = max(0.86, min(1.0, value * 1.08))
        return colorsys.hsv_to_rgb(hue, saturation, value)

    def _spectrum_outputs(self) -> tuple[SpectrumOutput, ...]:
        display = Gdk.Display.get_default()
        if display is None:
            return ()
        rows = []
        for monitor in self._monitors(display):
            connector = monitor.get_connector() or ""
            geometry = monitor.get_geometry()
            palette = self.current_palette_by_connector.get(connector)
            if connector and palette is not None and geometry.width > 0:
                rows.append((geometry.x, geometry.width, connector, palette))
        rows.sort(key=lambda row: row[0])
        if not rows:
            return ()
        desktop_left = rows[0][0]
        desktop_right = max(x + width for x, width, _connector, _palette in rows)
        desktop_width = desktop_right - desktop_left
        seam_colors = [
            self._spectrum_color(rows[0][3].secondary),
            self._spectrum_color(rows[-1][3].secondary),
        ]
        seam = tuple((left + right) * 0.5 for left, right in zip(*seam_colors, strict=True))
        outputs = []
        for index, (x, width, connector, palette) in enumerate(rows):
            primary = self._spectrum_color(palette.primary)
            left, right = (primary, seam) if index == 0 else (seam, primary)
            outputs.append(SpectrumOutput(connector, x, width, desktop_left, desktop_width, left, right))
        return tuple(outputs)

    def _sync_glow_layer(self) -> bool:
        controller = self.glow_layer
        if controller is None:
            self._glow_layer_source = 0
            return False
        controller.spectrum_outputs = self._spectrum_outputs()
        external_surfaces = {
            name: surface
            for name, surface in self.surfaces.items()
            if getattr(surface, "external_layer_glow", False)
        }
        external_surfaces.update({
            f"osd-{connector}": layer
            for connector, layer in self.osd_layers.items()
            if getattr(layer, "external_layer_glow", False)
        })
        if not controller.sync(external_surfaces):
            self._glow_layer_source = 0
            return False
        return True

    def _glow_layer_failed(self, reason: str) -> bool:
        LOG.error("direct glow layer disabled: %s", reason)
        for surface in self.surfaces.values():
            if (
                surface.glow.effective_renderer == "layer"
                and getattr(surface, "external_layer_glow", False)
            ):
                surface.glow.fallback_to_gsk(reason)
        return False

    def _open_launcher(self, query: str) -> None:
        surface = self.surfaces.get("launcher")
        if not surface or not self.launcher_panel:
            return
        if self.catalog:
            self.catalog.refresh()
            if self.app_icon_provider:
                self.app_icon_provider.invalidate()
            if self.taskbar:
                self.taskbar.update(self.windows, self.hypr_monitors)
        surface.set_expanded(True)
        GLib.idle_add(self.launcher_panel.focus_query, query)

    def _toggle_spatial_editor(self) -> bool:
        editor = self.spatial_editor_surface
        if editor is None:
            return False
        try:
            x, y = self.hyprland.cursor_position()
            # Cursor coordinates belong to the compositor. GDK output geometry
            # may use a different origin/order; match GDK only by connector.
            connector = None
            for output in self.hyprland.query("monitors"):
                width, height = output.get("width", 0), output.get("height", 0)
                if int(output.get("transform", 0)) % 2:
                    width, height = height, width
                scale = float(output.get("scale", 1))
                if scale <= 0:
                    continue
                left, top = output.get("x", 0), output.get("y", 0)
                if left <= x < left+width/scale and top <= y < top+height/scale:
                    connector = output.get("name")
                    break
            display = Gdk.Display.get_default()
            monitor = next((m for m in self._monitors(display) if m.get_connector() == connector), None) if display else None
            if monitor is not None:
                if editor.visible and editor.monitor.get_connector() != connector:
                    editor.close(animate=False)
                if not editor.visible:
                    editor.set_monitor(monitor)
        except (HyprlandError, TypeError, ValueError):
            return False
        if not editor.visible:
            for osd in self.osd_layers.values():
                osd.dismiss_spatial()
        editor.toggle(self.windows)
        return True

    def _request_overview_visibility(self, completed):
        from .shell_visibility import VisibilityReader
        if not hasattr(self, "_visibility_reader"):
            self._visibility_reader = VisibilityReader(self.hyprland.spatial_state, GLib.idle_add)
        self._visibility_sequence = getattr(self, "_visibility_sequence", 0) + 1
        sequence = self._visibility_sequence
        self._visibility_pending = True
        def deliver(occupied):
            if sequence != self._visibility_sequence or not self._visibility_pending:
                return False
            self._visibility_pending = False
            completed(occupied)
            return False
        self._visibility_reader.request(deliver)
        GLib.timeout_add(250, deliver, frozenset())

    def _toggle_overview(self, *, _occupied=None) -> None:
        if os.getenv("LUMINOPHORE_COMPOSITOR") == "1" and _occupied is None:
            if getattr(self, "_visibility_pending", False):
                self._visibility_sequence += 1
                self._visibility_pending = False
                return
            self._request_overview_visibility(lambda occupied: self._toggle_overview(_occupied=occupied))
            return
        surfaces = [
            self.surfaces[name]
            for name in ("launcher", "notifications", "weather", "system", "spotify")
            if name in self.surfaces
        ]
        if not surfaces:
            return
        overview_names = getattr(self, "_overview_surface_names", set())
        overview_phase = getattr(self, "_overview_phase", "hidden")
        if overview_phase in {"revealing", "visible"} or (
            overview_phase != "hiding" and (overview_names or all(surface.expanded for surface in surfaces))
        ):
            self._hide_overview(_occupied=_occupied)
            return
        if self.catalog:
            self.catalog.refresh()
            if self.app_icon_provider:
                self.app_icon_provider.invalidate()
            if self.taskbar:
                self.taskbar.update(self.windows, self.hypr_monitors)
        resuming_hide = overview_phase == "hiding"
        self._overview_surface_names = {surface.name for surface in surfaces}
        if not resuming_hide:
            hypr_monitors = getattr(self, "hypr_monitors", [])
            if _occupied is not None:
                occupied = _occupied
            elif hypr_monitors:
                occupied = overview_occupied_monitor_names(
                    hypr_monitors,
                    getattr(self, "windows", []),
                )
            else:
                occupied = frozenset()
            self._overview_reveal_names = {
                surface.name
                for surface in surfaces
                if _surface_connector(surface) in occupied
            }
        self._overview_phase = "revealing"
        revealing = [surface for surface in surfaces if surface.name in self._overview_reveal_names]
        self._overview_pending = len(revealing)
        for index, surface in enumerate(revealing):
            begin = getattr(surface, "begin_overview_reveal", None)
            if callable(begin):
                begin(True, index * 35, self._overview_surface_revealed)
            else:
                self._overview_surface_revealed()
        if not revealing:
            self._overview_phase = "visible"
        self.coordinator.open_group(surfaces)
        if self.launcher_panel:
            GLib.idle_add(self.launcher_panel.focus_query, "")

    def _overview_surface_revealed(self) -> None:
        self._overview_pending = max(0, getattr(self, "_overview_pending", 0) - 1)
        if self._overview_pending == 0 and self._overview_surface_names:
            self._overview_phase = "visible"

    def _overview_surface_hidden(self, surface: object) -> None:
        finish = getattr(surface, "finish_overview_hide", None)
        if callable(finish):
            finish()
        else:
            surface.set_expanded(False)
        self._overview_pending = max(0, getattr(self, "_overview_pending", 0) - 1)
        if self._overview_pending == 0:
            self._overview_phase = "hidden"
            self._overview_surface_names.clear()
            self._overview_reveal_names.clear()

    def _hide_overview(self, keeper: object | None = None, *, _occupied=None) -> None:
        if os.getenv("LUMINOPHORE_COMPOSITOR") == "1" and _occupied is None:
            self._request_overview_visibility(lambda occupied: self._hide_overview(keeper, _occupied=occupied))
            return
        reveal_names = getattr(self, "_overview_reveal_names", set())
        self._overview_reveal_names = reveal_names
        ordered = [
            self.surfaces[name]
            for name in ("launcher", "notifications", "weather", "system", "spotify")
            if name in self.surfaces and name in getattr(self, "_overview_surface_names", set())
        ]
        if not ordered:
            ordered = [surface for surface in self.surfaces.values() if surface.expanded]
            self._overview_surface_names = {
                getattr(surface, "name", name)
                for name, surface in self.surfaces.items()
                if surface in ordered
            }
        if keeper is not None:
            # The selected widget leaves the group but remains an overview-
            # promoted surface. Its later outside-click dismissal must play the
            # same reverse reveal instead of falling back to an abrupt collapse.
            self._overview_surface_names.discard(getattr(keeper, "name", ""))
            reveal_names.discard(getattr(keeper, "name", ""))
        closing = [surface for surface in reversed(ordered) if surface is not keeper]
        hiding = []
        for surface in closing:
            if _occupied is not None and _surface_connector(surface) not in _occupied:
                surface.release_overview_reveal()
                reveal_names.discard(surface.name)
                surface.set_expanded(False)
                continue
            owned = getattr(surface, "overview_reveal_owned", None)
            if surface.name in reveal_names or (callable(owned) and owned()):
                hiding.append(surface)
            else:
                surface.set_expanded(False)
        if not hiding:
            self._overview_phase = "hidden"
            self._overview_surface_names.clear()
            self._overview_reveal_names.clear()
            return
        self._overview_phase = "hiding"
        self._overview_pending = len(hiding)
        for index, surface in enumerate(hiding):
            begin = getattr(surface, "begin_overview_reveal", None)
            completed = lambda selected=surface: self._overview_surface_hidden(selected)
            if callable(begin):
                begin(False, index * 35, completed)
            else:
                completed()

    def _app_icons_changed(self) -> bool:
        if self.launcher_panel:
            self.launcher_panel.refresh()
        if self.taskbar:
            self.taskbar.update(self.windows, self.hypr_monitors)
        if self.system_view and self.system_view.settings_view.category in {"appearance", "launcher"}:
            self.system_view.settings_view.refresh_current_category()
        return False

    def _generated_icons_changed(self) -> bool:
        if self.app_icon_provider:
            self.app_icon_provider.reconfigure(
                self.config.theme.app_icon_theme,
                self.config.theme.app_icon_aliases,
            )
        return self._app_icons_changed()

    def _set_pinned(self, app_key: str, enabled: bool) -> None:
        pins = list(self.config.taskbar.pinned)
        if enabled and app_key not in pins:
            pins.append(app_key)
        elif not enabled:
            pins = [item for item in pins if item != app_key]
        digest = self._settings_fixture_host.controller_snapshot()[1]
        reply = self._settings_fixture_host.apply({'request_id': 'pins-'+uuid4().hex,
            'expected_digest': digest, 'changes': {'taskbar.pinned': pins}})
        if not reply.get('ok'):
            LOG.error('taskbar pin change rejected: %s', reply.get('error'))

    def _hypr_event(self, event: str, payload: str) -> None:
        if event == "luminophoreconnection":
            if reader := getattr(self, "_window_reader", None):
                reader.reset()
            self.hyprland.reset_projections()
            def invalidate_visibility():
                self._visibility_sequence = getattr(self, "_visibility_sequence", 0) + 1
                self._visibility_pending = False
                for surface in (*self.surfaces.values(), *self.osd_layers.values()):
                    surface._projection_generation = ""
                    surface._projection_status = "unsubmitted"
                    surface._projection_rejections = 0
                    surface._projection_handshake.start()
                return False
            GLib.idle_add(invalidate_visibility)
            if editor := getattr(self, "spatial_editor_surface", None):
                GLib.idle_add(editor.connection_reset)
            GLib.idle_add(self._resync_visual_settings)
            GLib.idle_add(self._queue_window_refresh)
            return
        if event == "luminophorespatialgrab":
            GLib.idle_add(self._handle_spatial_grab, payload)
            return
        if event == "luminophorespatialhistory":
            GLib.idle_add(self._history_feedback, payload)
            GLib.idle_add(self._queue_window_refresh)
            return
        if event == "luminophorefullscreen":
            GLib.idle_add(self._fullscreen_feedback, payload)
            return
        if event == "luminophorespatialaction":
            GLib.idle_add(self._queue_spatial_feedback, payload)
        if event == "luminophoreshellpresented":
            GLib.idle_add(self._spatial_editor_presented, payload)
            return
        if event == "custom" and payload.startswith("luminophore-shell-click:"):
            try:
                raw_x, raw_y = payload.removeprefix("luminophore-shell-click:").split(",", 1)
                x, y = round(float(raw_x)), round(float(raw_y))
            except ValueError:
                return
            GLib.idle_add(self._dismiss_expanded_at, x, y)
            return
        if event.startswith("monitor"):
            if reader := getattr(self, "_window_reader", None):
                reader.reset()
        if event.startswith(("openwindow", "closewindow", "activewindow", "movewindow", "workspace", "focusedmon", "monitor", "windowtitle", "urgent", "fullscreen", "luminophoreplacement", "luminophorespatial")):
            GLib.idle_add(self._queue_window_refresh)

    def _history_feedback(self, payload: str) -> bool:
        try:
            result = json.loads(payload)
            action = {"undo": "공간 되돌리기", "redo": "공간 다시 실행", "cancel": "크기 조절 취소"}[result["action"]]
            message = {
                "applied": action,
                "empty": "더 이상 되돌릴 공간 이력이 없습니다" if result["action"] == "undo" else "다시 실행할 공간 이력이 없습니다",
                "busy": "현재 조작을 끝낸 뒤 다시 시도하세요",
                "stale": "공간 상태가 바뀌었습니다. 확인한 뒤 다시 시도하세요",
                "unavailable": "현재 창·모니터 상태에서는 이 배치를 복원할 수 없습니다",
                "commit-failed": "공간 복원을 적용하지 못했습니다. 이력은 유지됩니다",
                "recovery-failed": "공간 복구에 실패해 추가 되돌리기를 중지했습니다",
                "cancel-failed": "크기 조절을 원래 상태로 되돌리지 못했습니다. 복구 이력은 유지됩니다",
            }[result["status"]]
        except (ValueError, TypeError, KeyError):
            return False
        editor = getattr(self, "spatial_editor_surface", None)
        if editor and editor.state.visible:
            editor.status.set_label(message)
            return False
        for osd in self.osd_layers.values():
            osd.show_spatial(message, None)
        return False

    def _spatial_editor_presented(self, payload: str) -> bool:
        # A presentation can arrive while the main thread is still recording
        # its commit acknowledgement. Match it only after that call returns.
        if not self.hyprland.shell_projection_presented(payload):
            return False
        if editor := getattr(self, "spatial_editor_surface", None):
            editor.projection_presented(payload)
        return False

    def _handle_spatial_grab(self, payload: str) -> bool:
        event = parse_spatial_grab(payload)
        editor = getattr(self, "spatial_editor_surface", None)
        if event is None or editor is None:
            return False
        display = Gdk.Display.get_default()
        monitors = tuple(self._monitors(display)) if display else ()
        editor.handle_grab(event, self.windows, monitors)
        return False

    def _queue_spatial_feedback(self, payload: str) -> bool:
        event = parse_spatial_feedback(payload)
        if event is None:
            return False
        self._spatial_feedback.push(event)
        if not self._spatial_feedback_timer:
            self._spatial_feedback_timer = GLib.timeout_add(40, self._flush_spatial_feedback)
        return False

    def _fullscreen_feedback(self, payload: str) -> bool:
        result = parse_fullscreen_feedback(payload)
        if result is None:
            return False
        connector, address, label = result
        osd = self.osd_layers.get(connector)
        if osd is not None:
            window = next((window for window in self.windows if normalize_address(window.address) == address), None)
            app = self.catalog.match_window_class(window.app_class) if window and self.catalog else None
            osd.show_spatial(label, app.icon if app else None)
        return False

    def _flush_spatial_feedback(self) -> bool:
        self._spatial_feedback_timer = 0
        for event in self._spatial_feedback.drain():
            editor = getattr(self, "spatial_editor_surface", None)
            if editor and editor.highlight(event):
                continue
            osd = self.osd_layers.get(event.connector)
            if osd is None:
                continue
            window = next((window for window in self.windows if normalize_address(window.address) == event.window), None)
            app = self.catalog.match_window_class(window.app_class) if window and self.catalog else None
            name = app.name if app else window.app_class if window else ""
            osd.show_spatial(event.label(name), app.icon if app else None)
        return False

    def _dismiss_expanded_at(self, x: int, y: int) -> bool:
        picker = getattr(self, "background_picker", None)
        if picker is not None and picker.target_visible and not picker.contains_global_point(x, y):
            picker.close()
        if time.monotonic() < self._outside_click_suppressed_until:
            return False
        expanded = [surface for surface in self.surfaces.values() if surface.expanded]
        if not expanded:
            return False
        keeper = next((surface for surface in expanded if surface.contains_global_point(x, y)), None)
        if getattr(self, "_overview_surface_names", set()):
            self._hide_overview(keeper)
            return False
        for surface in expanded:
            if surface is not keeper:
                owned = getattr(surface, "overview_reveal_owned", None)
                begin = getattr(surface, "begin_overview_reveal", None)
                finish = getattr(surface, "finish_overview_hide", None)
                if callable(owned) and owned() and callable(begin) and callable(finish):
                    begin(False, 0, finish)
                else:
                    surface.set_expanded(False)
        return False

    def _screen_color_pick_busy(self) -> bool:
        return self._screen_pick_pending or self.screen_color_picker.busy

    def _begin_screen_color_pick(
        self,
        completed: Callable[[ScreenColorPickResult], None],
    ) -> bool:
        surface = self.surfaces.get("system")
        if (
            not self.screen_color_picker.available
            or self._screen_color_pick_busy()
            or surface is None
            or self.system_view is None
        ):
            return False
        self._screen_pick_pending = True

        def start_after_close() -> None:
            current = self.surfaces.get("system")
            if current is surface:
                current.window.set_visible(False)
            GLib.timeout_add(80, self._launch_screen_color_picker, completed)

        surface.when_transition_complete(False, start_after_close)
        surface.set_expanded(False)
        return True

    def _launch_screen_color_picker(
        self,
        completed: Callable[[ScreenColorPickResult], None],
    ) -> bool:
        started = self.screen_color_picker.pick(
            lambda result: GLib.idle_add(self._finish_screen_color_pick, completed, result)
        )
        if not started:
            self._finish_screen_color_pick(
                completed,
                ScreenColorPickResult(
                    False,
                    message="화면 색상 선택기를 시작하지 못했습니다",
                    error_category="picker_failed",
                ),
            )
        return False

    def _finish_screen_color_pick(
        self,
        completed: Callable[[ScreenColorPickResult], None],
        result: ScreenColorPickResult,
    ) -> bool:
        self._screen_pick_pending = False
        self._last_screen_color_pick = result
        completed(result)
        surface = self.surfaces.get("system")
        if surface and self.system_view:
            self.system_view.resume_settings_after_screen_pick()
            surface.window.present()
            self._outside_click_suppressed_until = time.monotonic() + 0.45
            surface.set_expanded(True)
        return False

    def _close_expanded(self) -> None:
        self._overview_phase = "hidden"
        self._overview_pending = 0
        self._overview_surface_names.clear()
        self._overview_reveal_names.clear()
        for surface in self.surfaces.values():
            if surface.expanded:
                release = getattr(surface, "release_overview_reveal", None)
                if callable(release):
                    release()
                surface.set_expanded(False)

    def _queue_window_refresh(self) -> bool:
        if self._window_refresh_closed:
            return False
        if not self._refresh_source:
            self._refresh_source = GLib.timeout_add(35, self._refresh_windows)
        return False

    def _refresh_windows(self) -> bool:
        self._refresh_source = 0
        if self._window_refresh_closed:
            return False
        if self._window_reader is None:
            self._window_reader = WindowSnapshotReader(self.hyprland, GLib.idle_add, self._accept_windows)
        self._window_reader.request()
        return False

    def _accept_windows(self, monitors, windows) -> None:
        self.hypr_monitors = monitors
        self.windows = windows
        if self.notification_manager:
            self.notification_manager.set_fullscreen(any(window.immersive_fullscreen for window in windows if not window.minimized))
        if self.minimize:
            self.minimize.reconcile(windows)
        self._update_window_widgets()

    def _update_window_widgets(self) -> None:
        if self.taskbar:
            self.taskbar.update(self.windows, self.hypr_monitors)
        if self.launcher_panel:
            self.launcher_panel.update(self.windows, self.hypr_monitors)
        if editor := getattr(self, "spatial_editor_surface", None):
            editor.update(self.windows)

    def _refresh_launcher(self) -> bool:
        if self.launcher_panel:
            self.launcher_panel.refresh()
        return False

    def _gdk_monitors_changed(self, *_args: object) -> None:
        display = Gdk.Display.get_default()
        if display:
            self._ddc_targets = {}
            self._build_surfaces(display)
            self._refresh_hardware_quick_controls("all")
            self._queue_window_refresh()

    def _open_notification_mode(self, mode: str) -> None:
        if not self.notification_view:
            return
        if mode == "power":
            self.notification_view.show_power()
        else:
            self.notification_view.show_history()
        surface = self.surfaces.get("notifications")
        if surface:
            surface.set_expanded(True)

    def _notifications_changed(self) -> bool:
        if self.notification_view:
            self.notification_view.update()
        return False

    def _tray_changed(self) -> bool:
        if self.notification_view:
            self.notification_view.update_tray()
        return False

    def _notification_popup(self, notification: Notification) -> None:
        connector = notification_target_connector(self._focused_connector(), tuple(self.toast_layers))
        for name, layer in self.toast_layers.items():
            if name != connector:
                layer.remove(notification.id)
        toast = self.toast_layers.get(connector)
        if toast:
            toast.show(notification)

    def _background_controller(self):
        from .background_controller import BackgroundSceneController
        from .background_integration import apply_scene_palette, retire_external_providers
        controller = getattr(self, "background_controller", None)
        if controller is None:
            controller = BackgroundSceneController(self.hyprland.monitors, palette=lambda scene, bindings: apply_scene_palette(self, scene, bindings), retire=retire_external_providers)
            self.background_controller = controller
        return controller

    def _choose_wallpaper(self) -> None:
        from .ui.background_picker import BackgroundPickerSurface
        display = Gdk.Display.get_default()
        monitors = self._monitors(display) if display else []
        if not monitors:
            return
        focused = next((m.name for m in self.hyprland.monitors() if m.focused), None)
        monitor = next((m for m in monitors if m.get_connector() == focused), monitors[0])
        picker = getattr(self, "background_picker", None)
        if picker is not None and picker.monitor != monitor:
            picker.destroy()
            picker = None
        if picker is None:
            picker = BackgroundPickerSurface(self, self._background_controller(), self.hyprland, monitor, self.config.layout.expansion_ms, monitors.index(monitor))
            self.background_picker = picker
        picker.show()

    def _choose_wallpaper_legacy(self) -> None:
        dialog = Gtk.FileChooserNative(
            title="배경화면 선택",
            transient_for=self.get_active_window(),
            action=Gtk.FileChooserAction.OPEN,
            accept_label="적용",
            cancel_label="취소",
        )
        image_filter = Gtk.FileFilter()
        image_filter.set_name("이미지")
        image_filter.add_mime_type("image/*")
        dialog.add_filter(image_filter)
        dialog.connect("response", self._wallpaper_chosen)
        dialog.show()

    def _wallpaper_chosen(self, dialog: Gtk.FileChooserNative, response: int) -> None:
        selected = dialog.get_file() if response == Gtk.ResponseType.ACCEPT else None
        dialog.destroy()
        path = selected.get_path() if selected else None
        if not path:
            return
        def worker() -> None:
            self._apply_wallpaper_compatibility(Path(path))

        threading.Thread(target=worker, name="luminophore-appearance-apply", daemon=True).start()

    def _apply_wallpaper_compatibility(self, path: Path) -> bool:
        background = getattr(self, "background_controller", None)
        if background is not None and background.active:
            self._choose_wallpaper()
            return False
        try:
            connectors = tuple(sorted(monitor.name for monitor in self._palette_monitors() if monitor.name))
            sources = {
                connector: AppearanceSource(AppearanceSourceKind.IMAGE, str(path))
                for connector in connectors
            }
            preview = self.appearance_service.preview(
                self.config.appearance.wallpaper_provider,
                sources,
                AppearanceMode(self.config.system_theme.mode),
            )
            self.appearance_service.apply(preview.token)
        except AppearanceServiceError as exc:
            LOG.warning("appearance apply failed: category=%s", exc.category.value)
            return False
        except Exception as exc:
            LOG.warning("appearance apply failed: category=unexpected error=%s", type(exc).__name__)
            return False
        LOG.info("appearance applied: monitors=%s provider=%s", len(connectors), self.config.appearance.wallpaper_provider)
        return True

    def _appearance_runtime_reload(self) -> None:
        done = threading.Event()

        def reload_state() -> bool:
            try:
                self._reload_config()
            finally:
                done.set()
            return False

        GLib.idle_add(reload_state)
        if not done.wait(8.0):
            raise RuntimeError("appearance runtime reload timed out")

    def _power(self, action: str) -> None:
        self._confirm_session_action(action)

    def _confirm_session_action(self, action: str) -> None:
        labels = {
            "lock": "잠금",
            "suspend": "절전",
            "logout": "로그아웃",
            "reboot": "재시작",
            "poweroff": "종료",
            "firmware-setup": "BIOS/UEFI 설정으로 재시작",
        }
        dialog = Gtk.MessageDialog(
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=f"{labels[action]}할까요?",
        )
        dialog.add_buttons("취소", Gtk.ResponseType.CANCEL, "실행", Gtk.ResponseType.ACCEPT)

        def response(_dialog: Gtk.MessageDialog, response_id: int) -> None:
            dialog.destroy()
            if response_id != Gtk.ResponseType.ACCEPT:
                return

            def worker() -> None:
                controller = SessionActionController(
                    lambda argv: subprocess.run(argv, check=False, capture_output=True, text=True),
                )
                try:
                    controller.execute(action, confirmed=True)
                except SessionActionError as exc:
                    LOG.error("session action failed: action=%s error=%s", action, exc)

            threading.Thread(target=worker, name=f"luminophore-session-{action}", daemon=True).start()

        dialog.connect("response", response)
        dialog.present()

    def _refresh_weather(self) -> None:
        if self.weather_provider:
            self.weather_provider.refresh(manual=True)

    def _weather_changed(self, snapshot: WeatherSnapshot | None, error: str) -> None:
        def update() -> bool:
            if self.weather_view:
                self.weather_view.update(snapshot, error)
            return False

        GLib.idle_add(update)

    def _spotify_changed(self, snapshot: SpotifySnapshot) -> None:
        GLib.idle_add(self._sync_spotify_view, snapshot)

    def _sync_spotify_view(self, snapshot: SpotifySnapshot) -> bool:
        if self.spotify_view:
            self.spotify_view.update(snapshot)
        return False

    def _metrics_changed(self, snapshot: MetricSnapshot) -> None:
        self.latest_metrics = snapshot

        def update() -> bool:
            if self.system_view:
                self.system_view.update(snapshot)
            return False

        GLib.idle_add(update)

    def _hardware_danger(self, key: str, message: str) -> None:
        LOG.warning("hardware danger %s: %s", key, message)
        if self.notification_manager:
            self.notification_manager.notify_hardware("하드웨어 위험", message)

    def _start_ipc(self) -> None:
        self._ipc = IpcServer(self._dispatch_from_thread)
        self._ipc.start()

    @staticmethod
    def _valid_job_id(value: object) -> bool:
        return isinstance(value, str) and bool(value) and len(value) <= 128 and not any(char.isspace() for char in value)

    @staticmethod
    def _appearance_fingerprint(payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _cleanup_appearance_jobs(self, now: float) -> None:
        expired_jobs = [key for key, finished in self._appearance_job_finished.items() if now - finished > 600]
        for key in expired_jobs:
            self._appearance_jobs.pop(key, None)
            self._appearance_job_fingerprints.pop(key, None)
            self._appearance_job_deadlines.pop(key, None)
            self._appearance_job_finished.pop(key, None)
        expired_tokens = [key for key, created in self._appearance_token_created.items() if now - created > 600]
        for key in expired_tokens:
            if key in self._appearance_token_reservations:
                continue
            self._appearance_tokens.pop(key, None)
            self._appearance_token_created.pop(key, None)
            self.appearance_service.discard(key)

    def _finish_appearance_job(self, request_id: str, result: dict[str, Any]) -> None:
        with self._appearance_job_lock:
            self._appearance_jobs[request_id] = result
            self._appearance_job_deadlines.pop(request_id, None)
            self._appearance_job_finished[request_id] = time.monotonic()

    def _start_appearance_preview(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        sources = request.get("sources")
        if not self._valid_job_id(request_id) or not isinstance(sources, dict) or not sources:
            return {"ok": False, "category": "validation", "error": "invalid appearance preview request"}
        if any(not isinstance(name, str) or not isinstance(value, str) or not value for name, value in sources.items()):
            return {"ok": False, "category": "validation", "error": "wallpaper assignments are invalid"}
        try:
            mode = AppearanceMode(str(request.get("mode", "")))
        except ValueError:
            return {"ok": False, "category": "validation", "error": "appearance mode must be dark or light"}
        fingerprint = self._appearance_fingerprint({"command": "preview", "mode": mode.value, "sources": sources})
        with self._appearance_job_lock:
            self._cleanup_appearance_jobs(time.monotonic())
            if request_id in self._appearance_jobs:
                if self._appearance_job_fingerprints.get(request_id) == fingerprint:
                    return {"ok": True, **dict(self._appearance_jobs[request_id])}
                return {"ok": False, "category": "conflict", "error": "appearance request ID already exists"}
            self._appearance_jobs[request_id] = {"request_id": request_id, "phase": "previewing"}
            self._appearance_job_fingerprints[request_id] = fingerprint
            self._appearance_job_deadlines[request_id] = time.monotonic() + 30.0

        assignments = {
            name: AppearanceSource(AppearanceSourceKind.IMAGE, value)
            for name, value in sources.items()
        }

        def worker() -> None:
            try:
                preview = self.appearance_service.preview(
                    self.config.appearance.wallpaper_provider, assignments, mode,
                )
                with self._appearance_job_lock:
                    self._appearance_tokens[preview.token.preview_id] = preview.token
                    self._appearance_token_created[preview.token.preview_id] = time.monotonic()
                self._finish_appearance_job(request_id, {
                    "request_id": request_id,
                    "phase": "ready",
                    "preview_id": preview.token.preview_id,
                    "provider": preview.token.provider.value,
                    "monitors": [
                        {
                            "connector": connector,
                            "generation_id": compiled.generation_id,
                            "palette": dict(compiled.palette),
                        }
                        for connector, compiled in preview.compiled
                    ],
                })
            except AppearanceServiceError as exc:
                self._finish_appearance_job(request_id, {
                    "request_id": request_id, "phase": "error",
                    "category": exc.category.value, "message": "appearance preview failed",
                })
            except Exception:
                LOG.exception("appearance preview failed unexpectedly")
                self._finish_appearance_job(request_id, {
                    "request_id": request_id, "phase": "error",
                    "category": "compiler_incompatible", "message": "appearance preview failed",
                })

        try:
            threading.Thread(target=worker, name="luminophore-appearance-preview", daemon=True).start()
        except RuntimeError:
            self._finish_appearance_job(request_id, {
                "request_id": request_id, "phase": "error",
                "category": "completion_unknown", "message": "appearance preview could not start",
            })
            return {"ok": False, "category": "completion_unknown", "error": "appearance preview could not start"}
        return {"ok": True, "request_id": request_id, "phase": "previewing"}

    def _start_appearance_apply(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        preview_id = request.get("preview_id")
        if not self._valid_job_id(request_id) or not isinstance(preview_id, str) or not preview_id:
            return {"ok": False, "category": "validation", "error": "invalid appearance apply request"}
        fingerprint = self._appearance_fingerprint({"command": "apply", "preview_id": preview_id})
        with self._appearance_job_lock:
            self._cleanup_appearance_jobs(time.monotonic())
            if request_id in self._appearance_jobs:
                if self._appearance_job_fingerprints.get(request_id) == fingerprint:
                    return {"ok": True, **dict(self._appearance_jobs[request_id])}
                return {"ok": False, "category": "conflict", "error": "appearance request ID already exists"}
            token = self._appearance_tokens.get(preview_id)
            if token is None or preview_id in self._appearance_token_reservations:
                return {"ok": False, "category": "stale_preview", "error": "appearance preview is stale"}
            self._appearance_token_reservations[preview_id] = request_id
            self._appearance_jobs[request_id] = {"request_id": request_id, "phase": "applying"}
            self._appearance_job_fingerprints[request_id] = fingerprint
            self._appearance_job_deadlines[request_id] = time.monotonic() + 20.0

        def worker() -> None:
            try:
                self.appearance_service.apply(token)
                with self._appearance_job_lock:
                    self._appearance_tokens.pop(preview_id, None)
                    self._appearance_token_created.pop(preview_id, None)
                    self._appearance_token_reservations.pop(preview_id, None)
                self._finish_appearance_job(request_id, {
                    "request_id": request_id, "phase": "complete",
                    "preview_id": preview_id,
                })
            except AppearanceServiceError as exc:
                with self._appearance_job_lock:
                    self._appearance_token_reservations.pop(preview_id, None)
                    if exc.category is not AppearanceErrorCategory.BUSY:
                        self._appearance_tokens.pop(preview_id, None)
                        self._appearance_token_created.pop(preview_id, None)
                self._finish_appearance_job(request_id, {
                    "request_id": request_id, "phase": "error",
                    "category": exc.category.value, "message": "appearance apply failed",
                })
            except Exception:
                LOG.exception("appearance apply failed unexpectedly")
                with self._appearance_job_lock:
                    self._appearance_token_reservations.pop(preview_id, None)
                self._finish_appearance_job(request_id, {
                    "request_id": request_id, "phase": "error",
                    "category": "completion_unknown", "message": "appearance apply completion is unknown",
                })

        try:
            threading.Thread(target=worker, name="luminophore-appearance-apply", daemon=True).start()
        except RuntimeError:
            with self._appearance_job_lock:
                self._appearance_token_reservations.pop(preview_id, None)
            self._finish_appearance_job(request_id, {
                "request_id": request_id, "phase": "error",
                "category": "completion_unknown", "message": "appearance apply could not start",
            })
            return {"ok": False, "category": "completion_unknown", "error": "appearance apply could not start"}
        return {"ok": True, "request_id": request_id, "phase": "applying"}

    def _dispatch_from_thread(self, request: dict[str, Any]) -> dict[str, Any]:
        pending = UiDispatchRequest(lambda: self._dispatch(request))
        GLib.idle_add(pending.invoke)
        return pending.wait(2.0)

    def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        command = request.get("command")
        if command == "wallpaper":
            controller = self._background_controller()
            action = request.get("action", "status")
            try:
                if action == "status": return controller.status()
                if action in {"list", "current"}:
                    return {**controller.status(), "scenes": [scene.to_dict() for scene in controller.store.scenes()]}
                if action == "open":
                    self._choose_wallpaper()
                    return {"ok": True}
                if action == "apply": return controller.request(request.get("scene", ""), request.get("request_id"))
                if action in {"next", "previous"}:
                    from .background_picker import ScenePickerModel
                    model = ScenePickerModel(controller.store.scenes(), controller.desired or controller.active)
                    scene = model.navigate(1 if action == "next" else -1)
                    if scene is None: return {"ok": False, "error": "no_scenes"}
                    return controller.request(scene.id, request.get("request_id"))
                return {"ok": False, "error": "invalid_action"}
            except (ValueError, OSError) as exc:
                return {"ok": False, "error": str(exc)}
        if command == "settings-snapshot":
            host = getattr(self, '_settings_fixture_host', None)
            return host.snapshot() if host else {'ok': False, 'error': 'settings service starting'}
        if command == "settings-capabilities":
            host = getattr(self, '_settings_fixture_host', None)
            theme_targets = self.system_theme_controller.targets.selected_targets()
            return {'ok': True, 'capabilities': {
                'settings_apply': host is not None, 'hyprland': host is not None,
                'hyprland_live': host is not None, 'visual_settings': host is not None,
                'appearance': self.appearance_service is not None,
                'system_theme': bool(theme_targets) and self.system_theme_controller.backend.executable.is_file(),
                'bindings': host is not None,
            }}
        if command == "appearance-preview":
            return self._start_appearance_preview(request)
        if command == "appearance-context":
            return {
                "ok": True,
                "provider": self.config.appearance.wallpaper_provider,
                "monitors": sorted({monitor.name for monitor in self._palette_monitors() if monitor.name}),
                "modes": [mode.value for mode in AppearanceMode],
            }
        if command == "appearance-apply":
            background = getattr(self, "background_controller", None)
            if background is not None and background.active:
                return {"ok": False, "category": "native_scene_owner", "error": "배경화면은 장면 선택기에서 적용하세요"}
            return self._start_appearance_apply(request)
        if command == "appearance-status":
            request_id = str(request.get("request_id", ""))
            with self._appearance_job_lock:
                now = time.monotonic()
                self._cleanup_appearance_jobs(now)
                deadline = self._appearance_job_deadlines.get(request_id)
                if deadline is not None and now > deadline:
                    current = self._appearance_jobs.get(request_id, {})
                    if current.get("phase") in {"previewing", "applying"}:
                        self._appearance_jobs[request_id] = {
                            "request_id": request_id, "phase": "completion_unknown",
                            "category": "completion_unknown",
                            "message": "appearance completion is unknown; do not retry the mutation",
                        }
                        self._appearance_job_deadlines.pop(request_id, None)
                        self._appearance_job_finished[request_id] = now
                job = self._appearance_jobs.get(request_id)
                return {"ok": True, "found": job is not None, **(dict(job) if job else {})}
        if command == "system-theme-status":
            return {"ok": True, **self._system_theme_wire(self.system_theme_controller.open())}
        if command == "system-theme-preview":
            self.system_theme_controller.preview(str(request.get("mode", "")))
            return {"ok": True, **self._system_theme_wire(self.system_theme_controller.state)}
        if command == "system-theme-apply":
            self.system_theme_controller.apply(str(request.get("preview_id", "")))
            state = self.system_theme_controller.state
            return {"ok": state.phase != "error", **self._system_theme_wire(state)}
        if command == "system-theme-rollback":
            self.system_theme_controller.rollback()
            state = self.system_theme_controller.state
            return {"ok": state.phase != "error", **self._system_theme_wire(state)}
        if command in {"bindings-snapshot", "bindings-apply"}:
            if getattr(self, "_settings_fixture_host", None) is None:
                return {"ok": False, "error": "settings service starting"}
            return self._settings_fixture_host.binding_snapshot() if command == "bindings-snapshot" else self._settings_fixture_host.apply_bindings(request)
        if command == "settings-confirm" and getattr(self, "_settings_fixture_host", None):
            return self._settings_fixture_host.decide(request.get("request_id"), request.get("keep"))
        if command == "settings-apply" and getattr(self, "_settings_fixture_host", None):
            if not isinstance(request.get("changes"), dict) or not isinstance(request.get("request_id"), str):
                return {"ok": False, "error": "invalid settings request"}
            try:
                SettingsApplyRequest.build(request["request_id"], request.get("expected_digest", ""), request["changes"])
            except ValueError as error:
                return {"ok": False, "error": str(error)}
            return self._settings_fixture_host.apply(request)
        if command == "settings-apply":
            return {"ok": False, "error": "settings service starting"}
        if command in {"settings-status", "settings-recover"}:
            return self._settings_status(str(request.get("request_id", "")), recover=command == "settings-recover")
        if command == "status":
            weather_state = "missing"
            if self.weather_provider:
                weather_state = "error" if self.weather_provider.error else "ready" if self.weather_provider.snapshot else "missing"
            return {
                "ok": True,
                "pid": os.getpid(),
                "osd_projection": {name: {
                    "state": getattr(layer, "_projection_status", "unsubmitted"),
                    "generation": getattr(layer, "_projection_generation", ""),
                    "projected": layer._compositor_projected,
                    "shown": layer._shown,
                    "presented_ms": round(getattr(layer, "_projection_presented_ms", 0), 2),
                    "show_to_presented_ms": round(getattr(layer, "_osd_latency_ms", 0), 2),
                    "waiting": getattr(layer, "_osd_waiting", False),
                    "geometry_revision": layer.stage._revision,
                    "pending_frame": layer._projection_handshake.pending,
                } for name, layer in getattr(self, "osd_layers", {}).items()},
                "surfaces": sorted(self.surfaces),
                "expanded": [name for name, surface in self.surfaces.items() if surface.expanded],
                "overview_reveal": sorted(getattr(self, "_overview_reveal_names", set())),
                "overview_phase": getattr(self, "_overview_phase", "hidden"),
                "surface_bounds": {name: surface.global_bounds() for name, surface in self.surfaces.items()},
                "glow": self._glow_status(),
                "config": str(self.config.path),
                "launcher_environment_clean": LAYER_SHELL not in preload_entries(os.environ.get("LD_PRELOAD")),
                "palette_source": self.config.theme.palette_source,
                "palette_outputs": sorted(self.current_palette_by_connector),
                "palette_captured_at": self.palette_captured_at,
                "palette_extraction_state": self.palette_controller.state.phase,
                "palette_extraction_provider": self.palette_controller.state.provider,
                "palette_backend": "matugen",
                "palette_generation_id": next(
                    (scheme.generation_id for scheme in self.current_scheme_by_connector.values()),
                    "",
                ),
                "palette_error_category": self.palette_controller.state.error_category,
                "app_icon_theme": self.config.theme.app_icon_theme,
                "app_icon_theme_available": bool(self.app_icon_provider and self.app_icon_provider.available),
                "app_icon_cache_entries": self.app_icon_provider.cache_entries if self.app_icon_provider else 0,
                "app_icon_arcticons_count": self.app_icon_provider.arcticons_count if self.app_icon_provider else 0,
                "app_icon_overlay_count": self.app_icon_provider.overlay_count if self.app_icon_provider else 0,
                "app_icon_fallback_count": self.app_icon_provider.fallback_count if self.app_icon_provider else 0,
                "icon_generation_jobs": len(
                    self.system_view.settings_view.icon_generation_busy
                    if self.system_view else ()
                ),
                "settings_state": self.settings_controller.state.phase,
                "settings_dirty": sorted(self.settings_controller.state.dirty_paths),
                "settings_error_category": self.settings_controller.state.error_category,
                "system_page": (
                    self.system_view.expanded.get_visible_child_name()
                    if self.system_view else ""
                ),
                "system_settings_mode": bool(self.system_view and self.system_view.settings_mode),
                "system_keyboard_mode": (
                    self.surfaces["system"].keyboard_mode_name()
                    if "system" in self.surfaces else "none"
                ),
                "screen_color_pick_state": (
                    "success" if self._last_screen_color_pick and self._last_screen_color_pick.ok
                    else self._last_screen_color_pick.error_category if self._last_screen_color_pick
                    else "idle"
                ),
                "screen_color_pick_value": (
                    self._last_screen_color_pick.color if self._last_screen_color_pick else ""
                ),
                "system_theme_state": self.system_theme_controller.state.phase,
                "system_theme_mode": self.config.system_theme.mode,
                "system_theme_source": (
                    self.system_theme_controller.state.preview.semantic.source_connector
                    if self.system_theme_controller.state.preview else ""
                ),
                "system_theme_error_category": self.system_theme_controller.state.error_category,
                "system_theme_drifted": self.system_theme_controller.state.drifted,
                "system_theme_backup_available": self.system_theme_controller.state.backup_available,
                "system_theme_targets": ["gtk3", "gtk4", "qt5", "qt6", "kde", "kitty", "alacritty", "btop", "ghostty", "hyprland", "cursor"],
                "notification_daemon": bool(self.notification_manager and self.notification_manager.service),
                "notification_history": len(self.notification_manager.history) if self.notification_manager else 0,
                "minimized": len(self.minimize.records) if self.minimize else 0,
                "taskbar_groups": sorted(self.taskbar.visible_group_keys) if self.taskbar else [],
                "tray_items": len(self.tray_host.items) if self.tray_host else 0,
                "weather": weather_state,
                "weather_error": self.weather_provider.error if self.weather_provider else "provider unavailable",
                "spotify": {
                    "connected": self.spotify_provider.snapshot.connected,
                    "playback_status": self.spotify_provider.snapshot.playback_status,
                    "error": self.spotify_provider.snapshot.error,
                },
                "metrics": {
                    "cpu_temperature": self.latest_metrics.cpu_temperature if self.latest_metrics else None,
                    "gpu_temperature": self.latest_metrics.gpu_temperature if self.latest_metrics else None,
                    "coolant_temperature": self.latest_metrics.coolant_temperature if self.latest_metrics else None,
                },
            }
        if command == "glow-probe":
            action = str(request.get("action", ""))
            if action == "osd":
                connector = str(request.get("connector", ""))
                layer = self.osd_layers.get(connector)
                kind = request.get("kind", "spatial")
                if layer is None or kind not in {"spatial", "volume"}:
                    return {"ok": False, "error": "invalid diagnostic OSD target"}
                if kind == "spatial":
                    layer.show_spatial("OSD 진단")
                else:
                    layer.show("volume", 50)
                return {"ok": True, "diagnostic": True, "connector": connector}
            if action not in {"start", "reset", "stop"}:
                return {"ok": False, "error": "glow probe action must be start, reset, or stop"}
            enabled = action != "stop"
            for surface in self.surfaces.values():
                surface.glow.enable_frame_timing_probe(enabled)
            return {
                "ok": True,
                "action": action,
                "enabled": enabled,
                "osd_projection": {name: {
                    "state": getattr(layer, "_projection_status", "unsubmitted"),
                    "generation": getattr(layer, "_projection_generation", ""),
                    "projected": layer._compositor_projected,
                    "shown": layer._shown,
                    "presented_ms": round(getattr(layer, "_projection_presented_ms", 0), 2),
                    "show_to_presented_ms": round(getattr(layer, "_osd_latency_ms", 0), 2),
                    "waiting": getattr(layer, "_osd_waiting", False),
                    "geometry_revision": layer.stage._revision,
                    "pending_frame": layer._projection_handshake.pending,
                } for name, layer in getattr(self, "osd_layers", {}).items()},
                "surfaces": sorted(self.surfaces),
            }
        if command in {"toggle", "open"}:
            panel = str(request.get("panel"))
            if panel == "spatial-editor" and command == "toggle":
                return {"ok": self._toggle_spatial_editor()}
            if panel == "overview" and command == "toggle":
                self._toggle_overview()
                return {"ok": True}
            if panel == "power":
                self._open_notification_mode("power")
                return {"ok": True}
            surface = self.surfaces.get(panel)
            if not surface:
                return {"ok": False, "error": f"unknown panel: {panel}"}
            if panel == "launcher" and command == "toggle" and surface.expanded:
                surface.set_expanded(False)
            elif panel == "launcher":
                prefixes = {
                    "default": "", "clip": "/clip ", "file": "/file ", "web": "/web ",
                    "command": "/ ", "emoji": "/emoji ", "wallpaper": "/wallpaper",
                }
                self._open_launcher(prefixes.get(str(request.get("provider", "default")), ""))
            else:
                if panel == "system" and command == "open" and self.system_view:
                    if request.get("provider") == "palette":
                        self.system_view.show_palette_settings()
                    elif request.get("provider") == "settings":
                        self.system_view.show_settings()
                    elif request.get("provider") == "system-theme":
                        self.system_view.show_system_theme()
                    elif request.get("provider") == "controls":
                        self.system_view.show_controls()
                surface.toggle() if command == "toggle" else surface.set_expanded(True)
            return {"ok": True}

        if command == "reload":
            self._reload_config()
            return {"ok": True}
        if command == "refresh" and request.get("provider") == "weather":
            self._refresh_weather()
            return {"ok": True}
        if command == "hardware":
            return self._start_hardware_action(str(request.get("action", "")))
        return {"ok": False, "error": f"unknown command: {command}"}

    def _glow_status(self) -> dict[str, object]:
        surfaces: dict[str, object] = {}
        for name, surface in sorted(self.surfaces.items()):
            glow = surface.glow
            timing = glow.frame_timing_snapshot()
            surfaces[name] = {
                "effective_renderer": glow.effective_renderer,
                "renderer_error": glow.renderer_error or "",
                "timing": {
                    "enabled": glow.frame_timing_enabled,
                    "sample_count": timing.sample_count,
                    "refresh_interval_us": timing.refresh_interval_us,
                    "p50_us": timing.p50_us,
                    "p95_us": timing.p95_us,
                    "p99_us": timing.p99_us,
                    "missed_frame_ratio": timing.missed_frame_ratio,
                    "max_interval_us": timing.max_interval_us,
                    "severe_stall_count": timing.severe_stall_count,
                    "max_consecutive_missed": timing.max_consecutive_missed,
                    "presentation_sample_count": timing.presentation_sample_count,
                    "presentation_p95_us": timing.presentation_p95_us,
                    "presentation_max_us": timing.presentation_max_us,
                    "event_counts": timing.event_counts or {},
                    "event_duration_us": timing.event_duration_us or {},
                    "recent_stalls": list(timing.recent_stalls),
                },
            }
        return {
            "requested_renderer": self.config.theme.glow_renderer,
            "process_gsk_renderer": os.environ.get("GSK_RENDERER", ""),
            "layer": (
                self.glow_layer.status().__dict__
                if getattr(self, "glow_layer", None)
                else {"running": False, "pid": 0, "rect_count": 0, "error": ""}
            ),
            "surfaces": surfaces,
        }

    def _focused_connector(self) -> str:
        focused = next((monitor.name for monitor in self.hypr_monitors if monitor.focused), "")
        return focused or (self.hypr_monitors[0].name if self.hypr_monitors else "")

    def _show_osd(self, connector: str, kind: str, value: int, muted: bool = False) -> bool:
        osd = self.osd_layers.get(connector) or next(iter(self.osd_layers.values()), None)
        if osd:
            osd.show(kind, value, muted=muted, connector=connector if kind == "brightness" else "")
        return False

    def _hardware_volume_applied(self, connector: str, value: int, muted: bool) -> bool:
        if self.notification_view:
            self.notification_view.hardware.update_volume(value, muted)
        self._show_osd(connector, "volume", value, muted)
        return False

    def _hardware_brightness_applied(self, connector: str, value: int) -> bool:
        self._brightness_values[connector] = value
        if self.notification_view:
            self.notification_view.hardware.update_brightness(connector, value)
        self._show_osd(connector, "brightness", value, False)
        return False

    def _hardware_brightness_previewed(self, connector: str, value: int) -> bool:
        if self.notification_view:
            self.notification_view.hardware.preview_brightness(connector, value)
        self._show_osd(connector, "brightness", value, False)
        return False

    def _refresh_hardware_quick_controls(self, scope: str = "all") -> None:
        if scope not in {"all", "audio", "brightness"}:
            raise ValueError(f"unsupported hardware refresh scope: {scope}")
        if scope in {"all", "audio"}:
            self._refresh_audio_quick_controls()
        if scope in {"all", "brightness"}:
            self._refresh_brightness_quick_controls()

    def _refresh_audio_quick_controls(self) -> None:
        lock = self._hardware_refresh_locks["audio"]
        if not lock.acquire(blocking=False):
            return

        def worker() -> None:
            try:
                state = read_audio_quick_state(self.audio_controller)
            except Exception as exc:
                LOG.error("audio quick-state refresh failed: %s", exc)
                return
            finally:
                lock.release()

            def update() -> bool:
                if self.notification_view:
                    self.notification_view.update_audio_hardware(state)
                return False

            GLib.idle_add(update)

        threading.Thread(target=worker, name="luminophore-audio-state", daemon=True).start()

    def _refresh_brightness_quick_controls(self) -> None:
        lock = self._hardware_refresh_locks["brightness"]
        if not lock.acquire(blocking=False):
            return
        expected_revision = (
            self.notification_view.hardware.brightness_revision
            if self.notification_view
            else 0
        )

        def worker() -> None:
            try:
                targets = self._ddc_targets
                if not targets:
                    targets = discover_ddc_targets()
                    self._ddc_targets = targets
                state = read_brightness_quick_state(self.brightness_controller, targets)
            except Exception as exc:
                LOG.error("brightness quick-state refresh failed: %s", exc)
                return
            finally:
                lock.release()

            def update() -> bool:
                accepted = True
                if self.notification_view:
                    accepted = self.notification_view.update_brightness_hardware(state, expected_revision)
                if accepted:
                    for monitor in state.monitors:
                        if monitor.percent is not None:
                            self._brightness_values[monitor.connector] = monitor.percent
                        if monitor.powered is not None:
                            self._monitor_powered[monitor.connector] = monitor.powered
                return False

            GLib.idle_add(update)

        threading.Thread(target=worker, name="luminophore-brightness-state", daemon=True).start()

    def _set_quick_volume(self, percent: int) -> None:
        connector = self._focused_connector()

        def worker() -> None:
            try:
                with self._hardware_locks["audio"]:
                    self.audio_controller.set_output_percent(percent)
                    value, muted = self.audio_controller.output_state()
                GLib.idle_add(self._hardware_volume_applied, connector, value, muted)
            except (HardwareControlError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
                LOG.error("volume slider failed: value=%s error=%s", percent, exc)
                self._refresh_hardware_quick_controls("audio")

        threading.Thread(target=worker, name="luminophore-volume-slider", daemon=True).start()

    def _toggle_quick_volume_mute(self) -> None:
        connector = self._focused_connector()

        def worker() -> None:
            try:
                with self._hardware_locks["audio"]:
                    self.audio_controller.toggle_output_mute()
                    value, muted = self.audio_controller.output_state()
                GLib.idle_add(self._hardware_volume_applied, connector, value, muted)
            except (HardwareControlError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
                LOG.error("volume mute control failed: %s", exc)
                self._refresh_hardware_quick_controls("audio")

        threading.Thread(target=worker, name="luminophore-volume-mute", daemon=True).start()

    def _set_quick_application_volume(self, node_ids: tuple[int, ...], percent: int) -> None:
        def worker() -> None:
            try:
                with self._hardware_locks["audio"]:
                    self.audio_controller.set_application_percent(node_ids, percent)
            except (HardwareControlError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
                LOG.error("application volume slider failed: nodes=%s value=%s error=%s", node_ids, percent, exc)
            self._refresh_hardware_quick_controls("audio")

        threading.Thread(target=worker, name="luminophore-application-volume", daemon=True).start()

    def _set_quick_application_muted(self, node_ids: tuple[int, ...], muted: bool) -> None:
        def worker() -> None:
            try:
                with self._hardware_locks["audio"]:
                    self.audio_controller.set_application_muted(node_ids, muted)
            except (HardwareControlError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
                LOG.error("application mute control failed: nodes=%s muted=%s error=%s", node_ids, muted, exc)
            self._refresh_hardware_quick_controls("audio")

        threading.Thread(target=worker, name="luminophore-application-mute", daemon=True).start()

    def _set_quick_brightness(self, connector: str, percent: int) -> None:
        target = self._ddc_targets.get(connector)
        if not target:
            LOG.error("brightness slider target unavailable: connector=%s", connector)
            self._refresh_hardware_quick_controls("brightness")
            return
        self.brightness_queue.submit_percent(
            target,
            percent,
            lambda value: GLib.idle_add(self._hardware_brightness_applied, connector, value),
            lambda exc: self._quick_brightness_failed(connector, percent, exc),
        )

    def _set_monitor_power(self, connectors: tuple[str, ...], powered: bool) -> None:
        for connector in connectors:
            target = self._ddc_targets.get(connector)
            if not target:
                LOG.error("monitor power target unavailable: connector=%s", connector)
                self._refresh_hardware_quick_controls("brightness")
                continue
            self.brightness_queue.submit_power(
                target,
                powered,
                lambda value, name=connector: GLib.idle_add(self._monitor_power_applied, name, bool(value)),
                lambda exc, name=connector: self._monitor_power_failed(name, exc),
            )

    def _monitor_power_applied(self, connector: str, powered: bool) -> bool:
        self._monitor_powered[connector] = powered
        if self.notification_view:
            self.notification_view.hardware.update_monitor_power(connector, powered)
        if powered:
            self._refresh_hardware_quick_controls("brightness")
        return False

    def _monitor_power_failed(self, connector: str, exc: Exception) -> None:
        LOG.error("monitor power control failed: connector=%s error=%s", connector, exc)
        self._ddc_targets = {}
        self._refresh_hardware_quick_controls("brightness")

    def _wake_all_monitors_if_dark(self) -> None:
        targets = tuple(sorted(
            connector
            for connector in self._ddc_targets
            if self._monitor_powered.get(connector) is False
        ))
        if targets and len(targets) == len(self._ddc_targets):
            self._set_monitor_power(targets, True)

    def _quick_brightness_failed(self, connector: str, percent: int, exc: Exception) -> None:
        LOG.error("brightness slider failed: connector=%s value=%s error=%s", connector, percent, exc)
        self._ddc_targets = {}
        GLib.idle_add(self._quick_brightness_rejected, connector)

    def _quick_brightness_rejected(self, connector: str) -> bool:
        if self.notification_view:
            self.notification_view.hardware.reject_brightness(connector)
        self._refresh_hardware_quick_controls("brightness")
        return False

    def _start_hardware_action(self, action: str) -> dict[str, object]:
        audio_actions = {"volume-up", "volume-down", "volume-mute", "mic-mute"}
        media_actions = {"media-toggle": "play-pause", "media-next": "next", "media-previous": "previous"}
        brightness_actions = {"brightness-up": 5, "brightness-down": -5}
        brightness_previews = {"brightness-preview-up": 5, "brightness-preview-down": -5}
        connector = self._focused_connector()
        if action in brightness_previews:
            return self._preview_keyboard_brightness(connector, brightness_previews[action])
        if action == "brightness-commit":
            return self._commit_keyboard_brightness()
        if action in brightness_actions:
            self._wake_all_monitors_if_dark()
            target = self._ddc_targets.get(connector)
            if not target:
                targets = discover_ddc_targets()
                self._ddc_targets = targets
                target = targets.get(connector)
            if not target:
                return {"ok": False, "error": f"no DDC/CI target for focused connector: {connector or 'unknown'}"}
            self.brightness_queue.submit_delta(
                target,
                brightness_actions[action],
                lambda value: GLib.idle_add(self._hardware_brightness_applied, connector, value),
                lambda exc: LOG.error("brightness action failed: connector=%s error=%s", connector, exc),
            )
            return {"ok": True, "accepted": True, "connector": connector, "bus": target.bus}
        if action in audio_actions:
            def audio_worker() -> None:
                try:
                    with self._hardware_locks["audio"]:
                        if action == "volume-up":
                            self.audio_controller.volume(5)
                        elif action == "volume-down":
                            self.audio_controller.volume(-5)
                        elif action == "volume-mute":
                            self.audio_controller.toggle_output_mute()
                        else:
                            self.audio_controller.toggle_input_mute()
                        if action == "mic-mute":
                            value, muted = self.audio_controller.input_state()
                            kind = "microphone"
                        else:
                            value, muted = self.audio_controller.output_state()
                            kind = "volume"
                    if kind == "volume":
                        GLib.idle_add(self._hardware_volume_applied, connector, value, muted)
                    else:
                        GLib.idle_add(self._show_osd, connector, kind, value, muted)
                except (HardwareControlError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
                    LOG.error("audio action failed: action=%s error=%s", action, exc)

            threading.Thread(target=audio_worker, name="luminophore-audio", daemon=True).start()
            return {"ok": True, "accepted": True}
        if action in media_actions:
            def media_worker() -> None:
                try:
                    with self._hardware_locks["media"]:
                        self.media_controller.run(media_actions[action])
                except (HardwareControlError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
                    LOG.error("media action failed: action=%s error=%s", action, exc)

            threading.Thread(target=media_worker, name="luminophore-media", daemon=True).start()
            return {"ok": True, "accepted": True}
        return {"ok": False, "error": f"unsupported hardware action: {action}"}

    def _preview_keyboard_brightness(self, connector: str, delta: int) -> dict[str, object]:
        self._wake_all_monitors_if_dark()
        with self._brightness_key_lock:
            current = self._brightness_key_intents.get(connector, self._brightness_values.get(connector))
            if current is None:
                self._refresh_hardware_quick_controls("brightness")
                return {"ok": False, "error": f"brightness state unavailable: {connector or 'unknown'}"}
            desired = max(0, min(100, current + delta))
            self._brightness_key_intents[connector] = desired
        GLib.idle_add(self._hardware_brightness_previewed, connector, desired)
        return {"ok": True, "accepted": True, "preview": True, "connector": connector, "value": desired}

    def _commit_keyboard_brightness(self) -> dict[str, object]:
        with self._brightness_key_lock:
            intents = self._brightness_key_intents
            self._brightness_key_intents = {}
        if not intents:
            return {"ok": True, "accepted": False, "reason": "no-pending-brightness-intent"}
        accepted: list[str] = []
        missing: list[str] = []
        for connector, value in intents.items():
            target = self._ddc_targets.get(connector)
            if not target:
                missing.append(connector)
                GLib.idle_add(self._quick_brightness_rejected, connector)
                continue
            accepted.append(connector)
            self.brightness_queue.submit_percent(
                target,
                value,
                lambda applied, name=connector: GLib.idle_add(self._hardware_brightness_applied, name, applied),
                lambda exc, name=connector, desired=value: self._quick_brightness_failed(name, desired, exc),
            )
        return {
            "ok": not missing,
            "accepted": bool(accepted),
            "connectors": accepted,
            "missing": missing,
        }

    def _refresh_system_controls(self) -> None:
        def worker() -> None:
            snapshot = self.system_controls.snapshot()

            def update() -> bool:
                self.system_control_snapshot = snapshot
                if self.system_view:
                    self.system_view.update_controls(snapshot)
                return False

            GLib.idle_add(update)

        threading.Thread(target=worker, name="luminophore-system-controls", daemon=True).start()

    def _pair_bluetooth(self, device: BluetoothDevice) -> None:
        def worker() -> None:
            try:
                self.bluez_agent.pair(device.address)
            except (SystemControlError, Exception) as exc:
                LOG.error("Bluetooth pairing failed: address=%s error=%s", device.address, exc)
                self._refresh_system_controls()

        threading.Thread(target=worker, name="luminophore-bluetooth-pair", daemon=True).start()

    def _pairing_prompted(self, prompt: PairingPrompt) -> None:
        def show() -> bool:
            if self.system_view:
                self.system_view.show_pairing_prompt(prompt, self._resolve_pairing_prompt)
            return False

        GLib.idle_add(show)

    def _resolve_pairing_prompt(self, request_id: int, decision: PairingDecision) -> None:
        try:
            self.bluez_agent.core.resolve(request_id, decision)
        except SystemControlError as exc:
            LOG.error("Bluetooth pairing prompt expired: %s", exc)

    def _change_system_control(self, action: str, value: object, extra: object = None) -> None:
        def worker() -> None:
            try:
                if action == "wifi":
                    self.system_controls.set_wifi(bool(value))
                elif action == "wifi-connect" and isinstance(value, WifiNetwork) and isinstance(extra, str):
                    self.system_controls.connect_wifi(value, extra)
                elif action == "bluetooth":
                    self.system_controls.set_bluetooth(bool(value))
                elif action == "bluetooth-connect" and isinstance(value, BluetoothDevice):
                    self.system_controls.connect_bluetooth(value)
                elif action == "power-profile" and isinstance(value, str) and isinstance(extra, tuple):
                    self.system_controls.set_power_profile(value, extra)
                else:
                    raise SystemControlError("invalid_system_control_action")
            except (SystemControlError, OSError, subprocess.TimeoutExpired) as exc:
                LOG.error("system control failed: action=%s error=%s", action, exc)
            finally:
                self._refresh_system_controls()

        threading.Thread(target=worker, name=f"luminophore-control-{action}", daemon=True).start()

    def _resolve_location_for_ui(self) -> None:
        if not self.location_provider:
            return

        def worker() -> None:
            snapshot = self.location_provider.resolve()
            error = self.location_provider.last_error
            GLib.idle_add(self._update_location_ui, snapshot, error)

        threading.Thread(target=worker, name="luminophore-location-resolve", daemon=True).start()

    def _update_location_ui(self, snapshot, error: str = "") -> bool:
        if self.system_view:
            self.system_view.update_location(snapshot, error)
        return False

    def _search_city(self, query: str) -> None:
        if not self.location_provider:
            return

        def worker() -> None:
            try:
                results = self.location_provider.search_city(query)
                error = ""
            except Exception as exc:
                results = ()
                error = str(exc)
            GLib.idle_add(self._update_city_results_ui, results, error)

        threading.Thread(target=worker, name="luminophore-city-search", daemon=True).start()

    def _update_city_results_ui(self, results: tuple[CityResult, ...], error: str = "") -> bool:
        if self.system_view:
            self.system_view.update_city_results(results, error)
        return False

    def _select_city(self, city: CityResult) -> None:
        old_config = self.config
        expected_digest = config_digest(old_config.path)

        def worker() -> None:
            try:
                new_config = write_config_patch(old_config.path, city_config_changes(city), expected_digest)
            except ConfigError as exc:
                GLib.idle_add(self._update_city_results_ui, (), str(exc))
                return

            def adopt() -> bool:
                self._adopt_config(old_config, new_config)
                self._resolve_location_for_ui()
                if self.weather_provider:
                    self.weather_provider.refresh(manual=True)
                return False

            GLib.idle_add(adopt)

        threading.Thread(target=worker, name="luminophore-city-select", daemon=True).start()

    def _connect_calendar(self) -> None:
        path = oauth_client_path()
        if not path.is_file():
            self._choose_google_oauth_client()
            return
        self._start_calendar_oauth()

    def _choose_google_oauth_client(self) -> None:
        self._update_calendar_ui(None, "Google OAuth JSON 파일을 선택해 주세요.")
        dialog = Gtk.FileChooserNative(
            title="Google OAuth JSON 선택",
            transient_for=self.get_active_window(),
            action=Gtk.FileChooserAction.OPEN,
            accept_label="선택하고 연결",
            cancel_label="취소",
        )
        json_filter = Gtk.FileFilter()
        json_filter.set_name("Google OAuth JSON")
        json_filter.add_mime_type("application/json")
        json_filter.add_pattern("*.json")
        dialog.add_filter(json_filter)

        def response(chooser: Gtk.FileChooserNative, response_id: int) -> None:
            selected = chooser.get_file() if response_id == Gtk.ResponseType.ACCEPT else None
            chooser.destroy()
            selected_path = selected.get_path() if selected else None
            if not selected_path:
                self._update_calendar_ui(None, "Google OAuth JSON 선택이 취소되었습니다.")
                return

            def install_worker() -> None:
                try:
                    install_oauth_client(Path(selected_path))
                except CalendarError as exc:
                    GLib.idle_add(self._update_calendar_ui, None, calendar_error_message(str(exc)))
                    return
                GLib.idle_add(self._start_calendar_oauth)

            self._update_calendar_ui(None, "OAuth 설정을 설치하는 중…")
            threading.Thread(target=install_worker, name="luminophore-google-oauth-install", daemon=True).start()

        dialog.connect("response", response)
        dialog.show()

    def _start_calendar_oauth(self) -> bool:
        self._update_calendar_ui(None, "Google 로그인 브라우저를 여는 중…")

        def worker() -> None:
            try:
                client = load_oauth_client(oauth_client_path())
                token = GoogleOAuthFlow(client).run()
                provider = GoogleCalendarProvider(client.client_id, OfflinePolicy(self.config.network.offline))
                provider.save_authorization(self.calendar_account, token)
                snapshot = provider.sync(self.calendar_account)
                self.calendar_provider = provider
                GLib.idle_add(self._update_calendar_ui, snapshot, "")
            except Exception as exc:
                GLib.idle_add(self._update_calendar_ui, None, calendar_error_message(str(exc)))

        threading.Thread(target=worker, name="luminophore-google-oauth", daemon=True).start()
        return False

    def _refresh_calendar(self) -> None:
        def worker() -> None:
            try:
                try:
                    client_id = load_oauth_client(oauth_client_path()).client_id
                except CalendarError:
                    client_id = ""
                provider = GoogleCalendarProvider(client_id, OfflinePolicy(self.config.network.offline))
                snapshot = provider.sync(self.calendar_account)
                self.calendar_provider = provider
                GLib.idle_add(self._update_calendar_ui, snapshot, "")
            except Exception as exc:
                GLib.idle_add(self._update_calendar_ui, None, str(exc))

        threading.Thread(target=worker, name="luminophore-calendar-sync", daemon=True).start()

    def _disconnect_calendar(self) -> None:
        def worker() -> None:
            try:
                (self.calendar_provider or GoogleCalendarProvider("")).disconnect(self.calendar_account)
                self.calendar_provider = None
                GLib.idle_add(self._update_calendar_ui, None, "연결되지 않음")
            except CalendarError as exc:
                GLib.idle_add(self._update_calendar_ui, None, str(exc))

        threading.Thread(target=worker, name="luminophore-calendar-disconnect", daemon=True).start()

    def _update_calendar_ui(self, snapshot: CalendarSnapshot | None, error: str = "") -> bool:
        if self.system_view:
            self.system_view.update_calendar(snapshot, error)
        return False

    def _privacy_changed(self, snapshot: PrivacySnapshot) -> None:
        GLib.idle_add(self._update_privacy_ui, snapshot)

    def _update_privacy_ui(self, snapshot: PrivacySnapshot) -> bool:
        if self.system_view:
            self.system_view.update_privacy(snapshot)
        newly_active = snapshot.active_kinds - self._privacy_active
        self._privacy_active = snapshot.active_kinds
        if newly_active:
            names = {"camera": "카메라 사용 중", "microphone": "마이크 사용 중", "screen": "화면 공유 중"}
            label = " · ".join(names[kind] for kind in sorted(newly_active))
            connector = self._focused_connector()
            osd = self.osd_layers.get(connector) or next(iter(self.osd_layers.values()), None)
            if osd:
                osd.show("privacy", 100, text=label)
        return False

    def _reload_config(self) -> None:
        # The settings host adopts candidates and confirms their generation.
        # Theme assets can change without changing settings values.
        self._apply_css()

    def _when_surfaces_collapsed(self, callback: Callable[[], None]) -> None:
        pending = {
            name: surface
            for name, surface in self.surfaces.items()
            if not surface.is_fully_collapsed()
        }
        if not pending:
            GLib.idle_add(self._run_callback, callback)
            return

        remaining = set(pending)

        def completed(name: str) -> None:
            remaining.discard(name)
            if not remaining:
                callback()

        for name, surface in pending.items():
            surface.when_transition_complete(False, lambda current=name: completed(current))
            surface.set_expanded(False)

    @staticmethod
    def _run_callback(callback: Callable[[], None]) -> bool:
        callback()
        return False

    def _finish_external_config_reload(self, old_config: ShellConfig, new_config: ShellConfig) -> bool:
        self._adopt_config(old_config, new_config)
        return False

    @staticmethod
    def _surface_layout_changed(old_config: ShellConfig, new_config: ShellConfig) -> bool:
        fields = (
            "edge_margin",
            "panel_height",
            "expansion_ms",
            "weather_width",
            "weather_height",
            "system_width",
            "system_height",
        )
        return any(getattr(old_config.layout, field) != getattr(new_config.layout, field) for field in fields)

    def _settings_status(self, request_id: str = "", *, recover: bool = False) -> dict[str, Any]:
        if getattr(self, "_settings_fixture_host", None):
            return self._settings_fixture_host.status(request_id, recover)
        return {"ok": False, "error": "settings service starting"}

    def _resync_visual_settings(self) -> bool:
        # The native coordinator restores the complete confirmed generation.
        return False

    def _visual_renderer_available(self) -> bool:
        try:
            state = self.hyprland.visual_state()
            return state.renderer_schema == 1 and bool(state.source)
        except HyprlandError:
            return False

    def _verify_visual_config(self, config: ShellConfig) -> None:
        try:
            state = self.hyprland.visual_state()
        except HyprlandError as error:
            raise SettingsCompletionUnknown("visual settings readback unavailable") from error
        if state.renderer_schema != 1 or not state.configured or state.recovery != "none" or state.settings != config.visual:
            raise SettingsCompletionUnknown("visual settings readback does not match saved settings")

    def _adopt_config(self, old_config: ShellConfig, new_config: ShellConfig) -> None:
        if (
            old_config.appearance.wallpaper_provider != new_config.appearance.wallpaper_provider
            and not self.palette_controller.set_provider(new_config.appearance.wallpaper_provider)
        ):
            raise ConfigError("wallpaper provider cannot change during a palette transaction")
        self.config = new_config
        if old_config.appearance.wallpaper_provider != new_config.appearance.wallpaper_provider:
            selected_provider = WallpaperProviderName(new_config.appearance.wallpaper_provider)
            self.appearance_service = AppearanceService(
                {selected_provider: provider_for(selected_provider)},
                self.appearance_compiler,
                FileAppearanceStateStore(new_config.path, self._appearance_runtime_reload),
                lambda: tuple(sorted(monitor.name for monitor in self._palette_monitors() if monitor.name)),
                lambda: config_digest(self.config.path),
                lambda: (),
            )
        self.config_mtime = config_mtime_ns(self.config.path)
        if self.minimize:
            self.minimize.layout = new_config.layout
        if self.clipboard:
            self.clipboard.limit = new_config.launcher.clipboard_limit
            self.clipboard.retention_hours = new_config.launcher.clipboard_retention_hours
        if self.catalog:
            self.catalog.set_preferred_actions(new_config.launcher.preferred_actions)
        if self.app_icon_provider:
            self.app_icon_provider.reconfigure(
                new_config.theme.app_icon_theme,
                new_config.theme.app_icon_aliases,
            )
        if self.launcher_panel:
            self.launcher_panel.config = new_config.launcher
            self.launcher_panel.refresh()
        if self.taskbar:
            self.taskbar.config = new_config.taskbar
            self.taskbar.update(self.windows, self.hypr_monitors)
        if self.weather_provider:
            self.weather_provider.config = new_config.weather
            self.weather_provider.policy = OfflinePolicy(new_config.network.offline)
        if self.location_provider:
            self.location_provider.config = new_config.location
            self.location_provider.policy = OfflinePolicy(new_config.network.offline)
        if self.metrics_provider:
            self.metrics_provider.reconfigure(new_config.metrics)
        if self.system_view:
            count = max(2, round(new_config.metrics.graph_seconds / new_config.metrics.sample_seconds))
            self.system_view.set_history_capacity(count, new_config.metrics.graph_seconds)
        if self.notification_manager:
            self.notification_manager.config = new_config.notifications
        for toast in self.toast_layers.values():
            toast.config = new_config.notifications
        if self.notification_view:
            self.notification_view.update()
        self._apply_css()
        if self._surface_layout_changed(old_config, new_config):
            display = Gdk.Display.get_default()
            if display:
                self._build_surfaces(display)

    def _verify_joint_shell_settings(self, candidate: ShellConfig) -> None:
        if settings_values(self.config) != settings_values(candidate):
            raise RuntimeError("Shell settings readback mismatch")
        # Read the actual reconstructed surface, not just the model assignment.
        for name in ("launcher", "notifications", "spotify"):
            surface = self.surfaces.get(name)
            if surface is None or surface.placement.height != candidate.layout.panel_height:
                raise RuntimeError("Shell panel consumer readback mismatch: " + name)

    def _watch_config(self) -> bool:
        if getattr(self, "_joint_settings_enabled", False): return True
        current = config_mtime_ns(self.config.path)
        if current and current != self.config_mtime:
            try:
                self._reload_config()
            except (ConfigError, SettingsCompletionUnknown) as exc:
                LOG.error("configuration reload rejected: %s", exc)
                self.config_mtime = current
        return True

    def do_shutdown(self) -> None:
        if getattr(self, "_settings_fixture_host", None): self._settings_fixture_host.close()
        self._window_refresh_closed = True
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = 0
        if reader := self._window_reader:
            reader.close()
        if picker := getattr(self, "background_picker", None): picker.destroy()
        if controller := getattr(self, "background_controller", None): controller.close()
        self._visibility_sequence = getattr(self, "_visibility_sequence", 0) + 1
        self._visibility_pending = False
        if reader := getattr(self, "_visibility_reader", None):
            reader.close()
        self.hyprland.close_projections()
        LOG.info("shutdown cleanup starting")
        if self._spatial_feedback_timer:
            GLib.source_remove(self._spatial_feedback_timer)
            self._spatial_feedback_timer = 0
        self._spatial_feedback.drain()
        if self.spatial_editor_surface:
            self.spatial_editor_surface.close()
        if self._hypr_events:
            self._hypr_events.stop()
        if self.clipboard:
            self.clipboard.stop()
        if self.metrics_provider:
            self.metrics_provider.stop()
        self.privacy_provider.stop()
        self.spotify_provider.stop()
        if self._glow_layer_source:
            GLib.source_remove(self._glow_layer_source)
            self._glow_layer_source = 0
        if self.glow_layer:
            self.glow_layer.stop()
        if self.toast_layers:
            # GtkApplication owns its application windows during shutdown.
            # Only detach our GLib sources here; explicitly destroying the
            # layer-shell window races GTK's own teardown and can segfault.
            for toast in self.toast_layers.values():
                toast.stop()
        self.bluez_agent.stop()
        for osd in self.osd_layers.values():
            osd.stop()
        if self._ipc:
            self._ipc.stop()
        self.palette_controller.shutdown()
        self.appearance_compiler.shutdown()
        self.screen_color_picker.shutdown()
        self.system_theme_controller.shutdown()
        if self.app_icon_provider:
            self.app_icon_provider.dispose()
        LOG.info("shutdown cleanup completed")
        Gio.Application.do_shutdown(self)


def run() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s luminophore-shell: %(message)s")
    config = boot_config()
    app = LuminophoreShellApplication(config)

    def quit_from_signal() -> bool:
        app.quit()
        return GLib.SOURCE_REMOVE

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, quit_from_signal)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, quit_from_signal)
    try:
        return int(app.run([]))
    except IpcError as exc:
        LOG.error("%s", exc)
        return 1
