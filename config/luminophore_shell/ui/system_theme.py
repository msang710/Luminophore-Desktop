from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..system_theme import SystemThemeController, SystemThemeSource, SystemThemeState


def _clear(container: Gtk.Box) -> None:
    child = container.get_first_child()
    while child:
        following = child.get_next_sibling()
        container.remove(child)
        child = following


class SystemThemeView:
    TOKEN_LABELS = (
        ("surface", "배경"),
        ("on_surface", "본문"),
        ("primary", "강조"),
        ("on_primary", "강조 위 글자"),
        ("secondary", "보조"),
        ("error", "오류"),
    )

    def __init__(
        self,
        controller: SystemThemeController,
        get_mode: Callable[[], str],
        back: Callable[[], None],
    ) -> None:
        self.controller = controller
        self.get_mode = get_mode
        self.state = controller.state
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        self.widget.set_size_request(560, 500)
        self.widget.set_hexpand(True)
        self.widget.set_vexpand(True)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        back_button = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        back_button.add_css_class("luminophore-button")
        back_button.set_tooltip_text("네온 색상 설정으로 돌아가기")
        back_button.connect("clicked", lambda _button: back())
        title = Gtk.Label(label="시스템 팔레트")
        title.add_css_class("section-title")
        title.set_hexpand(True)
        title.set_xalign(0)
        header.append(back_button)
        header.append(title)
        self.widget.append(header)

        self.source = Gtk.Label(label="")
        self.source.set_xalign(0)
        self.widget.append(self.source)

        mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        mode_label = Gtk.Label(label="시스템 밝기")
        mode_label.set_hexpand(True)
        mode_label.set_xalign(0)
        self.mode = Gtk.DropDown.new_from_strings(["다크", "라이트"])
        self.mode.connect("notify::selected", lambda *_args: self._sync_controls())
        mode_row.append(mode_label)
        mode_row.append(self.mode)
        self.widget.append(mode_row)

        seed_title = Gtk.Label(label="왼쪽 주 모니터 네온색")
        seed_title.add_css_class("section-title")
        seed_title.set_xalign(0)
        self.widget.append(seed_title)
        self.seed_swatches = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.widget.append(self.seed_swatches)

        preview_title = Gtk.Label(label="시스템 semantic 미리보기")
        preview_title.add_css_class("section-title")
        preview_title.set_xalign(0)
        self.widget.append(preview_title)
        self.preview_swatches = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.widget.append(self.preview_swatches)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        controls.set_halign(Gtk.Align.END)
        self.rollback_button = Gtk.Button(label="이전 팔레트 복구")
        self.rollback_button.add_css_class("luminophore-button")
        self.rollback_button.connect("clicked", lambda _button: self.controller.rollback())
        self.preview_button = Gtk.Button(label="미리보기")
        self.preview_button.add_css_class("luminophore-button")
        self.preview_button.connect("clicked", lambda _button: self.controller.preview(self.selected_mode()))
        self.apply_button = Gtk.Button(label="시스템에도 적용")
        self.apply_button.add_css_class("luminophore-button")
        self.apply_button.connect("clicked", lambda _button: self._apply())
        self.spinner = Gtk.Spinner()
        controls.append(self.rollback_button)
        controls.append(self.preview_button)
        controls.append(self.apply_button)
        controls.append(self.spinner)
        self.widget.append(controls)

        self.status = Gtk.Label(label="")
        self.status.add_css_class("muted")
        self.status.set_xalign(0)
        self.status.set_wrap(True)
        self.widget.append(self.status)

    def selected_mode(self) -> str:
        return "light" if self.mode.get_selected() == 1 else "dark"

    def open(self) -> None:
        self.mode.set_selected(1 if self.get_mode() == "light" else 0)
        self.update(self.controller.open())

    def _source(self) -> SystemThemeSource | None:
        try:
            return self.controller.source()
        except Exception:
            return None

    def _set_seed(self, source: SystemThemeSource | None) -> None:
        _clear(self.seed_swatches)
        if source is None:
            self.source.set_label("기준 화면 · 왼쪽 주 작업 모니터를 찾을 수 없음")
            empty = Gtk.Label(label="팔레트를 사용할 수 없습니다")
            empty.add_css_class("muted")
            empty.set_xalign(0)
            self.seed_swatches.append(empty)
            return
        self.source.set_label(f"기준 화면 · {source.connector} · 왼쪽 주 작업 모니터")
        label = Gtk.Label()
        label.set_xalign(0)
        label.set_markup(
            f'<span foreground="{source.palette.primary}">●</span> primary {source.palette.primary}  '
            f'<span foreground="{source.palette.secondary}">●</span> secondary {source.palette.secondary}'
        )
        self.seed_swatches.append(label)

    def _set_preview(self, state: SystemThemeState) -> None:
        _clear(self.preview_swatches)
        if not state.preview:
            empty = Gtk.Label(label="미리보기를 생성하면 GTK·Qt·KDE·Kitty·Alacritty·Btop 색을 확인할 수 있습니다")
            empty.add_css_class("muted")
            empty.set_xalign(0)
            empty.set_wrap(True)
            self.preview_swatches.append(empty)
            return
        tokens = state.preview.semantic.tokens
        for key, title in self.TOKEN_LABELS:
            value = tokens.get(key, "")
            label = Gtk.Label()
            label.set_xalign(0)
            label.set_markup(f'{title}  <span foreground="{value}">●</span> {value}')
            self.preview_swatches.append(label)

    def update(self, state: SystemThemeState) -> None:
        self.state = state
        self._set_seed(self._source())
        self._set_preview(state)
        message = state.message
        if state.drifted and not message:
            message = "다른 도구가 시스템 팔레트를 변경했습니다 · 다시 미리보기 후 적용할 수 있습니다"
        self.status.set_label(message or "미리보기는 시스템 파일을 변경하지 않습니다")
        self._sync_controls()

    def _sync_controls(self) -> None:
        busy = self.state.phase in {"generating", "applying", "rolling_back"}
        preview_matches = bool(
            self.state.preview
            and self.state.preview.semantic.mode == self.selected_mode()
            and self.state.phase in {"preview", "error"}
        )
        self.mode.set_sensitive(not busy)
        self.preview_button.set_sensitive(not busy and self._source() is not None)
        self.apply_button.set_visible(bool(self.state.preview))
        self.apply_button.set_sensitive(not busy and preview_matches)
        self.rollback_button.set_visible(self.state.backup_available)
        self.rollback_button.set_sensitive(not busy and self.state.backup_available)
        self.spinner.set_visible(busy)
        self.spinner.set_spinning(busy)

    def _apply(self) -> None:
        preview = self.state.preview
        if preview and preview.semantic.mode == self.selected_mode():
            self.controller.apply(preview.preview_id)
