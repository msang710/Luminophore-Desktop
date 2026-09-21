from __future__ import annotations

from collections import defaultdict
import time
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Gtk4LayerShell  # noqa: E402

from ..config import NotificationConfig
from ..applications import ApplicationCatalog
from ..app_icons import ApplicationIconProvider
from ..hardware_controls import AudioApplicationState, HardwareQuickState
from ..notifications import Notification, NotificationManager
from ..tray import StatusNotifierHost, TrayItem
from .dimensions import CONTROL_PANEL_WIDTH
from .effects import app_icon, attach_luminophore_state
from .power import power_expanded
from .widgets import icon_button


class HardwareQuickControlsView:
    BRIGHTNESS_DRAG_WATCHDOG_MS = 2000
    BRIGHTNESS_ADJUST_KEYS = frozenset(
        {
            Gdk.KEY_Up,
            Gdk.KEY_Down,
            Gdk.KEY_Left,
            Gdk.KEY_Right,
            Gdk.KEY_Page_Up,
            Gdk.KEY_Page_Down,
            Gdk.KEY_Home,
            Gdk.KEY_End,
            Gdk.KEY_KP_Up,
            Gdk.KEY_KP_Down,
            Gdk.KEY_KP_Page_Up,
            Gdk.KEY_KP_Page_Down,
            Gdk.KEY_KP_Home,
            Gdk.KEY_KP_End,
        }
    )

    def __init__(
        self,
        request_refresh: Callable[[str], None],
        set_volume: Callable[[int], None],
        toggle_volume_mute: Callable[[], None],
        set_brightness: Callable[[str, int], None],
        set_monitor_power: Callable[[tuple[str, ...], bool], None],
        set_application_volume: Callable[[tuple[int, ...], int], None],
        set_application_muted: Callable[[tuple[int, ...], bool], None],
        catalog: ApplicationCatalog | None = None,
        icon_provider: ApplicationIconProvider | None = None,
    ) -> None:
        self.request_refresh = request_refresh
        self.set_volume = set_volume
        self.toggle_volume_mute = toggle_volume_mute
        self.set_brightness = set_brightness
        self.set_monitor_power = set_monitor_power
        self.set_application_volume = set_application_volume
        self.set_application_muted = set_application_muted
        self.catalog = catalog
        self.icon_provider = icon_provider
        self._updating = False
        self._volume_timer = 0
        self._pending_volume = 0
        self._brightness_timers: dict[str, int] = {}
        self._pending_brightness: dict[str, tuple[tuple[str, ...], int]] = {}
        self._brightness_dragging: set[str] = set()
        self._brightness_drag_watchdogs: dict[str, int] = {}
        self._application_timers: dict[str, int] = {}
        self._pending_application_volume: dict[str, tuple[tuple[int, ...], int]] = {}
        self._brightness_connectors: tuple[str, ...] = ()
        self._brightness_values: dict[str, int] = {}
        self._brightness_powered: dict[str, bool | None] = {}
        self._desired_brightness: dict[str, int] = {}
        self.brightness_revision = 0
        self._volume_refresh_timer = 0
        self.brightness_scales: dict[str, Gtk.Scale] = {}
        self.brightness_power_buttons: dict[str, Gtk.Button] = {}
        self.application_scales: dict[str, Gtk.Scale] = {}
        self.application_mutes: dict[str, Gtk.ToggleButton] = {}
        self._application_signature: tuple[tuple[str, str, str, tuple[int, ...]], ...] | None = None

        self.brightness_button = self._menu_button("display-brightness-symbolic", "모니터 밝기")
        brightness_popover = Gtk.Popover()
        brightness_popover.add_css_class("luminophore-popover")
        brightness_popover.connect("notify::visible", self._popover_visibility_changed)
        brightness_root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._popover_margins(brightness_root, 410)
        brightness_title = Gtk.Label(label="모니터 밝기")
        brightness_title.add_css_class("section-title")
        brightness_title.add_css_class("luminophore-key-primary")
        brightness_title.set_xalign(0)
        brightness_root.append(brightness_title)
        brightness_mixer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        brightness_mixer.add_css_class("hardware-mixer")
        self.brightness_master_scale = self._vertical_scale(0)
        self.brightness_master_scale.set_sensitive(False)
        self.brightness_master_scale.connect("value-changed", self._master_brightness_changed)
        self._observe_brightness_release(self.brightness_master_scale, "__all__")
        self.brightness_master_power = self._power_button("전체 모니터 끄기")
        self.brightness_master_power.connect("clicked", self._master_power_clicked)
        brightness_mixer.append(
            self._channel(
                "전체",
                self.brightness_master_scale,
                self.brightness_master_power,
                master=True,
            )
        )
        brightness_mixer.append(self._separator())
        brightness_right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        brightness_right.set_hexpand(True)
        brightness_right.append(self._subheading("개별 모니터"))
        brightness_scroll = self._horizontal_scroll(270)
        self.brightness_channels = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.brightness_channels.append(self._status_label("불러오는 중"))
        brightness_scroll.set_child(self.brightness_channels)
        brightness_right.append(brightness_scroll)
        brightness_mixer.append(brightness_right)
        brightness_root.append(brightness_mixer)
        brightness_popover.set_child(brightness_root)
        self.brightness_button.set_popover(brightness_popover)

        self.volume_button = self._menu_button("audio-volume-high-symbolic", "음량")
        volume_popover = Gtk.Popover()
        self.volume_popover = volume_popover
        volume_popover.add_css_class("luminophore-popover")
        volume_popover.connect("notify::visible", self._popover_visibility_changed)
        volume_root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._popover_margins(volume_root, 540)
        volume_title = Gtk.Label(label="볼륨 믹서")
        volume_title.add_css_class("section-title")
        volume_title.add_css_class("luminophore-key-primary")
        volume_title.set_xalign(0)
        volume_root.append(volume_title)
        volume_mixer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        volume_mixer.add_css_class("hardware-mixer")
        self.volume_scale = self._vertical_scale(0)
        self.volume_scale.set_sensitive(False)
        self.volume_scale.connect("value-changed", self._volume_changed)
        self.volume_mute = Gtk.ToggleButton(icon_name="audio-volume-high-symbolic")
        self.volume_mute.add_css_class("luminophore-button")
        self.volume_mute.set_tooltip_text("전체 출력 음소거")
        self.volume_mute.connect("toggled", self._mute_toggled)
        attach_luminophore_state(self.volume_mute)
        volume_mixer.append(self._channel("전체 출력", self.volume_scale, self.volume_mute, master=True))
        volume_mixer.append(self._separator())
        volume_right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        volume_right.set_hexpand(True)
        volume_right.append(self._subheading("재생 중인 앱"))
        volume_scroll = self._horizontal_scroll(390)
        self.volume_channels = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.volume_channels.append(self._status_label("불러오는 중"))
        volume_scroll.set_child(self.volume_channels)
        volume_right.append(volume_scroll)
        volume_mixer.append(volume_right)
        volume_root.append(volume_mixer)
        self.volume_error = self._status_label("불러오는 중")
        volume_root.append(self.volume_error)
        volume_popover.set_child(volume_root)
        self.volume_button.set_popover(volume_popover)

    @staticmethod
    def _menu_button(icon_name: str, tooltip: str) -> Gtk.MenuButton:
        button = Gtk.MenuButton(icon_name=icon_name)
        button.add_css_class("luminophore-button")
        button.set_tooltip_text(tooltip)
        icon = button.get_child()
        if icon:
            icon.add_css_class("luminophore-symbol-primary")
        attach_luminophore_state(button)
        return button

    @staticmethod
    def _popover_margins(box: Gtk.Box, width: int) -> None:
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_size_request(width, -1)

    @staticmethod
    def _status_label(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.add_css_class("muted")
        label.set_xalign(0)
        return label

    @staticmethod
    def _vertical_scale(value: int) -> Gtk.Scale:
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, 0, 100, 1)
        scale.add_css_class("mixer-scale")
        scale.set_digits(0)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.TOP)
        scale.set_inverted(True)
        scale.set_vexpand(True)
        scale.set_size_request(54, 190)
        scale.set_value(value)
        return scale

    @staticmethod
    def _symbol(icon_name: str, role: str = "secondary") -> Gtk.Image:
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(22)
        icon.add_css_class(f"luminophore-symbol-{role}")
        return icon

    @staticmethod
    def _power_button(tooltip: str) -> Gtk.Button:
        button = Gtk.Button(icon_name="video-display-symbolic")
        button.add_css_class("luminophore-button")
        button.set_tooltip_text(tooltip)
        attach_luminophore_state(button)
        return button

    def _master_power_clicked(self, _button: Gtk.Button) -> None:
        known = [value for value in self._brightness_powered.values() if value is not None]
        if self._brightness_connectors:
            self.set_monitor_power(self._brightness_connectors, not (known and all(known)))

    def _monitor_power_clicked(self, _button: Gtk.Button, connector: str) -> None:
        self.set_monitor_power((connector,), self._brightness_powered.get(connector) is False)

    def _update_power_buttons(self) -> None:
        known = [value for value in self._brightness_powered.values() if value is not None]
        all_on = bool(known) and all(known)
        any_on = any(known)
        self.brightness_master_power.set_icon_name(
            "display-brightness-symbolic" if any_on else "video-display-symbolic"
        )
        self.brightness_master_power.set_tooltip_text("전체 모니터 끄기" if all_on else "전체 모니터 켜기")
        self.brightness_master_power.set_sensitive(bool(self._brightness_connectors))
        for connector, button in self.brightness_power_buttons.items():
            powered = self._brightness_powered.get(connector)
            button.set_icon_name("display-brightness-symbolic" if powered is not False else "video-display-symbolic")
            button.set_tooltip_text(f"{connector} 끄기" if powered is not False else f"{connector} 켜기")

    @staticmethod
    def _subheading(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text)
        label.add_css_class("mixer-subheading")
        label.add_css_class("muted")
        label.set_xalign(0)
        return label

    @staticmethod
    def _separator() -> Gtk.Separator:
        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        separator.add_css_class("mixer-separator")
        return separator

    @staticmethod
    def _horizontal_scroll(width: int) -> Gtk.ScrolledWindow:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        scroll.set_propagate_natural_width(True)
        scroll.set_max_content_width(width)
        scroll.set_min_content_width(min(180, width))
        return scroll

    @staticmethod
    def _channel(title: str, scale: Gtk.Scale, footer: Gtk.Widget, *, master: bool = False) -> Gtk.Box:
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        column.add_css_class("mixer-channel")
        if master:
            column.add_css_class("mixer-master")
        column.set_size_request(82 if master else 76, -1)
        label = Gtk.Label(label=title)
        label.add_css_class("mixer-channel-title")
        label.add_css_class("luminophore-key-primary" if master else "luminophore-key-secondary")
        label.set_justify(Gtk.Justification.CENTER)
        label.set_wrap(True)
        label.set_max_width_chars(11)
        label.set_tooltip_text(title)
        column.append(label)
        column.append(scale)
        footer.set_halign(Gtk.Align.CENTER)
        column.append(footer)
        return column

    def _popover_visibility_changed(self, popover: Gtk.Popover, _param: object) -> None:
        if popover.get_visible():
            self.request_refresh("audio" if popover is self.volume_popover else "brightness")
        if popover is self.volume_popover:
            if popover.get_visible() and not self._volume_refresh_timer:
                self._volume_refresh_timer = GLib.timeout_add_seconds(3, self._refresh_open_volume)
            elif not popover.get_visible() and self._volume_refresh_timer:
                GLib.source_remove(self._volume_refresh_timer)
                self._volume_refresh_timer = 0

    def _refresh_open_volume(self) -> bool:
        if not self.volume_popover.get_visible():
            self._volume_refresh_timer = 0
            return False
        if not self._volume_timer and not self._application_timers:
            self.request_refresh("audio")
        return True

    def _volume_changed(self, scale: Gtk.Scale) -> None:
        if self._updating:
            return
        self._pending_volume = round(scale.get_value())
        if not self._volume_timer:
            self._volume_timer = GLib.timeout_add(45, self._commit_volume)

    def _commit_volume(self) -> bool:
        self._volume_timer = 0
        self.set_volume(self._pending_volume)
        return False

    def _mute_toggled(self, _button: Gtk.ToggleButton) -> None:
        if not self._updating:
            self.toggle_volume_mute()

    def _master_brightness_changed(self, scale: Gtk.Scale) -> None:
        self._brightness_changed(scale, "__all__", self._brightness_connectors)

    def _observe_brightness_release(self, scale: Gtk.Scale, key: str) -> None:
        pointer = Gtk.EventControllerLegacy()
        pointer.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        pointer.connect("event", self._brightness_pointer_event, key)
        scale.add_controller(pointer)
        keyboard = Gtk.EventControllerKey()
        keyboard.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keyboard.connect("key-pressed", self._brightness_key_pressed, key)
        keyboard.connect("key-released", self._brightness_key_released, key)
        scale.add_controller(keyboard)

    def _brightness_pointer_event(
        self,
        _controller: Gtk.EventControllerLegacy,
        event: Gdk.Event | None,
        key: str,
    ) -> bool:
        if event is None:
            return False
        event_type = event.get_event_type()
        if event_type == Gdk.EventType.BUTTON_PRESS:
            self._brightness_drag_started(key)
        elif event_type == Gdk.EventType.BUTTON_RELEASE:
            self._brightness_drag_finished(key)
        return False

    def _brightness_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
        key: str,
    ) -> bool:
        if keyval in self.BRIGHTNESS_ADJUST_KEYS:
            self._brightness_drag_started(key)
        return False

    def _brightness_key_released(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
        key: str,
    ) -> None:
        if keyval in self.BRIGHTNESS_ADJUST_KEYS:
            self._brightness_drag_finished(key)

    def _brightness_drag_started(self, key: str) -> None:
        self._brightness_dragging.add(key)
        timer = self._brightness_timers.pop(key, 0)
        if timer:
            GLib.source_remove(timer)

    def _brightness_drag_finished(self, key: str) -> None:
        if key not in self._brightness_dragging:
            return
        self._brightness_dragging.discard(key)
        watchdog = getattr(self, "_brightness_drag_watchdogs", {}).pop(key, 0)
        if watchdog:
            GLib.source_remove(watchdog)
        GLib.idle_add(self._commit_brightness, key)

    def _brightness_drag_timed_out(self, key: str) -> bool:
        getattr(self, "_brightness_drag_watchdogs", {}).pop(key, None)
        self._brightness_dragging.discard(key)
        return self._commit_brightness(key)

    def _reset_brightness_drag_watchdog(self, key: str) -> None:
        watchdogs = getattr(self, "_brightness_drag_watchdogs", None)
        if watchdogs is None:
            watchdogs = {}
            self._brightness_drag_watchdogs = watchdogs
        previous = watchdogs.pop(key, 0)
        if previous:
            GLib.source_remove(previous)
        watchdogs[key] = GLib.timeout_add(
            self.BRIGHTNESS_DRAG_WATCHDOG_MS,
            self._brightness_drag_timed_out,
            key,
        )

    def _brightness_changed(self, scale: Gtk.Scale, key: str, connectors: tuple[str, ...]) -> None:
        if self._updating:
            return
        self.brightness_revision += 1
        value = round(scale.get_value())
        self._pending_brightness[key] = (connectors, value)
        for connector in connectors:
            self._desired_brightness[connector] = value
        if key in self._brightness_dragging:
            self._reset_brightness_drag_watchdog(key)
        elif key not in self._brightness_timers:
            self._brightness_timers[key] = GLib.timeout_add(65, self._commit_brightness, key)

    def _commit_brightness(self, key: str) -> bool:
        self._brightness_timers.pop(key, None)
        pending = self._pending_brightness.pop(key, None)
        if pending:
            connectors, value = pending
            for connector in connectors:
                self.set_brightness(connector, value)
        return False

    def _application_volume_changed(
        self,
        scale: Gtk.Scale,
        key: str,
        node_ids: tuple[int, ...],
    ) -> None:
        if self._updating:
            return
        self._pending_application_volume[key] = (node_ids, round(scale.get_value()))
        if key not in self._application_timers:
            self._application_timers[key] = GLib.timeout_add(45, self._commit_application_volume, key)

    def _commit_application_volume(self, key: str) -> bool:
        self._application_timers.pop(key, None)
        pending = self._pending_application_volume.pop(key, None)
        if pending:
            node_ids, value = pending
            self.set_application_volume(node_ids, value)
        return False

    def _application_mute_toggled(
        self,
        button: Gtk.ToggleButton,
        node_ids: tuple[int, ...],
    ) -> None:
        if not self._updating:
            self.set_application_muted(node_ids, button.get_active())

    def _application_button(self, application: AudioApplicationState) -> Gtk.ToggleButton:
        button = Gtk.ToggleButton()
        button.add_css_class("luminophore-button")
        button.set_active(application.muted)
        button.set_tooltip_text(f"{application.name} 음소거" if not application.muted else f"{application.name} 음소거 해제")
        app = None
        if self.catalog:
            app = self.catalog.match_window_class(application.icon_hint) or self.catalog.match_window_class(application.name)
        if app:
            button.set_child(
                app_icon(
                    app,
                    24,
                    provider=self.icon_provider,
                    catalog_revision=getattr(self.catalog, "revision", 0),
                )
            )
        else:
            icon = self._symbol(application.icon_hint or "application-x-executable-symbolic", "secondary")
            button.set_child(icon)
        button.connect("toggled", self._application_mute_toggled, application.node_ids)
        attach_luminophore_state(button)
        return button

    @staticmethod
    def _clear(box: Gtk.Box) -> None:
        child = box.get_first_child()
        while child:
            following = child.get_next_sibling()
            box.remove(child)
            child = following

    @staticmethod
    def _volume_icon(percent: int | None, muted: bool) -> str:
        if muted or percent == 0:
            return "audio-volume-muted-symbolic"
        if percent is not None and percent < 35:
            return "audio-volume-low-symbolic"
        if percent is not None and percent < 70:
            return "audio-volume-medium-symbolic"
        return "audio-volume-high-symbolic"

    @staticmethod
    def _range_is_active(scale: Gtk.Scale) -> bool:
        return bool(scale.get_state_flags() & Gtk.StateFlags.ACTIVE)

    def update_audio(self, state: HardwareQuickState) -> None:
        self._updating = True
        try:
            if state.volume_percent is None:
                self.volume_scale.set_sensitive(False)
                self.volume_error.set_label(state.audio_error or "WirePlumber 사용 불가")
            else:
                if not self._range_is_active(self.volume_scale) and not self._volume_timer:
                    self.volume_scale.set_value(state.volume_percent)
                self.volume_scale.set_sensitive(True)
                self.volume_error.set_label("")
            self.volume_mute.set_active(state.volume_muted)
            volume_icon = self._volume_icon(state.volume_percent, state.volume_muted)
            self.volume_mute.set_icon_name(volume_icon)
            self.volume_mute.set_tooltip_text("전체 출력 음소거 해제" if state.volume_muted else "전체 출력 음소거")
            self.volume_button.set_icon_name(volume_icon)

            signature = tuple(
                (application.key, application.name, application.icon_hint, application.node_ids)
                for application in state.applications
            )
            if signature != self._application_signature or not state.applications:
                self._clear(self.volume_channels)
                self.application_scales.clear()
                self.application_mutes.clear()
                for application in state.applications:
                    scale = self._vertical_scale(application.percent or 0)
                    scale.set_sensitive(application.percent is not None)
                    scale.set_tooltip_text(application.error or None)
                    scale.connect(
                        "value-changed",
                        self._application_volume_changed,
                        application.key,
                        application.node_ids,
                    )
                    mute = self._application_button(application)
                    self.application_scales[application.key] = scale
                    self.application_mutes[application.key] = mute
                    self.volume_channels.append(self._channel(application.name, scale, mute))
                self._application_signature = signature
            else:
                for application in state.applications:
                    scale = self.application_scales.get(application.key)
                    mute = self.application_mutes.get(application.key)
                    if scale:
                        if application.percent is not None and not self._range_is_active(scale) and application.key not in self._application_timers:
                            scale.set_value(application.percent)
                        scale.set_sensitive(application.percent is not None)
                        scale.set_tooltip_text(application.error or None)
                    if mute:
                        mute.set_active(application.muted)
                        mute.set_tooltip_text(
                            f"{application.name} 음소거 해제"
                            if application.muted
                            else f"{application.name} 음소거"
                        )
            if not state.applications:
                self.volume_channels.append(
                    self._status_label(state.applications_error or "현재 소리를 재생하는 앱이 없습니다")
                )
        finally:
            self._updating = False

    def update_brightness_state(
        self,
        state: HardwareQuickState,
        expected_revision: int | None = None,
    ) -> bool:
        if expected_revision is not None and expected_revision != self.brightness_revision:
            return False
        if self._desired_brightness:
            return False
        self._updating = True
        try:
            self._clear(self.brightness_channels)
            self.brightness_scales.clear()
            self.brightness_power_buttons.clear()
            self._brightness_values.clear()
            self._brightness_powered.clear()
            self._brightness_connectors = tuple(monitor.connector for monitor in state.monitors)
            valid_values = [monitor.percent for monitor in state.monitors if monitor.percent is not None]
            if valid_values:
                self.brightness_master_scale.set_value(round(sum(valid_values) / len(valid_values)))
            self.brightness_master_scale.set_sensitive(bool(state.monitors))
            for monitor in state.monitors:
                scale = self._vertical_scale(monitor.percent or 0)
                scale.set_sensitive(monitor.percent is not None and monitor.powered is not False)
                scale.set_tooltip_text(monitor.error or None)
                scale.connect("value-changed", self._brightness_changed, monitor.connector, (monitor.connector,))
                self._observe_brightness_release(scale, monitor.connector)
                self.brightness_scales[monitor.connector] = scale
                if monitor.percent is not None:
                    self._brightness_values[monitor.connector] = monitor.percent
                self._brightness_powered[monitor.connector] = monitor.powered
                footer = self._power_button(f"{monitor.connector} 전원 전환")
                footer.connect("clicked", self._monitor_power_clicked, monitor.connector)
                self.brightness_power_buttons[monitor.connector] = footer
                if monitor.error:
                    footer.set_tooltip_text(f"DDC/CI 읽기 실패: {monitor.error}")
                self.brightness_channels.append(self._channel(monitor.connector, scale, footer))
            if not state.monitors:
                self.brightness_channels.append(self._status_label("DDC/CI 모니터 없음"))
            self._update_power_buttons()
        finally:
            self._updating = False
        return True

    def update_monitor_power(self, connector: str, powered: bool) -> None:
        self._brightness_powered[connector] = powered
        scale = self.brightness_scales.get(connector)
        if scale:
            scale.set_sensitive(powered and connector in self._brightness_values)
        self._update_power_buttons()

    def reject_brightness(self, connector: str) -> None:
        self._desired_brightness.pop(connector, None)

    def preview_brightness(self, connector: str, percent: int) -> None:
        scale = self.brightness_scales.get(connector)
        if not scale:
            return
        self._desired_brightness[connector] = percent
        self._updating = True
        try:
            scale.set_value(percent)
            self._brightness_values[connector] = percent
            if self._brightness_values:
                self.brightness_master_scale.set_value(
                    round(sum(self._brightness_values.values()) / len(self._brightness_values))
                )
        finally:
            self._updating = False

    def update(self, state: HardwareQuickState) -> None:
        self.update_audio(state)
        self.update_brightness_state(state)

    def update_volume(self, percent: int, muted: bool) -> None:
        self._updating = True
        try:
            self.volume_scale.set_value(percent)
            self.volume_scale.set_sensitive(True)
            self.volume_mute.set_active(muted)
            volume_icon = self._volume_icon(percent, muted)
            self.volume_mute.set_icon_name(volume_icon)
            self.volume_mute.set_tooltip_text("전체 출력 음소거 해제" if muted else "전체 출력 음소거")
            self.volume_button.set_icon_name(volume_icon)
            self.volume_error.set_label("")
        finally:
            self._updating = False

    @staticmethod
    def _settled_master_brightness(
        values: dict[str, int],
        pending: dict[str, int],
    ) -> int | None:
        if pending or not values:
            return None
        return round(sum(values.values()) / len(values))

    def update_brightness(self, connector: str, percent: int) -> None:
        scale = self.brightness_scales.get(connector)
        if not scale:
            return
        desired = self._desired_brightness.get(connector)
        if desired is not None and desired != percent:
            return
        self._updating = True
        try:
            self._desired_brightness.pop(connector, None)
            scale.set_value(percent)
            self._brightness_values[connector] = percent
            master_value = self._settled_master_brightness(
                self._brightness_values,
                self._desired_brightness,
            )
            if master_value is not None:
                self.brightness_master_scale.set_value(master_value)
        finally:
            self._updating = False


