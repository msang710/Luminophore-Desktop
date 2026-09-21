from __future__ import annotations

import copy
import threading
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from ..applications import ApplicationCatalog, ApplicationRecord
from ..app_icons import ApplicationIconProvider
from ..font_catalog import font_dropdown_items, installed_font_families
from ..icon_generation import (
    GeneratedIconStore,
    IconDraft,
    IconDraftGenerator,
    IconGenerationError,
    inspect_icon_source,
)
from ..palette_controller import PaletteSettingsState
from ..screen_color_picker import ScreenColorPickResult
from ..config import ConfigError
from ..settings_controller import SettingsApplyResult, SettingsController, SettingsSnapshot
from ..settings_schema import CATEGORIES, SettingSpec, specs_for_category
from ..theme import Palette


_FIXED_MONITOR_PALETTES_PATH = "theme.fixed_monitor_palettes"


def _clear(container: Gtk.Box) -> None:
    child = container.get_first_child()
    while child:
        following = child.get_next_sibling()
        container.remove(child)
        child = following


def _app_image(
    app: ApplicationRecord | None,
    size: int = 22,
    provider: ApplicationIconProvider | None = None,
    catalog_revision: int = 0,
) -> Gtk.Widget:
    from .effects import app_icon

    return app_icon(app, size, provider=provider, catalog_revision=catalog_revision)


def _original_app_image(app: ApplicationRecord, size: int = 64) -> Gtk.Image:
    if isinstance(app.icon, Gdk.Paintable):
        image = Gtk.Image.new_from_paintable(app.icon)
    elif isinstance(app.icon, Gio.Icon):
        image = Gtk.Image.new_from_gicon(app.icon)
    else:
        image = Gtk.Image.new_from_icon_name("application-x-executable-symbolic")
    image.set_pixel_size(size)
    return image


def _color_rgba(value: str) -> Gdk.RGBA | None:
    if len(value) != 7 or not value.startswith("#"):
        return None
    rgba = Gdk.RGBA()
    try:
        parsed = rgba.parse(value)
    except TypeError:
        return None
    if not parsed:
        return None
    rgba.alpha = 1.0
    return rgba


def _rgba_hex(rgba: Gdk.RGBA) -> str:
    channels = (rgba.red, rgba.green, rgba.blue)
    return "#" + "".join(f"{round(max(0.0, min(1.0, channel)) * 255):02X}" for channel in channels)


def _monitor_palette_connectors(current: dict[str, Palette], saved: dict[str, list[str]]) -> list[str]:
    connectors = list(current)
    connectors.extend(sorted(connector for connector in saved if connector not in current))
    return connectors


def _monitor_palette_colors(
    connector: str,
    saved: dict[str, list[str]],
    fallback_primary: str,
    fallback_secondary: str,
) -> tuple[str, str]:
    colors = saved.get(connector)
    if isinstance(colors, list) and len(colors) == 2:
        return str(colors[0]), str(colors[1])
    return fallback_primary, fallback_secondary


class ColorEditor:
    def __init__(self, value: str, changed: Callable[[str], None], title: str) -> None:
        self.changed = changed
        self._syncing = False
        self.rgba = _color_rgba(value) or _color_rgba("#78DCE8")
        assert self.rgba is not None
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        self.button = Gtk.Button()
        self.button.add_css_class("luminophore-button")
        self.button.set_tooltip_text(f"{title} 색상표를 패널 안에서 펼치기")
        self.swatch = Gtk.DrawingArea()
        self.swatch.set_content_width(30)
        self.swatch.set_content_height(22)
        self.swatch.set_draw_func(self._draw_swatch)
        self.button.set_child(self.swatch)
        self.button.connect("clicked", self._toggle_chooser)
        controls.append(self.button)

        self.entry = Gtk.Entry()
        self.entry.set_text(value)
        self.entry.set_width_chars(10)
        self.entry.set_max_width_chars(10)
        self.entry.set_hexpand(False)
        self.entry.set_placeholder_text("#RRGGBB")
        self.entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "edit-clear-symbolic")
        self.entry.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "입력 지우기")
        self.entry.connect("icon-release", lambda *_args: self.entry.set_text(""))
        self.entry.connect("changed", self._entry_changed)
        self.entry.connect("notify::has-focus", self._entry_focus_changed)
        controls.append(self.entry)
        self.widget.append(controls)

        channel_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.channels: dict[str, Gtk.Scale] = {}
        for channel, label_text, value in (
            ("red", "R", self.rgba.red),
            ("green", "G", self.rgba.green),
            ("blue", "B", self.rgba.blue),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
            label = Gtk.Label(label=label_text)
            label.set_width_chars(1)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 255, 1)
            scale.set_digits(0)
            scale.set_value_pos(Gtk.PositionType.RIGHT)
            scale.set_hexpand(True)
            scale.set_value(round(value * 255))
            scale.connect("value-changed", self._channels_changed)
            self.channels[channel] = scale
            row.append(label)
            row.append(scale)
            channel_box.append(row)
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.revealer.set_transition_duration(140)
        self.revealer.set_reveal_child(False)
        self.revealer.set_child(channel_box)
        self.widget.append(self.revealer)

    def _draw_swatch(self, _area: Gtk.DrawingArea, context: object, width: int, height: int) -> None:
        context.set_source_rgba(self.rgba.red, self.rgba.green, self.rgba.blue, 1.0)
        context.rectangle(1, 1, max(1, width - 2), max(1, height - 2))
        context.fill_preserve()
        context.set_source_rgba(1.0, 1.0, 1.0, 0.42)
        context.set_line_width(1.0)
        context.stroke()

    def _toggle_chooser(self, _button: Gtk.Button) -> None:
        visible = not self.revealer.get_reveal_child()
        self.revealer.set_reveal_child(visible)
        self.button.set_tooltip_text("색상표 접기" if visible else "색상표를 패널 안에서 펼치기")

    def _channels_changed(self, _scale: Gtk.Scale) -> None:
        if self._syncing:
            return
        color = "#" + "".join(
            f"{round(self.channels[channel].get_value()):02X}"
            for channel in ("red", "green", "blue")
        )
        self.entry.set_text(color)

    def _apply_rgba(self, rgba: Gdk.RGBA) -> None:
        self._syncing = True
        self.rgba = rgba
        for channel in ("red", "green", "blue"):
            self.channels[channel].set_value(round(getattr(rgba, channel) * 255))
        self._syncing = False
        self.swatch.queue_draw()

    def _entry_changed(self, entry: Gtk.Entry) -> None:
        value = entry.get_text()
        self.changed(value)
        rgba = _color_rgba(value)
        if not rgba:
            return
        self._apply_rgba(rgba)

    def _entry_focus_changed(self, entry: Gtk.Entry, _property: object) -> None:
        if entry.has_focus():
            GLib.idle_add(self._select_entry_value, entry)

    @staticmethod
    def _select_entry_value(entry: Gtk.Entry) -> bool:
        if entry.has_focus():
            entry.select_region(0, -1)
        return False

    def set_color(self, color: str) -> None:
        normalized = color.upper()
        rgba = _color_rgba(normalized)
        if not rgba:
            return
        if self.entry.get_text() == normalized:
            self._apply_rgba(rgba)
        else:
            self.entry.set_text(normalized)


