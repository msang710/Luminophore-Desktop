from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from .dimensions import CONTROL_PANEL_HEIGHT, CONTROL_PANEL_WIDTH
from .effects import attach_luminophore_state


def icon_button(icon: str, tooltip: str, callback: Callable[[], None], role: str = "primary") -> Gtk.Button:
    button = Gtk.Button()
    button.add_css_class("luminophore-button")
    button.set_tooltip_text(tooltip)
    image = Gtk.Image.new_from_icon_name(icon)
    image.add_css_class(f"luminophore-symbol-{role}")
    button.set_child(image)
    button.connect("clicked", lambda _button: callback())
    attach_luminophore_state(button)
    return button


def launcher_collapsed(on_launcher: Callable[[], None], taskbar: Gtk.Widget | None = None) -> Gtk.Widget:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    row.append(icon_button("system-search-symbolic", "런처", on_launcher))
    taskbar_widget = taskbar or Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    taskbar_widget.set_name("taskbar-items")
    if taskbar is None:
        empty = Gtk.Label(label="실행 중인 앱 없음")
        empty.add_css_class("muted")
        taskbar_widget.append(empty)
    row.append(taskbar_widget)
    return row


def launcher_expanded() -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    entry = Gtk.SearchEntry(placeholder_text="앱, 창 또는 /file · /clip · /web · / 명령")
    entry.set_name("launcher-entry")
    box.append(entry)
    hint = Gtk.Label(label="검색 결과를 준비하는 중")
    hint.add_css_class("muted")
    hint.set_xalign(0)
    box.append(hint)
    box.set_size_request(CONTROL_PANEL_WIDTH, CONTROL_PANEL_HEIGHT)
    return box


def notification_collapsed(on_notifications: Callable[[], None], on_power: Callable[[], None]) -> Gtk.Widget:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    row.append(icon_button("notifications-symbolic", "알림", on_notifications))
    count = Gtk.Label(label="0")
    count.add_css_class("numeric")
    count.set_name("notification-count")
    row.append(count)
    dots = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
    dots.set_name("notification-dots")
    row.append(dots)
    row.append(icon_button("pan-down-symbolic", "트레이", lambda: None))
    row.append(icon_button("system-shutdown-symbolic", "전원", on_power))
    return row


def notification_expanded() -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    title = Gtk.Label(label="알림")
    title.add_css_class("section-title")
    title.set_hexpand(True)
    title.set_xalign(0)
    header.append(title)
    dnd = Gtk.ToggleButton(label="방해 금지")
    dnd.add_css_class("luminophore-button")
    header.append(dnd)
    clear = Gtk.Button(label="모두 지우기")
    clear.add_css_class("luminophore-button")
    header.append(clear)
    box.append(header)
    empty = Gtk.Label(label="놓친 알림이 없습니다")
    empty.add_css_class("muted")
    box.append(empty)
    box.set_size_request(420, 420)
    return box


def power_expanded(
    on_restart: Callable[[], None],
    on_shutdown: Callable[[], None],
    on_firmware_setup: Callable[[], None],
) -> Gtk.Widget:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    row.set_halign(Gtk.Align.CENTER)
    restart = Gtk.Button(label="재시작")
    shutdown = Gtk.Button(label="종료")
    firmware = Gtk.Button(label="BIOS/UEFI 진입")
    for button in (restart, shutdown, firmware):
        button.add_css_class("luminophore-button")
        button.set_can_focus(False)
        attach_luminophore_state(button)
    restart.connect("clicked", lambda _button: on_restart())
    shutdown.connect("clicked", lambda _button: on_shutdown())
    firmware.connect("clicked", lambda _button: on_firmware_setup())
    row.append(restart)
    row.append(shutdown)
    row.append(firmware)
    return row


def weather_collapsed(on_toggle: Callable[[], None], on_refresh: Callable[[], None]) -> Gtk.Widget:
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
    top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    clock_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    clock = Gtk.Label(label="00:00:00")
    clock.add_css_class("numeric")
    clock.add_css_class("weather-clock")
    date = Gtk.Label(label="")
    date.add_css_class("weather-date")
    clock_box.append(clock)
    clock_box.append(date)
    top.append(clock_box)
    current = Gtk.Label(label="서울  --.-°C  날씨 없음")
    current.add_css_class("numeric")
    current.set_hexpand(True)
    current.set_xalign(1)
    top.append(current)
    top.append(icon_button("view-refresh-symbolic", "날씨 새로고침", on_refresh))
    root.append(top)
    days = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True, spacing=2)
    for offset in range(7):
        cell = Gtk.Label(label=f"+{offset}\n--°/--°")
        cell.add_css_class("weather-cell")
        days.append(cell)
    root.append(days)
    gesture = Gtk.GestureClick()
    gesture.connect("released", lambda *_args: on_toggle())
    root.add_controller(gesture)

    weekdays = "월화수목금토일"

    def tick() -> bool:
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        clock.set_label(now.strftime("%H:%M:%S"))
        date.set_label(f"{now.year}년 {now.month}월 {now.day}일 {weekdays[now.weekday()]}요일")
        return True

    tick()
    GLib.timeout_add(250, tick)
    return root


def weather_expanded() -> Gtk.Widget:
    grid = Gtk.Grid(column_spacing=4, row_spacing=4, column_homogeneous=True, row_homogeneous=True)
    for day in range(7, 28):
        cell = Gtk.Label(label=f"+{day}  ☁\n--°/--°")
        cell.add_css_class("weather-cell")
        grid.attach(cell, (day - 7) % 7, (day - 7) // 7, 1, 1)
    grid.set_size_request(530, 150)
    return grid


def system_collapsed(on_toggle: Callable[[], None]) -> Gtk.Widget:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    for label in ("CPU --°", "GPU --°", "냉각수 --°"):
        value = Gtk.Label(label=label)
        value.add_css_class("numeric")
        value.add_css_class("metric-value")
        row.append(value)
    gesture = Gtk.GestureClick()
    gesture.connect("released", lambda *_args: on_toggle())
    row.add_controller(gesture)
    return row


def system_expanded() -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    for title in ("CPU", "GPU", "RAM / SWAP", "디스크", "네트워크", "NVMe", "펌프 / 팬 / 냉각수"):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=title)
        name.set_hexpand(True)
        name.set_xalign(0)
        value = Gtk.Label(label="--")
        value.add_css_class("numeric")
        row.append(name)
        row.append(value)
        box.append(row)
    box.set_size_request(410, 330)
    return box