def _notification_image(
    notification: Notification,
    catalog: ApplicationCatalog | None = None,
    icon_provider: ApplicationIconProvider | None = None,
) -> Gtk.Widget | None:
    raw = notification.hints.get("image-data") or notification.hints.get("image_data") or notification.hints.get("icon_data")
    if isinstance(raw, (tuple, list)) and len(raw) >= 7:
        try:
            width, height, rowstride, alpha, bits, _channels, data = raw[:7]
            pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(bytes(data)), GdkPixbuf.Colorspace.RGB, bool(alpha), int(bits),
                int(width), int(height), int(rowstride),
            )
            image = Gtk.Image.new_from_pixbuf(pixbuf)
            image.set_pixel_size(52)
            return image
        except (TypeError, ValueError, GLib.Error):
            pass
    image_path = str(notification.hints.get("image-path", ""))
    if not image_path and catalog and icon_provider and notification.desktop_entry:
        app = catalog.match_desktop_id(notification.desktop_entry)
        if app:
            return app_icon(
                app,
                52,
                provider=icon_provider,
                catalog_revision=getattr(catalog, "revision", 0),
            )
    value = image_path or notification.app_icon
    if not value:
        return None
    if value.startswith("file://"):
        value = value[7:]
    image = Gtk.Image.new_from_file(value) if value.startswith("/") else Gtk.Image.new_from_icon_name(value)
    image.set_pixel_size(52)
    return image


