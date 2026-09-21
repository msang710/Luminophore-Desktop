from __future__ import annotations

import logging
from pathlib import Path
import subprocess
from typing import Callable
from urllib.parse import quote_plus

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from ..applications import ApplicationCatalog, ApplicationRecord
from ..app_icons import ApplicationIconProvider
from ..clipboard import ClipboardHistory, ClipboardItem
from ..config import LauncherConfig, TaskbarConfig
from ..emoji import search_emoji
from ..hyprland import HyprlandClient, HyprlandError, MonitorRecord, WindowRecord
from ..minimize import MinimizeController
from .dimensions import CONTROL_PANEL_HEIGHT, CONTROL_PANEL_WIDTH
from .effects import app_icon, attach_luminophore_state


LOG = logging.getLogger("luminophore-shell")


def _notify_launch_failure(label: str) -> None:
    LOG.warning("application launch failed: %s", label)
    application = Gio.Application.get_default()
    if application is not None:
        notification = Gio.Notification.new("앱 실행 실패")
        notification.set_body(f"{label}을(를) 실행하지 못했습니다")
        # Replace an earlier failure instead of accumulating notifications.
        application.send_notification("application-launch-failed", notification)


def _clear(container: Gtk.Widget) -> None:
    child = container.get_first_child()
    while child:
        following = child.get_next_sibling()
        if isinstance(container, Gtk.Box):
            container.remove(child)
        elif isinstance(container, Gtk.FlowBox):
            container.remove(child)
        child = following


def _app_image(
    app: ApplicationRecord | None,
    size: int = 22,
    provider: ApplicationIconProvider | None = None,
    catalog_revision: int = 0,
) -> Gtk.Widget:
    return app_icon(app, size, provider=provider, catalog_revision=catalog_revision)


def _group_badge_text(window_count: int) -> str:
    return str(window_count) if window_count >= 2 else ""


def _is_taskbar_window_visible(
    window: WindowRecord,
    minimized_addresses: set[str],
) -> bool:
    if window.workspace.role == "service":
        return False
    if window.address in minimized_addresses:
        return True
    if window.minimized:
        return False
    return window.mapped


def _activate_window(
    address: str,
    windows: list[WindowRecord],
    monitors: list[MonitorRecord],
    client: HyprlandClient,
    minimize: MinimizeController,
) -> None:
    # Native minimized placement owns restoration. Ordinary base-desktop windows
    # stay on their monitor and are focused in place instead of being pulled into
    # whichever numbered workspace happened to be active at click time.
    if address in minimize.records:
        minimize.restore(address)
        return
    window = next((row for row in windows if row.address == address), None)
    if not window:
        return
    client.focus(address)


def _windows_for_app(
    app: ApplicationRecord,
    windows: list[WindowRecord],
    catalog: ApplicationCatalog,
) -> list[WindowRecord]:
    desktop_id = app.desktop_id.casefold()
    matches: list[WindowRecord] = []
    for window in windows:
        app_class = window.initial_class or window.app_class
        matched = catalog.match_window_class(app_class)
        if matched and matched.desktop_id.casefold() == desktop_id:
            matches.append(window)
    return sorted(
        matches,
        key=lambda window: window.focus_history_id if window.focus_history_id >= 0 else 9999,
    )


