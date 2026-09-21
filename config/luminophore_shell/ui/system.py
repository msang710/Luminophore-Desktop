from __future__ import annotations

from collections import deque
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..applications import ApplicationCatalog
from ..app_icons import ApplicationIconProvider
from ..icon_generation import GeneratedIconStore, IconDraftGenerator
from ..metrics import MetricSnapshot
from ..palette_controller import PaletteSettingsState
from ..screen_color_picker import ScreenColorPickResult
from ..settings_controller import SettingsController
from ..system_theme import SystemThemeController, SystemThemeState
from ..system_controls import BluetoothDevice, PairingDecision, PairingPrompt, SystemControlSnapshot, WifiNetwork
from ..location import CityResult, LocationSnapshot
from ..calendar import CalendarSnapshot
from ..privacy import PrivacySnapshot
from ..theme import Palette
from .settings import SettingsView
from .system_theme import SystemThemeView
from .control_center import ControlCenterView
from .effects import attach_luminophore_state


def _value(value: float | None, suffix: str = "", precision: int = 0) -> str:
    return "--" if value is None else f"{value:.{precision}f}{suffix}"


def _rate(value: float | None) -> str:
    if value is None:
        return "--"
    units = ("B/s", "KiB/s", "MiB/s", "GiB/s")
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    return f"{value:.1f} {units[index]}"


