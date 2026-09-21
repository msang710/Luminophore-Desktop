from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
import hashlib
import json
import logging
import os
import re
import tomllib
import tempfile
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .visual_settings import VisualSettings, resolve_visual_config
from .owned_settings import DEFAULTS, validate as validate_owned_settings
from .input_settings import decode_devices, validate_input, validate_absolute_inputs, validate_acceleration


PALETTE_SOURCES = {"hyprpaper", "awww", "native", "fixed"}
APP_ICON_THEMES = {"luminophore-shell-arcticons", "system"}
GLOW_RENDERERS = {"layer", "gsk"}
WALLPAPER_PROVIDERS = {"hyprpaper", "awww"}
DEFAULT_APP_ICON_ALIASES = (
    ("com.visualstudio.code.oss", "visual-studio-code"),
    ("org.pulseaudio.pavucontrol", "pavucontrol"),
)


class ConfigError(ValueError):
    pass


class ConfigConflictError(ConfigError):
    pass


class ConfigWriteError(ConfigError):
    pass


@dataclass(frozen=True)
class LayoutConfig:
    edge_margin: int = 24
    panel_height: int = 44
    radius: int = 12
    max_height_ratio: float = 0.60
    expansion_ms: int = 200
    weather_width: int = 560
    weather_height: int = 124
    system_width: int = 224
    system_height: int = 32


@dataclass(frozen=True)
class ThemeConfig:
    backdrop_opacity: float = 0.68
    outline_width: int = 1
    outline_glow_intensity: float = 0.42
    outline_glow_radius: int = 8
    glow_renderer: str = "gsk"
    animate_glow: bool = True
    audio_spectrum_enabled: bool = False
    palette_transition_ms: int = 600
    body_font: str = "Noto Sans CJK KR"
    numeric_font: str = "MesloLGS Nerd Font Mono"
    fallback_primary: str = "#78DCE8"
    fallback_secondary: str = "#AB9DF2"
    palette_source: str = "hyprpaper"
    fixed_primary: str = "#78DCE8"
    fixed_secondary: str = "#AB9DF2"
    fixed_monitor_palettes: tuple[tuple[str, str, str], ...] = ()
    app_icon_theme: str = "luminophore-shell-arcticons"
    app_icon_aliases: tuple[tuple[str, str], ...] = DEFAULT_APP_ICON_ALIASES
    ui_scale: float = 1.0
    high_contrast: bool = False


@dataclass(frozen=True)
class CompositorConfig:
    swallow_enabled: bool = DEFAULTS['compositor.swallow_enabled']
    swallow_regex: str = DEFAULTS['compositor.swallow_regex']
    swallow_exception_regex: str = DEFAULTS['compositor.swallow_exception_regex']
    screen_shader: str = DEFAULTS['compositor.screen_shader']
    motion_blur_enabled: bool = DEFAULTS['compositor.motion_blur_enabled']
    motion_blur_samples: int = DEFAULTS['compositor.motion_blur_samples']
    snap_enabled: bool = DEFAULTS['compositor.snap_enabled']
    snap_window_gap: int = DEFAULTS['compositor.snap_window_gap']
    snap_monitor_gap: int = DEFAULTS['compositor.snap_monitor_gap']
    snap_border_overlap: bool = DEFAULTS['compositor.snap_border_overlap']
    snap_respect_gaps: bool = DEFAULTS['compositor.snap_respect_gaps']
    color_management: bool = DEFAULTS["compositor.color_management"]
    lock_background: bool = DEFAULTS["compositor.lock_background"]
    lock_blur: bool = DEFAULTS["compositor.lock_blur"]
    cursor_start_output: str = DEFAULTS["compositor.cursor_start_output"]
    zoom_factor: float = DEFAULTS["compositor.zoom_factor"]
    locale: str = DEFAULTS["compositor.locale"]
    font_family: str = DEFAULTS["compositor.font_family"]
    wake_on_key: bool = DEFAULTS["compositor.wake_on_key"]
    wake_on_pointer: bool = DEFAULTS["compositor.wake_on_pointer"]
    display_idle_minutes: int = DEFAULTS["compositor.display_idle_minutes"]
    primary_selection: bool = DEFAULTS["compositor.primary_selection"]
    auto_hdr: int = DEFAULTS["compositor.auto_hdr"]
    sdr_transfer: str = DEFAULTS["compositor.sdr_transfer"]
    icc_vcgt: bool = DEFAULTS["compositor.icc_vcgt"]
    xwayland_native_pixels: bool = DEFAULTS["compositor.xwayland_native_pixels"]
    background_color: str = DEFAULTS["compositor.background_color"]
    shadow_enabled: bool = DEFAULTS["compositor.shadow_enabled"]
    shadow_range: int = DEFAULTS["compositor.shadow_range"]
    shadow_power: int = DEFAULTS["compositor.shadow_power"]
    shadow_sharp: bool = DEFAULTS["compositor.shadow_sharp"]
    shadow_color: str = DEFAULTS["compositor.shadow_color"]
    shadow_inactive_color: str = DEFAULTS["compositor.shadow_inactive_color"]
    shadow_scale: float = DEFAULTS["compositor.shadow_scale"]
    shadow_offset_x: float = DEFAULTS["compositor.shadow_offset_x"]
    shadow_offset_y: float = DEFAULTS["compositor.shadow_offset_y"]
    float_gap_top: int = DEFAULTS["compositor.float_gap_top"]
    float_gap_right: int = DEFAULTS["compositor.float_gap_right"]
    float_gap_bottom: int = DEFAULTS["compositor.float_gap_bottom"]
    float_gap_left: int = DEFAULTS["compositor.float_gap_left"]
    xwayland_nearest_neighbor: bool = DEFAULTS["compositor.xwayland_nearest_neighbor"]
    allow_tearing: bool = DEFAULTS["compositor.allow_tearing"]
    pointer_focus_output: bool = DEFAULTS["compositor.pointer_focus_output"]
    fullscreen_focus_policy: int = DEFAULTS["compositor.fullscreen_focus_policy"]
    fullscreen_after_close: bool = DEFAULTS["compositor.fullscreen_after_close"]
    zoom_rigid: bool = DEFAULTS["compositor.zoom_rigid"]
    zoom_detached_camera: bool = DEFAULTS["compositor.zoom_detached_camera"]
    zoom_disable_aa: bool = DEFAULTS["compositor.zoom_disable_aa"]
    fullscreen_opacity: float = DEFAULTS["compositor.fullscreen_opacity"]
    dim_inactive: bool = DEFAULTS["compositor.dim_inactive"]
    dim_modal: bool = DEFAULTS["compositor.dim_modal"]
    dim_strength: float = DEFAULTS["compositor.dim_strength"]
    dim_around: float = DEFAULTS["compositor.dim_around"]
    blur_popups: bool = DEFAULTS["compositor.blur_popups"]
    blur_input_methods: bool = DEFAULTS["compositor.blur_input_methods"]
    render_unfocused_fps: int = DEFAULTS["compositor.render_unfocused_fps"]
    default_view_columns: int = DEFAULTS["compositor.default_view_columns"]
    default_view_rows: int = DEFAULTS["compositor.default_view_rows"]
    gaps_in: int = DEFAULTS["compositor.gaps_in"]
    gaps_out: int = DEFAULTS["compositor.gaps_out"]
    border_size: int = DEFAULTS["compositor.border_size"]
    rounding: int = DEFAULTS["compositor.rounding"]
    active_opacity: float = DEFAULTS["compositor.active_opacity"]
    inactive_opacity: float = DEFAULTS["compositor.inactive_opacity"]
    dim_special: float = DEFAULTS["compositor.dim_special"]
    blur_enabled: bool = DEFAULTS["compositor.blur_enabled"]
    blur_size: int = DEFAULTS["compositor.blur_size"]
    blur_passes: int = DEFAULTS["compositor.blur_passes"]
    vrr: int = DEFAULTS["compositor.vrr"]