class NotificationView:
    def __init__(
        self,
        manager: NotificationManager,
        open_history: Callable[[], None],
        open_power: Callable[[], None],
        lock: Callable[[], None],
        suspend: Callable[[], None],
        logout: Callable[[], None],
        restart: Callable[[], None],
        shutdown: Callable[[], None],
        firmware_setup: Callable[[], None],
        tray_host: StatusNotifierHost,
        refresh_hardware: Callable[[str], None],
        set_volume: Callable[[int], None],
        toggle_volume_mute: Callable[[], None],
        set_brightness: Callable[[str, int], None],
        set_monitor_power: Callable[[tuple[str, ...], bool], None],
        set_application_volume: Callable[[tuple[int, ...], int], None],
        set_application_muted: Callable[[tuple[int, ...], bool], None],
        catalog: ApplicationCatalog | None = None,
        icon_provider: ApplicationIconProvider | None = None,
    ) -> None:
        self.manager = manager
        self.tray_host = tray_host
        self.collapsed = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.collapsed.append(icon_button("notifications-symbolic", "알림", open_history))
        self.count = Gtk.Label(label="0")
        self.count.add_css_class("numeric")
        self.count.add_css_class("luminophore-key-secondary")
        self.collapsed.append(self.count)
        self.dots = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        self.collapsed.append(self.dots)
        self.tray = Gtk.MenuButton(icon_name="pan-down-symbolic")
        self.tray.add_css_class("luminophore-button")
        self.tray_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.tray_box.set_margin_top(8)
        self.tray_box.set_margin_bottom(8)
        self.tray_box.set_margin_start(8)
        self.tray_box.set_margin_end(8)
        tray_popover = Gtk.Popover()
        tray_popover.set_child(self.tray_box)
        self.tray.set_popover(tray_popover)
        self.collapsed.append(self.tray)
        self.hardware = HardwareQuickControlsView(
            refresh_hardware,
            set_volume,
            toggle_volume_mute,
            set_brightness,
            set_monitor_power,
            set_application_volume,
            set_application_muted,
            catalog,
            icon_provider,
        )
        self.collapsed.append(self.hardware.brightness_button)
        self.collapsed.append(self.hardware.volume_button)
        self.collapsed.append(icon_button("system-shutdown-symbolic", "전원", open_power))

        self.expanded = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120)
        self.expanded.set_hhomogeneous(False)
        self.expanded.set_vhomogeneous(False)
        self.history_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="알림")
        title.add_css_class("section-title")
        title.add_css_class("luminophore-key-primary")
        title.set_hexpand(True)
        title.set_xalign(0)
        self.dnd = Gtk.ToggleButton(label="방해 금지")
        self.dnd.add_css_class("luminophore-button")
        self.dnd.set_active(manager.dnd)
        self.dnd.connect("toggled", lambda button: manager.set_dnd(button.get_active()))
        attach_luminophore_state(self.dnd)
        clear = Gtk.Button(label="모두 지우기")
        clear.add_css_class("luminophore-button")
        clear.connect("clicked", lambda _button: manager.clear_all())
        attach_luminophore_state(clear)
        header.append(title)
        header.append(self.dnd)
        header.append(clear)
        self.history_box.append(header)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        scroll.set_propagate_natural_height(True)
        scroll.set_max_content_height(680)
        self.history_rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll.set_child(self.history_rows)
        self.history_box.append(scroll)
        self.history_box.set_size_request(CONTROL_PANEL_WIDTH, -1)
        self.expanded.add_named(self.history_box, "history")
        power = power_expanded(lock, suspend, logout, restart, shutdown, firmware_setup)
        self.expanded.add_named(power, "power")
        self.update()

    def show_history(self) -> None:
        self.expanded.set_visible_child_name("history")
        self.manager.mark_all_read()

    def show_power(self) -> None:
        self.expanded.set_visible_child_name("power")

    def update_hardware(self, state: HardwareQuickState) -> None:
        self.hardware.update(state)

    def update_audio_hardware(self, state: HardwareQuickState) -> None:
        self.hardware.update_audio(state)

    def update_brightness_hardware(
        self,
        state: HardwareQuickState,
        expected_revision: int | None = None,
    ) -> bool:
        return self.hardware.update_brightness_state(state, expected_revision)

    def update(self) -> None:
        self.manager.prune_history(emit_changed=False)
        unread = [item for item in self.manager.history if item.unread]
        self.count.set_label(str(len(unread)))
        self.count.set_visible(bool(unread))
        child = self.dots.get_first_child()
        while child:
            following = child.get_next_sibling()
            self.dots.remove(child)
            child = following
        keys: list[str] = []
        for item in unread:
            if item.app_key not in keys:
                keys.append(item.app_key)
        for _key in keys[: self.manager.config.dot_limit]:
            dot = Gtk.Label(label="●")
            dot.add_css_class("luminophore-key-secondary")
            self.dots.append(dot)
        if len(keys) > self.manager.config.dot_limit:
            self.dots.append(Gtk.Label(label=f"+{len(keys) - self.manager.config.dot_limit}"))
        self.dots.set_visible(bool(keys))
        child = self.history_rows.get_first_child()
        while child:
            following = child.get_next_sibling()
            self.history_rows.remove(child)
            child = following
        groups: dict[str, list[Notification]] = defaultdict(list)
        for item in self.manager.history:
            groups[item.app_key].append(item)
        for key, items in groups.items():
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            group.add_css_class("notification-group")
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            label = Gtk.Label(label=items[0].app_name or key)
            label.add_css_class("section-title")
            label.add_css_class("luminophore-key-primary")
            label.set_hexpand(True)
            label.set_xalign(0)
            clear = Gtk.Button(icon_name="window-close-symbolic")
            clear.add_css_class("luminophore-button")
            clear_icon = clear.get_child()
            if clear_icon:
                clear_icon.add_css_class("luminophore-symbol-secondary")
            clear.connect("clicked", lambda _button, app_key=key: self.manager.clear_group(app_key))
            attach_luminophore_state(clear)
            header.append(label)
            header.append(clear)
            group.append(header)
            for item in items:
                notification_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                notification_row.set_margin_bottom(2)
                summary = Gtk.Label(label=item.summary)
                summary.set_xalign(0)
                summary.set_wrap(True)
                notification_row.append(summary)
                if item.body:
                    body = Gtk.Label(label=item.body)
                    body.add_css_class("muted")
                    body.set_xalign(0)
                    body.set_wrap(True)
                    notification_row.append(body)
                group.append(notification_row)
            self.history_rows.append(group)
        if not groups:
            empty = Gtk.Label(label="놓친 알림이 없습니다")
            empty.add_css_class("muted")
            self.history_rows.append(empty)
        self.update_tray()

    def update_tray(self) -> None:
        child = self.tray_box.get_first_child()
        while child:
            following = child.get_next_sibling()
            self.tray_box.remove(child)
            child = following
        for item in self.tray_host.items:
            button = Gtk.Button()
            button.add_css_class("luminophore-button")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            icon = Gtk.Image.new_from_icon_name(item.icon_name or "application-x-executable-symbolic")
            icon.set_pixel_size(20)
            label = Gtk.Label(label=item.title or item.service)
            label.set_xalign(0)
            row.append(icon)
            row.append(label)
            button.set_child(row)
            button.connect("clicked", lambda clicked, selected=item: self._activate_tray_item(clicked, selected))
            secondary = Gtk.GestureClick(button=3)
            secondary.connect("released", lambda *_args, selected=item: self.tray_host.context_menu(selected))
            button.add_controller(secondary)
            self.tray_box.append(button)
        if not self.tray_host.items:
            empty = Gtk.Label(label="StatusNotifier 항목 없음")
            empty.add_css_class("muted")
            self.tray_box.append(empty)

    def _activate_tray_item(self, button: Gtk.Button, item: TrayItem) -> None:
        if not item.item_is_menu:
            self.tray_host.activate(item)
            return
        entries = self.tray_host.menu_entries(item)
        if not entries:
            return
        popover = Gtk.Popover()
        popover.add_css_class("luminophore-popover")
        popover.set_parent(button)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.set_margin_top(7)
        box.set_margin_bottom(7)
        box.set_margin_start(7)
        box.set_margin_end(7)
        self._append_tray_menu_entries(box, popover, item, entries)
        popover.set_child(box)
        popover.connect("closed", lambda widget: widget.unparent())
        popover.popup()

    def _append_tray_menu_entries(
        self,
        box: Gtk.Box,
        popover: Gtk.Popover,
        item: TrayItem,
        entries,
        depth: int = 0,
    ) -> None:
        for entry in entries:
            if not entry.visible:
                continue
            if entry.separator:
                box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
                continue
            if entry.children:
                heading = Gtk.Label(label=f"{'  ' * depth}{entry.label}", xalign=0)
                heading.add_css_class("muted")
                box.append(heading)
                self._append_tray_menu_entries(box, popover, item, entry.children, depth + 1)
                continue
            prefix = "✓ " if entry.toggle_state > 0 else ""
            action = Gtk.Button(label=f"{'  ' * depth}{prefix}{entry.label}")
            action.add_css_class("luminophore-button")
            action.set_sensitive(entry.enabled)
            action.connect(
                "clicked",
                lambda _button, item_id=entry.item_id: (
                    self.tray_host.activate_menu_entry(item, item_id),
                    popover.popdown(),
                ),
            )
            attach_luminophore_state(action)
            box.append(action)


