from __future__ import annotations

from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..weather import WeatherSnapshot, weather_icon_name, weather_text
from .widgets import icon_button


class ForecastCell(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.add_css_class("weather-cell")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.FILL)

        self.day = Gtk.Label(label="--")
        self.day.add_css_class("weather-day")
        self.day.add_css_class("luminophore-key-primary")
        self.day.set_single_line_mode(True)
        self.icon = Gtk.Image.new_from_icon_name("weather-severe-alert-symbolic")
        self.icon.add_css_class("weather-icon")
        self.icon.add_css_class("luminophore-symbol-secondary")
        self.icon.set_pixel_size(18)
        self.temperature = Gtk.Label(label="--°/--°")
        self.temperature.add_css_class("numeric")
        self.temperature.add_css_class("weather-temperature")
        self.temperature.add_css_class("luminophore-key-secondary")
        self.temperature.set_single_line_mode(True)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        header.set_halign(Gtk.Align.CENTER)
        header.append(self.day)
        header.append(self.icon)
        self.append(header)
        self.temperature.set_halign(Gtk.Align.CENTER)
        self.append(self.temperature)

    def set_forecast(self, day: int, code: int, minimum: int, maximum: int) -> None:
        self.day.set_label(str(day))
        self.icon.set_from_icon_name(weather_icon_name(code))
        self.icon.set_visible(True)
        self.temperature.set_label(f"{minimum}°/{maximum}°")

    def set_empty(self) -> None:
        self.day.set_label("--")
        self.icon.set_visible(False)
        self.temperature.set_label("--°/--°")


class WeatherView:
    def __init__(self, on_toggle: Callable[[], None], on_refresh: Callable[[], None]) -> None:
        self.snapshot: WeatherSnapshot | None = None
        self.error = ""
        self.collapsed = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        clock_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.clock = Gtk.Label(label="00:00:00")
        self.clock.add_css_class("numeric")
        self.clock.add_css_class("weather-clock")
        self.clock.add_css_class("luminophore-key-primary")
        self.date = Gtk.Label()
        self.date.add_css_class("weather-date")
        self.date.add_css_class("luminophore-key-secondary")
        clock_box.append(self.clock)
        clock_box.append(self.date)
        top.append(clock_box)
        self.current = Gtk.Label(label="서울  --.-°C  날씨 없음")
        self.current.add_css_class("numeric")
        self.current.add_css_class("luminophore-key-primary")
        self.current.set_hexpand(True)
        self.current.set_xalign(1)
        top.append(self.current)
        top.append(icon_button("view-refresh-symbolic", "날씨 새로고침", on_refresh, role="secondary"))
        self.collapsed.append(top)
        self.week = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True, spacing=2)
        self.week_cells: list[ForecastCell] = []
        for _index in range(7):
            cell = ForecastCell()
            cell.set_empty()
            self.week.append(cell)
            self.week_cells.append(cell)
        self.collapsed.append(self.week)
        gesture = Gtk.GestureClick()
        gesture.connect("released", lambda *_args: on_toggle())
        self.collapsed.add_controller(gesture)

        self.expanded = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.expanded.add_css_class("weather-expanded")
        self.month = Gtk.Grid(column_spacing=4, row_spacing=4, column_homogeneous=True, row_homogeneous=True)
        self.month_cells: list[ForecastCell] = []
        for day in range(7, 28):
            cell = ForecastCell()
            cell.set_empty()
            self.month.attach(cell, (day - 7) % 7, (day - 7) // 7, 1, 1)
            self.month_cells.append(cell)
        self.month.set_size_request(530, 150)
        self.expanded.append(self.month)
        self.status = Gtk.Label()
        self.status.add_css_class("muted")
        self.status.set_halign(Gtk.Align.END)
        self.status.set_xalign(1)
        self.expanded.append(self.status)
        self._tick()
        GLib.timeout_add(250, self._tick)

    def _tick(self) -> bool:
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        weekdays = "월화수목금토일"
        self.clock.set_label(now.strftime("%H:%M:%S"))
        self.date.set_label(f"{now.year}년 {now.month}월 {now.day}일 {weekdays[now.weekday()]}요일")
        return True

    def update(self, snapshot: WeatherSnapshot | None, error: str = "") -> None:
        self.snapshot = snapshot
        self.error = error
        if not snapshot:
            self.current.set_label("서울  --.-°C  날씨 없음")
            self.status.set_label("날씨를 불러오지 못했습니다" if error else "날씨 데이터 없음")
            return
        stale = " · 오래된 캐시" if snapshot.stale else ""
        self.current.set_label(f"서울  {snapshot.current_temperature:.1f}°C  {weather_text(snapshot.current_code)}")
        fetched = datetime.fromtimestamp(snapshot.fetched_at, ZoneInfo("Asia/Seoul")).strftime("%m월 %d일 %H:%M")
        self.status.set_label(f"마지막 갱신 {fetched}{stale}" + (" · 새로고침 실패" if error else ""))
        for index, cell in enumerate(self.week_cells):
            if index >= len(snapshot.days):
                cell.set_empty()
                continue
            day = snapshot.days[index]
            stamp = datetime.fromisoformat(day.date)
            cell.set_forecast(stamp.day, day.code, day.minimum, day.maximum)
        for index, cell in enumerate(self.month_cells):
            day_index = index + 7
            if day_index >= len(snapshot.days):
                cell.set_empty()
                continue
            day = snapshot.days[day_index]
            stamp = datetime.fromisoformat(day.date)
            cell.set_forecast(stamp.day, day.code, day.minimum, day.maximum)
