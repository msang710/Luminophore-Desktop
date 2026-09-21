from __future__ import annotations

from dataclasses import dataclass
import time

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Graphene, Gdk, GLib, Gtk, Gtk4LayerShell  # noqa: E402

from ..app_icons import ApplicationIconProvider
from ..applications import ApplicationCatalog, ApplicationRecord
from ..hyprland import HyprlandClient, HyprlandError, SpatialState, WindowRecord
from .effects import app_icon


class EditorIconStack(Gtk.Overlay):
    """Only the snapshot moves; allocation and input geometry stay fixed."""
    lift = 0.0
    zoom = 1.0

    def do_snapshot(self, snapshot):
        snapshot.save()
        point = Graphene.Point()
        point.init(self.get_width()*(1-self.zoom)/2, self.get_height()*(1-self.zoom)/2-self.lift)
        snapshot.translate(point)
        snapshot.scale(self.zoom, self.zoom)
        Gtk.Overlay.do_snapshot(self, snapshot)
        snapshot.restore()


CELL_SIZE = 12
ICON_SIZE = 9


@dataclass(frozen=True)
class SpatialCell:
    x: int
    y: int
    addresses: tuple[str, ...] = ()
    view_output_ids: tuple[int, ...] = ()
    target_output: bool = False
    wide: bool = False
    wide_key: bool = False

    @property
    def address(self) -> str:
        return self.addresses[0] if self.addresses else ""

    @property
    def in_view(self) -> bool:
        return bool(self.view_output_ids)