class TaskbarWidget(Gtk.Box):
    def __init__(
        self,
        catalog: ApplicationCatalog,
        taskbar_config: TaskbarConfig,
        client: HyprlandClient,
        minimize: MinimizeController,
        open_launcher: Callable[[str], None],
        set_pinned: Callable[[str, bool], None],
        icon_provider: ApplicationIconProvider | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.catalog = catalog
        self.config = taskbar_config
        self.client = client
        self.minimize = minimize
        self.open_launcher = open_launcher
        self.set_pinned = set_pinned
        self.icon_provider = icon_provider
        self.windows: list[WindowRecord] = []
        self.monitors: list[MonitorRecord] = []
        self.visible_group_keys: tuple[str, ...] = ()

    def update(self, windows: list[WindowRecord], monitors: list[MonitorRecord]) -> None:
        self.windows = windows
        self.monitors = monitors
        _clear(self)
        minimized = set(self.minimize.records)
        visible = [
            window for window in windows
            if _is_taskbar_window_visible(window, minimized)
        ]
        groups: dict[str, list[WindowRecord]] = {}
        for window in visible:
            groups.setdefault(window.app_key, []).append(window)
        running_keys = set(groups)
        closed_pins = [key.casefold() for key in self.config.pinned if key.casefold() not in running_keys]

        def group_rank(item: tuple[str, list[WindowRecord]]) -> tuple[int, int, str]:
            key, rows = item
            try:
                pin_rank = self.config.pinned.index(key)
                pinned_rank = 0
            except ValueError:
                pin_rank = 999
                pinned_rank = 1
            focus = min((row.focus_history_id for row in rows if row.focus_history_id >= 0), default=9999)
            return (pinned_rank, pin_rank if not pinned_rank else focus, key)

        entries: list[tuple[str, list[WindowRecord]]] = [(key, []) for key in closed_pins]
        entries.extend(sorted(groups.items(), key=group_rank))
        self.visible_group_keys = tuple(key for key, _rows in entries)

        visible_entries = entries[: self.config.visible_limit]
        for key, rows in visible_entries:
            self.append(self._group_button(key, rows))
        overflow = len(entries) - len(visible_entries)
        if overflow:
            more = Gtk.Button(label=f"+{overflow}")
            more.add_css_class("luminophore-button")
            more.connect("clicked", lambda _button: self.open_launcher(""))
            attach_luminophore_state(more)
            self.append(more)
        if not entries:
            empty = Gtk.Label(label="실행 중인 앱 없음")
            empty.add_css_class("muted")
            self.append(empty)

    def _group_button(self, key: str, rows: list[WindowRecord]) -> Gtk.Button:
        sample_class = (rows[0].initial_class or rows[0].app_class) if rows else key
        app = self.catalog.match_window_class(sample_class)
        button = Gtk.Button()
        button.add_css_class("luminophore-button")
        if not rows:
            button.add_css_class("closed-pin")
        urgent = any(row.urgent for row in rows)
        if urgent:
            button.add_css_class("urgent")
        overlay = Gtk.Overlay()
        icon_holder = Gtk.Box()
        icon_holder.set_size_request(30, 26)
        icon = _app_image(app, 20, self.icon_provider, getattr(self.catalog, "revision", 0))
        icon.set_halign(Gtk.Align.CENTER)
        icon.set_valign(Gtk.Align.CENTER)
        icon_holder.append(icon)
        overlay.set_child(icon_holder)
        badge_text = _group_badge_text(len(rows))
        if badge_text:
            badge = Gtk.Label(label=badge_text)
            badge.add_css_class("taskbar-badge")
            badge.add_css_class("luminophore-key-secondary")
            badge.set_halign(Gtk.Align.END)
            badge.set_valign(Gtk.Align.END)
            overlay.add_overlay(badge)
        button.set_child(overlay)
        titles = [row.title or row.app_class for row in rows]
        button.set_tooltip_text("\n".join(titles) if titles else (app.name if app else key))

        def activate(_button: Gtk.Button) -> None:
            try:
                if len(rows) == 1:
                    _activate_window(rows[0].address, self.windows, self.monitors, self.client, self.minimize)
                elif rows:
                    self.open_launcher(sample_class)
                elif app:
                    self._submit_launch(lambda: self.catalog.launch(app), app.name)
            except HyprlandError as exc:
                LOG.warning("taskbar activation failed: app=%s error=%s", key, exc)

        button.connect("clicked", activate)
        middle = Gtk.GestureClick(button=2)
        middle.connect(
            "released",
            lambda *_args: self._submit_launch(
                lambda: self.catalog.launch_for_class(sample_class, use_preferred=False), sample_class),
        )
        button.add_controller(middle)
        secondary = Gtk.GestureClick(button=3)
        secondary.connect("released", lambda *_args: self._show_context(button, key, sample_class, rows, app))
        button.add_controller(secondary)
        attach_luminophore_state(button)
        return button

    def _show_context(
        self,
        button: Gtk.Button,
        key: str,
        sample_class: str,
        rows: list[WindowRecord],
        app: ApplicationRecord | None,
    ) -> None:
        popover = Gtk.Popover()
        popover.add_css_class("luminophore-popover")
        popover.set_parent(button)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(7)
        box.set_margin_bottom(7)
        box.set_margin_start(7)
        box.set_margin_end(7)
        for row in rows:
            title = row.title or row.app_class
            item = Gtk.Button()
            item.add_css_class("luminophore-button")
            label = Gtk.Label(label=title)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(42)
            label.set_xalign(0)
            item.set_child(label)
            item.set_tooltip_text(title)
            item.connect("clicked", lambda _button, address=row.address: self._activate_address(address))
            attach_luminophore_state(item)
            box.append(item)
        launch = Gtk.Button(label="새 창")
        launch.add_css_class("luminophore-button")
        launch.set_sensitive(app is not None)
        launch.connect(
            "clicked",
            lambda _button: self._submit_launch(
                lambda: self.catalog.launch_for_class(sample_class, use_preferred=False), sample_class),
        )
        attach_luminophore_state(launch)
        box.append(launch)
        if app:
            for action in app.actions:
                action_button = Gtk.Button(label=action.name)
                action_button.add_css_class("luminophore-button")
                action_button.connect(
                    "clicked",
                    lambda _button, selected=action.action_id: self._submit_launch(
                        lambda: self.catalog.launch(app, selected), app.name),
                )
                attach_luminophore_state(action_button)
                box.append(action_button)
        pinned = key in self.config.pinned
        pin = Gtk.Button(label="고정 해제" if pinned else "태스크바에 고정")
        pin.add_css_class("luminophore-button")
        pin.connect("clicked", lambda _button: self.set_pinned(key, not pinned))
        attach_luminophore_state(pin)
        box.append(pin)
        popover.set_child(box)
        popover.connect("closed", lambda widget: widget.unparent())
        popover.popup()

    def _activate_address(self, address: str) -> None:
        _activate_window(address, self.windows, self.monitors, self.client, self.minimize)

    def _submit_launch(self, operation: Callable[[], bool], label: str) -> None:
        def complete(success: bool) -> None:
            if not success:
                self.set_tooltip_text(f"{label}을(를) 실행하지 못했습니다")
                _notify_launch_failure(label)

        if not self.catalog.submit_launch(operation, complete):
            self.set_tooltip_text("앱 실행 요청을 처리 중입니다")


class LauncherPanel(Gtk.Box):
    def __init__(
        self,
        config: LauncherConfig,
        catalog: ApplicationCatalog,
        clipboard: ClipboardHistory,
        client: HyprlandClient,
        minimize: MinimizeController,
        close: Callable[[], None],
        resize_all_apps: Callable[[bool], None],
        choose_wallpaper: Callable[[], None],
        icon_provider: ApplicationIconProvider | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.config = config
        self.catalog = catalog
        self._launch_epoch = 0
        self.connect("unmap", self._invalidate_launch_completion)
        self.clipboard = clipboard
        self.client = client
        self.minimize = minimize
        self.close_panel = close
        self.resize_all_apps = resize_all_apps
        self.choose_wallpaper = choose_wallpaper
        self.icon_provider = icon_provider
        self.windows: list[WindowRecord] = []
        self.monitors: list[MonitorRecord] = []
        self._file_timer = 0
        self.show_all_apps = False
        self.fixed_apps: list[tuple[str, ApplicationRecord | None]] = []
        self.entry = Gtk.SearchEntry(placeholder_text="앱, 창 또는 /file · /clip · /emoji · /wallpaper · /web · / 명령")
        self.entry.set_hexpand(True)
        self.entry.connect("search-changed", self._search_changed)
        self.entry.connect("activate", self._activate_first)
        self.append(self.entry)

        self.launch_status = Gtk.Label(label="")
        self.launch_status.add_css_class("muted")
        self.launch_status.set_xalign(0)
        self.launch_status.set_ellipsize(Pango.EllipsizeMode.END)
        self.launch_status.set_single_line_mode(True)
        self.launch_status.set_visible(False)
        self.append(self.launch_status)

        self.results_title = Gtk.Label(label="검색 결과")
        self.results_title.add_css_class("section-title")
        self.results_title.add_css_class("luminophore-key-primary")
        self.results_title.set_xalign(0)
        self.append(self.results_title)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.set_propagate_natural_width(False)
        scroll.set_max_content_width(CONTROL_PANEL_WIDTH)
        self.results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.results.set_hexpand(True)
        scroll.set_child(self.results)
        self.append(scroll)

        fixed_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        fixed_title = Gtk.Label(label="고정 앱")
        fixed_title.add_css_class("section-title")
        fixed_title.add_css_class("luminophore-key-primary")
        fixed_title.set_hexpand(True)
        fixed_title.set_xalign(0)
        self.all_apps_button = Gtk.ToggleButton(label="전체 앱")
        self.all_apps_button.add_css_class("luminophore-button")
        self.all_apps_button.set_tooltip_text("설치된 모든 앱 보기")
        self.all_apps_button.connect("toggled", self._toggle_all_apps)
        attach_luminophore_state(self.all_apps_button)
        fixed_header.append(fixed_title)
        fixed_header.append(self.all_apps_button)
        self.append(fixed_header)
        self.fixed_flow = Gtk.FlowBox(
            max_children_per_line=5,
            min_children_per_line=5,
            selection_mode=Gtk.SelectionMode.NONE,
        )
        self.fixed_flow.set_column_spacing(5)
        self.fixed_flow.set_row_spacing(5)
        self.fixed_flow.set_homogeneous(True)
        self.fixed_flow.set_hexpand(True)
        self.append(self.fixed_flow)
        self.fixed_name = Gtk.Label(label="")
        self.fixed_name.add_css_class("muted")
        self.fixed_name.set_ellipsize(Pango.EllipsizeMode.END)
        self.fixed_name.set_max_width_chars(48)
        self.fixed_name.set_xalign(0)
        self.append(self.fixed_name)
        self.fixed_empty = Gtk.Label(label="고정 앱이 없습니다")
        self.fixed_empty.add_css_class("muted")
        self.fixed_empty.set_xalign(0)
        self.append(self.fixed_empty)
        self.set_size_request(CONTROL_PANEL_WIDTH, CONTROL_PANEL_HEIGHT)
        self._refresh_fixed_apps()

    def update(self, windows: list[WindowRecord], monitors: list[MonitorRecord]) -> None:
        self.windows = windows
        self.monitors = monitors
        self.refresh()

    def focus_query(self, query: str = "") -> None:
        self._set_all_apps(False)
        self.entry.set_text(query)
        self.entry.grab_focus()
        self.entry.set_position(-1)
        self.refresh()

    def _search_changed(self, _entry: Gtk.SearchEntry) -> None:
        if self.entry.get_text() and self.show_all_apps:
            self._set_all_apps(False)
        if self._file_timer:
            GLib.source_remove(self._file_timer)
            self._file_timer = 0
        if self.entry.get_text().startswith("/file"):
            self._file_timer = GLib.timeout_add(self.config.file_debounce_ms, self._file_search)
        else:
            self.refresh()

    def _file_search(self) -> bool:
        self._file_timer = 0
        query = self.entry.get_text()[5:].strip()

        def worker() -> None:
            root = str(Path(self.config.file_root).expanduser())
            try:
                result = subprocess.run(
                    ["fd", "--color", "never", "--max-results", "50", "--", query or ".", root],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                )
                paths = [Path(line) for line in result.stdout.splitlines()]
            except (OSError, subprocess.TimeoutExpired):
                paths = []
            GLib.idle_add(self._show_files, paths, query)

        import threading
        threading.Thread(target=worker, name="luminophore-file-search", daemon=True).start()
        return False

    def refresh(self) -> None:
        query = self.entry.get_text()
        self.results_title.set_label("전체 앱" if self.show_all_apps and not query else "검색 결과")
        self.launch_status.set_visible(False)
        _clear(self.results)
        self._refresh_fixed_apps()
        if query.startswith("/clip"):
            self._show_clipboard(query[5:].strip())
        elif query.startswith("/emoji"):
            self._show_emoji(query[6:].strip())
        elif query.startswith("/wallpaper"):
            self._show_wallpaper()
        elif query.startswith("/web"):
            self._show_web(query[4:].strip())
        elif query.startswith("/ ") or query == "/":
            self._show_command(query[2:] if query.startswith("/ ") else "")
        elif query.startswith("/file"):
            self._section("파일을 찾는 중")
        else:
            self._show_default(query.strip())

    def _section(self, text: str) -> None:
        label = Gtk.Label(label=text)
        label.add_css_class("section-title")
        label.add_css_class("luminophore-key-primary")
        label.set_xalign(0)
        self.results.append(label)

    def _action(self, title: str, subtitle: str, icon: Gtk.Widget, callback: Callable[[], None], disabled: bool = False) -> Gtk.Button:
        button = Gtk.Button()
        button.add_css_class("launcher-result")
        button.set_hexpand(True)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_hexpand(True)
        if isinstance(icon, Gtk.Image):
            icon.add_css_class("luminophore-symbol-primary")
        row.append(icon)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        labels.set_hexpand(True)
        name = Gtk.Label(label=title)
        name.add_css_class("luminophore-key-primary")
        name.set_hexpand(True)
        name.set_xalign(0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(44)
        name.set_single_line_mode(True)
        detail = Gtk.Label(label=subtitle)
        detail.add_css_class("muted")
        detail.set_hexpand(True)
        detail.set_xalign(0)
        detail.set_ellipsize(Pango.EllipsizeMode.END)
        detail.set_max_width_chars(44)
        detail.set_single_line_mode(True)
        labels.append(name)
        if subtitle:
            labels.append(detail)
        row.append(labels)
        button.set_child(row)
        button.set_tooltip_text("\n".join(part for part in (title, subtitle) if part))
        button.set_sensitive(not disabled)
        button.connect("clicked", lambda _button: callback())
        attach_luminophore_state(button)
        self.results.append(button)
        return button

    def _show_default(self, query: str) -> None:
        if self.show_all_apps and not query:
            for app in self.catalog.apps:
                self._append_app(app)
            if not self.catalog.apps:
                self._section("설치된 앱이 없습니다")
            return
        focused = next((window.address for window in self.windows if window.focus_history_id == 0), "")
        minimized_rows = [row for row in self.minimize.ordered() if not query or query.casefold() in (row.title + row.app_class).casefold()]
        connected = {monitor.name for monitor in self.monitors}
        if minimized_rows:
            self._section("최소화된 창")
            for item in minimized_rows:
                waiting = item.monitor_name not in connected
                self._action(
                    item.title or item.app_class,
                    "연결 대기" if waiting else f"{item.monitor_name} · 복원",
                    Gtk.Image.new_from_icon_name("go-down-symbolic"),
                    lambda address=item.address: self._restore(address),
                    waiting,
                )
        normal = [
            window for window in self.windows
            if window.address != focused and window.address not in self.minimize.records and not window.minimized
            and window.workspace.role != "service"
            and (not query or query.casefold() in (window.title + window.app_class).casefold())
        ]
        normal.sort(key=lambda window: window.focus_history_id if window.focus_history_id >= 0 else 9999)
        if normal:
            self._section("창")
            for window in normal[: self.config.window_limit]:
                self._action(
                    window.title or window.app_class,
                    window.monitor_name,
                    Gtk.Image.new_from_icon_name("window-symbolic"),
                    lambda address=window.address: self._focus(address),
                )
        apps = self.catalog.search(query, self.config.app_limit) if query else []
        if apps:
            self._section("앱")
            for app in apps:
                self._append_app(app)
        if not minimized_rows and not normal and not apps:
            self._section("결과 없음")

    def _append_app(self, app: ApplicationRecord) -> None:
        button = self._action(
            app.name,
            app.description or app.desktop_id,
            _app_image(app, provider=self.icon_provider, catalog_revision=getattr(self.catalog, "revision", 0)),
            lambda selected=app: self._activate_or_launch(selected, button),
        )
        self._attach_app_actions(button, app)

    def _set_all_apps(self, active: bool) -> None:
        self.show_all_apps = active
        self.resize_all_apps(active)
        if self.all_apps_button.get_active() != active:
            self.all_apps_button.set_active(active)

    def _toggle_all_apps(self, button: Gtk.ToggleButton) -> None:
        self.show_all_apps = button.get_active()
        self.resize_all_apps(self.show_all_apps)
        if self.show_all_apps and self.entry.get_text():
            self.entry.set_text("")
            return
        self.refresh()

    def _refresh_fixed_apps(self) -> None:
        self.fixed_apps = [
            (app_id, self.catalog.match_window_class(app_id))
            for app_id in self.config.fixed_apps
        ]
        _clear(self.fixed_flow)
        self.fixed_name.set_label("")
        self.fixed_flow.set_visible(bool(self.fixed_apps))
        self.fixed_name.set_visible(bool(self.fixed_apps))
        self.fixed_empty.set_visible(not self.fixed_apps)
        for app_id, app in self.fixed_apps:
            name = app.name if app else app_id
            button = Gtk.Button()
            button.add_css_class("app-tile")
            button.set_tooltip_text(name if app else f"{name} · 설치되지 않음")
            button.set_child(_app_image(app, 28, self.icon_provider, getattr(self.catalog, "revision", 0)))
            button.set_sensitive(app is not None)
            attach_luminophore_state(button)
            if app:
                button.connect(
                    "clicked",
                    lambda app_button, selected=app: self._activate_or_launch(selected, app_button),
                )
                self._attach_app_actions(button, app)
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda *_args, title=name: self.fixed_name.set_label(title))
            motion.connect("leave", lambda *_args: self.fixed_name.set_label(""))
            button.add_controller(motion)
            focus = Gtk.EventControllerFocus()
            focus.connect("enter", lambda _controller, title=name: self.fixed_name.set_label(title))
            focus.connect("leave", lambda _controller: self.fixed_name.set_label(""))
            button.add_controller(focus)
            self.fixed_flow.append(button)

    def _show_clipboard(self, query: str) -> None:
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="클립보드")
        title.set_hexpand(True)
        title.set_xalign(0)
        clear = Gtk.Button(label="전체 비우기")
        clear.add_css_class("luminophore-button")
        clear.connect("clicked", lambda _button: self._clear_clipboard())
        header.append(title)
        header.append(clear)
        self.results.append(header)
        rows = [item for item in self.clipboard.items if not query or query.casefold() in item.search_text.casefold()]
        for item in rows:
            if item.is_image:
                preview = f"이미지 · {item.mime_type}"
                icon = self._clipboard_image(item)
            else:
                preview = " ".join(item.text.splitlines())[:180]
                icon = Gtk.Image.new_from_icon_name("edit-paste-symbolic")
            self._action(preview, "", icon, lambda value=item: self._copy_item(value))
        if not rows:
            self._section("저장된 클립보드 항목이 없습니다")

    @staticmethod
    def _clipboard_image(item: ClipboardItem) -> Gtk.Widget:
        try:
            stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(item.data))
            pixbuf = GdkPixbuf.Pixbuf.new_from_stream_at_scale(stream, 48, 48, True, None)
            return Gtk.Image.new_from_pixbuf(pixbuf)
        except GLib.Error:
            return Gtk.Image.new_from_icon_name("image-x-generic-symbolic")

    def _show_web(self, query: str) -> None:
        title = f"Google에서 ‘{query}’ 검색" if query else "웹 검색어를 입력하세요"
        self._action(title, self.config.web_url, Gtk.Image.new_from_icon_name("web-browser-symbolic"), lambda: self._web(query), not query)

    def _show_emoji(self, query: str) -> None:
        self._section("이모지")
        rows = search_emoji(query)
        for item in rows:
            self._action(
                f"{item.glyph}  {item.name}",
                " · ".join(item.keywords),
                Gtk.Image.new_from_icon_name("face-smile-symbolic"),
                lambda value=item.glyph: self._copy(value),
            )
        if not rows:
            self._section("일치하는 이모지가 없습니다")

    def _show_wallpaper(self) -> None:
        self._section("배경화면")
        self._action(
            "이미지 선택",
            "포커스된 모니터에 적용하고 Matugen 색상을 자동 갱신합니다",
            Gtk.Image.new_from_icon_name("preferences-desktop-wallpaper-symbolic"),
            self.choose_wallpaper,
        )

    def _show_command(self, command: str) -> None:
        self._action(
            f"실행: {command}" if command else "명령을 입력하세요",
            f"새 Ghostty · {self.config.shell}",
            Gtk.Image.new_from_icon_name("utilities-terminal-symbolic"),
            lambda: self._command(command),
            not command,
        )

    def _show_files(self, paths: list[Path], query: str) -> bool:
        if not self.entry.get_text().startswith("/file") or self.entry.get_text()[5:].strip() != query:
            return False
        _clear(self.results)
        self._section("파일")
        for path in paths:
            icon = "folder-symbolic" if path.is_dir() else "text-x-generic-symbolic"
            self._action(path.name or str(path), str(path.parent), Gtk.Image.new_from_icon_name(icon), lambda value=path: self._open_path(value))
        if not paths:
            self._section("결과 없음")
        return False

    def _activate_first(self, _entry: Gtk.SearchEntry) -> None:
        child = self.results.get_first_child()
        while child and not isinstance(child, Gtk.Button):
            child = child.get_next_sibling()
        if isinstance(child, Gtk.Button) and child.get_sensitive():
            child.emit("clicked")

    def _restore(self, address: str) -> None:
        self.minimize.restore(address)
        self.close_panel()

    def _focus(self, address: str) -> None:
        _activate_window(address, self.windows, self.monitors, self.client, self.minimize)
        self.close_panel()

    def _attach_app_actions(self, button: Gtk.Button, app: ApplicationRecord) -> None:
        secondary = Gtk.GestureClick(button=3)
        secondary.connect("released", lambda *_args: self._show_app_actions(button, app))
        button.add_controller(secondary)

    def _show_app_actions(self, button: Gtk.Button, app: ApplicationRecord) -> None:
        popover = Gtk.Popover()
        popover.add_css_class("luminophore-popover")
        popover.set_parent(button)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(7)
        box.set_margin_bottom(7)
        box.set_margin_start(7)
        box.set_margin_end(7)
        default = Gtk.Button(label="새 창")
        default.add_css_class("luminophore-button")
        default.connect("clicked", lambda _button: self._launch(app, use_preferred=False))
        box.append(default)
        for action in app.actions:
            item = Gtk.Button(label=action.name)
            item.add_css_class("luminophore-button")
            item.connect(
                "clicked",
                lambda _button, selected=action.action_id: self._launch(app, selected),
            )
            box.append(item)
        popover.set_child(box)
        popover.connect("closed", lambda widget: widget.unparent())
        popover.popup()

    def _activate_or_launch(self, app: ApplicationRecord, anchor: Gtk.Widget) -> None:
        windows = _windows_for_app(app, self.windows, self.catalog)
        if not windows:
            self._launch(app)
            return
        if len(windows) == 1:
            self._focus(windows[0].address)
            return
        self._show_running_app_windows(anchor, app, windows)

    def _show_running_app_windows(
        self,
        anchor: Gtk.Widget,
        app: ApplicationRecord,
        windows: list[WindowRecord],
    ) -> None:
        popover = Gtk.Popover()
        popover.add_css_class("luminophore-popover")
        popover.set_parent(anchor)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(7)
        box.set_margin_bottom(7)
        box.set_margin_start(7)
        box.set_margin_end(7)
        for window in windows:
            item = Gtk.Button(label=window.title or app.name)
            item.add_css_class("luminophore-button")
            item.connect("clicked", lambda _button, address=window.address: self._focus(address))
            attach_luminophore_state(item)
            box.append(item)
        launch = Gtk.Button(label="새 창")
        launch.add_css_class("luminophore-button")
        launch.connect("clicked", lambda _button: self._launch(app, use_preferred=False))
        attach_luminophore_state(launch)
        box.append(launch)
        popover.set_child(box)
        popover.connect("closed", lambda widget: widget.unparent())
        popover.popup()

    def _launch(
        self,
        app: ApplicationRecord,
        action_id: str | None = None,
        *,
        use_preferred: bool = True,
    ) -> None:
        self._submit_launch(
            lambda: self.catalog.launch(app, action_id, use_preferred=use_preferred), app.name)

    def _invalidate_launch_completion(self, *_args: object) -> None:
        self._launch_epoch += 1
        self.launch_status.set_visible(False)

    def _submit_launch(self, operation: Callable[[], bool], label: str) -> None:
        epoch = self._launch_epoch

        def complete(success: bool) -> None:
            if epoch != self._launch_epoch or not self.get_mapped():
                if not success:
                    _notify_launch_failure(label)
                return
            if success:
                self.close_panel()
            else:
                self._show_launch_failure(label)

        accepted = self.catalog.submit_launch(operation, complete)
        self.launch_status.set_label(f"{label} 실행 중…" if accepted else "앱 실행 요청을 처리 중입니다")
        self.launch_status.set_tooltip_text(label)
        self.launch_status.set_visible(True)

    def _copy(self, value: str) -> None:
        self.clipboard.copy(value)
        self.close_panel()

    def _copy_item(self, item: ClipboardItem) -> None:
        self.clipboard.copy_item(item)
        self.close_panel()

    def _clear_clipboard(self) -> None:
        root = self.get_root()
        dialog = Gtk.MessageDialog(
            transient_for=root if isinstance(root, Gtk.Window) else None,
            modal=True,
            buttons=Gtk.ButtonsType.NONE,
            message_type=Gtk.MessageType.WARNING,
            text="클립보드 기록을 모두 비울까요?",
            secondary_text="현재 클립보드와 Secret Service에 저장된 기록이 함께 삭제됩니다.",
        )
        dialog.add_button("취소", Gtk.ResponseType.CANCEL)
        dialog.add_button("전체 비우기", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        dialog.connect("response", self._clipboard_clear_response)
        dialog.present()

    def _clipboard_clear_response(self, dialog: Gtk.MessageDialog, response: int) -> None:
        if response == Gtk.ResponseType.ACCEPT:
            self.clipboard.clear()
            self.refresh()
        dialog.destroy()

    def _web(self, query: str) -> None:
        url = self.config.web_url.format(query=quote_plus(query))
        self._submit_launch(lambda: self.catalog.open_uri(url), "웹 브라우저")

    def _command(self, command: str) -> None:
        shell = self.config.shell
        argv = [self.config.terminal, f"--working-directory={Path.home()}", "-e", shell, "-lc", f"{command}; exec {shell} -i"]
        self._submit_launch(lambda: self.catalog.launch_argv(argv, app_name="launcher-command"), "터미널")

    def _open_path(self, path: Path) -> None:
        uri = path.as_uri()
        self._submit_launch(lambda: self.catalog.open_uri(uri), path.name)

    def _show_launch_failure(self, label: str) -> None:
        self.launch_status.set_label(f"{label}을(를) 실행하지 못했습니다")
        self.launch_status.set_tooltip_text(label)
        self.launch_status.set_visible(True)
