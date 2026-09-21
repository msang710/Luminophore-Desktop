from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol
from uuid import uuid4

from .settings_contract import (
    SettingsApplyPhase,
    SettingsApplyRequest,
    SettingsApplyResult,
    SettingsResultCategory,
    SettingsRoute,
    SettingsSnapshot,
)


APPLICATION_ID = "io.github.msang710.LuminophoreSettings"


def _route_from_arguments(
    arguments: list[str] | tuple[str, ...], fallback: SettingsRoute,
) -> SettingsRoute:
    """Extract a deep link from a local or forwarded GApplication command line."""
    values = tuple(str(value) for value in arguments)
    for index, value in enumerate(values):
        candidate = ""
        if value.startswith("--page="):
            candidate = value.partition("=")[2]
        elif value == "--page" and index + 1 < len(values):
            candidate = values[index + 1]
        if candidate:
            try:
                return SettingsRoute(candidate)
            except ValueError:
                return SettingsRoute.HOME
    return fallback


class SettingsBackendFacade(Protocol):
    def snapshot(self) -> SettingsSnapshot: ...

    def apply(self, request: SettingsApplyRequest) -> SettingsApplyResult: ...

    def settings_status(self, request_id: str) -> SettingsApplyResult | None: ...

    def recover_settings(self, request_id: str) -> SettingsApplyResult | None: ...

    def capabilities(self) -> Mapping[str, bool]: ...

    def binding_snapshot(self) -> Mapping[str, Any]: ...

    def apply_bindings(
        self, expected_digest: str, changes: Mapping[str, Mapping[str, Any]],
    ) -> Mapping[str, Any]: ...

    def system_theme_status(self) -> Mapping[str, Any]: ...

    def preview_system_theme(self, mode: str) -> Mapping[str, Any]: ...

    def apply_system_theme(self, preview_id: str) -> Mapping[str, Any]: ...

    def rollback_system_theme(self) -> Mapping[str, Any]: ...

    def preview_appearance(
        self, request_id: str, sources: Mapping[str, str], mode: str,
    ) -> Mapping[str, Any]: ...

    def apply_appearance(self, request_id: str, preview_id: str) -> Mapping[str, Any]: ...

    def appearance_status(self, request_id: str) -> Mapping[str, Any]: ...

    def appearance_context(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SettingsAppState:
    route: SettingsRoute = SettingsRoute.HOME
    online: bool = False
    busy: bool = False
    conflict: bool = False
    completion_unknown: bool = False
    pending_next_start: bool = False
    pending_hyprland_reload: bool = False
    rollback_failed: bool = False
    backend_incompatible: bool = False
    message: str = ""

    @property
    def banner(self) -> str:
        if self.backend_incompatible:
            return "실행 중인 Shell이 이 설정 앱과 호환되지 않습니다. Shell 업데이트 후 다시 시도하세요"
        if self.rollback_failed:
            return "적용과 자동 복구가 모두 실패했습니다. 현재 상태를 확인하세요"
        if self.completion_unknown:
            return "적용 결과가 불확실합니다. 적용 상태 확인을 누르세요"
        if self.conflict:
            return "설정이 외부에서 변경되었습니다. 다시 불러오세요"
        if self.busy:
            return "다른 설정을 적용하는 중입니다"
        if self.pending_next_start:
            return "저장했습니다. 다음 Shell 시작 시 적용됩니다"
        if self.pending_hyprland_reload:
            return "저장했습니다. Hyprland 설정을 다시 불러오면 적용됩니다"
        if not self.online:
            return "Shell이 실행 중이 아닙니다. 일반 설정만 저장할 수 있습니다"
        return self.message


class SettingsAppModel:
    def __init__(self, backend: SettingsBackendFacade) -> None:
        self.backend = backend
        self.state = SettingsAppState()
        self.snapshot: SettingsSnapshot | None = None
        self.binding_data: Mapping[str, Any] = {}
        self.system_theme_data: Mapping[str, Any] = {}
        self.appearance_data: Mapping[str, Any] = {}
        self.settings_request_id = ""
        self.appearance_request_id = ""
        self.appearance_preview_id = ""
        self.appearance_monitors: tuple[str, ...] = ()
        self._capabilities: dict[str, bool] = {}

    @staticmethod
    def parse_route(value: str | SettingsRoute | None) -> SettingsRoute:
        if isinstance(value, SettingsRoute):
            return value
        try:
            return SettingsRoute(value or SettingsRoute.HOME.value)
        except ValueError:
            return SettingsRoute.HOME

    def activate(self, route: str | SettingsRoute | None = None) -> SettingsAppState:
        selected = self.parse_route(route)
        pending_unknown = self.state.completion_unknown
        try:
            self.snapshot = self.backend.snapshot()
        except RuntimeError:
            self.snapshot = None
            self._capabilities = {}
            self.state = SettingsAppState(
                route=selected,
                online=False,
                backend_incompatible=True,
                completion_unknown=pending_unknown,
            )
            return self.state
        self.state = SettingsAppState(route=selected, online=self.snapshot.online, completion_unknown=pending_unknown)
        try:
            self._capabilities = {
                str(key): bool(value) for key, value in self.backend.capabilities().items()
            }
        except RuntimeError:
            self._capabilities = {}
        if self.snapshot.online and callable(getattr(self.backend, "settings_status", None)):
            try:
                pending = self.backend.settings_status("")
                if pending is not None:
                    self.settings_request_id = pending.request_id
                    self.consume_result(pending)
            except RuntimeError:
                pass
        return self.state

    def navigate(self, route: str | SettingsRoute) -> SettingsAppState:
        self.state = replace(self.state, route=self.parse_route(route))
        return self.state

    def capability_enabled(self, capability: str) -> bool:
        return self.state.online and not self.state.busy and self.capability_available(capability)

    def capability_available(self, capability: str) -> bool:
        return bool(self._capabilities.get(capability, False))

    @property
    def values(self) -> dict[str, Any]:
        return dict(self.snapshot.values) if self.snapshot is not None else {}

    def values_for(self, prefix: str) -> dict[str, Any]:
        marker = prefix + "."
        return {key: value for key, value in self.values.items() if key.startswith(marker)}

    def apply_settings(self, changes: Mapping[str, Any], request_id: str | None = None) -> SettingsAppState:
        if self.snapshot is None:
            raise RuntimeError("settings snapshot is not loaded")
        if self.state.completion_unknown:
            raise RuntimeError("적용 상태를 먼저 확인하세요")
        self.settings_request_id = request_id or f"settings-{uuid4().hex}"
        result = self.backend.apply(SettingsApplyRequest.build(
            self.settings_request_id, self.snapshot.digest, changes,
        ))
        self.consume_result(result)
        if result.category is SettingsResultCategory.BUSY:
            pending = self.backend.settings_status("")
            if pending is not None:
                self.settings_request_id = pending.request_id
                self.consume_result(pending)
                if not self.state.completion_unknown:
                    self.snapshot = self.backend.snapshot()
        if result.category in {
            SettingsResultCategory.OK,
            SettingsResultCategory.SAVED_PENDING_NEXT_START,
            SettingsResultCategory.PENDING_HYPRLAND_RELOAD,
        }:
            values = self.values
            values.update(changes)
            self.snapshot = SettingsSnapshot.build(result.digest, values, self.snapshot.online)
        return self.state

    def refresh_settings_status(self, *, recover: bool = False) -> SettingsAppState:
        if not self.settings_request_id:
            return self.state
        result = (self.backend.recover_settings(self.settings_request_id) if recover
                  else self.backend.settings_status(self.settings_request_id))
        if result is None:
            pending = self.backend.settings_status("")
            if pending is None:
                return self.state
            self.settings_request_id = pending.request_id
            result = pending
        if result.request_id != self.settings_request_id:
            raise RuntimeError("다른 설정 요청의 상태가 반환되었습니다")
        self.consume_result(result)
        if result.category in {SettingsResultCategory.OK, SettingsResultCategory.CONFLICT}:
            self.snapshot = self.backend.snapshot()
        return self.state

    def load_bindings(self) -> Mapping[str, Any]:
        self.binding_data = self.backend.binding_snapshot()
        return self.binding_data

    def apply_binding_changes(self, changes: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]:
        digest = str(self.binding_data.get("digest", ""))
        if not digest:
            raise RuntimeError("binding snapshot is not loaded")
        result = self.backend.apply_bindings(digest, changes)
        self.binding_data = {**self.binding_data, "digest": result.get("digest", digest)}
        self.state = replace(
            self.state,
            pending_hyprland_reload=bool(result.get("reload_required")),
            message="Key binding을 저장했습니다",
        )
        return result

    def load_system_theme(self) -> Mapping[str, Any]:
        self.system_theme_data = self.backend.system_theme_status()
        return self.system_theme_data

    def preview_system_theme(self, mode: str) -> Mapping[str, Any]:
        self.system_theme_data = self.backend.preview_system_theme(mode)
        return self.system_theme_data

    def apply_system_theme(self) -> Mapping[str, Any]:
        preview = self.system_theme_data.get("preview")
        preview_id = str(preview.get("preview_id", "")) if isinstance(preview, Mapping) else ""
        if not preview_id:
            raise RuntimeError("system theme preview is not ready")
        self.system_theme_data = self.backend.apply_system_theme(preview_id)
        return self.system_theme_data

    def rollback_system_theme(self) -> Mapping[str, Any]:
        self.system_theme_data = self.backend.rollback_system_theme()
        return self.system_theme_data

    def load_appearance_context(self) -> Mapping[str, Any]:
        self.appearance_data = self.backend.appearance_context()
        self.appearance_monitors = tuple(sorted(str(value) for value in self.appearance_data.get("monitors", ())))
        return self.appearance_data

    def preview_appearance(self, sources: Mapping[str, str], mode: str) -> Mapping[str, Any]:
        self.appearance_request_id = f"appearance-preview-{uuid4().hex}"
        self.appearance_preview_id = ""
        self.appearance_data = self.backend.preview_appearance(self.appearance_request_id, sources, mode)
        return self.appearance_data

    def refresh_appearance(self) -> Mapping[str, Any]:
        if not self.appearance_request_id:
            raise RuntimeError("appearance request is not active")
        self.appearance_data = self.backend.appearance_status(self.appearance_request_id)
        if self.appearance_data.get("phase") == "ready":
            self.appearance_preview_id = str(self.appearance_data.get("preview_id", ""))
        return self.appearance_data

    def apply_appearance(self) -> Mapping[str, Any]:
        if not self.appearance_preview_id:
            raise RuntimeError("appearance preview is not ready")
        self.appearance_request_id = f"appearance-apply-{uuid4().hex}"
        self.appearance_data = self.backend.apply_appearance(
            self.appearance_request_id, self.appearance_preview_id,
        )
        return self.appearance_data

    def consume_result(self, result: SettingsApplyResult) -> SettingsAppState:
        category = result.category
        phase = result.phase
        self.state = replace(
            self.state,
            busy=category is SettingsResultCategory.BUSY or phase in {
                SettingsApplyPhase.VALIDATING,
                SettingsApplyPhase.APPLYING,
                SettingsApplyPhase.VERIFYING,
                SettingsApplyPhase.ROLLING_BACK,
            },
            conflict=category is SettingsResultCategory.CONFLICT,
            completion_unknown=category is SettingsResultCategory.COMPLETION_UNKNOWN
            or phase is SettingsApplyPhase.COMPLETION_UNKNOWN,
            pending_next_start=category is SettingsResultCategory.SAVED_PENDING_NEXT_START
            or phase is SettingsApplyPhase.PENDING_NEXT_START,
            pending_hyprland_reload=category is SettingsResultCategory.PENDING_HYPRLAND_RELOAD
            or phase is SettingsApplyPhase.PENDING_HYPRLAND_RELOAD,
            rollback_failed=category is SettingsResultCategory.ROLLBACK_FAILED,
            # IPC/backend diagnostics can contain private local paths.  The
            # standalone surface renders the typed result, never raw details.
            message="설정을 적용했습니다" if category is SettingsResultCategory.OK else "",
        )
        return self.state


try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, GLib, Gtk
except (ImportError, ValueError):  # pragma: no cover - exercised on installed GTK runtime
    Gio = GLib = Gtk = None  # type: ignore[assignment]


if Gtk is not None:
    class LuminophoreSettingsApplication(Gtk.Application):
        def __init__(self, backend: SettingsBackendFacade, route: str | SettingsRoute = SettingsRoute.HOME) -> None:
            super().__init__(
                application_id=APPLICATION_ID,
                flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
            )
            self.model = SettingsAppModel(backend)
            self._requested_route = self.model.parse_route(route)
            self.window: Gtk.ApplicationWindow | None = None
            self.stack: Gtk.Stack | None = None
            self.banner: Gtk.Label | None = None
            self._setting_entries: dict[str, Gtk.Entry] = {}
            self._binding_entries: dict[str, tuple[Gtk.Entry, Mapping[str, Any]]] = {}
            self._binding_rows: dict[str, tuple[Gtk.Widget, str]] = {}
            self._appearance_sources: dict[str, str] = {}
            self._appearance_status: dict[SettingsRoute, Gtk.Label] = {}
            self._appearance_apply: dict[SettingsRoute, Gtk.Button] = {}
            self._appearance_preview: dict[SettingsRoute, tuple[Gtk.Button, ...]] = {}
            self._system_theme_status: Gtk.Label | None = None
            self._system_theme_apply: Gtk.Button | None = None
            self._settings_status_button: Gtk.Button | None = None
            self._settings_recover_button: Gtk.Button | None = None
            self._visual_controls: dict[str, Any] = {}
            self.connect("activate", self._activate_window)
            self.connect("command-line", self._command_line)

        def run(self, argv: list[str] | None = None) -> int:
            arguments = list(argv or ["luminophore-settings"])
            if not any(value == "--page" or value.startswith("--page=") for value in arguments[1:]):
                arguments.append(f"--page={self._requested_route.value}")
            return super().run(arguments)

        def _command_line(self, _application: Gtk.Application, command_line: Gio.ApplicationCommandLine) -> int:
            self._requested_route = _route_from_arguments(
                list(command_line.get_arguments()), self._requested_route,
            )
            self.activate()
            return 0

        def request_route(self, route: str | SettingsRoute) -> None:
            self._requested_route = self.model.parse_route(route)
            if self.window is not None:
                self.model.navigate(self._requested_route)
                self._show_route()
                self.window.present()

        def _activate_window(self, _application: Gtk.Application) -> None:
            self.model.activate(self._requested_route)
            if self.window is None:
                self.window = Gtk.ApplicationWindow(application=self)
                self.window.set_title("Luminophore 설정")
                self.window.set_default_size(940, 680)
                self.window.set_size_request(420, 320)
                self.window.add_css_class("luminophore-surface")
                from .config import load_config
                from .theme import build_css
                provider = Gtk.CssProvider()
                provider.load_from_string(build_css(load_config().theme, {}))
                display = self.window.get_display()
                Gtk.StyleContext.add_provider_for_display(
                    display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )
                shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                shell.add_css_class("luminophore-panel")
                self.banner = Gtk.Label(xalign=0)
                self.banner.add_css_class("warning")
                shell.append(self.banner)
                self._settings_status_button = Gtk.Button(label="적용 상태 확인")
                self._settings_status_button.connect("clicked", self._refresh_settings_status)
                self._settings_status_button.set_visible(False)
                shell.append(self._settings_status_button)
                self._settings_recover_button = Gtk.Button(label="미확정 요청 종료 · 현재 효과 유지")
                self._settings_recover_button.connect("clicked", lambda button: self._refresh_settings_status(button, recover=True))
                self._settings_recover_button.set_visible(False)
                shell.append(self._settings_recover_button)
                content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
                navigation = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                self.stack = Gtk.Stack()
                self.stack.set_hexpand(True)
                self.stack.set_vexpand(True)
                for route in SettingsRoute:
                    button = Gtk.Button(label=route.value)
                    button.connect("clicked", lambda _button, selected=route: self.request_route(selected))
                    navigation.append(button)
                    self.stack.add_named(self._build_page(route), route.value)
                content.append(navigation)
                content.append(self.stack)
                shell.append(content)
                self.window.set_child(shell)
            self._show_route()
            self.window.present()

        def _page(self, title: str) -> Gtk.Box:
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            for margin in ("top", "bottom", "start", "end"):
                getattr(page, f"set_margin_{margin}")(18)
            heading = Gtk.Label(label=title, xalign=0)
            heading.add_css_class("title-1")
            page.append(heading)
            return page

        @staticmethod
        def _scrolled(page: Gtk.Widget) -> Gtk.ScrolledWindow:
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_hexpand(True)
            scrolled.set_vexpand(True)
            scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scrolled.set_child(page)
            return scrolled

        def _append_placement_controls(self, page: Gtk.Box) -> None:
            from .placement_rules import PlacementRules, DIRECTIONS
            service = PlacementRules()
            page.append(Gtk.Label(label="앱별 처음 나타날 위치", xalign=0))
            page.append(Gtk.Label(label="Shell·앱 실행 단축키에서 직접 열 때 적용 · 출처 창 배치 우선", xalign=0))
            listing = Gtk.Label(xalign=0, selectable=True, wrap=True)
            page.append(listing)
            app = Gtk.Entry(placeholder_text="앱 ID (예: firefox, steam)")
            page.append(app)
            direction = Gtk.DropDown.new_from_strings(list(DIRECTIONS.values()))
            page.append(direction)
            choose = Gtk.DropDown.new_from_strings(["실행 중인 앱 선택"])
            choose.set_sensitive(False)
            page.append(choose)
            running_apps = []
            def select_running(_widget, _param):
                index = choose.get_selected()
                if 0 < index <= len(running_apps):
                    app.set_text(running_apps[index - 1])
            choose.connect("notify::selected", select_running)
            find_apps = Gtk.Button(label="실행 중인 앱 목록 가져오기")
            def load_running(*_args):
                from threading import Thread
                from .hyprland import HyprlandClient
                find_apps.set_sensitive(False)
                def read():
                    try:
                        values = sorted({w.initial_class or w.app_class for w in HyprlandClient().windows()} - {""})
                        error = ""
                    except RuntimeError as exc:
                        values, error = [], str(exc)
                    def done():
                        running_apps[:] = values
                        choose.set_model(Gtk.StringList.new(["실행 중인 앱 선택", *values]))
                        choose.set_selected(0)
                        choose.set_sensitive(bool(values))
                        find_apps.set_sensitive(True)
                        if error:
                            status.set_text(error)
                        return False
                    GLib.idle_add(done)
                Thread(target=read, daemon=True).start()
            find_apps.connect("clicked", load_running)
            page.append(find_apps)
            status = Gtk.Label(xalign=0, wrap=True)
            state = {"digest": None}

            def refresh(*_args):
                try:
                    rules, state["digest"] = service.snapshot()
                    listing.set_text("\n".join(f"{key} → {DIRECTIONS[value]}" for key, value in rules.items()) or "등록된 앱 규칙 없음")
                    status.set_text("")
                except (OSError, ValueError) as exc:
                    state["digest"] = None
                    status.set_text(str(exc))

            def save(*_args):
                if state["digest"] is None:
                    status.set_text("설정을 먼저 새로고침하세요")
                    return
                selected_app = app.get_text().strip()
                selected_direction = list(DIRECTIONS)[direction.get_selected()]
                digest = state["digest"]
                save_button.set_sensitive(False)
                status.set_text("배치 규칙을 적용하는 중…")
                from threading import Thread
                def apply_rule():
                    try:
                        result = service.set(selected_app, selected_direction, digest)
                        error = None
                    except (OSError, ValueError, RuntimeError) as exc:
                        result, error = None, str(exc)
                    def complete():
                        save_button.set_sensitive(True)
                        if error:
                            status.set_text(error)
                        else:
                            rules, state["digest"] = result
                            listing.set_text("\n".join(f"{key} → {DIRECTIONS[value]}" for key, value in rules.items()) or "등록된 앱 규칙 없음")
                            status.set_text("저장·적용 완료 · 새 창부터 적용됩니다")
                        return False
                    GLib.idle_add(complete)
                Thread(target=apply_rule, daemon=True).start()

            save_button = Gtk.Button(label="배치 규칙 저장 및 적용 · 기본 선택 시 해제")
            save_button.connect("clicked", save)
            refresh_button = Gtk.Button(label="목록 새로고침")
            refresh_button.connect("clicked", refresh)
            page.append(save_button)
            page.append(refresh_button)
            page.append(status)
            refresh()

        def _build_page(self, route: SettingsRoute) -> Gtk.Widget:
            page = self._page(route.value)
            if route is SettingsRoute.APPEARANCE:
                self._append_visual_controls(page)
            if route is SettingsRoute.COMPOSITOR:
                from .ui.settings_monitors import MonitorSettings
                page.append(MonitorSettings())
                self._append_placement_controls(page)
                from .ui.settings_bundles import BundleSettings
                page.append(BundleSettings())
            if route in {SettingsRoute.COMPOSITOR, SettingsRoute.MOTION}:
                from .settings_schema import SETTINGS, SHADOW_STYLES, shadow_style
                specs = {spec.path: spec for spec in SETTINGS}
                for key, value in self.model.values_for(route.value).items():
                    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                    spec = specs.get(key)
                    row.append(Gtk.Label(label=spec.label if spec else key.removeprefix(route.value + "."), xalign=0))
                    entry = Gtk.Entry(text=str(value))
                    entry.set_hexpand(True)
                    self._setting_entries[key] = entry
                    row.append(entry)
                    if not spec or spec.visible:
                        if spec and spec.help:
                            row.set_tooltip_text(spec.help)
                        page.append(row)
                if route is SettingsRoute.COMPOSITOR:
                    names = ["사용자 지정", *SHADOW_STYLES]
                    style = Gtk.DropDown.new_from_strings(names)
                    style.set_selected(names.index(shadow_style(self.model.values)))
                    def change_shadow_style(control, _param):
                        selected = control.get_selected()
                        if selected == 0 or selected >= len(names):
                            return
                        for key, value in SHADOW_STYLES[names[selected]].items():
                            self._setting_entries["compositor." + key].set_text(str(value))
                    style.connect("notify::selected", change_shadow_style)
                    page.append(Gtk.Label(label="그림자 스타일", xalign=0))
                    page.append(style)
                apply_button = Gtk.Button(label="적용")
                apply_button.connect("clicked", self._apply_typed_settings, route.value)
                apply_button.set_sensitive(
                    not self.model.state.online or self.model.capability_available("settings_apply")
                )
                if self.model.state.online and not self.model.capability_available("settings_apply"):
                    apply_button.set_tooltip_text("설정 저장 backend을 사용할 수 없습니다")
                page.append(apply_button)
            elif route is SettingsRoute.BINDINGS:
                if not self.model.capability_enabled("bindings"):
                    page.append(Gtk.Label(label="Key binding backend을 사용할 수 없습니다", xalign=0))
                    return self._scrolled(page)
                try:
                    data = self.model.load_bindings()
                except RuntimeError:
                    page.append(Gtk.Label(label="Shell 연결 후 key binding을 편집할 수 있습니다", xalign=0))
                    return self._scrolled(page)
                search = Gtk.SearchEntry(placeholder_text="동작 또는 key 검색")
                search.connect("search-changed", self._filter_bindings)
                page.append(search)
                current_category = ""
                for row_data in data.get("bindings", ()):
                    if not isinstance(row_data, Mapping):
                        continue
                    category = str(row_data.get("category", "other"))
                    if category != current_category:
                        current_category = category
                        heading = Gtk.Label(label=category, xalign=0)
                        heading.add_css_class("heading")
                        page.append(heading)
                    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                    label = str(row_data.get("label", row_data.get("action_id", "")))
                    row.append(Gtk.Label(label=label, xalign=0))
                    entry = Gtk.Entry(text=str(row_data.get("chord") or ""))
                    entry.set_hexpand(True)
                    action_id = str(row_data.get("action_id", ""))
                    self._binding_entries[action_id] = (entry, row_data)
                    self._binding_rows[action_id] = (row, f"{action_id} {label} {category}".casefold())
                    row.append(entry)
                    page.append(row)
                apply_button = Gtk.Button(label="키 바인딩 저장")
                apply_button.connect("clicked", self._apply_bindings)
                page.append(apply_button)
            elif route is SettingsRoute.SYSTEM_THEME:
                if not self.model.capability_enabled("system_theme"):
                    page.append(Gtk.Label(label="설치된 시스템 테마 target을 사용할 수 없습니다", xalign=0))
                    return self._scrolled(page)
                try:
                    status = self.model.load_system_theme()
                except RuntimeError:
                    page.append(Gtk.Label(label="Shell 연결 후 시스템 테마를 적용할 수 있습니다", xalign=0))
                    return self._scrolled(page)
                self._system_theme_status = Gtk.Label(label=str(status.get("phase", "idle")), xalign=0)
                page.append(self._system_theme_status)
                for mode in ("dark", "light"):
                    button = Gtk.Button(label=f"{mode} 미리보기")
                    button.connect("clicked", self._preview_theme, mode)
                    page.append(button)
                apply_button = Gtk.Button(label="테마 적용")
                apply_button.connect("clicked", self._apply_theme)
                apply_button.set_sensitive(False)
                self._system_theme_apply = apply_button
                page.append(apply_button)
                rollback_button = Gtk.Button(label="이전 테마로 복구")
                rollback_button.connect("clicked", self._rollback_theme)
                page.append(rollback_button)
                refresh_button = Gtk.Button(label="상태 새로고침")
                refresh_button.connect("clicked", self._refresh_theme)
                page.append(refresh_button)
            elif route == SettingsRoute.WALLPAPER:
                page.append(Gtk.Label(label="양쪽 화면을 하나의 장면으로 제작하고 선택합니다.", xalign=0))
                create = Gtk.Button(label="배경화면 제작")
                create.connect("clicked", self._choose_appearance_source, "", route)
                page.append(create)
                picker = Gtk.Button(label="저장된 장면 선택")
                status = Gtk.Label(xalign=0, wrap=True)
                def open_picker(_button):
                    from .ipc import IpcClient, IpcError
                    try:
                        reply = IpcClient().request({"command": "wallpaper", "action": "open"})
                        status.set_label("" if reply.get("ok") else str(reply.get("error", "장면 선택기를 열 수 없습니다")))
                    except (IpcError, OSError) as exc:
                        status.set_label(f"Shell 연결이 필요합니다: {exc}")
                picker.connect("clicked", open_picker)
                page.append(picker)
                page.append(status)
            elif route in {SettingsRoute.APPEARANCE, SettingsRoute.PALETTE}:
                if not self.model.capability_enabled("appearance"):
                    page.append(Gtk.Label(label="Wallpaper backend을 사용할 수 없습니다", xalign=0))
                    return self._scrolled(page)
                try:
                    context = self.model.load_appearance_context()
                except RuntimeError:
                    page.append(Gtk.Label(label="Shell 연결 후 배경화면을 적용할 수 있습니다", xalign=0))
                    return self._scrolled(page)
                page.append(Gtk.Label(label=f"provider: {context.get('provider', 'unavailable')}", xalign=0))
                monitors = context.get("monitors", ())
                for connector in map(str, monitors):
                    button = Gtk.Button(label=f"{connector} · 배경화면 선택")
                    button.connect("clicked", self._choose_appearance_source, connector, route)
                    page.append(button)
                preview_buttons: list[Gtk.Button] = []
                for mode in ("dark", "light"):
                    preview_button = Gtk.Button(label=f"{mode} 색상 미리보기")
                    preview_button.connect("clicked", self._preview_appearance, mode, route)
                    preview_button.set_sensitive(False)
                    preview_buttons.append(preview_button)
                    page.append(preview_button)
                refresh_button = Gtk.Button(label="미리보기 상태 새로고침")
                refresh_button.connect("clicked", self._refresh_appearance, route)
                page.append(refresh_button)
                apply_button = Gtk.Button(label="배경화면과 위젯 색상 적용")
                apply_button.connect("clicked", self._apply_appearance, route)
                apply_button.set_sensitive(False)
                page.append(apply_button)
                status_label = Gtk.Label(xalign=0)
                self._appearance_status[route] = status_label
                self._appearance_apply[route] = apply_button
                self._appearance_preview[route] = tuple(preview_buttons)
                page.append(status_label)
            else:
                page.append(Gtk.Label(label="Luminophore Shell 공통 설정", xalign=0))
            return self._scrolled(page)

        def _append_visual_controls(self, page: Gtk.Box) -> None:
            values = self.model.values_for("visual")
            if not values:
                return
            page.append(Gtk.Label(label="시각 효과 · 균형 프리셋", xalign=0))
            for key, label in (("enabled", "배경 블러와 네온 효과"), ("breathing", "호흡 애니메이션")):
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
                text = Gtk.Label(label=label, xalign=0)
                text.set_hexpand(True)
                row.append(text)
                control = Gtk.Switch()
                control.set_active(bool(values[f"visual.{key}"]))
                self._visual_controls[key] = control
                row.append(control)
                page.append(row)
            page.append(Gtk.Label(label="네온 밝기", xalign=0))
            intensity = Gtk.SpinButton.new_with_range(0, 3, 0.05)
            intensity.set_digits(2)
            intensity.set_value(float(values["visual.intensity"]))
            self._visual_controls["intensity"] = intensity
            page.append(intensity)
            apply_button = Gtk.Button(label="시각 효과 적용")
            apply_button.connect("clicked", self._apply_visual_settings)
            apply_button.set_sensitive(not self.model.state.online or self.model.capability_available("visual_settings"))
            page.append(apply_button)
            if self.model.state.online and not self.model.capability_available("visual_settings"):
                page.append(Gtk.Label(label="이 Shell에서는 시각 효과 설정을 적용할 수 없습니다", xalign=0))

        def _apply_visual_settings(self, _button: Gtk.Button) -> None:
            try:
                self.model.apply_settings({
                    "visual.enabled": self._visual_controls["enabled"].get_active(),
                    "visual.breathing": self._visual_controls["breathing"].get_active(),
                    "visual.intensity": self._visual_controls["intensity"].get_value(),
                    "visual.preset": "balanced",
                })
            except (RuntimeError, ValueError) as exc:
                self._show_error(exc)
            self._sync_banner()

        def _refresh_settings_status(self, _button: Gtk.Button, *, recover: bool = False) -> None:
            try:
                self.model.refresh_settings_status(recover=recover)
                values = self.model.values
                if not self.model.state.completion_unknown and self._visual_controls:
                    for key in ("enabled", "breathing"):
                        self._visual_controls[key].set_active(values[f"visual.{key}"])
                    self._visual_controls["intensity"].set_value(values["visual.intensity"])
            except (RuntimeError, ValueError) as exc:
                self._show_error(exc)
            self._sync_banner()

        @staticmethod
        def _typed_value(text: str, original: Any) -> Any:
            if isinstance(original, bool):
                lowered = text.strip().lower()
                if lowered not in {"true", "false"}:
                    raise ValueError("boolean value must be true or false")
                return lowered == "true"
            if isinstance(original, int):
                return int(text)
            if isinstance(original, float):
                return float(text)
            return text

        def _apply_typed_settings(self, _button: Gtk.Button, prefix: str) -> None:
            try:
                originals = self.model.values
                changes = {
                    key: self._typed_value(entry.get_text(), originals[key])
                    for key, entry in self._setting_entries.items() if key.startswith(prefix + ".")
                }
                self.model.apply_settings(changes)
            except (RuntimeError, ValueError) as exc:
                self._show_error(exc)
            self._sync_banner()

        def _preview_theme(self, _button: Gtk.Button, mode: str) -> None:
            try:
                data = self.model.preview_system_theme(mode)
                self._sync_system_theme(data)
                if data.get("phase") == "generating":
                    GLib.timeout_add(200, self._poll_system_theme)
            except RuntimeError as exc:
                self._show_error(exc)

        def _apply_bindings(self, _button: Gtk.Button) -> None:
            changes = {}
            for action_id, (entry, original) in self._binding_entries.items():
                chord = entry.get_text().strip() or None
                if chord != original.get("chord"):
                    changes[action_id] = {"chord": chord, "flags": dict(original.get("flags", {}))}
            if changes:
                try:
                    self.model.apply_binding_changes(changes)
                except (RuntimeError, ValueError) as exc:
                    self._show_error(exc)
                self._sync_banner()

        def _filter_bindings(self, search: Gtk.SearchEntry) -> None:
            query = search.get_text().strip().casefold()
            for row, searchable in self._binding_rows.values():
                row.set_visible(not query or query in searchable)

        def _apply_theme(self, _button: Gtk.Button) -> None:
            try:
                data = self.model.apply_system_theme()
                self._sync_system_theme(data)
                if data.get("phase") == "applying":
                    GLib.timeout_add(200, self._poll_system_theme)
            except RuntimeError as exc:
                self._show_error(exc)

        def _rollback_theme(self, _button: Gtk.Button) -> None:
            try:
                data = self.model.rollback_system_theme()
                self._sync_system_theme(data)
                if data.get("phase") == "rolling_back":
                    GLib.timeout_add(200, self._poll_system_theme)
            except RuntimeError as exc:
                self._show_error(exc)

        def _refresh_theme(self, _button: Gtk.Button) -> None:
            try:
                self._sync_system_theme(self.model.load_system_theme())
            except RuntimeError as exc:
                self._show_error(exc)

        def _choose_appearance_source(
            self, _button: Gtk.Button, connector: str, route: SettingsRoute,
        ) -> None:
            if self.window is None:
                return
            if route == SettingsRoute.WALLPAPER:
                from .ui.scene_profile_workbench import SceneProfileWorkbench
                self._profile_workbench = SceneProfileWorkbench(self, self.window)
                return
            dialog = Gtk.FileChooserNative(
                title=f"{connector} 배경화면 선택", transient_for=self.window,
                action=Gtk.FileChooserAction.OPEN, accept_label="선택", cancel_label="취소",
            )

            def selected(chooser: Gtk.FileChooserNative, response: int) -> None:
                if response == Gtk.ResponseType.ACCEPT:
                    file = chooser.get_file()
                    path = file.get_path() if file is not None else None
                    if path:
                        self._appearance_sources[connector] = path
                        _button.set_label(f"{connector} · {file.get_basename()}")
                        self._sync_appearance(route, self.model.appearance_data)
                chooser.destroy()

            dialog.connect("response", selected)
            dialog.show()

        def _preview_appearance(
            self, _button: Gtk.Button, mode: str, route: SettingsRoute,
        ) -> None:
            context = self.model.appearance_data
            monitors = {str(value) for value in context.get("monitors", ())}
            if monitors and set(self._appearance_sources) == monitors:
                try:
                    result = self.model.preview_appearance(self._appearance_sources, mode)
                    self._sync_appearance(route, result)
                    GLib.timeout_add(200, self._poll_appearance, route)
                except RuntimeError as exc:
                    self._show_error(exc)

        def _refresh_appearance(self, _button: Gtk.Button, route: SettingsRoute) -> None:
            try:
                self._sync_appearance(route, self.model.refresh_appearance())
            except RuntimeError as exc:
                self._show_error(exc)

        def _apply_appearance(self, _button: Gtk.Button, route: SettingsRoute) -> None:
            try:
                result = self.model.apply_appearance()
                self._sync_appearance(route, result)
                GLib.timeout_add(200, self._poll_appearance, route)
            except RuntimeError as exc:
                self._show_error(exc)

        def _poll_system_theme(self) -> bool:
            try:
                data = self.model.load_system_theme()
                self._sync_system_theme(data)
                return data.get("phase") in {"generating", "applying", "rolling_back"}
            except RuntimeError as exc:
                self._show_error(exc)
                return False

        def _sync_system_theme(self, data: Mapping[str, Any]) -> None:
            phase = str(data.get("phase", "idle"))
            if self._system_theme_status is not None:
                self._system_theme_status.set_text(phase)
            if self._system_theme_apply is not None:
                preview = data.get("preview")
                self._system_theme_apply.set_sensitive(
                    phase == "preview" and isinstance(preview, Mapping) and bool(preview.get("preview_id"))
                )

        def _poll_appearance(self, route: SettingsRoute) -> bool:
            try:
                data = self.model.refresh_appearance()
                self._sync_appearance(route, data)
                return data.get("phase") in {"previewing", "applying"}
            except RuntimeError as exc:
                self._show_error(exc)
                return False

        def _sync_appearance(self, route: SettingsRoute, data: Mapping[str, Any]) -> None:
            phase = str(data.get("phase", "idle"))
            label = self._appearance_status.get(route)
            if label is not None:
                label.set_text(phase)
            busy = phase in {"previewing", "applying"}
            monitors = set(self.model.appearance_monitors)
            complete = bool(monitors) and set(self._appearance_sources) == monitors
            for button in self._appearance_preview.get(route, ()):
                button.set_sensitive(complete and not busy)
            apply_button = self._appearance_apply.get(route)
            if apply_button is not None:
                apply_button.set_sensitive(phase == "ready" and bool(self.model.appearance_preview_id))

        def _show_error(self, error: Exception) -> None:
            del error
            self.model.state = replace(
                self.model.state,
                message="요청을 완료하지 못했습니다. 상태를 새로고침하세요",
                busy=False,
            )
            self._sync_banner()

        def _sync_banner(self) -> None:
            if self._settings_status_button is not None:
                self._settings_status_button.set_visible(self.model.state.completion_unknown)
            if self._settings_recover_button is not None:
                self._settings_recover_button.set_visible(self.model.state.completion_unknown)
            if self.banner is not None:
                self.banner.set_text(self.model.state.banner)
                self.banner.set_visible(bool(self.model.state.banner))

        def _show_route(self) -> None:
            if self.stack is not None:
                self.stack.set_visible_child_name(self.model.state.route.value)
            self._sync_banner()
else:
    class LuminophoreSettingsApplication:  # type: ignore[no-redef]
        def __init__(self, backend: SettingsBackendFacade, route: str | SettingsRoute = SettingsRoute.HOME) -> None:
            self.model = SettingsAppModel(backend)
            self._requested_route = self.model.parse_route(route)

        def request_route(self, route: str | SettingsRoute) -> None:
            self._requested_route = self.model.parse_route(route)
            self.model.navigate(self._requested_route)

        def run(self, _argv: list[str] | None = None) -> int:
            raise RuntimeError("GTK 4 is unavailable")


__all__ = [
    "APPLICATION_ID",
    "LuminophoreSettingsApplication",
    "SettingsAppModel",
    "SettingsAppState",
    "SettingsBackendFacade",
]