class AppListEditor:
    def __init__(
        self,
        catalog: ApplicationCatalog,
        values: list[str],
        taskbar: bool,
        changed: Callable[[list[str]], None],
        icon_provider: ApplicationIconProvider | None = None,
    ) -> None:
        self.catalog = catalog
        self.values = list(values)
        self.taskbar = taskbar
        self.changed = changed
        self.icon_provider = icon_provider
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.widget.append(self.rows)
        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        labels = [app.name for app in catalog.apps]
        self.apps = list(catalog.apps)
        self.dropdown = Gtk.DropDown.new_from_strings(labels or ["설치된 앱 없음"])
        self.dropdown.set_hexpand(True)
        self.dropdown.set_enable_search(True)
        add = Gtk.Button(label="추가")
        add.add_css_class("luminophore-button")
        add.set_sensitive(bool(self.apps))
        add.connect("clicked", self._add)
        add_row.append(self.dropdown)
        add_row.append(add)
        self.widget.append(add_row)
        self._refresh()

    def _stored_value(self, app: ApplicationRecord) -> str:
        if self.taskbar:
            return app.window_class or app.desktop_id.removesuffix(".desktop")
        return app.desktop_id

    def _match(self, value: str) -> ApplicationRecord | None:
        direct = next(
            (
                app for app in self.catalog.apps
                if value.casefold() in {
                    app.desktop_id.casefold(),
                    app.desktop_id.removesuffix(".desktop").casefold(),
                    app.window_class.casefold(),
                }
            ),
            None,
        )
        return direct or self.catalog.match_window_class(value)

    def _add(self, _button: Gtk.Button) -> None:
        index = self.dropdown.get_selected()
        if index >= len(self.apps):
            return
        value = self._stored_value(self.apps[index])
        if value.casefold() in {item.casefold() for item in self.values}:
            return
        self.values.append(value)
        self.changed(list(self.values))
        self._refresh()

    def _move(self, index: int, offset: int) -> None:
        target = index + offset
        if target < 0 or target >= len(self.values):
            return
        self.values[index], self.values[target] = self.values[target], self.values[index]
        self.changed(list(self.values))
        self._refresh()

    def _remove(self, index: int) -> None:
        self.values.pop(index)
        self.changed(list(self.values))
        self._refresh()

    def _refresh(self) -> None:
        _clear(self.rows)
        for index, value in enumerate(self.values):
            app = self._match(value)
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.append(_app_image(app, provider=self.icon_provider, catalog_revision=getattr(self.catalog, "revision", 0)))
            label = Gtk.Label(label=app.name if app else f"{value} · 설치되지 않음")
            label.set_hexpand(True)
            label.set_xalign(0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_tooltip_text(value)
            row.append(label)
            for icon, offset, tooltip in (
                ("go-up-symbolic", -1, "위로"),
                ("go-down-symbolic", 1, "아래로"),
            ):
                button = Gtk.Button.new_from_icon_name(icon)
                button.add_css_class("luminophore-button")
                button.set_tooltip_text(tooltip)
                button.set_sensitive(0 <= index + offset < len(self.values))
                button.connect("clicked", lambda _button, current=index, step=offset: self._move(current, step))
                row.append(button)
            remove = Gtk.Button.new_from_icon_name("window-close-symbolic")
            remove.add_css_class("luminophore-button")
            remove.set_tooltip_text("제거")
            remove.connect("clicked", lambda _button, current=index: self._remove(current))
            row.append(remove)
            self.rows.append(row)
        if not self.values:
            empty = Gtk.Label(label="등록된 앱이 없습니다")
            empty.add_css_class("muted")
            empty.set_xalign(0)
            self.rows.append(empty)


class AppActionsEditor:
    def __init__(
        self,
        catalog: ApplicationCatalog,
        values: dict[str, str],
        changed: Callable[[dict[str, str]], None],
        icon_provider: ApplicationIconProvider | None = None,
    ) -> None:
        self.catalog = catalog
        self.values = dict(values)
        self.changed = changed
        self.icon_provider = icon_provider
        self.apps = [app for app in catalog.apps if app.actions]
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.widget.append(self.rows)
        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.app_dropdown = Gtk.DropDown.new_from_strings([app.name for app in self.apps] or ["동작이 있는 앱 없음"])
        self.app_dropdown.set_hexpand(True)
        self.app_dropdown.set_enable_search(True)
        self.action_dropdown = Gtk.DropDown.new_from_strings([])
        self.action_dropdown.set_hexpand(True)
        self.app_dropdown.connect("notify::selected", lambda *_args: self._refresh_actions())
        add = Gtk.Button(label="추가")
        add.add_css_class("luminophore-button")
        add.set_sensitive(bool(self.apps))
        add.connect("clicked", self._add)
        add_row.append(self.app_dropdown)
        add_row.append(self.action_dropdown)
        add_row.append(add)
        self.widget.append(add_row)
        self._refresh_actions()
        self._refresh_rows()

    def _refresh_actions(self) -> None:
        index = self.app_dropdown.get_selected()
        actions = self.apps[index].actions if index < len(self.apps) else ()
        self.action_dropdown.set_model(Gtk.StringList.new([action.name for action in actions]))
        if actions:
            self.action_dropdown.set_selected(0)

    def _add(self, _button: Gtk.Button) -> None:
        app_index = self.app_dropdown.get_selected()
        if app_index >= len(self.apps):
            return
        app = self.apps[app_index]
        action_index = self.action_dropdown.get_selected()
        if action_index >= len(app.actions):
            return
        self.values[app.desktop_id] = app.actions[action_index].action_id
        self.changed(dict(self.values))
        self._refresh_rows()

    def _remove(self, app_id: str) -> None:
        self.values.pop(app_id, None)
        self.changed(dict(self.values))
        self._refresh_rows()

    def _refresh_rows(self) -> None:
        _clear(self.rows)
        for app_id, action_id in self.values.items():
            app = next((item for item in self.catalog.apps if item.desktop_id.casefold() == app_id.casefold()), None)
            action = next((item for item in app.actions if item.action_id == action_id), None) if app else None
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.append(_app_image(app, provider=self.icon_provider, catalog_revision=getattr(self.catalog, "revision", 0)))
            text = f"{app.name if app else app_id} · {action.name if action else action_id}"
            label = Gtk.Label(label=text)
            label.set_hexpand(True)
            label.set_xalign(0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_tooltip_text(f"{app_id} = {action_id}")
            row.append(label)
            remove = Gtk.Button.new_from_icon_name("window-close-symbolic")
            remove.add_css_class("luminophore-button")
            remove.connect("clicked", lambda _button, selected=app_id: self._remove(selected))
            row.append(remove)
            self.rows.append(row)
        if not self.values:
            empty = Gtk.Label(label="지정된 선호 동작이 없습니다")
            empty.add_css_class("muted")
            empty.set_xalign(0)
            self.rows.append(empty)


class IconAliasesEditor:
    def __init__(
        self,
        catalog: ApplicationCatalog,
        values: dict[str, str],
        changed: Callable[[dict[str, str]], None],
        icon_provider: ApplicationIconProvider | None = None,
    ) -> None:
        self.catalog = catalog
        self.values = dict(values)
        self.changed = changed
        self.icon_provider = icon_provider
        self.apps = list(catalog.apps)
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.widget.append(self.rows)
        add_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.dropdown = Gtk.DropDown.new_from_strings([app.name for app in self.apps] or ["설치된 앱 없음"])
        self.dropdown.set_enable_search(True)
        self.dropdown.set_hexpand(True)
        self.target = Gtk.Entry(placeholder_text="Arcticons 이름")
        self.target.set_width_chars(22)
        add = Gtk.Button(label="추가")
        add.add_css_class("luminophore-button")
        add.set_sensitive(bool(self.apps))
        add.connect("clicked", self._add)
        add_row.append(self.dropdown)
        add_row.append(self.target)
        add_row.append(add)
        self.widget.append(add_row)
        self._refresh()

    def _add(self, _button: Gtk.Button) -> None:
        index = self.dropdown.get_selected()
        target = self.target.get_text().strip()
        if index >= len(self.apps) or not target:
            return
        app = self.apps[index]
        key = app.icon_names[0] if app.icon_names else app.desktop_id
        self.values[key] = target
        self.changed(dict(self.values))
        self.target.set_text("")
        self._refresh()

    def _remove(self, key: str) -> None:
        self.values.pop(key, None)
        self.changed(dict(self.values))
        self._refresh()

    def _refresh(self) -> None:
        _clear(self.rows)
        for key, target in self.values.items():
            app = self.catalog.match_desktop_id(key) or next(
                (item for item in self.catalog.apps if key.casefold() in {name.casefold() for name in item.icon_names}),
                None,
            )
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.append(_app_image(app, provider=self.icon_provider, catalog_revision=getattr(self.catalog, "revision", 0)))
            label = Gtk.Label(label=f"{app.name if app else key}  →  {target}")
            label.set_hexpand(True)
            label.set_xalign(0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_tooltip_text(f"{key} = {target}")
            row.append(label)
            remove = Gtk.Button.new_from_icon_name("window-close-symbolic")
            remove.add_css_class("luminophore-button")
            remove.set_tooltip_text("별칭 제거")
            remove.connect("clicked", lambda _button, selected=key: self._remove(selected))
            row.append(remove)
            self.rows.append(row)
        if not self.values:
            empty = Gtk.Label(label="지정된 별칭이 없습니다")
            empty.add_css_class("muted")
            empty.set_xalign(0)
            self.rows.append(empty)


class SettingsView:
    def __init__(
        self,
        controller: SettingsController,
        catalog: ApplicationCatalog,
        leave: Callable[[], None],
        extract_palette: Callable[[], bool],
        apply_palette: Callable[[], bool],
        open_system_theme_tools: Callable[[], None],
        screen_color_available: Callable[[], bool],
        screen_color_busy: Callable[[], bool],
        begin_screen_color_pick: Callable[[Callable[[ScreenColorPickResult], None]], bool],
        icon_provider: ApplicationIconProvider | None = None,
        icon_generator: IconDraftGenerator | None = None,
        generated_icon_store: GeneratedIconStore | None = None,
    ) -> None:
        self.controller = controller
        self.catalog = catalog
        self.leave = leave
        self.extract_palette = extract_palette
        self.apply_palette = apply_palette
        self.open_system_theme_tools = open_system_theme_tools
        self.screen_color_available = screen_color_available
        self.screen_color_busy = screen_color_busy
        self.begin_screen_color_pick = begin_screen_color_pick
        self.icon_provider = icon_provider
        self.icon_generator = icon_generator
        self.generated_icon_store = generated_icon_store
        self.icon_drafts: dict[str, tuple[IconDraft, ...]] = {}
        self.icon_generation_messages: dict[str, str] = {}
        self.icon_generation_busy: set[str] = set()
        self.color_editors: dict[str, ColorEditor] = {}
        self.screen_picker_buttons: dict[str, Gtk.Button] = {}
        self.monitor_color_targets: dict[str, tuple[str, int]] = {}
        self.system_theme_button: Gtk.Button | None = None
        self.palette_state = PaletteSettingsState()
        self.palette_source_name = "hyprpaper"
        self.palette_current: dict[str, Palette] = {}
        self.palette_source_label: Gtk.Label | None = None
        self.palette_current_swatches: Gtk.Box | None = None
        self.palette_extract_button: Gtk.Button | None = None
        self.palette_spinner: Gtk.Spinner | None = None
        self.palette_status: Gtk.Label | None = None
        self.palette_preview_title: Gtk.Label | None = None
        self.palette_preview_swatches: Gtk.Box | None = None
        self.palette_apply_button: Gtk.Button | None = None
        self.snapshot: SettingsSnapshot | None = None
        self.base: dict[str, Any] = {}
        self.draft: dict[str, Any] = {}
        self.category = ""
        self.widget = Gtk.Stack()
        self.widget.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.widget.set_transition_duration(160)
        self.widget.set_hhomogeneous(False)
        self.widget.set_vhomogeneous(False)
        self.widget.set_hexpand(True)
        self.widget.set_vexpand(True)
        self.widget.set_size_request(560, 500)

        self.home = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        home_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        back = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        back.add_css_class("luminophore-button")
        back.set_tooltip_text("성능 정보로 돌아가기")
        back.connect("clicked", lambda _button: self._leave_settings())
        title = Gtk.Label(label="설정")
        title.add_css_class("section-title")
        title.set_hexpand(True)
        title.set_xalign(0)
        home_header.append(back)
        home_header.append(title)
        self.home.append(home_header)
        for label, recover in (("적용 상태 확인", False), ("미확정 요청 종료 · 현재 효과 유지", True)):
            button = Gtk.Button(label=label)
            button.add_css_class("luminophore-button")
            button.connect("clicked", lambda _button, recovery=recover: self._refresh_apply_status(recovery))
            self.home.append(button)
        for category in CATEGORIES:
            button = Gtk.Button()
            button.add_css_class("launcher-result")
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            icon = Gtk.Image.new_from_icon_name(category.icon)
            icon.set_pixel_size(22)
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            labels.set_hexpand(True)
            name = Gtk.Label(label=category.title)
            name.set_xalign(0)
            name.add_css_class("section-title")
            detail = Gtk.Label(label=category.description)
            detail.set_xalign(0)
            detail.add_css_class("muted")
            labels.append(name)
            labels.append(detail)
            row.append(icon)
            row.append(labels)
            row.append(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            button.set_child(row)
            button.connect("clicked", lambda _button, selected=category.category_id: self._open_category(selected))
            self.home.append(button)
        self.home_status = Gtk.Label(label="")
        self.home_status.add_css_class("muted")
        self.home_status.set_xalign(0)
        self.home.append(self.home_status)
        self.widget.add_named(self.home, "home")

        self.category_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        category_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        category_back = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        category_back.add_css_class("luminophore-button")
        category_back.set_tooltip_text("설정 목록으로 돌아가기")
        category_back.connect("clicked", lambda _button: self.show_home())
        self.category_title = Gtk.Label(label="")
        self.category_title.add_css_class("section-title")
        self.category_title.set_hexpand(True)
        self.category_title.set_xalign(0)
        category_header.append(category_back)
        category_header.append(self.category_title)
        self.category_page.append(category_header)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        self.form_scroll = scroll
        self.form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        scroll.set_child(self.form)
        self.category_page.append(scroll)
        self.status = Gtk.Label(label="")
        self.status.add_css_class("muted")
        self.status.set_xalign(0)
        self.status.set_wrap(True)
        self.category_page.append(self.status)
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        footer.set_halign(Gtk.Align.END)
        self.reload_button = Gtk.Button(label="다시 불러오기")
        self.reload_button.add_css_class("luminophore-button")
        self.reload_button.set_visible(False)
        self.reload_button.connect("clicked", lambda _button: self.open())
        cancel = Gtk.Button(label="변경 취소")
        cancel.add_css_class("luminophore-button")
        cancel.connect("clicked", lambda _button: self._discard())
        self.apply_button = Gtk.Button(label="적용")
        self.apply_button.add_css_class("luminophore-button")
        self.apply_button.set_sensitive(False)
        self.apply_button.connect("clicked", lambda _button: self._apply())
        footer.append(self.reload_button)
        footer.append(cancel)
        footer.append(self.apply_button)
        self.category_page.append(footer)
        self.widget.add_named(self.category_page, "category")
        self.widget.set_visible_child_name("home")

    def open(self) -> None:
        try:
            self.snapshot = self.controller.open()
        except Exception as exc:
            self.home_status.set_label(f"설정을 읽지 못했습니다: {exc}")
            self.widget.set_visible_child_name("home")
            return
        self.base = copy.deepcopy(self.snapshot.values)
        self.draft = copy.deepcopy(self.snapshot.values)
        self.category = ""
        self.home_status.set_label("")
        self.reload_button.set_visible(False)
        self.widget.set_visible_child_name("home")
        self._sync_dirty()
        self.icon_drafts = {}
        self.icon_generation_messages = {}

    def show_home(self) -> None:
        self.category = ""
        self.widget.set_visible_child_name("home")
        self._sync_dirty()

    def show_category(self, category: str) -> None:
        self._open_category(category)

    def refresh_current_category(self) -> None:
        if self.category and self.snapshot:
            self._build_form()

    def _leave_settings(self) -> None:
        self.base = {}
        self.draft = {}
        self.snapshot = None
        self.controller.set_dirty(set())
        self.leave()

    def _open_category(self, category: str) -> None:
        if not self.snapshot:
            self.open()
        self.category = category
        selected = next(item for item in CATEGORIES if item.category_id == category)
        self.category_title.set_label(selected.title)
        self._build_form()
        self.widget.set_visible_child_name("category")

    def _build_form(self) -> None:
        _clear(self.form)
        self.color_editors = {}
        self.screen_picker_buttons = {}
        self.monitor_color_targets = {}
        self.system_theme_button = None
        self.palette_source_label = None
        self.palette_current_swatches = None
        self.palette_extract_button = None
        self.palette_spinner = None
        self.palette_status = None
        self.palette_preview_title = None
        self.palette_preview_swatches = None
        self.palette_apply_button = None
        for spec in specs_for_category(self.category):
            self._append_control(spec)
        if self.category == "appearance":
            self._append_icon_generation_tools()
        if self.category == "palette":
            self._append_palette_tools()
            self.system_theme_button = Gtk.Button(label="시스템 팔레트")
            self.system_theme_button.add_css_class("luminophore-button")
            self.system_theme_button.connect("clicked", lambda _button: self._open_system_theme())
            self.form.append(self.system_theme_button)
        self._sync_screen_picker_buttons()
        self._sync_dirty()

    def _append_icon_generation_tools(self) -> None:
        if not self.icon_provider or not self.icon_generator or not self.generated_icon_store:
            return
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.form.append(separator)
        title = Gtk.Label(label="미지원 앱 선화")
        title.add_css_class("section-title")
        title.set_xalign(0)
        self.form.append(title)
        help_label = Gtk.Label(
            label="Arcticons에 없는 앱만 표시합니다. 원본에서 만든 초안은 승인하기 전까지 적용되지 않습니다."
        )
        help_label.add_css_class("muted")
        help_label.set_xalign(0)
        help_label.set_wrap(True)
        self.form.append(help_label)
        rows = 0
        revision = getattr(self.catalog, "revision", 0)
        for app in self.catalog.apps:
            entry = self.generated_icon_store.entry_for(app.desktop_id)
            resolution = self.icon_provider.resolve(app, 28, 1, revision)
            if entry is None and resolution.origin != "original":
                continue
            rows += 1
            self._append_generated_icon_row(app, bool(entry))
        if not rows:
            empty = Gtk.Label(label="현재 미지원 앱이 없습니다")
            empty.add_css_class("muted")
            empty.set_xalign(0)
            self.form.append(empty)

    def _append_generated_icon_row(self, app: ApplicationRecord, custom: bool) -> None:
        assert self.icon_provider and self.icon_generator and self.generated_icon_store
        block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        block.add_css_class("notification-group")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.append(_app_image(app, 28, self.icon_provider, getattr(self.catalog, "revision", 0)))
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        labels.set_hexpand(True)
        name = Gtk.Label(label=app.name)
        name.set_xalign(0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        source = self.icon_provider.source_file(app)
        status_text = self.generated_icon_store.status(app, source) if custom else "미지원"
        status = Gtk.Label(label=self.icon_generation_messages.get(app.desktop_id, status_text))
        status.add_css_class("muted")
        status.set_xalign(0)
        status.set_wrap(True)
        labels.append(name)
        labels.append(status)
        row.append(labels)
        if custom:
            regenerate = Gtk.Button(label="다시 만들기")
            regenerate.add_css_class("luminophore-button")
            regenerate.set_sensitive(app.desktop_id not in self.icon_generation_busy and source is not None)
            regenerate.set_tooltip_text("local SVG/PNG 원본이 없습니다" if source is None else "현재 적용본을 유지한 채 새 초안 만들기")
            regenerate.connect("clicked", lambda _button, selected=app: self._generate_icon(selected))
            row.append(regenerate)
            remove = Gtk.Button(label="사용자 선화 삭제")
            remove.add_css_class("luminophore-button")
            remove.connect("clicked", lambda _button, selected=app: self._remove_generated_icon(selected))
            row.append(remove)
        else:
            generate = Gtk.Button(label="선화 만들기")
            generate.add_css_class("luminophore-button")
            generate.set_sensitive(app.desktop_id not in self.icon_generation_busy and source is not None)
            generate.set_tooltip_text("local SVG/PNG 원본이 없습니다" if source is None else "원본에서 선화 초안 만들기")
            generate.connect("clicked", lambda _button, selected=app: self._generate_icon(selected))
            row.append(generate)
        block.append(row)
        drafts = self.icon_drafts.get(app.desktop_id, ())
        if drafts:
            preview = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            original = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            original.append(_original_app_image(app))
            original_label = Gtk.Label(label="원본")
            original_label.add_css_class("muted")
            original.append(original_label)
            preview.append(original)
            for draft in drafts:
                candidate = Gtk.Button()
                candidate.add_css_class("luminophore-button")
                content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                image = Gtk.Image.new_from_file(str(draft.svg_path))
                image.set_pixel_size(64)
                label = Gtk.Label(label={"silhouette": "외곽", "edge": "내부선", "combined": "혼합"}.get(draft.strategy, draft.strategy))
                content.append(image)
                content.append(label)
                candidate.set_child(content)
                candidate.set_tooltip_text(f"{label.get_label()} 후보 승인")
                candidate.connect("clicked", lambda _button, selected=draft, selected_app=app: self._approve_icon(selected_app, selected))
                preview.append(candidate)
            cancel = Gtk.Button(label="취소")
            cancel.add_css_class("luminophore-button")
            cancel.connect("clicked", lambda _button, selected=app: self._cancel_icon_drafts(selected))
            preview.append(cancel)
            block.append(preview)
        self.form.append(block)

    def _generate_icon(self, app: ApplicationRecord) -> None:
        if not self.icon_generator or app.desktop_id in self.icon_generation_busy:
            return
        self.icon_generation_busy.add(app.desktop_id)
        self.icon_generation_messages[app.desktop_id] = "원본을 분석하는 중…"
        self._build_form()

        def worker() -> None:
            try:
                drafts = self.icon_generator.generate(app)
                GLib.idle_add(self._finish_icon_generation, app, drafts, "")
            except IconGenerationError as exc:
                GLib.idle_add(self._finish_icon_generation, app, (), str(exc))
            except Exception as exc:
                GLib.idle_add(self._finish_icon_generation, app, (), f"선화 생성 실패: {exc}")

        threading.Thread(target=worker, name=f"luminophore-icon-{app.key}", daemon=True).start()

    def _finish_icon_generation(
        self,
        app: ApplicationRecord,
        drafts: tuple[IconDraft, ...],
        error: str,
    ) -> bool:
        self.icon_generation_busy.discard(app.desktop_id)
        if drafts:
            try:
                source_path = self.icon_provider.source_file(app) if self.icon_provider else None
                current = inspect_icon_source(app, source_path) if source_path else None
            except IconGenerationError:
                current = None
            if current is None or current.sha256 != drafts[0].source_sha256:
                if self.icon_generator:
                    self.icon_generator.discard(drafts)
                self.icon_generation_messages[app.desktop_id] = "분석 중 원본이 변경되어 초안을 폐기했습니다. 다시 만들어 주세요"
            else:
                self.icon_drafts[app.desktop_id] = drafts
                self.icon_generation_messages[app.desktop_id] = "원본과 후보를 비교해 적용할 선화를 누르세요"
        else:
            self.icon_generation_messages[app.desktop_id] = error or "선화 후보를 만들지 못했습니다"
        if self.category == "appearance":
            self._build_form()
        return False

    @staticmethod
    def _target_icon_name(app: ApplicationRecord) -> str:
        return app.icon_names[0] if app.icon_names else app.desktop_id.removesuffix(".desktop")

    def _approve_icon(self, app: ApplicationRecord, draft: IconDraft) -> None:
        if not self.generated_icon_store:
            return
        try:
            source_path = self.icon_provider.source_file(app) if self.icon_provider else None
            current = inspect_icon_source(app, source_path) if source_path else None
            if current is None or current.sha256 != draft.source_sha256:
                raise IconGenerationError("원본이 변경되었습니다. 새 초안을 만들어 주세요")
            self.generated_icon_store.approve(draft, self._target_icon_name(app))
        except (IconGenerationError, OSError, ValueError) as exc:
            self.icon_generation_messages[app.desktop_id] = f"적용하지 못했습니다: {exc}"
        else:
            self.icon_drafts.pop(app.desktop_id, None)
            self.icon_generation_messages[app.desktop_id] = "사용자 선화를 적용했습니다"
        self._build_form()

    def _cancel_icon_drafts(self, app: ApplicationRecord) -> None:
        drafts = self.icon_drafts.pop(app.desktop_id, ())
        if self.icon_generator:
            self.icon_generator.discard(drafts)
        self.icon_generation_messages[app.desktop_id] = "초안을 취소했습니다 · 원본 아이콘 유지"
        self._build_form()

    def _remove_generated_icon(self, app: ApplicationRecord) -> None:
        if self.generated_icon_store and self.generated_icon_store.remove(app.desktop_id):
            self.icon_generation_messages[app.desktop_id] = "사용자 선화를 삭제했습니다"
        self._build_form()

    @staticmethod
    def _set_swatches(container: Gtk.Box, palettes: dict[str, Palette]) -> None:
        _clear(container)
        if not palettes:
            empty = Gtk.Label(label="색상 정보가 없습니다")
            empty.add_css_class("muted")
            empty.set_xalign(0)
            container.append(empty)
            return
        for connector, palette in sorted(palettes.items()):
            label = Gtk.Label()
            label.set_xalign(0)
            label.set_markup(
                f'{connector}  <span foreground="{palette.primary}">●</span> {palette.primary}  '
                f'<span foreground="{palette.secondary}">●</span> {palette.secondary}'
            )
            container.append(label)

    def _append_palette_tools(self) -> None:
        title = Gtk.Label(label="현재 배경에서 색상 추출")
        title.add_css_class("section-title")
        title.set_xalign(0)
        self.form.append(title)
        help_label = Gtk.Label(label="설정에서 선택한 Hyprpaper 또는 Awww 배경에서 모니터별 색상을 만듭니다")
        help_label.add_css_class("muted")
        help_label.set_xalign(0)
        help_label.set_wrap(True)
        self.form.append(help_label)

        self.palette_source_label = Gtk.Label(label="")
        self.palette_source_label.set_xalign(0)
        self.form.append(self.palette_source_label)
        current_title = Gtk.Label(label="현재 색상")
        current_title.add_css_class("section-title")
        current_title.set_xalign(0)
        self.form.append(current_title)
        self.palette_current_swatches = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.form.append(self.palette_current_swatches)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.palette_extract_button = Gtk.Button(label="색상 추출")
        self.palette_extract_button.add_css_class("luminophore-button")
        self.palette_extract_button.connect("clicked", lambda _button: self.extract_palette())
        self.palette_spinner = Gtk.Spinner()
        controls.append(self.palette_extract_button)
        controls.append(self.palette_spinner)
        self.form.append(controls)

        self.palette_status = Gtk.Label(label="")
        self.palette_status.add_css_class("muted")
        self.palette_status.set_xalign(0)
        self.palette_status.set_wrap(True)
        self.form.append(self.palette_status)
        self.palette_preview_title = Gtk.Label(label="추출 미리보기")
        self.palette_preview_title.add_css_class("section-title")
        self.palette_preview_title.set_xalign(0)
        self.form.append(self.palette_preview_title)
        self.palette_preview_swatches = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.form.append(self.palette_preview_swatches)
        self.palette_apply_button = Gtk.Button(label="추출 색상 적용")
        self.palette_apply_button.add_css_class("luminophore-button")
        self.palette_apply_button.set_halign(Gtk.Align.END)
        self.palette_apply_button.connect("clicked", lambda _button: self._apply_extracted_palette())
        self.form.append(self.palette_apply_button)
        self._sync_palette_tools()

    def update_palette_settings(
        self,
        state: PaletteSettingsState,
        source: str,
        current: dict[str, Palette],
    ) -> None:
        previous = self.palette_state
        self.palette_state = state
        self.palette_source_name = source
        self.palette_current = dict(current)
        self._sync_palette_tools()
        if (
            state.phase == "preview"
            and state.preview
            and (previous.phase != "preview" or previous.captured_at != state.captured_at)
            and self.category == "palette"
            and self.palette_preview_swatches is not None
        ):
            GLib.idle_add(self._reveal_palette_preview)

    def _reveal_palette_preview(self) -> bool:
        adjustment = self.form_scroll.get_vadjustment()
        adjustment.set_value(max(adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size()))
        return False

    def _sync_palette_tools(self) -> None:
        if not self.palette_source_label or not self.palette_current_swatches:
            return
        source_names = {
            "hyprpaper": "Hyprpaper 추출값",
            "awww": "Awww 추출값",
            "fixed": "고정색",
        }
        self.palette_source_label.set_label(
            f"현재 색상 방식 · {source_names.get(self.palette_source_name, self.palette_source_name)}"
        )
        self._set_swatches(self.palette_current_swatches, self.palette_current)
        if self.palette_preview_swatches:
            self._set_swatches(self.palette_preview_swatches, self.palette_state.preview)
        has_preview = bool(self.palette_state.preview) and self.palette_state.phase in {"preview", "applying"}
        if self.palette_preview_title:
            self.palette_preview_title.set_visible(has_preview)
        if self.palette_preview_swatches:
            self.palette_preview_swatches.set_visible(has_preview)
        dirty = bool(self._dirty_paths())
        if self.palette_apply_button:
            self.palette_apply_button.set_visible(has_preview)
            self.palette_apply_button.set_sensitive(self.palette_state.phase == "preview" and not dirty)
            self.palette_apply_button.set_tooltip_text(
                "일반 설정 변경을 먼저 적용하거나 취소하세요" if dirty else "추출 결과를 저장하고 색상 방식으로 선택"
            )
        busy = self.palette_state.phase in {"discovering", "rendering", "applying"}
        if self.palette_extract_button:
            self.palette_extract_button.set_sensitive(not busy)
        if self.palette_spinner:
            self.palette_spinner.set_spinning(busy)
            self.palette_spinner.set_visible(busy)
        provider_names = {
            "hyprpaper": "Hyprpaper",
            "awww": "Awww",
        }
        message = self.palette_state.message
        if self.palette_state.provider and self.palette_state.phase in {"preview", "applying"}:
            message = f"{provider_names.get(self.palette_state.provider, self.palette_state.provider)} · {message}"
        if not message:
            message = "버튼을 누르면 현재 배경에서 모니터별 색상을 추출합니다"
        if dirty and has_preview:
            message += " · 추출 결과 적용 전 일반 설정 변경을 먼저 적용하거나 취소하세요"
        if self.palette_status:
            self.palette_status.set_label(message)

    def _apply_extracted_palette(self) -> None:
        if self._dirty_paths():
            if self.palette_status:
                self.palette_status.set_label("일반 설정 변경을 먼저 적용하거나 취소하세요")
            return
        if not self.apply_palette():
            return
        try:
            snapshot = self.controller.open()
        except Exception as exc:
            if self.palette_status:
                self.palette_status.set_label(f"적용 후 설정을 다시 읽지 못했습니다: {exc}")
            return
        self.snapshot = snapshot
        self.base = copy.deepcopy(snapshot.values)
        self.draft = copy.deepcopy(snapshot.values)
        if self.category == "palette":
            self._build_form()

    def _append_control(self, spec: SettingSpec) -> None:
        if spec.kind == "monitor_palette_map":
            self._append_monitor_palette_control(spec)
            return
        vertical = spec.kind in {"app_list", "taskbar_apps", "app_actions", "icon_aliases", "string_list", "color"}
        holder = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
            spacing=5 if vertical else 10,
        )
        label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        label_box.set_hexpand(True)
        label_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        label = Gtk.Label(label=spec.label)
        label.set_xalign(0)
        label_row.append(label)
        if spec.screen_picker:
            picker = Gtk.Button.new_from_icon_name("color-select-symbolic")
            picker.add_css_class("luminophore-button")
            picker.set_tooltip_text("화면의 배경 색상 선택")
            picker.connect("clicked", lambda _button, path=spec.path: self._pick_screen_color(path))
            label_row.append(picker)
            self.screen_picker_buttons[spec.path] = picker
        help_label = Gtk.Label(label=spec.help)
        help_label.add_css_class("muted")
        help_label.set_xalign(0)
        help_label.set_visible(bool(spec.help))
        label_box.append(label_row)
        label_box.append(help_label)
        holder.append(label_box)
        value = self.draft.get(spec.path)

        if spec.kind in {"integer", "decimal"}:
            lower = spec.minimum if spec.minimum is not None else -1_000_000_000
            upper = spec.maximum if spec.maximum is not None else 1_000_000_000
            control = Gtk.SpinButton.new_with_range(lower, upper, spec.step)
            control.set_digits(spec.digits)
            control.set_value(float(value))
            control.set_width_chars(10)
            control.connect(
                "value-changed",
                lambda widget, path=spec.path, integer=spec.kind == "integer": self._set_value(
                    path, widget.get_value_as_int() if integer else widget.get_value()
                ),
            )
            holder.append(control)
        elif spec.kind == "string":
            control = Gtk.Entry()
            control.set_text(str(value))
            control.set_width_chars(24)
            control.set_hexpand(False)
            control.connect("changed", lambda widget, path=spec.path: self._set_value(path, widget.get_text()))
            holder.append(control)
        elif spec.kind == "color":
            editor = ColorEditor(
                str(value),
                lambda color, path=spec.path: self._set_value(path, color),
                spec.label,
            )
            self.color_editors[spec.path] = editor
            holder.append(editor.widget)
        elif spec.kind == "font_family":
            items = font_dropdown_items(str(value), installed_font_families())
            control = Gtk.DropDown.new_from_strings(list(items.labels))
            control.set_enable_search(True)
            control.set_selected(items.selected)
            control.set_size_request(250, -1)
            control.connect(
                "notify::selected",
                lambda widget, _prop, path=spec.path, values=items.values: self._set_value(
                    path,
                    values[widget.get_selected()],
                ) if widget.get_selected() < len(values) else None,
            )
            holder.append(control)
        elif spec.kind == "boolean":
            control = Gtk.Switch()
            control.set_active(bool(value))
            control.connect("notify::active", lambda widget, _prop, path=spec.path: self._set_value(path, widget.get_active()))
            holder.append(control)
        elif spec.kind == "choice":
            labels = [label for _choice, label in spec.choices]
            control = Gtk.DropDown.new_from_strings(labels)
            values = [choice for choice, _label in spec.choices]
            control.set_selected(values.index(value) if value in values else 0)
            control.connect(
                "notify::selected",
                lambda widget, _prop, path=spec.path, options=values: self._set_value(path, options[widget.get_selected()]),
            )
            holder.append(control)
        elif spec.kind == "string_list":
            view = Gtk.TextView()
            view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            view.get_buffer().set_text("\n".join(value))
            view.get_buffer().connect(
                "changed",
                lambda buffer, path=spec.path: self._set_value(
                    path,
                    [line.strip() for line in buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).splitlines() if line.strip()],
                ),
            )
            area = Gtk.ScrolledWindow()
            area.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            area.set_min_content_height(90)
            area.set_child(view)
            holder.append(area)
        elif spec.kind in {"app_list", "taskbar_apps"}:
            editor = AppListEditor(
                self.catalog,
                list(value),
                spec.kind == "taskbar_apps",
                lambda items, path=spec.path: self._set_value(path, items),
                self.icon_provider,
            )
            holder.append(editor.widget)
        elif spec.kind == "app_actions":
            editor = AppActionsEditor(
                self.catalog,
                dict(value),
                lambda mapping, path=spec.path: self._set_value(path, mapping),
                self.icon_provider,
            )
            holder.append(editor.widget)
        elif spec.kind == "icon_aliases":
            editor = IconAliasesEditor(
                self.catalog,
                dict(value),
                lambda mapping, path=spec.path: self._set_value(path, mapping),
                self.icon_provider,
            )
            holder.append(editor.widget)
        self.form.append(holder)
        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.form.append(separator)

    def _append_monitor_palette_control(self, spec: SettingSpec) -> None:
        holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        title = Gtk.Label(label=spec.label)
        title.add_css_class("section-title")
        title.set_xalign(0)
        holder.append(title)
        help_label = Gtk.Label(label=spec.help)
        help_label.add_css_class("muted")
        help_label.set_xalign(0)
        help_label.set_wrap(True)
        holder.append(help_label)

        raw_saved = self.draft.get(_FIXED_MONITOR_PALETTES_PATH, {})
        saved = {
            str(connector): list(colors)
            for connector, colors in dict(raw_saved).items()
            if isinstance(colors, (list, tuple))
        }
        connectors = _monitor_palette_connectors(self.palette_current, saved)
        if not connectors:
            empty = Gtk.Label(label="연결되었거나 저장된 monitor가 없습니다")
            empty.add_css_class("muted")
            empty.set_xalign(0)
            holder.append(empty)

        for row_index, connector in enumerate(connectors):
            overridden = connector in saved
            primary, secondary = _monitor_palette_colors(
                connector,
                saved,
                str(self.draft.get("theme.fixed_primary", "#78DCE8")),
                str(self.draft.get("theme.fixed_secondary", "#AB9DF2")),
            )
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            card.add_css_class("notification-group")
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
            connector_label = Gtk.Label(label=connector)
            connector_label.add_css_class("section-title")
            connector_label.set_xalign(0)
            connector_label.set_hexpand(True)
            header.append(connector_label)
            state = Gtk.Label(label="직접 지정" if overridden else "미지정")
            state.add_css_class("muted")
            header.append(state)
            reset = Gtk.Button(label="지정 삭제")
            reset.add_css_class("luminophore-button")
            reset.set_sensitive(overridden)
            reset.connect("clicked", lambda _button, selected=connector: self._remove_monitor_palette(selected))
            header.append(reset)
            card.append(header)

            for role, color_index, color in (("Primary", 0, primary), ("Secondary", 1, secondary)):
                role_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                role_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
                role_label = Gtk.Label(label=role)
                role_label.set_xalign(0)
                role_header.append(role_label)
                target = f"monitor-palette-{row_index}-{color_index}"
                self.monitor_color_targets[target] = (connector, color_index)
                picker = Gtk.Button.new_from_icon_name("color-select-symbolic")
                picker.add_css_class("luminophore-button")
                picker.set_tooltip_text(f"{connector} {role} 화면 색상 선택")
                picker.connect("clicked", lambda _button, path=target: self._pick_screen_color(path))
                role_header.append(picker)
                self.screen_picker_buttons[target] = picker
                role_box.append(role_header)
                editor = ColorEditor(
                    color,
                    lambda selected_color, selected=connector, index=color_index: self._set_monitor_palette_color(
                        selected,
                        index,
                        selected_color,
                    ),
                    f"{connector} {role}",
                )
                self.color_editors[target] = editor
                role_box.append(editor.widget)
                card.append(role_box)
            holder.append(card)

        self.form.append(holder)
        self.form.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

    def _set_monitor_palette_color(self, connector: str, color_index: int, color: str) -> None:
        mapping = copy.deepcopy(dict(self.draft.get(_FIXED_MONITOR_PALETTES_PATH, {})))
        colors = list(mapping.get(connector, ()))
        if len(colors) != 2:
            colors = [
                str(self.draft.get("theme.fixed_primary", "#78DCE8")),
                str(self.draft.get("theme.fixed_secondary", "#AB9DF2")),
            ]
        colors[color_index] = color
        mapping[connector] = colors
        self._set_value(_FIXED_MONITOR_PALETTES_PATH, mapping)

    def _remove_monitor_palette(self, connector: str) -> None:
        mapping = copy.deepcopy(dict(self.draft.get(_FIXED_MONITOR_PALETTES_PATH, {})))
        if connector not in mapping:
            return
        del mapping[connector]
        self._set_value(_FIXED_MONITOR_PALETTES_PATH, mapping)
        self._build_form()

    def _pick_screen_color(self, path: str) -> None:
        if not self.screen_color_available():
            self.status.set_label("화면 스포이드를 사용하려면 hyprpicker가 필요합니다")
            self._sync_screen_picker_buttons()
            return
        started = self.begin_screen_color_pick(
            lambda result, selected=path: self._screen_color_picked(selected, result)
        )
        if not started:
            self.status.set_label("다른 색상을 선택하고 있습니다")
            self._sync_screen_picker_buttons()
            return
        self.status.set_label("설정 패널을 숨긴 뒤 배경에서 색상을 선택합니다 · Esc로 취소")
        self._sync_screen_picker_buttons()

    def _screen_color_picked(self, path: str, result: ScreenColorPickResult) -> bool:
        if result.ok:
            # The app invokes this callback on GTK's main loop. Commit the
            # canonical draft first, then mirror it into the current editor;
            # do not depend on a widget signal to preserve the picked value.
            monitor_target = getattr(self, "monitor_color_targets", {}).get(path)
            if monitor_target:
                self._set_monitor_palette_color(monitor_target[0], monitor_target[1], result.color)
            else:
                self._set_value(path, result.color)
            editor = self.color_editors.get(path)
            if editor:
                editor.set_color(result.color)
            self.status.set_label(f"{result.message} · {result.color}")
        else:
            self.status.set_label(result.message)
        self._sync_screen_picker_buttons()
        return False

    def _sync_screen_picker_buttons(self) -> None:
        available = self.screen_color_available()
        enabled = available and not self.screen_color_busy()
        for button in self.screen_picker_buttons.values():
            button.set_sensitive(enabled)
            if available:
                button.set_tooltip_text("두 모니터의 실제 화면에서 색상 선택")
            else:
                button.set_tooltip_text("hyprpicker가 설치되어 있지 않습니다")

    def _set_value(self, path: str, value: Any) -> None:
        self.draft[path] = value
        self.status.set_label("")
        self.reload_button.set_visible(False)
        self._sync_dirty()

    def _dirty_paths(self) -> set[str]:
        return {path for path in self.base if self.draft.get(path) != self.base[path]}

    def _sync_dirty(self) -> None:
        dirty = self._dirty_paths()
        self.controller.set_dirty(dirty)
        self.apply_button.set_sensitive(bool(dirty))
        suffix = f" · 변경 {len(dirty)}개" if dirty else ""
        if self.category:
            selected = next(item for item in CATEGORIES if item.category_id == self.category)
            self.category_title.set_label(selected.title + suffix)
        self.home_status.set_label(f"저장하지 않은 변경 {len(dirty)}개" if dirty else "")
        if self.system_theme_button:
            self.system_theme_button.set_sensitive(not dirty)
            self.system_theme_button.set_tooltip_text(
                "다른 설정을 먼저 적용하거나 취소하세요" if dirty else "GTK·Qt·KDE 색상 미리보기와 적용"
            )
        self._sync_palette_tools()

    def _open_system_theme(self) -> None:
        if self._dirty_paths():
            self.status.set_label("다른 설정을 먼저 적용하거나 취소하세요")
            return
        self.open_system_theme_tools()

    def _discard(self) -> None:
        self.draft = copy.deepcopy(self.base)
        self.status.set_label("변경 내용을 취소했습니다")
        self.reload_button.set_visible(False)
        self._build_form()

    def _refresh_apply_status(self, recover: bool = False) -> None:
        try:
            result = self.controller.refresh_status(recover=recover)
            if result.snapshot and result.state.phase != "completion_unknown":
                self.snapshot = result.snapshot
                self.base = copy.deepcopy(result.snapshot.values)
                self.draft = copy.deepcopy(result.snapshot.values)
                if self.category:
                    self._build_form()
            self.home_status.set_label(result.state.message)
            self.status.set_label(result.state.message)
        except (RuntimeError, ConfigError) as error:
            self.home_status.set_label(str(error))

    def _apply(self) -> None:
        if not self.snapshot:
            return
        self.apply_button.set_sensitive(False)
        result: SettingsApplyResult = self.controller.apply(self.draft, self.snapshot.digest)
        self.status.set_label(result.state.message)
        self.reload_button.set_visible(result.state.error_category == "conflict")
        if not result.ok:
            self._sync_dirty()
            return
        if result.snapshot:
            self.snapshot = result.snapshot
            self.base = copy.deepcopy(result.snapshot.values)
            self.draft = copy.deepcopy(result.snapshot.values)
        self._sync_dirty()
        self.leave()