class ToastLayer:
    def __init__(
        self,
        application: Gtk.Application,
        monitor: Gdk.Monitor,
        config: NotificationConfig,
        manager: NotificationManager,
        palette_index: int = 0,
        catalog: ApplicationCatalog | None = None,
        icon_provider: ApplicationIconProvider | None = None,
    ) -> None:
        self.config = config
        self.manager = manager
        self.catalog = catalog
        self.icon_provider = icon_provider
        self.cards: dict[int, Gtk.Widget] = {}
        self.timers: dict[int, int] = {}
        self.deadlines: dict[int, float] = {}
        self.remaining: dict[int, int] = {}
        self.window = Gtk.ApplicationWindow(application=application)
        self.window.set_decorated(False)
        self.window.add_css_class("luminophore-surface")
        self.window.add_css_class(f"palette-{palette_index}")
        Gtk4LayerShell.init_for_window(self.window)
        Gtk4LayerShell.set_namespace(self.window, "luminophore-shell-toasts")
        Gtk4LayerShell.set_monitor(self.window, monitor)
        Gtk4LayerShell.set_layer(self.window, Gtk4LayerShell.Layer.OVERLAY)
        Gtk4LayerShell.set_anchor(self.window, Gtk4LayerShell.Edge.TOP, True)
        Gtk4LayerShell.set_anchor(self.window, Gtk4LayerShell.Edge.RIGHT, True)
        Gtk4LayerShell.set_margin(self.window, Gtk4LayerShell.Edge.TOP, 80)
        Gtk4LayerShell.set_margin(self.window, Gtk4LayerShell.Edge.RIGHT, 24)
        Gtk4LayerShell.set_exclusive_zone(self.window, 0)
        Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.NONE)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.box.set_size_request(380, -1)
        self.window.set_child(self.box)

    def show(self, notification: Notification) -> None:
        if notification.id in self.cards:
            self._remove_card(notification.id)
        while len(self.cards) >= self.config.toast_limit:
            oldest = next(iter(self.cards))
            self._expire(oldest)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        card.add_css_class("luminophore-panel")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        app = Gtk.Label(label=notification.app_name)
        app.add_css_class("muted")
        app.set_hexpand(True)
        app.set_xalign(0)
        close = Gtk.Button(icon_name="window-close-symbolic")
        close.add_css_class("luminophore-button")
        close_icon = close.get_child()
        if close_icon:
            close_icon.add_css_class("luminophore-symbol-secondary")
        close.connect("clicked", lambda _button: self._dismiss(notification.id))
        attach_luminophore_state(close)
        header.append(app)
        header.append(close)
        card.append(header)
        image = _notification_image(notification, self.catalog, self.icon_provider)
        if image:
            image.set_halign(Gtk.Align.START)
            card.append(image)
        summary = Gtk.Label(label=notification.summary)
        summary.set_xalign(0)
        summary.set_wrap(True)
        card.append(summary)
        if notification.body:
            body = Gtk.Label(label=notification.body)
            body.add_css_class("muted")
            body.set_xalign(0)
            body.set_wrap(True)
            card.append(body)
        if notification.actions:
            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            for key, label in notification.actions:
                button = Gtk.Button(label=label)
                button.add_css_class("luminophore-button")
                button.connect("clicked", lambda _button, action=key: self._invoke(notification.id, action))
                attach_luminophore_state(button)
                actions.append(button)
            card.append(actions)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_args: self._pause(notification.id))
        motion.connect("leave", lambda *_args: self._resume(notification.id))
        card.add_controller(motion)
        self.cards[notification.id] = card
        self.box.append(card)
        self.window.present()
        duration = notification.expire_timeout
        if duration < 0:
            duration = self.config.default_timeout_ms
        if duration > 0 and not notification.critical:
            self._start_timer(notification.id, duration)

    def _start_timer(self, notification_id: int, duration: int) -> None:
        self.deadlines[notification_id] = time.monotonic() + duration / 1000
        self.timers[notification_id] = GLib.timeout_add(duration, self._expire, notification_id)

    def _pause(self, notification_id: int) -> None:
        source = self.timers.pop(notification_id, 0)
        if source:
            GLib.source_remove(source)
            self.remaining[notification_id] = max(1, round((self.deadlines.get(notification_id, time.monotonic()) - time.monotonic()) * 1000))

    def _resume(self, notification_id: int) -> None:
        duration = self.remaining.pop(notification_id, 0)
        if duration and notification_id in self.cards:
            self._start_timer(notification_id, duration)

    def _expire(self, notification_id: int) -> bool:
        self._remove_card(notification_id)
        self.manager.popup_expired(notification_id)
        return False

    def _dismiss(self, notification_id: int) -> None:
        self._remove_card(notification_id)
        self.manager.dismiss(notification_id)

    def _invoke(self, notification_id: int, action: str) -> None:
        self._remove_card(notification_id)
        self.manager.invoke(notification_id, action)

    def _remove_card(self, notification_id: int) -> None:
        source = self.timers.pop(notification_id, 0)
        if source:
            GLib.source_remove(source)
        self.deadlines.pop(notification_id, None)
        self.remaining.pop(notification_id, None)
        card = self.cards.pop(notification_id, None)
        if card:
            self.box.remove(card)
        if not self.cards:
            self.window.set_visible(False)

    def remove(self, notification_id: int) -> None:
        """Remove a toast presentation without closing the notification."""
        self._remove_card(notification_id)

    def stop(self) -> None:
        for source in self.timers.values():
            GLib.source_remove(source)
        self.timers.clear()
        self.deadlines.clear()
        self.remaining.clear()

    def destroy(self) -> None:
        self.stop()
        self.window.destroy()