@dataclass(frozen=True)
class MotionConfig:
    enabled: bool = DEFAULTS["motion.enabled"]
    preset: str = DEFAULTS["motion.preset"]
    speed: float = DEFAULTS["motion.speed"]


@dataclass(frozen=True)
class AppearanceConfig:
    wallpaper_provider: str = "hyprpaper"


@dataclass(frozen=True)
class SystemThemeConfig:
    mode: str = "dark"


@dataclass(frozen=True)
class NetworkConfig:
    offline: bool = False


@dataclass(frozen=True)
class LocationConfig:
    automatic: bool = True
    latitude: float = 37.5665
    longitude: float = 126.9780
    timezone: str = "Asia/Seoul"
    geoclue_timeout_seconds: float = 5.0
    city_search_timeout_seconds: float = 5.0


@dataclass(frozen=True)
class LauncherConfig:
    file_root: str = "~"
    web_url: str = "https://www.google.com/search?q={query}"
    fixed_apps: tuple[str, ...] = ()
    preferred_actions: tuple[tuple[str, str], ...] = ()
    app_limit: int = 10
    window_limit: int = 8
    clipboard_limit: int = 50
    clipboard_retention_hours: float = 24
    file_debounce_ms: int = 120
    shell: str = "/bin/fish"
    terminal: str = "/usr/bin/ghostty"


@dataclass(frozen=True)
class TaskbarConfig:
    pinned: tuple[str, ...] = ()
    visible_limit: int = 8
    hover_delay_ms: int = 250


@dataclass(frozen=True)
class NotificationConfig:
    history_limit: int = 100
    retention_hours: float = 24
    toast_limit: int = 3
    default_timeout_ms: int = 5000
    dot_limit: int = 3
    dnd_allowlist: tuple[str, ...] = ()


@dataclass(frozen=True)
class WeatherConfig:
    forecast_days: int = 28
    cache_hours: int = 48
    timeout_seconds: float = 5.0


@dataclass(frozen=True)
class Threshold:
    warning: float
    danger: float


@dataclass(frozen=True)
class MetricsConfig:
    sample_seconds: float = 2.0
    graph_seconds: int = 60
    cpu: Threshold = field(default_factory=lambda: Threshold(85.0, 95.0))
    gpu: Threshold = field(default_factory=lambda: Threshold(80.0, 90.0))
    coolant: Threshold = field(default_factory=lambda: Threshold(45.0, 50.0))
    nvme_0700: Threshold = field(default_factory=lambda: Threshold(70.0, 78.0))
    nvme_0100: Threshold = field(default_factory=lambda: Threshold(80.0, 86.0))
    pump_warning_rpm: int = 1500
    pump_danger_rpm: int = 500
    fan_warning_rpm: int = 300
    fan_coolant_gate: float = 40.0


@dataclass(frozen=True)
class InputConfig:
    drag_threshold: int = DEFAULTS["input.drag_threshold"]
    scroll_event_delay: int = DEFAULTS["input.scroll_event_delay"]
    cursor_inactive_timeout: float = DEFAULTS["input.cursor_inactive_timeout"]
    cursor_no_warps: bool = DEFAULTS["input.cursor_no_warps"]
    cursor_persistent_warps: bool = DEFAULTS["input.cursor_persistent_warps"]
    cursor_hide_on_key_press: bool = DEFAULTS["input.cursor_hide_on_key_press"]
    cursor_hide_on_touch: bool = DEFAULTS["input.cursor_hide_on_touch"]
    cursor_hide_on_tablet: bool = DEFAULTS["input.cursor_hide_on_tablet"]
    cursor_warp_back_after_non_mouse_input: bool = DEFAULTS["input.cursor_warp_back_after_non_mouse_input"]
    resize_on_border: bool = DEFAULTS["input.resize_on_border"]
    extend_border_grab_area: int = DEFAULTS["input.extend_border_grab_area"]
    resize_on_border_inner_area: int = DEFAULTS["input.resize_on_border_inner_area"]
    hover_icon_on_border: bool = DEFAULTS["input.hover_icon_on_border"]
    resize_corner: int = DEFAULTS["input.resize_corner"]
    close_gesture_timeout: int = DEFAULTS["input.close_gesture_timeout"]
    kb_file: str = DEFAULTS["input.kb_file"]
    kb_snapshot: str = DEFAULTS["input.kb_snapshot"]
    accel_profile: str = DEFAULTS["input.accel_profile"]
    scroll_points: str = DEFAULTS["input.scroll_points"]
    focus_on_close: int = DEFAULTS["input.focus_on_close"]
    float_switch_override_focus: int = DEFAULTS["input.float_switch_override_focus"]
    follow_mouse: int = DEFAULTS["input.follow_mouse"]
    follow_mouse_threshold: float = DEFAULTS["input.follow_mouse_threshold"]
    mouse_refocus: bool = DEFAULTS["input.mouse_refocus"]
    follow_mouse_shrink: int = DEFAULTS["input.follow_mouse_shrink"]
    off_window_axis_events: int = DEFAULTS["input.off_window_axis_events"]
    emulate_discrete_scroll: int = DEFAULTS["input.emulate_discrete_scroll"]
    numlock_by_default: bool = DEFAULTS["input.numlock_by_default"]
    resolve_binds_by_sym: bool = DEFAULTS["input.resolve_binds_by_sym"]
    scroll_method: str = DEFAULTS["input.scroll_method"]
    scroll_button: int = DEFAULTS["input.scroll_button"]
    scroll_button_lock: bool = DEFAULTS["input.scroll_button_lock"]
    rotation: int = DEFAULTS["input.rotation"]
    kb_layout: str = DEFAULTS["input.kb_layout"]
    kb_model: str = DEFAULTS["input.kb_model"]
    kb_variant: str = DEFAULTS["input.kb_variant"]
    kb_options: str = DEFAULTS["input.kb_options"]
    kb_rules: str = DEFAULTS["input.kb_rules"]
    repeat_rate: int = DEFAULTS["input.repeat_rate"]
    repeat_delay: int = DEFAULTS["input.repeat_delay"]
    sensitivity: float = DEFAULTS["input.sensitivity"]
    scroll_factor: float = DEFAULTS["input.scroll_factor"]
    natural_scroll: bool = DEFAULTS["input.natural_scroll"]
    left_handed: bool = DEFAULTS["input.left_handed"]
    force_no_accel: bool = DEFAULTS["input.force_no_accel"]