def spatial_cells(state: SpatialState, editor_viewport: tuple[int, int] | None = None) -> tuple[SpatialCell, ...]:
    views = state.output_views
    if state.protocol_version == 2:
        chosen = next((v for v in views if v.output_id == state.target_output_id), next(iter(views), None))
        views = (chosen,) if chosen else ()
        board = chosen.board_id if chosen else 0
        rect = chosen.rect if chosen else state.view
        columns = min(16, max(4, rect.columns + 2))
        rows = min(12, max(4, rect.rows + 2))
        if editor_viewport:
            columns, rows = editor_viewport
        default_origin = (rect.x - (columns - rect.columns)//2, rect.y - (rows - rect.rows)//2) if editor_viewport else (rect.x-1, rect.y-1)
        origin = state.editor_origin or default_origin
        # UI viewport only: navigation moves this bounded window over the board.
        xs = range(origin[0], origin[0] + columns)
        ys = range(origin[1], origin[1] + rows)
    else:
        board = 0
        xs, ys = range(state.columns), range(state.rows)
    occupied: dict[tuple[int, int], list[str]] = {}
    for window in state.windows:
        if state.protocol_version == 2 and window.board_id != board:
            continue
        occupied.setdefault((window.x, window.y), []).append(window.address)
    target_output_id = state.target_output_id
    wide_rect: tuple[int, int, int, int] | None = None
    if state.presentation_mode == "wide" and state.output_views:
        wide_rect = (
            min(view.rect.x for view in views),
            min(view.rect.y for view in views),
            max(view.rect.x + view.rect.columns for view in views),
            max(view.rect.y + view.rect.rows for view in views),
        )
    return tuple(
        SpatialCell(
            x=x,
            y=y,
            addresses=tuple(occupied.get((x, y), ())),
            view_output_ids=tuple(view.output_id for view in views if view.rect.contains(x, y))
            if views
            else ((0,) if state.view.contains(x, y) else ()),
            target_output=bool(target_output_id) and any(view.output_id == target_output_id and view.rect.contains(x, y) for view in views),
            wide=wide_rect is not None and wide_rect[0] <= x < wide_rect[2] and wide_rect[1] <= y < wide_rect[3],
            wide_key=state.presentation_mode == "wide" and any(address == state.wide_key for address in occupied.get((x, y), ())),
        )
        for y in ys
        for x in xs
    )


class SpatialMapPanel(Gtk.Grid):
    def __init__(
        self,
        catalog: ApplicationCatalog,
        icon_provider: ApplicationIconProvider | None,
        view_palette_index: int,
        *,
        cell_size: int = CELL_SIZE,
        icon_size: int = ICON_SIZE,
        show_coordinates: bool = False,
        editor_style: bool = False,
    ) -> None:
        super().__init__(column_spacing=1, row_spacing=1)
        self.editor_style = editor_style
        if editor_style:
            self.set_column_spacing(0)
            self.set_row_spacing(0)
        self.cell_size = cell_size
        self.icon_size = icon_size
        self.show_coordinates = show_coordinates
        if show_coordinates or editor_style:
            self.set_column_homogeneous(True)
            self.set_row_homogeneous(True)
        self.holders: dict[tuple[int, int], Gtk.Widget] = {}
        self.icon_widgets: dict[str, Gtk.Widget] = {}
        self.catalog = catalog
        self.icon_provider = icon_provider
        self.view_palette_index = view_palette_index
        self.add_css_class("spatial-map-grid")
        self.set_can_target(False)

    def _application_for(self, window: WindowRecord | None) -> ApplicationRecord | None:
        if window is None:
            return None
        return self.catalog.match_window_class(window.initial_class or window.app_class)

    def update(self, state: SpatialState, windows: list[WindowRecord]) -> None:
        child = self.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.remove(child)
            child = following

        self.holders.clear()
        self.icon_widgets.clear()
        windows_by_address = {window.address: window for window in windows}
        self.remove_css_class("spatial-map-diagnostic")
        self.remove_css_class("spatial-map-wide")
        if state.diagnostics:
            self.add_css_class("spatial-map-diagnostic")
            self.set_tooltip_text(", ".join(state.diagnostics))
        else:
            self.set_tooltip_text(None)
        if state.presentation_mode == "wide":
            self.add_css_class("spatial-map-wide")
        cells = spatial_cells(state, getattr(self, "editor_viewport", (10, 8)) if self.editor_style else None)
        origin_x = min((c.x for c in cells), default=0)
        origin_y = min((c.y for c in cells), default=0)
        for cell in cells:
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            display_x, display_y = state.display_point(cell.x, cell.y)
            if not self.editor_style:
                holder.set_tooltip_text(f"({display_x}, {display_y})")
            holder.set_size_request(self.cell_size, self.cell_size)
            holder.set_halign(Gtk.Align.CENTER)
            holder.set_valign(Gtk.Align.CENTER)
            if self.editor_style:
                holder.set_halign(Gtk.Align.FILL)
                holder.set_valign(Gtk.Align.FILL)
            holder.add_css_class("spatial-map-cell")
            if state.focused_key and state.focused_key in cell.addresses:
                holder.add_css_class("spatial-editor-focused")
            if self.show_coordinates:
                holder.append(Gtk.Label(label=f"{display_x:+d}, {display_y:+d}"))
            if cell.in_view:
                holder.add_css_class("spatial-map-cell-view")
                holder.add_css_class(f"palette-{self.view_palette_index}")
            if self.show_coordinates:
                for output in state.output_views:
                    rect = output.rect
                    if output.output_id not in cell.view_output_ids or not rect.contains(cell.x, cell.y):
                        continue
                    for edge, boundary in (("left", cell.x == rect.x), ("right", cell.x == rect.x + rect.columns - 1),
                                           ("top", cell.y == rect.y), ("bottom", cell.y == rect.y + rect.rows - 1)):
                        if boundary:
                            holder.add_css_class(f"spatial-editor-view-{edge}")
            if cell.target_output:
                holder.add_css_class("spatial-map-cell-target")
            if len(cell.view_output_ids) > 1:
                holder.add_css_class("spatial-map-cell-conflict")
            if cell.wide:
                holder.add_css_class("spatial-map-cell-wide")
            if cell.wide_key:
                holder.add_css_class("spatial-map-cell-wide-key")
            if cell.addresses:
                icon_stack = Gtk.Box(spacing=0) if self.show_coordinates else (EditorIconStack() if self.editor_style else Gtk.Overlay())
                for index, address in enumerate(cell.addresses):
                    window = windows_by_address.get(address)
                    icon = app_icon(
                        self._application_for(window),
                        min(self.icon_size, max(1, (self.cell_size - 8) // len(cell.addresses))) if self.show_coordinates else self.icon_size,
                        role="primary",
                        provider=self.icon_provider,
                        catalog_revision=self.catalog.revision,
                    )
                    icon.add_css_class("spatial-map-icon")
                    if self.show_coordinates and window:
                        icon.set_tooltip_text(window.title or window.app_class)
                    self.icon_widgets[address] = icon
                    if self.show_coordinates:
                        icon_stack.append(icon)
                    elif index == 0:
                        icon_stack.set_child(icon)
                    else:
                        icon.set_halign(Gtk.Align.END)
                        icon.set_valign(Gtk.Align.END)
                        icon_stack.add_overlay(icon)
                if self.editor_style:
                    icon_stack.set_halign(Gtk.Align.CENTER)
                    icon_stack.set_valign(Gtk.Align.CENTER)
                    icon_stack.set_vexpand(True)
                holder.append(icon_stack)
            self.attach(holder, cell.x - origin_x, cell.y - origin_y, 1, 1)
            self.holders[(cell.x, cell.y)] = holder


class SpatialMapSurface:
    def __init__(
        self,
        application: Gtk.Application,
        monitor: Gdk.Monitor,
        client: HyprlandClient,
        catalog: ApplicationCatalog,
        icon_provider: ApplicationIconProvider | None,
        palette_index: int,
        view_palette_index: int,
        margin: int,
        transition_ms: int,
    ) -> None:
        self.client = client
        self.transition_ms = max(1, transition_ms)
        self._tick_id = 0
        self._started_at = 0.0
        self._start_opacity = 0.0
        self._target_opacity = 0.0
        self.window = Gtk.ApplicationWindow(application=application)
        self.window.set_name("luminophore-spatial-map")
        self.window.add_css_class("luminophore-surface")
        self.window.add_css_class(f"palette-{palette_index}")
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_focusable(False)

        Gtk4LayerShell.init_for_window(self.window)
        Gtk4LayerShell.set_namespace(self.window, "luminophore-shell-spatial-map")
        Gtk4LayerShell.set_monitor(self.window, monitor)
        # This surface only exists while the overview is visible. Keep its
        # physical layer stable so state refreshes cannot remap it below client
        # windows or flash an intermediate layer-shell commit.
        Gtk4LayerShell.set_layer(self.window, Gtk4LayerShell.Layer.OVERLAY)
        Gtk4LayerShell.set_exclusive_zone(self.window, 0)
        Gtk4LayerShell.set_anchor(self.window, Gtk4LayerShell.Edge.RIGHT, True)
        Gtk4LayerShell.set_anchor(self.window, Gtk4LayerShell.Edge.BOTTOM, True)
        Gtk4LayerShell.set_margin(self.window, Gtk4LayerShell.Edge.RIGHT, margin)
        Gtk4LayerShell.set_margin(self.window, Gtk4LayerShell.Edge.BOTTOM, margin)
        Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.NONE)

        frame = Gtk.Box()
        frame.add_css_class("luminophore-panel")
        frame.add_css_class("spatial-map-panel")
        frame.set_can_target(False)
        self.panel = SpatialMapPanel(catalog, icon_provider, view_palette_index)
        frame.append(self.panel)
        self.window.set_child(frame)

    def present(self) -> None:
        self.window.set_opacity(0.0)
        self.window.present()
        GLib.idle_add(self._finish_prepare)

    def _finish_prepare(self) -> bool:
        if self._target_opacity <= 0.0:
            self.window.set_visible(False)
        return False

    def set_overview_visible(self, visible: bool) -> None:
        target = 1.0 if visible else 0.0
        current = self.window.get_opacity()
        if not visible and not self.window.get_visible():
            self._target_opacity = 0.0
            self.window.set_opacity(0.0)
            return
        if visible:
            self.window.present()
        self._start_opacity = current
        self._target_opacity = target
        self._started_at = time.monotonic()
        if not self._tick_id:
            self._tick_id = self.window.add_tick_callback(self._animate_visibility)

    def _animate_visibility(self, _widget: Gtk.Widget, _clock: Gdk.FrameClock) -> bool:
        elapsed_ms = (time.monotonic() - self._started_at) * 1000.0
        progress = max(0.0, min(1.0, elapsed_ms / self.transition_ms))
        eased = progress * progress * (3.0 - 2.0 * progress)
        opacity = self._start_opacity + (self._target_opacity - self._start_opacity) * eased
        self.window.set_opacity(opacity)
        if progress < 1.0:
            return True
        self._tick_id = 0
        self.window.set_opacity(self._target_opacity)
        if self._target_opacity <= 0.0:
            self.window.set_visible(False)
        return False

    def update(self, windows: list[WindowRecord]) -> None:
        try:
            state = self.client.spatial_state()
        except HyprlandError:
            self.window.set_visible(False)
            return
        self.panel.update(state, windows)

    def destroy(self) -> None:
        if self._tick_id:
            self.window.remove_tick_callback(self._tick_id)
            self._tick_id = 0
        self.window.destroy()