class SystemView:
    def __init__(
        self,
        on_toggle: Callable[[], None],
        on_extract_palette: Callable[[], bool],
        on_apply_palette: Callable[[], bool],
        settings_controller: SettingsController,
        system_theme_controller: SystemThemeController,
        get_system_theme_mode: Callable[[], str],
        catalog: ApplicationCatalog,
        on_settings_mode: Callable[[bool], None],
        screen_color_available: Callable[[], bool],
        screen_color_busy: Callable[[], bool],
        begin_screen_color_pick: Callable[[Callable[[ScreenColorPickResult], None]], bool],
        refresh_controls: Callable[[], None],
        set_wifi: Callable[[bool], None],
        connect_wifi: Callable[[WifiNetwork, str], None],
        set_bluetooth: Callable[[bool], None],
        connect_bluetooth: Callable[[BluetoothDevice], None],
        pair_bluetooth: Callable[[BluetoothDevice], None],
        set_power_profile: Callable[[str, tuple[str, ...]], None],
        resolve_location: Callable[[], None],
        search_city: Callable[[str], None],
        select_city: Callable[[CityResult], None],
        connect_calendar: Callable[[], None],
        refresh_calendar: Callable[[], None],
        disconnect_calendar: Callable[[], None],
        icon_provider: ApplicationIconProvider | None = None,
        icon_generator: IconDraftGenerator | None = None,
        generated_icon_store: GeneratedIconStore | None = None,
    ) -> None:
        self.on_settings_mode = on_settings_mode
        self.settings_mode = False
        self.collapsed = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.cpu = self._metric_label()
        self.gpu = self._metric_label()
        self.coolant = self._metric_label()
        for label in (self.cpu, self.gpu, self.coolant):
            self.collapsed.append(label)
        gesture = Gtk.GestureClick()
        gesture.connect("released", lambda *_args: on_toggle())
        self.collapsed.add_controller(gesture)

        self.expanded = Gtk.Stack()
        self.expanded.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.expanded.set_transition_duration(180)
        self.expanded.set_hhomogeneous(False)
        self.expanded.set_vhomogeneous(False)
        self.metrics_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.cpu_history: deque[float | None] = deque(maxlen=30)
        self.gpu_history: deque[float | None] = deque(maxlen=30)
        self.primary = (0.47, 0.86, 0.91)
        self.secondary = (0.67, 0.62, 0.95)
        self.rows: dict[str, Gtk.Label] = {}
        for key, title in (
            ("cpu", "CPU"), ("gpu", "GPU"), ("memory", "RAM / SWAP"), ("disk", "루트 디스크"),
            ("network", "네트워크"), ("nvme", "NVMe"), ("cooling", "펌프 / 팬 / 냉각수"),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name = Gtk.Label(label=title)
            name.add_css_class("luminophore-key-primary")
            name.set_hexpand(True)
            name.set_xalign(0)
            value = Gtk.Label(label="--")
            value.add_css_class("numeric")
            value.add_css_class("luminophore-key-secondary")
            value.set_xalign(1)
            row.append(name)
            row.append(value)
            self.metrics_page.append(row)
            self.rows[key] = value
        self.graph = Gtk.DrawingArea()
        self.graph.set_content_height(72)
        self.graph.set_content_width(430)
        self.graph.set_draw_func(self._draw_graph)
        self.graph.set_tooltip_text("최근 60초 · CPU / GPU 사용률")
        self.metrics_page.append(self.graph)
        metrics_footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        metrics_footer.set_halign(Gtk.Align.END)
        settings = Gtk.Button.new_from_icon_name("preferences-system-symbolic")
        settings.add_css_class("luminophore-button")
        settings_icon = settings.get_child()
        if settings_icon:
            settings_icon.add_css_class("luminophore-symbol-primary")
        settings.set_tooltip_text("설정")
        settings.connect("clicked", lambda _button: self.show_settings())
        attach_luminophore_state(settings)
        controls = Gtk.Button.new_from_icon_name("emblem-system-symbolic")
        controls.add_css_class("luminophore-button")
        controls.set_tooltip_text("제어센터")
        controls.connect("clicked", lambda _button: self.show_controls())
        attach_luminophore_state(controls)
        metrics_footer.append(controls)
        metrics_footer.append(settings)
        self.metrics_page.append(metrics_footer)
        self.expanded.add_named(self.metrics_page, "metrics")

        self.settings_view = SettingsView(
            settings_controller,
            catalog,
            self._leave_settings,
            on_extract_palette,
            on_apply_palette,
            self._open_system_theme_tools,
            screen_color_available,
            screen_color_busy,
            begin_screen_color_pick,
            icon_provider,
            icon_generator,
            generated_icon_store,
        )
        self.expanded.add_named(self.settings_view.widget, "settings")
        self.system_theme_view = SystemThemeView(
            system_theme_controller,
            get_system_theme_mode,
            self._back_from_system_theme,
        )
        self.expanded.add_named(self.system_theme_view.widget, "system-theme")
        self.control_center = ControlCenterView(
            refresh_controls,
            set_wifi,
            connect_wifi,
            set_bluetooth,
            connect_bluetooth,
            pair_bluetooth,
            set_power_profile,
            resolve_location,
            search_city,
            select_city,
            connect_calendar,
            refresh_calendar,
            disconnect_calendar,
            self._back_from_controls,
        )
        control_scroll = Gtk.ScrolledWindow()
        control_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        control_scroll.set_child(self.control_center.widget)
        self.expanded.add_named(control_scroll, "controls")
        self.expanded.set_visible_child_name("metrics")
        self.expanded.set_size_request(460, 350)
        self.update_palette_settings(PaletteSettingsState(), "wallpaper", {})

    def _metric_label(self) -> Gtk.Label:
        label = Gtk.Label(label="--")
        label.add_css_class("numeric")
        label.add_css_class("metric-value")
        label.add_css_class("luminophore-key-primary")
        return label

    def update(self, data: MetricSnapshot) -> None:
        self.cpu_history.append(data.cpu_usage)
        self.gpu_history.append(data.gpu_usage)
        self.graph.queue_draw()
        self.cpu.set_label(f"CPU {_value(data.cpu_temperature, '°', 0)}")
        self.gpu.set_label(f"GPU {_value(data.gpu_temperature, '°', 0)}")
        self.coolant.set_label(f"냉각수 {_value(data.coolant_temperature, '°', 0)}")
        self.rows["cpu"].set_label(
            f"{_value(data.cpu_usage, '%', 0)} · {_value(data.cpu_temperature, '°C', 0)} · {_value(data.cpu_clock_mhz, ' MHz', 0)}"
        )
        self.rows["gpu"].set_label(
            f"{_value(data.gpu_usage, '%', 0)} · {_value(data.gpu_temperature, '°C', 0)} · "
            f"{_value(data.gpu_vram_used_mb, '', 0)}/{_value(data.gpu_vram_total_mb, ' MiB', 0)} · {_value(data.gpu_power_w, ' W', 0)}"
        )
        self.rows["memory"].set_label(
            f"{_value(data.ram_used_gb, '', 1)}/{_value(data.ram_total_gb, ' GiB', 1)} · "
            f"swap {_value(data.swap_used_gb, '', 1)}/{_value(data.swap_total_gb, ' GiB', 1)}"
        )
        self.rows["disk"].set_label(_value(data.root_used_percent, "%", 0))
        self.rows["network"].set_label(
            f"{data.network_interface or '--'} · ↓ {_rate(data.network_rx_bps)} · ↑ {_rate(data.network_tx_bps)}"
        )
        self.rows["nvme"].set_label(
            f"07:00 {_value(data.nvme_0700_temperature, '°C', 0)} · 01:00 {_value(data.nvme_0100_temperature, '°C', 0)}"
        )
        self.rows["cooling"].set_label(
            f"{_value(data.pump_rpm, ' RPM', 0)} · {_value(data.fan_rpm, ' RPM', 0)} · {_value(data.coolant_temperature, '°C', 1)}"
        )

    def set_colors(self, primary: str, secondary: str) -> None:
        def rgb(value: str) -> tuple[float, float, float]:
            clean = value.removeprefix("#")
            return tuple(int(clean[index:index + 2], 16) / 255 for index in (0, 2, 4))  # type: ignore[return-value]

        self.primary = rgb(primary)
        self.secondary = rgb(secondary)
        self.graph.queue_draw()

    def set_history_capacity(self, count: int, seconds: int | None = None) -> None:
        capacity = max(2, count)
        self.cpu_history = deque(self.cpu_history, maxlen=capacity)
        self.gpu_history = deque(self.gpu_history, maxlen=capacity)
        if seconds is not None:
            self.graph.set_tooltip_text(f"최근 {seconds}초 · CPU / GPU 사용률")
        self.graph.queue_draw()

    def update_palette_settings(
        self,
        state: PaletteSettingsState,
        source: str,
        current: dict[str, Palette],
    ) -> None:
        self.settings_view.update_palette_settings(state, source, current)

    def show_palette_settings(self) -> None:
        self.settings_view.open()
        self.settings_view.show_category("palette")
        self._show_page("settings", True)

    def show_settings(self) -> None:
        self.settings_view.open()
        self._show_page("settings", True)

    def show_system_theme(self) -> None:
        self.system_theme_view.open()
        self._show_page("system-theme", True)

    def show_controls(self) -> None:
        self.control_center.refresh()
        self._show_page("controls", True)

    def update_controls(self, snapshot: SystemControlSnapshot) -> None:
        self.control_center.update(snapshot)

    def show_pairing_prompt(self, prompt: PairingPrompt, resolve: Callable[[int, PairingDecision], None]) -> None:
        self.control_center.show_pairing_prompt(prompt, resolve)

    def update_location(self, snapshot: LocationSnapshot, error: str = "") -> None:
        self.control_center.update_location(snapshot, error)

    def update_city_results(self, results: tuple[CityResult, ...], error: str = "") -> None:
        self.control_center.update_city_results(results, error)

    def update_calendar(self, snapshot: CalendarSnapshot | None, error: str = "") -> None:
        self.control_center.update_calendar(snapshot, error)

    def update_privacy(self, snapshot: PrivacySnapshot) -> None:
        self.control_center.update_privacy(snapshot)

    def update_system_theme(self, state: SystemThemeState) -> None:
        self.system_theme_view.update(state)

    def _open_system_theme_tools(self) -> None:
        self.system_theme_view.open()
        self._show_page("system-theme", True)

    def _back_from_system_theme(self) -> None:
        self.settings_view.open()
        self.settings_view.show_category("palette")
        self._show_page("settings", True)

    def _back_from_controls(self) -> None:
        self._show_page("metrics", False)

    def resume_settings_after_screen_pick(self) -> None:
        self._show_page("settings", True)

    def _leave_settings(self) -> None:
        self._show_page("metrics", False)

    def _show_page(self, page: str, settings_mode: bool) -> None:
        # Full height and keyboard interactivity are properties of the page,
        # not of whether the layer surface happens to be open right now.
        self.settings_mode = settings_mode
        self.on_settings_mode(settings_mode)
        self.expanded.set_visible_child_name(page)

    def _draw_graph(self, _area: Gtk.DrawingArea, context, width: int, height: int) -> None:
        context.set_line_width(1)
        context.set_source_rgba(1, 1, 1, 0.09)
        for step in (0.25, 0.5, 0.75):
            y = height * step
            context.move_to(0, y)
            context.line_to(width, y)
        context.stroke()

        def draw(values: deque[float | None], color: tuple[float, float, float]) -> None:
            if len(values) < 2:
                return
            context.set_source_rgba(*color, 0.9)
            context.set_line_width(1.7)
            drawing = False
            divisor = max(1, values.maxlen - 1 if values.maxlen else len(values) - 1)
            offset = (values.maxlen or len(values)) - len(values)
            for index, value in enumerate(values):
                if value is None:
                    drawing = False
                    continue
                x = (offset + index) / divisor * width
                y = height - max(0, min(100, value)) / 100 * height
                if drawing:
                    context.line_to(x, y)
                else:
                    context.move_to(x, y)
                    drawing = True
            context.stroke()

        draw(self.cpu_history, self.primary)
        draw(self.gpu_history, self.secondary)