@dataclass(frozen=True)
class TouchpadConfig:
    disable_while_typing: bool = DEFAULTS["touchpad.disable_while_typing"]
    natural_scroll: bool = DEFAULTS["touchpad.natural_scroll"]
    scroll_factor: float = DEFAULTS["touchpad.scroll_factor"]
    middle_button_emulation: bool = DEFAULTS["touchpad.middle_button_emulation"]
    tap_button_map: str = DEFAULTS["touchpad.tap_button_map"]
    clickfinger_behavior: bool = DEFAULTS["touchpad.clickfinger_behavior"]
    tap_to_click: bool = DEFAULTS["touchpad.tap_to_click"]
    drag_lock: int = DEFAULTS["touchpad.drag_lock"]
    tap_and_drag: bool = DEFAULTS["touchpad.tap_and_drag"]
    flip_x: bool = DEFAULTS["touchpad.flip_x"]
    flip_y: bool = DEFAULTS["touchpad.flip_y"]
    drag_3fg: int = DEFAULTS["touchpad.drag_3fg"]


@dataclass(frozen=True)
class TouchDeviceConfig:
    transform: int = DEFAULTS["touchdevice.transform"]
    output: str = DEFAULTS["touchdevice.output"]
    enabled: bool = DEFAULTS["touchdevice.enabled"]


@dataclass(frozen=True)
class VirtualKeyboardConfig:
    share_states: int = DEFAULTS["virtualkeyboard.share_states"]
    release_pressed_on_close: bool = DEFAULTS["virtualkeyboard.release_pressed_on_close"]


@dataclass(frozen=True)
class TabletConfig:
    transform: int = DEFAULTS["tablet.transform"]
    output: str = DEFAULTS["tablet.output"]
    absolute_region_position: bool = DEFAULTS["tablet.absolute_region_position"]
    relative_input: bool = DEFAULTS["tablet.relative_input"]
    left_handed: bool = DEFAULTS["tablet.left_handed"]
    region_position_x: float = DEFAULTS["tablet.region_position_x"]
    region_position_y: float = DEFAULTS["tablet.region_position_y"]
    region_size_x: float = DEFAULTS["tablet.region_size_x"]
    region_size_y: float = DEFAULTS["tablet.region_size_y"]
    active_area_size_x: float = DEFAULTS["tablet.active_area_size_x"]
    active_area_size_y: float = DEFAULTS["tablet.active_area_size_y"]
    active_area_position_x: float = DEFAULTS["tablet.active_area_position_x"]
    active_area_position_y: float = DEFAULTS["tablet.active_area_position_y"]


@dataclass(frozen=True)
class TabletToolConfig:
    eraser_button_mode: int = DEFAULTS["tablettool.eraser_button_mode"]
    eraser_button_override: int = DEFAULTS["tablettool.eraser_button_override"]
    pressure_range_min: float = DEFAULTS["tablettool.pressure_range_min"]
    pressure_range_max: float = DEFAULTS["tablettool.pressure_range_max"]


@dataclass(frozen=True)
class ShellConfig:
    path: Path
    layout: LayoutConfig
    theme: ThemeConfig
    compositor: CompositorConfig
    motion: MotionConfig
    appearance: AppearanceConfig
    system_theme: SystemThemeConfig
    network: NetworkConfig
    location: LocationConfig
    launcher: LauncherConfig
    taskbar: TaskbarConfig
    notifications: NotificationConfig
    weather: WeatherConfig
    metrics: MetricsConfig
    visual: VisualSettings = field(default_factory=VisualSettings)
    input: InputConfig = field(default_factory=InputConfig)
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    touchpad: TouchpadConfig = field(default_factory=TouchpadConfig)
    touchdevice: TouchDeviceConfig = field(default_factory=TouchDeviceConfig)
    virtualkeyboard: VirtualKeyboardConfig = field(default_factory=VirtualKeyboardConfig)
    tablet: TabletConfig = field(default_factory=TabletConfig)
    tablettool: TabletToolConfig = field(default_factory=TabletToolConfig)



CONFIG_PATH = (Path(os.environ['LUMINOPHORE_CONFIG_ROOT']) if os.environ.get('LUMINOPHORE_CONFIG_ROOT')
               else Path(os.environ.get('XDG_CONFIG_HOME') or Path.home()/'.config')/'luminophore')/'settings.toml'


def _native_path(path):
    return Path(path).absolute() == CONFIG_PATH.absolute()


def _section(raw: dict[str, Any], name: str, allowed: set[str]) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{name}: expected table")
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(f"{name}: unknown keys: {', '.join(sorted(unknown))}")
    return value


