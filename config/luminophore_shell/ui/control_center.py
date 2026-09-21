from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..system_controls import BluetoothDevice, PairingDecision, PairingPrompt, SystemControlSnapshot, WifiNetwork
from ..location import CityResult, LocationSnapshot
from ..calendar import CalendarSnapshot
from ..privacy import PrivacySnapshot
from ..power_profiles import PROFILE_LABELS
from .effects import attach_luminophore_state


class ControlCenterView:
    def __init__(
        self,
        refresh: Callable[[], None],
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
        back: Callable[[], None],
    ) -> None:
        def refresh_all() -> None:
            refresh()
            resolve_location()
            refresh_calendar()

        self.refresh = refresh_all
        self.set_wifi = set_wifi
        self.connect_wifi = connect_wifi
        self.set_bluetooth = set_bluetooth
        self.connect_bluetooth = connect_bluetooth
        self.pair_bluetooth = pair_bluetooth
        self.set_power_profile = set_power_profile
        self.resolve_location = resolve_location
        self.search_city = search_city
        self.select_city = select_city
        self.connect_calendar = connect_calendar
        self.refresh_calendar = refresh_calendar
        self.disconnect_calendar = disconnect_calendar
        self.updating = False
        self.power_profiles: tuple[str, ...] = ()
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        back_button = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        back_button.add_css_class("luminophore-button")
        back_button.connect("clicked", lambda _button: back())
        attach_luminophore_state(back_button)
        title = Gtk.Label(label="제어센터")
        title.add_css_class("section-title")
        title.set_hexpand(True)
        title.set_xalign(0)
        refresh_button = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh_button.add_css_class("luminophore-button")
        refresh_button.connect("clicked", lambda _button: self.refresh())
        attach_luminophore_state(refresh_button)
        header.append(back_button)
        header.append(title)
        header.append(refresh_button)
        self.widget.append(header)

        self.wifi = Gtk.Switch()
        self.bluetooth = Gtk.Switch()
        self.wifi.set_sensitive(False)
        self.bluetooth.set_sensitive(False)
        self.wifi.connect("notify::active", lambda switch, _param: self._toggle_wifi(switch.get_active()))
        self.bluetooth.connect("notify::active", lambda switch, _param: self._toggle_bluetooth(switch.get_active()))
        self.wifi_row = self._switch_row("Wi-Fi", self.wifi)
        self.widget.append(self.wifi_row)
        self.wifi_status = Gtk.Label(label="확인 중")
        self.wifi_status.set_xalign(0)
        self.wifi_status.add_css_class("muted")
        self.widget.append(self.wifi_status)
        self.networks = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.widget.append(self.networks)
        self.bluetooth_status = Gtk.Label(label="확인 중")
        self.bluetooth_status.set_xalign(0)
        self.bluetooth_status.add_css_class("muted")
        self.widget.append(self._switch_row("Bluetooth", self.bluetooth))
        self.widget.append(self.bluetooth_status)
        self.devices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.widget.append(self.devices)

        profile_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        profile_label = Gtk.Label(label="전원 프로필")
        profile_label.set_hexpand(True)
        profile_label.set_xalign(0)
        self.profile = Gtk.DropDown.new_from_strings([])
        self.profile.set_sensitive(False)
        self.profile.connect("notify::selected", self._profile_selected)
        profile_row.append(profile_label)
        profile_row.append(self.profile)
        self.widget.append(profile_row)
        privacy_title = Gtk.Label(label="개인정보 사용")
        privacy_title.set_xalign(0)
        self.widget.append(privacy_title)
        self.privacy_status = Gtk.Label(label="확인 중")
        self.privacy_status.add_css_class("muted")
        self.privacy_status.set_xalign(0)
        self.privacy_status.set_wrap(True)
        self.widget.append(self.privacy_status)
        location_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        location_title = Gtk.Label(label="위치")
        location_title.set_hexpand(True)
        location_title.set_xalign(0)
        locate = Gtk.Button(label="현재 위치 확인")
        locate.add_css_class("luminophore-button")
        locate.connect("clicked", lambda _button: resolve_location())
        attach_luminophore_state(locate)
        location_header.append(location_title)
        location_header.append(locate)
        self.widget.append(location_header)
        self.location_status = Gtk.Label(label="위치 확인 전")
        self.location_status.add_css_class("muted")
        self.location_status.set_xalign(0)
        self.widget.append(self.location_status)
        city_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.city_query = Gtk.SearchEntry(placeholder_text="도시 검색")
        self.city_query.set_hexpand(True)
        self.city_query.connect("activate", lambda entry: search_city(entry.get_text()))
        city_button = Gtk.Button(label="검색")
        city_button.add_css_class("luminophore-button")
        city_button.connect("clicked", lambda _button: search_city(self.city_query.get_text()))
        attach_luminophore_state(city_button)
        city_row.append(self.city_query)
        city_row.append(city_button)
        self.widget.append(city_row)
        self.city_results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.widget.append(self.city_results)
        calendar_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        calendar_title = Gtk.Label(label="Google Calendar")
        calendar_title.set_hexpand(True)
        calendar_title.set_xalign(0)
        connect_calendar_button = Gtk.Button(label="연결")
        connect_calendar_button.add_css_class("luminophore-button")
        connect_calendar_button.connect("clicked", lambda _button: connect_calendar())
        refresh_calendar_button = Gtk.Button(label="동기화")
        refresh_calendar_button.add_css_class("luminophore-button")
        refresh_calendar_button.connect("clicked", lambda _button: refresh_calendar())
        disconnect_calendar_button = Gtk.Button(label="연결 해제")
        disconnect_calendar_button.add_css_class("luminophore-button")
        disconnect_calendar_button.connect("clicked", lambda _button: disconnect_calendar())
        for button in (connect_calendar_button, refresh_calendar_button, disconnect_calendar_button):
            attach_luminophore_state(button)
        calendar_header.append(calendar_title)
        calendar_header.append(connect_calendar_button)
        calendar_header.append(refresh_calendar_button)
        calendar_header.append(disconnect_calendar_button)
        self.widget.append(calendar_header)
        self.calendar_status = Gtk.Label(label="연결되지 않음")
        self.calendar_status.add_css_class("muted")
        self.calendar_status.set_xalign(0)
        self.widget.append(self.calendar_status)
        self.calendar_events = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.widget.append(self.calendar_events)
        self.error = Gtk.Label(label="")
        self.error.add_css_class("muted")
        self.error.set_xalign(0)
        self.error.set_wrap(True)
        self.widget.append(self.error)

    @staticmethod
    def _switch_row(label: str, control: Gtk.Switch) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label=label)
        title.set_hexpand(True)
        title.set_xalign(0)
        row.append(title)
        row.append(control)
        return row

    def _toggle_wifi(self, enabled: bool) -> None:
        if not self.updating:
            self.set_wifi(enabled)

    def _toggle_bluetooth(self, enabled: bool) -> None:
        if not self.updating:
            self.set_bluetooth(enabled)

    def _request_wifi(self, network: WifiNetwork) -> None:
        if network.security.strip() in {"", "--", "NONE"}:
            self.connect_wifi(network, "")
            return
        dialog = Gtk.Dialog(modal=True, title=f"{network.ssid} 연결")
        dialog.add_buttons("취소", Gtk.ResponseType.CANCEL, "연결", Gtk.ResponseType.ACCEPT)
        entry = Gtk.PasswordEntry()
        entry.set_show_peek_icon(True)
        entry.set_placeholder_text("Wi-Fi 비밀번호")
        entry.set_margin_top(12)
        entry.set_margin_bottom(12)
        entry.set_margin_start(12)
        entry.set_margin_end(12)
        dialog.get_content_area().append(entry)

        def response(_dialog: Gtk.Dialog, response_id: int) -> None:
            password = entry.get_text()
            entry.set_text("")
            dialog.destroy()
            if response_id == Gtk.ResponseType.ACCEPT and password:
                self.connect_wifi(network, password)

        dialog.connect("response", response)
        dialog.present()

    def _profile_selected(self, dropdown: Gtk.DropDown, _param: object) -> None:
        if self.updating or not self.power_profiles:
            return
        index = dropdown.get_selected()
        if index < len(self.power_profiles):
            self.profile.set_sensitive(False)
            self.set_power_profile(self.power_profiles[index], self.power_profiles)

    def show_pairing_prompt(self, prompt: PairingPrompt, resolve: Callable[[int, PairingDecision], None]) -> None:
        title = "Bluetooth 페어링"
        dialog = Gtk.Dialog(modal=True, title=title)
        dialog.add_buttons("거부", Gtk.ResponseType.REJECT, "허용", Gtk.ResponseType.ACCEPT)
        box = dialog.get_content_area()
        message = Gtk.Label(label=f"{prompt.name}\n{prompt.passkey}" if prompt.passkey else prompt.name)
        message.set_margin_top(12)
        message.set_margin_bottom(8)
        box.append(message)
        pin_entry = Gtk.PasswordEntry()
        if prompt.kind in {"pin", "passkey"} and not prompt.passkey:
            pin_entry.set_placeholder_text("PIN")
            pin_entry.set_margin_start(12)
            pin_entry.set_margin_end(12)
            pin_entry.set_margin_bottom(12)
            box.append(pin_entry)

        def response(_dialog: Gtk.Dialog, response_id: int) -> None:
            pin = pin_entry.get_text()
            pin_entry.set_text("")
            dialog.destroy()
            resolve(prompt.request_id, PairingDecision(response_id == Gtk.ResponseType.ACCEPT, pin))

        dialog.connect("response", response)
        dialog.present()

    def update(self, snapshot: SystemControlSnapshot) -> None:
        self.updating = True
        try:
            self.wifi.set_sensitive(snapshot.network.available)
            self.wifi.set_active(snapshot.network.wifi_enabled)
            self.wifi_row.set_visible(snapshot.network.wifi_available)
            connected = tuple(device for device in snapshot.network.devices if device.connected)
            if not snapshot.network.available:
                network_label = "NetworkManager 사용 불가"
            elif connected:
                labels = []
                for device in connected:
                    kind = {"ethernet": "유선", "wifi": "Wi-Fi"}.get(device.kind, device.kind)
                    name = device.connection if device.connection not in {"", "--"} else device.interface
                    labels.append(f"{kind}: {name} ({device.interface})")
                network_label = " · ".join(labels) + f" · {snapshot.network.connectivity}"
            else:
                network_label = f"연결 안 됨 · {snapshot.network.connectivity}"
            self.wifi_status.set_label(network_label)
            child = self.networks.get_first_child()
            while child:
                following = child.get_next_sibling()
                self.networks.remove(child)
                child = following
            for network in snapshot.network.networks:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                label = Gtk.Label(label=f"{network.ssid} · {network.signal}% · {network.security or 'Open'}")
                label.set_hexpand(True)
                label.set_xalign(0)
                row.append(label)
                if not network.active:
                    button = Gtk.Button(label="연결")
                    button.add_css_class("luminophore-button")
                    button.connect("clicked", lambda _button, item=network: self._request_wifi(item))
                    attach_luminophore_state(button)
                    row.append(button)
                self.networks.append(row)
            self.networks.set_visible(snapshot.network.wifi_available)
            self.bluetooth.set_sensitive(snapshot.bluetooth.available)
            self.bluetooth.set_active(snapshot.bluetooth.powered)
            connected = sum(1 for item in snapshot.bluetooth.devices if item.connected)
            self.bluetooth_status.set_label(
                f"연결 {connected}개" if snapshot.bluetooth.available else "BlueZ 사용 불가"
            )
            child = self.devices.get_first_child()
            while child:
                following = child.get_next_sibling()
                self.devices.remove(child)
                child = following
            for device in snapshot.bluetooth.devices:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                label = Gtk.Label(label=f"{device.name} · {'연결됨' if device.connected else '페어링됨' if device.paired else '페어링 필요'}")
                label.set_hexpand(True)
                label.set_xalign(0)
                row.append(label)
                if device.paired and not device.connected:
                    button = Gtk.Button(label="연결")
                    button.add_css_class("luminophore-button")
                    button.connect("clicked", lambda _button, item=device: self.connect_bluetooth(item))
                    attach_luminophore_state(button)
                    row.append(button)
                elif not device.paired:
                    button = Gtk.Button(label="페어링")
                    button.add_css_class("luminophore-button")
                    button.connect("clicked", lambda _button, item=device: self.pair_bluetooth(item))
                    attach_luminophore_state(button)
                    row.append(button)
                self.devices.append(row)
            self.power_profiles = snapshot.power.profiles
            self.profile.set_model(Gtk.StringList.new([PROFILE_LABELS.get(item, item) for item in self.power_profiles]))
            if snapshot.power.active in self.power_profiles:
                self.profile.set_selected(self.power_profiles.index(snapshot.power.active))
            self.profile.set_sensitive(snapshot.power.available)
            errors = tuple(filter(None, (snapshot.network.error, snapshot.bluetooth.error, snapshot.power.error)))
            self.error.set_label(" · ".join(errors))
        finally:
            self.updating = False

    def update_location(self, snapshot: LocationSnapshot, error: str = "") -> None:
        accuracy = f" · ±{snapshot.accuracy_m:.0f}m" if snapshot.accuracy_m is not None else ""
        fallback = f" · 자동 위치 실패: {error}" if error and snapshot.source == "manual" else ""
        self.location_status.set_label(
            f"{snapshot.source} · {snapshot.latitude:.4f}, {snapshot.longitude:.4f} · {snapshot.timezone}{accuracy}{fallback}"
        )

    def update_city_results(self, results: tuple[CityResult, ...], error: str = "") -> None:
        child = self.city_results.get_first_child()
        while child:
            following = child.get_next_sibling()
            self.city_results.remove(child)
            child = following
        if error:
            label = Gtk.Label(label=error)
            label.add_css_class("muted")
            label.set_xalign(0)
            self.city_results.append(label)
            return
        for city in results:
            button = Gtk.Button(label=f"{city.name}, {city.country} · {city.timezone}")
            button.add_css_class("luminophore-button")
            button.connect("clicked", lambda _button, selected=city: self.select_city(selected))
            attach_luminophore_state(button)
            self.city_results.append(button)
        if not results:
            label = Gtk.Label(label="검색 결과가 없습니다")
            label.add_css_class("muted")
            label.set_xalign(0)
            self.city_results.append(label)

    def update_calendar(self, snapshot: CalendarSnapshot | None, error: str = "") -> None:
        child = self.calendar_events.get_first_child()
        while child:
            following = child.get_next_sibling()
            self.calendar_events.remove(child)
            child = following
        if snapshot is None:
            self.calendar_status.set_label(error or "연결되지 않음")
            return
        self.calendar_status.set_label(
            f"{'오프라인 캐시' if snapshot.stale else '동기화됨'} · {len(snapshot.events)}개"
            + (f" · {snapshot.error}" if snapshot.error else "")
        )
        for event in snapshot.events[:10]:
            label = Gtk.Label(label=f"{event.start} · {event.summary}")
            label.set_xalign(0)
            label.set_ellipsize(3)
            self.calendar_events.append(label)

    def update_privacy(self, snapshot: PrivacySnapshot) -> None:
        if not snapshot.available:
            self.privacy_status.set_label(f"PipeWire 상태 사용 불가 · {snapshot.error}")
            return
        if not snapshot.sessions:
            self.privacy_status.set_label("카메라·마이크·화면 공유 사용 없음")
            return
        names = {"camera": "카메라", "microphone": "마이크", "screen": "화면 공유"}
        self.privacy_status.set_label(" · ".join(
            f"{names.get(item.kind, item.kind)}: {item.application}" for item in snapshot.sessions
        ))