def _threshold(raw: dict[str, Any], name: str, default: Threshold) -> Threshold:
    item = raw.get(name, {})
    if not isinstance(item, dict) or set(item) - {"warning", "danger"}:
        raise ConfigError(f"metrics.{name}: expected warning/danger table")
    result = Threshold(float(item.get("warning", default.warning)), float(item.get("danger", default.danger)))
    if result.warning >= result.danger:
        raise ConfigError(f"metrics.{name}: warning must be lower than danger")
    return result


def _valid_hex_color(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        return False
    try:
        int(value[1:], 16)
    except ValueError:
        return False
    return True


COLLECTION_ENCODINGS = {
    "launcher.fixed_apps": "strings",
    "taskbar.pinned": "strings",
    "notifications.dnd_allowlist": "strings",
    "launcher.preferred_actions": "string_table",
    "theme.app_icon_aliases": "string_table",
    "theme.fixed_monitor_palettes": "palette_table",
}


def validate_collection(value: Any, path: str) -> None:
    """Validate TOML wire containers before converting them to immutable tuples."""
    encoding = COLLECTION_ENCODINGS[path]
    def nonempty(item):
        return type(item) is str and bool(item.strip())
    if encoding == "strings":
        if type(value) is not list or not all(nonempty(item) for item in value):
            raise ConfigError(f"{path}: expected array of non-empty strings")
        names = value
    else:
        if type(value) is not dict or not all(nonempty(key) for key in value):
            raise ConfigError(f"{path}: expected table with non-empty string keys")
        names = list(value)
        if encoding == "string_table" and not all(nonempty(item) for item in value.values()):
            raise ConfigError(f"{path}: expected non-empty string app and action IDs or icon names")
        if encoding == "palette_table":
            for colors in value.values():
                if type(colors) is not list or len(colors) != 2 or not all(nonempty(c) for c in colors):
                    raise ConfigError(f"{path}: must contain primary and secondary")
    if len({name.casefold() for name in names}) != len(names):
        raise ConfigError(f"{path}: duplicate names")


def _config_from_raw(raw: dict[str, Any], config_path: Path) -> ShellConfig:
    for location in COLLECTION_ENCODINGS:
        section, key = location.split('.')
        values = raw.get(section, {})
        if isinstance(values, dict) and key in values:
            validate_collection(values[key], location)
    known_sections = {
        "layout",
        "theme",
        "compositor",
        "input",
        "devices",
        "touchpad",
        "touchdevice",
        "virtualkeyboard",
        "tablet",
        "tablettool",

        "motion",
        "visual",
        "appearance",
        "system_theme",
        "network",
        "location",
        "launcher",
        "taskbar",
        "notifications",
        "weather",
        "metrics",
    }
    unknown_sections = set(raw) - known_sections
    if unknown_sections:
        raise ConfigError(f"unknown sections: {', '.join(sorted(unknown_sections))}")

    visual, visual_recovery = resolve_visual_config(raw.get("visual"))
    if visual_recovery != "none":
        logging.getLogger("luminophore-shell").warning(
            "visual settings recovered to complete balanced defaults: reason=%s", visual_recovery,
        )

    retired_layout = {"drop_halo", "maximize_drop_width"}
    layout_raw = dict(_section(raw, "layout", set(LayoutConfig.__dataclass_fields__) | retired_layout))
    for name in sorted(retired_layout & layout_raw.keys()):
        logging.getLogger("luminophore-shell").warning("layout.%s is retired and ignored; spatial editor replaces window drop targets", name)
        layout_raw.pop(name)
    theme_raw = _section(raw, "theme", set(ThemeConfig.__dataclass_fields__))
    if "visual" not in raw and any(key in theme_raw for key in ("outline_glow_intensity", "outline_glow_radius", "animate_glow")):
        logging.getLogger("luminophore-shell").warning("legacy effect settings detected; compositor uses balanced defaults until semantic visual settings are saved")
    compositor_raw = _section(raw, "compositor", set(CompositorConfig.__dataclass_fields__))
    motion_raw = _section(raw, "motion", set(MotionConfig.__dataclass_fields__))
    appearance_raw = _section(raw, "appearance", set(AppearanceConfig.__dataclass_fields__))
    system_theme_raw = _section(raw, "system_theme", set(SystemThemeConfig.__dataclass_fields__))
    network_raw = _section(raw, "network", set(NetworkConfig.__dataclass_fields__))
    location_raw = _section(raw, "location", set(LocationConfig.__dataclass_fields__))
    launcher_raw = _section(raw, "launcher", set(LauncherConfig.__dataclass_fields__))
    taskbar_raw = _section(raw, "taskbar", set(TaskbarConfig.__dataclass_fields__))
    notification_raw = _section(raw, "notifications", set(NotificationConfig.__dataclass_fields__))
    weather_raw = _section(raw, "weather", set(WeatherConfig.__dataclass_fields__))
    metrics_allowed = set(MetricsConfig.__dataclass_fields__)
    metrics_raw = _section(raw, "metrics", metrics_allowed)

    layout = LayoutConfig(**layout_raw)
    fixed_monitor_raw = theme_raw.pop("fixed_monitor_palettes", {})
    if not isinstance(fixed_monitor_raw, dict):
        raise ConfigError("theme.fixed_monitor_palettes: expected table")
    fixed_monitor_palettes: list[tuple[str, str, str]] = []
    for connector, colors in fixed_monitor_raw.items():
        if not isinstance(connector, str) or not connector.strip() or connector != connector.strip():
            raise ConfigError("theme.fixed_monitor_palettes must contain non-empty connector keys")
        if (
            not isinstance(colors, list)
            or len(colors) != 2
            or any(not isinstance(color, str) for color in colors)
        ):
            raise ConfigError(f"theme.fixed_monitor_palettes.{connector} must contain primary and secondary")
        fixed_monitor_palettes.append((connector, colors[0], colors[1]))
    aliases_present = "app_icon_aliases" in theme_raw
    app_icon_aliases_raw = theme_raw.pop("app_icon_aliases", {})
    if not isinstance(app_icon_aliases_raw, dict):
        raise ConfigError("theme.app_icon_aliases: expected table")
    if any(
        not isinstance(key, str)
        or not key.strip()
        or not isinstance(value, str)
        or not value.strip()
        for key, value in app_icon_aliases_raw.items()
    ):
        raise ConfigError("theme.app_icon_aliases must contain non-empty string names")
    app_icon_aliases = (
        tuple((key, value) for key, value in app_icon_aliases_raw.items())
        if aliases_present else DEFAULT_APP_ICON_ALIASES
    )
    theme = ThemeConfig(
        **theme_raw,
        fixed_monitor_palettes=tuple(fixed_monitor_palettes),
        app_icon_aliases=app_icon_aliases,
    )
    if "visual" in raw:
        # Compatibility fields are derived only; the semantic table is the
        # persisted authority for the Shell's existing style consumers.
        theme = replace(theme, outline_glow_intensity=visual.intensity if visual.enabled else 0.0,
                        animate_glow=visual.enabled and visual.breathing, outline_glow_radius=8)
        legacy_effect_keys = ({"outline_glow_intensity", "animate_glow", "outline_glow_radius"} & theme_raw.keys())
        if legacy_effect_keys:
            logging.getLogger("luminophore-shell").warning("legacy glow fields are superseded by visual settings")
    input_raw = _section(raw, "input", set(InputConfig.__dataclass_fields__))
    try:
        validate_input(input_raw)
        devices = decode_devices(raw.get("devices", {}))
        validate_acceleration(input_raw, devices)
        from .keymap_snapshot import validate_keymaps
        validate_keymaps(input_raw, devices)
    except ValueError as error:
        raise ConfigError(str(error)) from error
    touchpad_raw = _section(raw, "touchpad", set(TouchpadConfig.__dataclass_fields__))
    try:
        validate_owned_settings({"touchpad." + key: value for key, value in touchpad_raw.items()})
    except ValueError as error:
        raise ConfigError(str(error)) from error
    touchpad_config = TouchpadConfig(**touchpad_raw)
    absolute_raw = {name: _section(raw, name, set(model.__dataclass_fields__)) for name, model in (
        ("touchdevice", TouchDeviceConfig),
        ("virtualkeyboard", VirtualKeyboardConfig),
        ("tablet", TabletConfig),
        ("tablettool", TabletToolConfig),
)}
    try:
        validate_absolute_inputs(absolute_raw, devices)
    except ValueError as error:
        raise ConfigError(str(error)) from error
    touchdevice_config = TouchDeviceConfig(**absolute_raw["touchdevice"])
    virtualkeyboard_config = VirtualKeyboardConfig(**absolute_raw["virtualkeyboard"])
    tablet_config = TabletConfig(**absolute_raw["tablet"])
    tablettool_config = TabletToolConfig(**absolute_raw["tablettool"])

    input_config = InputConfig(**input_raw)
    compositor = CompositorConfig(**compositor_raw)
    motion = MotionConfig(**motion_raw)
    appearance = AppearanceConfig(**appearance_raw)
    system_theme = SystemThemeConfig(**system_theme_raw)
    network = NetworkConfig(**network_raw)
    location = LocationConfig(**location_raw)
    preferred_actions = launcher_raw.get("preferred_actions", {})
    if not isinstance(preferred_actions, dict):
        raise ConfigError("launcher.preferred_actions: expected table")
    if any(
        not isinstance(app_id, str) or not isinstance(action_id, str)
        for app_id, action_id in preferred_actions.items()
    ):
        raise ConfigError("launcher.preferred_actions must contain string app and action IDs")
    launcher = LauncherConfig(**{
        **launcher_raw,
        "fixed_apps": tuple(launcher_raw.get("fixed_apps", ())),
        "preferred_actions": tuple(preferred_actions.items()),
    })
    taskbar = TaskbarConfig(**{**taskbar_raw, "pinned": tuple(taskbar_raw.get("pinned", ()))})
    notifications = NotificationConfig(
        **{**notification_raw, "dnd_allowlist": tuple(notification_raw.get("dnd_allowlist", ()))},
    )
    weather = WeatherConfig(**weather_raw)

    metrics_defaults = MetricsConfig()
    threshold_names = {"cpu", "gpu", "coolant", "nvme_0700", "nvme_0100"}
    scalar_metrics = {key: value for key, value in metrics_raw.items() if key not in threshold_names}
    metrics = MetricsConfig(
        **scalar_metrics,
        **{name: _threshold(metrics_raw, name, getattr(metrics_defaults, name)) for name in threshold_names},
    )

    if not 0.2 <= theme.backdrop_opacity <= 0.95:
        raise ConfigError("theme.backdrop_opacity must be between 0.2 and 0.95")
    if theme.palette_source not in PALETTE_SOURCES:
        raise ConfigError(f"theme.palette_source must be one of: {', '.join(sorted(PALETTE_SOURCES))}")
    if theme.app_icon_theme not in APP_ICON_THEMES:
        raise ConfigError(f"theme.app_icon_theme must be one of: {', '.join(sorted(APP_ICON_THEMES))}")
    if theme.glow_renderer not in GLOW_RENDERERS:
        raise ConfigError(f"theme.glow_renderer must be one of: {', '.join(sorted(GLOW_RENDERERS))}")
    for field_name in ("fallback_primary", "fallback_secondary", "fixed_primary", "fixed_secondary"):
        if not _valid_hex_color(getattr(theme, field_name)):
            raise ConfigError(f"theme.{field_name} must be #RRGGBB")
    if theme.fixed_primary.casefold() == theme.fixed_secondary.casefold():
        raise ConfigError("theme.fixed_primary and theme.fixed_secondary must differ")
    if len({connector.casefold() for connector, _primary, _secondary in theme.fixed_monitor_palettes}) != len(theme.fixed_monitor_palettes):
        raise ConfigError("theme.fixed_monitor_palettes cannot contain duplicate connectors")
    if len({key.casefold() for key, _value in theme.app_icon_aliases}) != len(theme.app_icon_aliases):
        raise ConfigError("theme.app_icon_aliases cannot contain duplicate names")
    icon_name = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
    if any(not icon_name.fullmatch(value) for _key, value in theme.app_icon_aliases):
        raise ConfigError("theme.app_icon_aliases values must be freedesktop icon names")
    for connector, primary, secondary in theme.fixed_monitor_palettes:
        if not _valid_hex_color(primary) or not _valid_hex_color(secondary):
            raise ConfigError(f"theme.fixed_monitor_palettes.{connector} colors must be #RRGGBB")
        if primary.casefold() == secondary.casefold():
            raise ConfigError(f"theme.fixed_monitor_palettes.{connector} primary and secondary must differ")
    if system_theme.mode not in {"dark", "light"}:
        raise ConfigError("system_theme.mode must be dark or light")
    if layout.edge_margin < 0 or layout.panel_height < 1 or layout.radius < 0:
        raise ConfigError("layout edge_margin/radius must be non-negative and panel_height must be positive")
    if not 0.2 <= layout.max_height_ratio <= 0.9:
        raise ConfigError("layout.max_height_ratio must be between 0.2 and 0.9")
    if layout.expansion_ms < 0:
        raise ConfigError("layout expansion_ms must be non-negative")
    if min(layout.weather_width, layout.weather_height, layout.system_width, layout.system_height) < 1:
        raise ConfigError("layout widget dimensions must be positive")
    if theme.palette_transition_ms < 0:
        raise ConfigError("theme.palette_transition_ms must be non-negative")
    if type(theme.animate_glow) is not bool:
        raise ConfigError("theme.animate_glow must be a boolean")
    if type(theme.audio_spectrum_enabled) is not bool:
        raise ConfigError("theme.audio_spectrum_enabled must be a boolean")
    if type(theme.outline_width) is not int or not 1 <= theme.outline_width <= 12:
        raise ConfigError("theme.outline_width must be an integer between 1 and 12")
    if (
        isinstance(theme.outline_glow_intensity, bool)
        or not isinstance(theme.outline_glow_intensity, (int, float))
        or not 0 <= theme.outline_glow_intensity <= 3
    ):
        raise ConfigError("theme.outline_glow_intensity must be between 0 and 3")
    if type(theme.outline_glow_radius) is not int or not 1 <= theme.outline_glow_radius <= 64:
        raise ConfigError("theme.outline_glow_radius must be an integer between 1 and 64")
    if not theme.body_font.strip() or not theme.numeric_font.strip():
        raise ConfigError("theme fonts must be non-empty")
    if not 0.8 <= theme.ui_scale <= 1.5:
        raise ConfigError("theme.ui_scale must be between 0.8 and 1.5")
    # Preserve the established validation contract for the two legacy blur
    # keys while the wider typed Hyprland allowlist is additive.
    if (
        type(compositor.blur_size) is not int
        or type(compositor.blur_passes) is not int
        or min(compositor.blur_size, compositor.blur_passes) < 1
    ):
        raise ConfigError("compositor blur_size/blur_passes must be integers of at least 1")
    if not motion.enabled and motion.preset == "custom":
        raise ConfigError("motion.preset custom requires motion.enabled")
    try:
        validate_owned_settings({
            **{"compositor." + key: value for key, value in asdict(compositor).items()},
            **{"motion." + key: value for key, value in asdict(motion).items()},
        })
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if compositor.inactive_opacity > compositor.active_opacity:
        raise ConfigError("compositor.inactive_opacity must not exceed active_opacity")
    if appearance.wallpaper_provider not in WALLPAPER_PROVIDERS:
        raise ConfigError(
            "appearance.wallpaper_provider must be one of: "
            + ", ".join(sorted(WALLPAPER_PROVIDERS))
        )
    if min(launcher.app_limit, launcher.window_limit, launcher.clipboard_limit) < 1:
        raise ConfigError("launcher app/window/clipboard limits must be positive")
    if launcher.clipboard_retention_hours <= 0:
        raise ConfigError("launcher.clipboard_retention_hours must be positive")
    if launcher.file_debounce_ms < 0:
        raise ConfigError("launcher.file_debounce_ms must be non-negative")
    if "{query}" not in launcher.web_url:
        raise ConfigError("launcher.web_url must contain {query}")
    if not launcher.file_root.strip() or not launcher.shell.strip() or not launcher.terminal.strip():
        raise ConfigError("launcher file_root/shell/terminal must be non-empty")
    if taskbar.visible_limit < 1 or taskbar.hover_delay_ms < 0:
        raise ConfigError("taskbar.visible_limit must be positive and hover_delay_ms non-negative")
    if len(launcher.fixed_apps) > launcher.app_limit:
        raise ConfigError("launcher.fixed_apps cannot exceed launcher.app_limit")
    if any(not isinstance(app_id, str) or not app_id.strip() for app_id in launcher.fixed_apps):
        raise ConfigError("launcher.fixed_apps must contain non-empty strings")
    if len({app_id.casefold() for app_id in launcher.fixed_apps}) != len(launcher.fixed_apps):
        raise ConfigError("launcher.fixed_apps cannot contain duplicates")
    if any(not app_id.strip() or not action_id.strip() for app_id, action_id in launcher.preferred_actions):
        raise ConfigError("launcher.preferred_actions must contain non-empty app and action IDs")
    if len({app_id.casefold() for app_id, _action_id in launcher.preferred_actions}) != len(launcher.preferred_actions):
        raise ConfigError("launcher.preferred_actions cannot contain duplicate app IDs")
    if any(not isinstance(app_id, str) or not app_id.strip() for app_id in taskbar.pinned):
        raise ConfigError("taskbar.pinned must contain non-empty strings")
    if len({app_id.casefold() for app_id in taskbar.pinned}) != len(taskbar.pinned):
        raise ConfigError("taskbar.pinned cannot contain duplicates")
    if min(notifications.history_limit, notifications.default_timeout_ms, notifications.dot_limit) < 0 or notifications.toast_limit < 1:
        raise ConfigError("notification history/timeout/dot values must be non-negative and toast_limit positive")
    if notifications.retention_hours <= 0:
        raise ConfigError("notifications.retention_hours must be positive")
    if len({item.casefold() for item in notifications.dnd_allowlist}) != len(notifications.dnd_allowlist):
        raise ConfigError("notifications.dnd_allowlist cannot contain duplicates")
    if not -90 <= location.latitude <= 90 or not -180 <= location.longitude <= 180:
        raise ConfigError("location latitude/longitude are out of range")
    try:
        ZoneInfo(location.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError("location.timezone must be a valid IANA timezone") from exc
    if location.geoclue_timeout_seconds <= 0 or location.city_search_timeout_seconds <= 0:
        raise ConfigError("location timeouts must be positive")
    if weather.forecast_days != 28:
        raise ConfigError("weather.forecast_days is fixed to 28")
    if weather.cache_hours < 0 or weather.timeout_seconds <= 0:
        raise ConfigError("weather.cache_hours must be non-negative and timeout_seconds positive")
    if metrics.sample_seconds < 1:
        raise ConfigError("metrics.sample_seconds must be at least 1")
    if metrics.graph_seconds < metrics.sample_seconds:
        raise ConfigError("metrics.graph_seconds must be at least sample_seconds")
    if min(metrics.pump_warning_rpm, metrics.pump_danger_rpm, metrics.fan_warning_rpm) < 0:
        raise ConfigError("metrics RPM thresholds must be non-negative")
    if metrics.pump_danger_rpm >= metrics.pump_warning_rpm:
        raise ConfigError("metrics.pump_danger_rpm must be lower than pump_warning_rpm")
    if metrics.fan_coolant_gate < 0:
        raise ConfigError("metrics.fan_coolant_gate must be non-negative")
    return ShellConfig(
        config_path,
        layout,
        theme,
        compositor,
        motion,
        appearance,
        system_theme,
        network,
        location,
        launcher,
        taskbar,
        notifications,
        weather,
        metrics,
        visual,
        input_config,
        devices,
        touchpad_config,
        touchdevice_config,
        virtualkeyboard_config,
        tablet_config,
        tablettool_config,
    )


def load_config_text(text: str, path: Path | None = None) -> ShellConfig:
    config_path = (path or CONFIG_PATH).expanduser().resolve()
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(str(exc)) from exc
    return _config_from_raw(raw, config_path)


def load_config(path: Path | None = None) -> ShellConfig:
    config_path = (path or CONFIG_PATH).expanduser().resolve()
    if _native_path(config_path):
        from .settings_generation import boot_config
        return boot_config()
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(str(exc)) from exc
    return load_config_text(text, config_path)


def config_mtime_ns(path: Path = CONFIG_PATH) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def config_digest(path: Path = CONFIG_PATH) -> str:
    if _native_path(path):
        from .settings_generation import fixture_store
        current = fixture_store().current()
        return current.id if current else ''
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


_TABLE_HEADER = re.compile(r"^\s*\[([^]]+)]\s*(?:#.*)?$")


def _toml_literal(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not (float("-inf") < value < float("inf")):
            raise ConfigError("non-finite numbers are not supported")
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
        return "[" + ", ".join(json.dumps(item, ensure_ascii=False) for item in value) + "]"
    raise ConfigError(f"unsupported TOML setting value: {type(value).__name__}")


def _section_bounds(lines: list[str], section: str) -> tuple[int, int]:
    starts: list[int] = []
    for index, line in enumerate(lines):
        match = _TABLE_HEADER.match(line.rstrip("\n"))
        if match and match.group(1) == section:
            starts.append(index)
    if len(starts) != 1:
        raise ConfigError(f"config section must exist exactly once: {section}")
    start = starts[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _TABLE_HEADER.match(lines[index].rstrip("\n")):
            end = index
            break
    return start, end


def render_config_patch(text: str, changes: Mapping[str, object], *, config_root: Path | None = None) -> str:
    import_devices = "devices" in changes
    if "devices" in changes:
        devices = decode_devices(changes["devices"])
        before = tomllib.loads(text)
        for name, fields in devices.items():
            old = before.get("devices", {}).get(name, {})
            if fields.get("kb_file") == old.get("kb_file") and "kb_snapshot" in old and "kb_snapshot" not in fields:
                fields["kb_snapshot"] = old["kb_snapshot"]
        kept = []; removing = False
        for line in text.splitlines(keepends=True):
            if line.lstrip().startswith("["):
                removing = bool(re.match(r"\s*\[\s*(?:devices|\"devices\"|'devices')(?:\s*[.\]])", line))
            if not removing: kept.append(line)
        text = "".join(kept)
        if text and not text.endswith("\n"): text += "\n"
        for name, fields in sorted(devices.items()):
            text += "\n[devices." + json.dumps(name) + "]\n"
            text += "".join(key + " = " + _toml_literal(value) + "\n" for key, value in sorted(fields.items()))
        expected = {**before, "devices": devices}
        if not devices: expected.pop("devices", None)
        if tomllib.loads(text) != expected:
            raise ConfigError("cannot safely replace device settings")
        changes = {key: value for key, value in changes.items() if key != "devices"}
    visual_changes = {path[7:]: value for path, value in changes.items() if path.startswith("visual.")}
    if visual_changes:
        if set(visual_changes) - set(VisualSettings.__dataclass_fields__):
            raise ConfigError("unknown visual setting")
        try:
            raw = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(str(exc)) from exc
        current, _recovery = resolve_visual_config(raw.get("visual"))
        fields = {name: getattr(current, name) for name in VisualSettings.__dataclass_fields__}
        fields.update(visual_changes)
        normalized, recovery = resolve_visual_config(fields)
        if recovery != "none":
            raise ConfigError(f"invalid visual settings: {recovery}")
        lines = text.splitlines(keepends=True)
        headers = [index for index, line in enumerate(lines)
                   if (match := _TABLE_HEADER.match(line.rstrip("\n"))) and match.group(1) == "visual"]
        if len(headers) > 1:
            raise ConfigError("config section must exist at most once: visual")
        encoded = [f"{name} = {_toml_literal(getattr(normalized, name))}\n"
                   for name in VisualSettings.__dataclass_fields__]
        if headers:
            start, end = _section_bounds(lines, "visual")
            lines[start + 1:end] = encoded + ["\n"]
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.extend(["\n[visual]\n", *encoded])
        text = "".join(lines)
        try:
            saved_visual = tomllib.loads(text).get("visual")
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError("visual table cannot be safely rewritten") from exc
        expected_visual = {name: getattr(normalized, name) for name in VisualSettings.__dataclass_fields__}
        if saved_visual != expected_visual:
            raise ConfigError("visual table contains unsupported nested or dotted fields")
        changes = {path: value for path, value in changes.items() if not path.startswith("visual.")}
    lines = text.splitlines(keepends=True)
    mapping_changes = {
        path: value for path, value in changes.items()
        if isinstance(value, Mapping)
    }
    scalar_changes = {
        path: value for path, value in changes.items()
        if path not in mapping_changes
    }

    for path, raw_mapping in sorted(mapping_changes.items()):
        assert isinstance(raw_mapping, Mapping)
        encoded: list[str] = []
        for key, value in raw_mapping.items():
            if not isinstance(key, str) or not key.strip():
                raise ConfigError(f"{path} must contain non-empty string keys")
            if isinstance(value, str):
                valid_value = bool(value.strip())
            elif isinstance(value, (tuple, list)):
                valid_value = bool(value) and all(isinstance(item, str) and item.strip() for item in value)
            else:
                valid_value = False
            if not valid_value:
                raise ConfigError(f"{path} must contain non-empty string values or string lists")
            encoded.append(f"{json.dumps(key, ensure_ascii=False)} = {_toml_literal(value)}\n")
        starts = [
            index for index, line in enumerate(lines)
            if (match := _TABLE_HEADER.match(line.rstrip("\n"))) and match.group(1) == path
        ]
        if len(starts) > 1:
            raise ConfigError(f"config section must exist at most once: {path}")
        if not starts:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            if lines and lines[-1].strip():
                lines.append("\n")
            lines.append(f"[{path}]\n")
            lines.extend(encoded)
            lines.append("\n")
            continue
        start, end = _section_bounds(lines, path)
        lines[start + 1:end] = encoded + (["\n"] if encoded else [])

    for path, value in sorted(scalar_changes.items()):
        if "." not in path:
            raise ConfigError(f"invalid setting path: {path}")
        section, key = path.rsplit(".", 1)
        from .owned_settings import SPECS
        if path in SPECS:
            try: SPECS[path].validate(value)
            except ValueError as error: raise ConfigError(str(error)) from error
            headers = [i for i, line in enumerate(lines)
                       if (m := _TABLE_HEADER.match(line.rstrip("\n"))) and m.group(1) == section]
            if not headers:
                if lines and not lines[-1].endswith("\n"): lines[-1] += "\n"
                lines.extend(["\n[" + section + "]\n"])
        start, end = _section_bounds(lines, section)
        pattern = re.compile(rf"^(\s*){re.escape(key)}\s*=")
        matches = [index for index in range(start + 1, end) if pattern.match(lines[index])]
        if not matches and path in SPECS:
            lines.insert(end, f"{key} = {_toml_literal(value)}\n")
            continue
        if len(matches) != 1:
            raise ConfigError(f"config key must exist exactly once: {path}")
        index = matches[0]
        indentation = pattern.match(lines[index]).group(1)  # type: ignore[union-attr]
        newline = "\n" if lines[index].endswith("\n") else ""
        old = lines[index]; quoted = None; escaped = False; comment = ""
        for i, char in enumerate(old):
            if escaped: escaped = False; continue
            if char == "\\" and quoted == '"': escaped = True; continue
            if quoted:
                if char == quoted: quoted = None
            elif char in ("'", '"'): quoted = char
            elif char == "#": comment = " " + old[i:].rstrip("\n"); break
        lines[index] = f"{indentation}{key} = {_toml_literal(value)}{comment}{newline}"
    candidate = "".join(lines)
    # Insertion must never create duplicate or mis-scoped dotted/inline keys.
    try: tomllib.loads(candidate)
    except tomllib.TOMLDecodeError as exc: raise ConfigError("cannot safely patch configuration") from exc
    if import_devices or "input.kb_file" in changes:
        from .keymap_snapshot import import_keymaps
        candidate = import_keymaps({"settings.toml": candidate}, config_root or Path.cwd(),
                                   refresh={("input",)} if "input.kb_file" in changes else ())['settings.toml']
    return candidate


def _write_text_atomic(path: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, path.stat().st_mode & 0o777)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_config_patch(
    path: Path,
    changes: Mapping[str, object],
    expected_digest: str,
) -> ShellConfig:
    if _native_path(path):
        from .domain_client import DomainClient
        try:
            DomainClient().commit(dict(changes), expected_digest)
        except (ValueError, RuntimeError, OSError) as error:
            raise ConfigError(str(error)) from error
        return load_config(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(str(exc)) from exc
    current_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if not expected_digest or current_digest != expected_digest:
        raise ConfigConflictError("설정 파일이 외부에서 변경되었습니다")
    candidate = render_config_patch(text, changes, config_root=path.parent)
    config = load_config_text(candidate, path)
    try:
        _write_text_atomic(path, candidate)
    except OSError as exc:
        raise ConfigWriteError(str(exc)) from exc
    return config


def restore_config_snapshot(path: Path, text: str, expected_digest: str) -> ShellConfig:
    """Restore a failed candidate only while it is still the current file."""
    if _native_path(path):
        raise ConfigError('restore the complete settings generation through the coordinator')
    if not expected_digest or config_digest(path) != expected_digest:
        raise ConfigConflictError("설정 파일이 외부에서 변경되었습니다")
    config = load_config_text(text, path)
    try:
        _write_text_atomic(path, text)
    except OSError as exc:
        raise ConfigWriteError(str(exc)) from exc
    return config


def write_taskbar_pins(path: Path, pinned: tuple[str, ...]) -> None:
    if _native_path(path):
        write_config_patch(path, {'taskbar.pinned': list(pinned)}, config_digest(path))
        return
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    in_taskbar = False
    replaced = False
    encoded = ", ".join(repr(item) for item in pinned)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_taskbar = stripped == "[taskbar]"
        elif in_taskbar and stripped.startswith("pinned") and "=" in stripped:
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f"pinned = [{encoded}]{newline}"
            replaced = True
            break
    if not replaced:
        raise ConfigError("taskbar.pinned setting is missing")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("".join(lines))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, path.stat().st_mode & 0o777)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_theme_source(path: Path, source: str) -> None:
    if source not in PALETTE_SOURCES:
        raise ConfigError(f"invalid palette source: {source}")
    if _native_path(path):
        write_config_patch(path, {'theme.palette_source': source}, config_digest(path))
        return
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    in_theme = False
    replaced = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_theme = stripped == "[theme]"
        elif in_theme and stripped.startswith("palette_source") and "=" in stripped:
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f'palette_source = "{source}"{newline}'
            replaced = True
            break
    if not replaced:
        raise ConfigError("theme.palette_source setting is missing")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("".join(lines))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, path.stat().st_mode & 0o777)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
