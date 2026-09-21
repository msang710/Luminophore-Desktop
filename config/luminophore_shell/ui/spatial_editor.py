from __future__ import annotations

import uuid
from dataclasses import replace

import cairo
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gdk, Gtk, Gtk4LayerShell, GLib

from ..hyprland import HyprlandClient, HyprlandError, WindowRecord, SpatialView
from ..spatial_editor import SpatialEditorState, SpatialEditorMode
from ..spatial_preview import SpatialPreviewController, SpatialNativeDrag, trace_editor_drag
from ..spatial_grab import SpatialGrabEvent
from ..spatial_edit import SpatialGrabLayout
from ..spatial_feedback import SpatialFeedback
from .projection import FrameProjectionHandshake, submit_surface_projection, forget_surface_projection
from .spatial_map import SpatialMapPanel
from .spatial_badge import SpatialBadgeSurface
from .spatial_editor_visual import EditorVisual, CELL_SIZE, ICON_SIZE


class SpatialEditorSurface:
    """One independent editor surface with a revision-consistent board view."""

    def __init__(self, application, monitor, client: HyprlandClient, catalog, icon_provider, palette_index: int, palette_indices=None) -> None:
        self._badge = None
        self.palette_indices = palette_indices or {}
        self.client = client
        self.monitor = monitor
        self.state = SpatialEditorState()
        self._preview = SpatialPreviewController(self.state.edits, client, GLib.idle_add, self._preview_changed, self._finish_drag)
        self._native_drag = SpatialNativeDrag(client, GLib.idle_add, self._native_started)
        self._pending_grab = None
        self._pending_grab_update = None
        self._windows: list[WindowRecord] = []
        self._editor_origin = None
        self._board_output = 0
        self._revision = 1
        self._allocation = (0, 0)
        self._projection_frame = None
        self._registered_layout = None
        self._geometry_tick_id = 0
        self._cell_geometry = ()
        self.window = Gtk.ApplicationWindow(application=application)
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.add_css_class("luminophore-surface")
        self.window.add_css_class("spatial-editor-light")
        self.window.add_css_class(f"palette-{palette_index}")
        Gtk4LayerShell.init_for_window(self.window)
        Gtk4LayerShell.set_namespace(self.window, "luminophore-shell-spatial-editor")
        Gtk4LayerShell.set_monitor(self.window, monitor)
        Gtk4LayerShell.set_layer(self.window, Gtk4LayerShell.Layer.BOTTOM)
        Gtk4LayerShell.set_exclusive_zone(self.window, 0)
        Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.NONE)
        # No anchors: layer-shell centers the compact editor on its monitor.
        self.outputs = Gtk.Label()
        self.status = Gtk.Label()
        self.panel = SpatialMapPanel(catalog, icon_provider, palette_index, cell_size=CELL_SIZE, icon_size=ICON_SIZE, show_coordinates=False, editor_style=True)
        scroll = Gtk.ScrolledWindow()
        self.scroll = scroll
        geometry = monitor.get_geometry()
        scroll.set_max_content_width(max(160, geometry.width - 64))
        scroll.set_max_content_height(max(120, geometry.height - 220))
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        scroll.set_propagate_natural_width(True)
        scroll.set_propagate_natural_height(True)
        self.board = Gtk.Overlay()
        self.board.set_child(self.panel)
        self.preview_panel = SpatialMapPanel(catalog, icon_provider, palette_index, cell_size=CELL_SIZE, icon_size=ICON_SIZE, show_coordinates=False, editor_style=True)
        self.preview_panel.set_can_target(False)
        self.preview_panel.set_visible(False)
        self.board.add_overlay(self.preview_panel)
        scroll.set_child(self.board)
        self.panel.set_can_target(True)
        self._native_press = None
        self._waiting_press = None
        self._settled_revision = 0
        self._drag_origin = (0.0, 0.0)
        self._drag_cells = {}
        self._shown_preview = None
        self._gesture = Gtk.GestureDrag.new()
        self._gesture.set_button(1)
        self._gesture.connect("drag-begin", self._drag_begin)
        self._gesture.connect("drag-update", self._drag_update)
        self._gesture.connect("drag-end", self._drag_end)
        self._gesture.connect("cancel", self._drag_cancel)
        self.panel.add_controller(self._gesture)
        self._pointer_position = None
        motion = Gtk.EventControllerMotion.new()
        motion.connect("enter", self._pointer_motion)
        motion.connect("motion", self._pointer_motion)
        motion.connect("leave", self._pointer_leave)
        self.panel.add_controller(motion)
        self.frame = EditorVisual(self, scroll)
        self.window.set_child(self.frame)
        self._size_visual()
        self.window.connect("close-request", self._close_requested)
        self.window.connect("realize", lambda _window: self.window.get_surface().set_input_region(cairo.Region()))
        self._handshake = FrameProjectionHandshake(
            self.window, "luminophore-shell-spatial-editor", self._submit_projection, self._frame_revision,
        )

    def set_monitor(self, monitor) -> None:
        if self.monitor != monitor:
            self._approved_input = None
            self._handshake.cancel()
            self._invalidate_grab_layout()
            self.client.forget_projection("spatial-editor")
            self._projection_generation = ""
        self.monitor = monitor
        Gtk4LayerShell.set_monitor(self.window, monitor)
        geometry = monitor.get_geometry()
        self.scroll.set_max_content_width(max(160, geometry.width - 64))
        self.scroll.set_max_content_height(max(120, geometry.height - 220))
        self._size_visual()
        self.frame.refresh_palette()

    @property
    def visible(self) -> bool:
        return self.state.visible

    def _close_requested(self, _window) -> bool:
        self.close()
        return True

    def toggle(self, windows: list[WindowRecord]) -> None:
        if self.visible:
            self.close()
            return
        self.state.toggle()
        self._opening_connector = self.monitor.get_connector()
        self._editor_origin = None
        self.update(windows)
        self.frame.animate(True, self._appearance_ready)
        self.window.present()
        self._handshake.start()

    def _clear_badge(self) -> None:
        if self._badge is not None:
            self._badge.destroy()
            self._badge = None

    def _show_badge(self, event) -> None:
        self._clear_badge()
        window = next((w for w in self._windows if w.address == event.window), None)
        self._badge = SpatialBadgeSurface(
            self.window.get_application(), self.monitor, event, self.panel._application_for(window),
            self.panel.icon_provider, self.panel.catalog.revision,
        )

    def close(self, *, animate=True) -> None:
        self._waiting_press = None
        self._settled_revision = 0
        self._native_press = None
        self._native_drag.cancel()
        self._clear_badge()
        self.client.forget_projection("spatial-editor")
        self._projection_generation = ""
        self._pending_grab = None
        self._pending_grab_update = None
        self._preview.cancel()
        self._stop_grab_geometry()
        self.state.close()
        self._pointer_position = None
        self.panel.set_cursor_from_name("default")
        self.window.set_cursor_from_name("default")
        self._handshake.cancel()
        surface = self.window.get_surface()
        if surface:
            surface.set_input_region(cairo.Region())
        visual = getattr(self, "frame", None)
        if animate and isinstance(visual, EditorVisual) and self.window.get_visible():
            visual.animate(False, lambda: self.window.set_visible(False))
        else:
            if isinstance(visual, EditorVisual):
                visual.cancel()
            self.window.set_visible(False)

    def connection_reset(self) -> bool:
        self._waiting_press = None
        self._settled_revision = 0
        self._native_drag.close()
        self._native_drag = SpatialNativeDrag(self.client, GLib.idle_add, self._native_started)
        self._native_press = None
        self._clear_badge()
        self._pending_grab = None
        self._pending_grab_update = None
        self._preview.cancel("연결이 갱신되어 진행 중인 이동을 취소했습니다")
        self._stop_grab_geometry()
        self._handshake.cancel()
        self.state.connection_reset()
        self._shown_preview = None
        self._drag_cells = {}
        self.preview_panel.set_visible(False)
        self.panel.set_opacity(1.0)
        for icon in self.panel.icon_widgets.values():
            icon.set_opacity(1.0)
        surface = self.window.get_surface()
        if surface:
            surface.set_input_region(cairo.Region())
        if self.visible:
            self.update(self._windows)
            self._handshake.start()
        else:
            self.window.set_visible(False)
        return False

    def handle_grab(self, event: SpatialGrabEvent, windows, monitors=()) -> bool:
        if monitors:
            self._grab_monitors = monitors
        if event.phase == "begin":
            self._waiting_press = None
            preserve_editor = event.editor_origin and self.state.mode is SpatialEditorMode.PERSISTENT
            if not monitors:
                return False
            pending = self._pending_grab
            if pending and pending.source == event.source and pending.generation >= event.generation:
                return False
            self._preview.cancel()
            self._pending_grab = event
            self._pending_grab_update = None
            def accept(snapshot, error):
                if self._pending_grab is not event:
                    return
                latest = self._pending_grab_update
                self._pending_grab = self._pending_grab_update = None
                if error is not None:
                    return
                connector = dict(snapshot.output_names).get(event.output_id)
                monitor = next((monitor for monitor in monitors if monitor.get_connector() == connector), None)
                if monitor is None or not self.state.accept_grab(event, snapshot):
                    return
                self._board_output=event.output_id
                if not preserve_editor:
                    self._editor_origin=None
                self.set_monitor(monitor)
                self._windows = windows
                self._render_view(True)
                surface = self.window.get_surface()
                if surface:
                    surface.set_input_region(cairo.Region())
                if not preserve_editor:
                    self.frame.animate(True, self._appearance_ready)
                self.window.present()
                self._show_badge(event)
                self._start_grab_geometry()
                self._handshake.start()
                if latest is not None:
                    self.handle_grab(latest, windows, monitors)
            self._preview.refresh(accept)
            return True  # Accepted for asynchronous validation, not yet presented.
        pending = self._pending_grab
        if pending and (event.source, event.generation, event.revision, event.topology_revision, event.output_id, event.window, event.floating) == (
                pending.source, pending.generation, pending.revision, pending.topology_revision, pending.output_id, pending.window, pending.floating):
            if event.phase in {"end", "cancel"}:
                self._finish_native_grab(event)
            elif event.phase == "update":
                self._pending_grab_update = event
            return True
        if pending is not None:
            return False  # An older accepted grab cannot finish the pending one.
        previous = self.state.grab_event
        if not self.state.accept_grab(event):
            return False
        if event.phase in {"end", "cancel"}:
            self._finish_native_grab(event)
        if event.phase == "update":
            if previous and event.target_epoch != previous.target_epoch:
                self._invalidate_grab_layout()
                self._editor_origin = None
                target = event.target_output_id
                if target:
                    connector = dict(self.state.view.state.output_names).get(target)
                    monitor = next((m for m in getattr(self, '_grab_monitors', ()) if m.get_connector() == connector), None)
                    if monitor:
                        self._board_output = target
                        self.set_monitor(monitor)
                        if self._badge is not None:
                            self._badge.retarget(monitor, event)
                        self._render_view(True)
                        self.window.present()
                        self._handshake.start()
                else:
                    self.window.set_visible(False)
            self._render_preview(event.result)
        elif self.visible:
            self._clear_badge()
            self._stop_grab_geometry()
            self.update(windows)
        else:
            self.close()
        return True

    def _finish_native_grab(self, event) -> None:
        # Call only after matching the pending or accepted grab identity.
        self._pending_grab = self._pending_grab_update = None
        self._preview.cancel()
        self._waiting_press = None
        self._settled_revision = event.settled_revision or 0
        self._native_press = None
        self._native_drag.finished()

    def update(self, windows: list[WindowRecord]) -> None:
        self._windows = windows
        if not self.visible:
            return
        if self._pending_grab is not None or self.state.mode is SpatialEditorMode.TRANSIENT_DRAG:
            return  # Compositor events own this immutable grab view.
        self._preview.refresh(self._accept_snapshot)

    def _accept_snapshot(self, snapshot, error):
        if not self.visible or self.state.mode is SpatialEditorMode.TRANSIENT_DRAG:
            return
        accepted = False
        if error is None and (not snapshot.committed or snapshot.revision < getattr(self, "_settled_revision", 0)):
            return
        if error is None:
            accepted = self.state.accept(snapshot)
        else:
            self.state.diagnostic = "공간 정보를 다시 확인하고 있습니다"
            self._preview.cancel(self.state.diagnostic)
        if accepted and self.state.edits.active:
            return  # Preserve allocation and input targets for the current gesture.
        pending = getattr(self, "_waiting_press", None)
        if accepted and pending is not None:
            if self._press_geometry(snapshot) == pending[4]:
                # Rebuilding icon children would cancel the GTK sequence whose
                # press we are preserving. The displayed geometry is unchanged.
                self._revision += 1
                self._handshake.start()
                return
            self._waiting_press = None
        self._render_view(accepted)
        if accepted and self.state.mode is SpatialEditorMode.PERSISTENT:
            # The drag's last presented envelope is already approved. Updating
            # icons inside unchanged bounds must not leave a click-through gap
            # while a fresh content projection crosses the IPC boundary.
            surface = self.window.get_surface()
            bounds = self._bounds(self.panel, self.window)
            approved = getattr(self, "_approved_input", None)
            if surface and approved == (surface, bounds) and getattr(self, "_projection_status", None) == "presented" and not getattr(self.frame, "animating", False):
                surface.set_input_region(cairo.Region(cairo.RectangleInt(*(int(v) for v in bounds))))
                surface.queue_render()

    def _render_view(self, accepted):
        windows = self._windows
        self._shown_preview = None
        self.preview_panel.set_visible(False)
        self.panel.set_opacity(1.0)
        for icon in self.panel.icon_widgets.values():
            icon.set_opacity(1.0)
        if accepted and self.state.view:
            snapshot = self.state.view.state
            names = dict(snapshot.output_names)
            opening = getattr(self, "_opening_connector", None)
            if opening:
                self._board_output = next((output for output, name in snapshot.output_names if name == opening), snapshot.target_output_id)
                self._opening_connector = None
            ids=tuple(v.output_id for v in snapshot.output_views)
            if getattr(self,"_board_ids",()) != ids:
                self._board_ids=ids
                if self._board_output not in ids:self._board_output=snapshot.target_output_id if snapshot.target_output_id in ids else (ids[0] if ids else 0)
            selected=self._board_output or snapshot.target_output_id
            if snapshot.protocol_version == 2:
                if self._editor_origin is None:
                    output = next((v for v in snapshot.output_views if v.output_id == selected), None)
                    if output:
                        rect = output.rect
                        columns, rows = min(16, max(8, rect.columns+2)), min(12, max(6, rect.rows+2))
                        self._editor_origin = (rect.x-(columns-rect.columns)//2, rect.y-(rows-rect.rows)//2)
                        self.panel.editor_viewport = self.preview_panel.editor_viewport = (columns, rows)
                snapshot=replace(snapshot,selected_output_id=selected,editor_origin=self._editor_origin)
            palette=self.palette_indices.get(names.get(selected,""),self.panel.view_palette_index)
            self.panel.view_palette_index=palette
            self.preview_panel.view_palette_index=palette
            self.panel.update(snapshot, windows)
            names = dict(snapshot.output_names)
            labels = []
            for output in snapshot.output_views:
                x, y = snapshot.display_point(output.rect.x, output.rect.y)
                labels.append(f"{names.get(output.output_id, '화면')} ({x:+d}, {y:+d}) · {output.rect.columns}×{output.rect.rows}")
            self.outputs.set_label("   /   ".join(labels))
        self.status.set_label(self.state.diagnostic or "Super+Space로 닫기")
        if isinstance(getattr(self, "frame", None), EditorVisual):
            self.frame.refresh()
        self._revision += 1
        self._handshake.start()
        if accepted and self.state.feedback:
            self.highlight(self.state.feedback)

    def highlight(self, event: SpatialFeedback) -> bool:
        if not self.visible:
            return False
        if self.state.edits.active or self.state.mode is SpatialEditorMode.TRANSIENT_DRAG:
            return True
        if not self.state.view or self.state.view.revision != (event.topology_revision, event.revision):
            self.update(self._windows)
        self.state.highlight(event)
        if self.state.feedback is not event:
            return True
        for point, holder in self.panel.holders.items():
            holder.remove_css_class("spatial-editor-feedback")
            if event.action in {"focus", "window-move"}:
                selected = point == event.to_point
            elif event.to_rect:
                x, y, width, height = event.to_rect
                selected = x <= point[0] < x + width and y <= point[1] < y + height
            else:
                selected = False
            if selected:
                holder.add_css_class("spatial-editor-feedback")
        window = next((window for window in self._windows if window.address == event.window), None)
        app = self.panel._application_for(window)
        self.status.set_label(event.label(app.name if app else window.app_class if window else ""))
        self._revision += 1
        self._handshake.start()
        return True

    @staticmethod
    def _bounds(widget, target):
        ok, rect = widget.compute_bounds(target)
        if not ok:
            return None
        return rect.get_x(), rect.get_y(), rect.get_width(), rect.get_height()

    @staticmethod
    def _inside(box, x, y):
        return box and box[0] <= x < box[0] + box[2] and box[1] <= y < box[1] + box[3]

    def _drag_point(self, x, y):
        return next((point for point, box in self._drag_cells.items() if self._inside(box, x, y)), None)

    @staticmethod
    def _resize_cursor(edges):
        edges = set(edges)
        if edges == {"left", "top"} or edges == {"right", "bottom"}:
            return "nwse-resize"
        if edges == {"right", "top"} or edges == {"left", "bottom"}:
            return "nesw-resize"
        return "ew-resize" if edges & {"left", "right"} else "ns-resize"

    def _pointer_motion(self, _controller, x, y):
        self._pointer_position = (x, y)
        self._refresh_cursor()

    def _pointer_leave(self, _controller):
        self._pointer_position = None
        cursor = "no-drop" if self.state.edits.active else "default"
        self.panel.set_cursor_from_name(cursor)
        self.window.set_cursor_from_name(cursor)

    def _refresh_cursor(self):
        position = getattr(self, "_pointer_position", None)
        drag = self.state.edits.drag
        cursor = "default"
        if drag:
            if position is None or self._drag_point(*position) is None:
                cursor = "no-drop"
            else:
                cursor = self._resize_cursor(drag.edges) if drag.action == "resize-view" else "grabbing"
        elif self._preview.busy:
            cursor = "progress"
        elif position is not None:
            hit = self._interaction_at(*position)
            if hit:
                cursor = self._resize_cursor(hit[4]) if hit[0] == "resize-view" else "grab"
        self.panel.set_cursor_from_name(cursor)
        self.window.set_cursor_from_name(cursor)
        if self.state.edits.drag and (surface := self.window.get_surface()):
            surface.set_cursor(Gdk.Cursor.new_from_name(cursor, None))

    def _interaction_at(self, x, y):
        if self.state.mode is not SpatialEditorMode.PERSISTENT or self.state.diagnostic or not self.state.view:
            return None
        cells = {point: box for point, holder in self.panel.holders.items()
                 if (box := self._bounds(holder, self.panel)) is not None}
        point = next((p for p, box in cells.items() if self._inside(box, x, y)), None)
        if point is None:
            return None
        snapshot = self.state.view.state
        address = next((address for address, icon in self.panel.icon_widgets.items()
                        if self._inside(self._bounds(icon, self.panel), x, y)), "")
        output = next((view for view in snapshot.output_views if view.output_id == (self._board_output or snapshot.target_output_id)), None) if snapshot.protocol_version == 2 else next((view for view in snapshot.output_views if view.rect.contains(*point)), None)
        if address and output is None:
            output = next((view for view in snapshot.output_views if view.output_id == snapshot.selected_output_id),
                          next(iter(snapshot.output_views), None))
        if output is None:
            return
        rect = output.rect
        for action, box, edges in self._view_handles(cells, rect):
            if self._inside(box, x, y):
                return action, output.output_id, point, "", edges
        if not address:
            address = next((w.address for w in snapshot.windows
                            if (w.x, w.y) == point and (snapshot.protocol_version != 2 or w.board_id == output.board_id)), "")
        if not address:
            return None
        action, edges = "move-window", ()
        return action, output.output_id, point, address, tuple(edges)

    @staticmethod
    def _view_handles(cells, rect):
        first = cells.get((rect.x, rect.y))
        last = cells.get((rect.x + rect.columns - 1, rect.y + rect.rows - 1))
        if not first or not last:
            return ()
        x, y = first[:2]
        right, bottom = last[0] + last[2], last[1] + last[3]
        middle_x, middle_y = (x + right)/2, (y + bottom)/2
        return (("move-view", (middle_x-12, y+7, 24, 8), ()),
                ("resize-view", (x,y,7,7), ("left","top")),
                ("resize-view", (right-7,y,7,7), ("right","top")),
                ("resize-view", (x,bottom-7,7,7), ("left","bottom")),
                ("resize-view", (right-7,bottom-7,7,7), ("right","bottom")),
                ("resize-view", (x,middle_y-7,6,14), ("left",)),
                ("resize-view", (right-6,middle_y-7,6,14), ("right",)),
                ("resize-view", (middle_x-7,y,14,6), ("top",)),
                ("resize-view", (middle_x-7,bottom-6,14,6), ("bottom",)))

    def _drag_begin(self, gesture, x, y):
        hit = self._interaction_at(x, y)
        trace_editor_drag("gesture", press=gesture.get_current_event_time(), hit=hit[0] if hit else None)
        if hit is None:
            return
        action, output_id, point, address, edges = hit
        snapshot = self.state.view.state
        if action == "move-window":
            press_time = gesture.get_current_event_time()
            if not press_time:
                return
            if self._preview.busy or self._native_drag.pending or getattr(self, "_projection_status", "presented") != "presented" or self._handshake.pending or snapshot.revision < getattr(self, "_settled_revision", 0):
                trace_editor_drag("waiting", press=press_time, preview_busy=self._preview.busy, native_pending=self._native_drag.pending,
                                  projection=getattr(self, "_projection_status", "presented"), handshake_pending=self._handshake.pending,
                                  revision=snapshot.revision, settled_revision=getattr(self, "_settled_revision", 0))
                self._waiting_press = (press_time, x, y, hit, self._press_geometry(snapshot), snapshot.topology_revision)
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                return
            if self._native_drag.begin(address, snapshot, output_id, press_time):
                self._native_press = press_time
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self.panel.set_cursor_from_name("grabbing")
                self.window.set_cursor_from_name("grabbing")
            return
        if self._preview.busy:
            return
        self._drag_origin = x, y
        self._pointer_position = (x, y)
        self._drag_cells = {p: box for p, holder in self.panel.holders.items()
                            if (box := self._bounds(holder, self.panel)) is not None}
        if self.state.edits.begin(snapshot, action, output_id, point, window=address, edges=edges):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._refresh_cursor()
            self.status.set_label("드래그해서 위치 또는 범위 변경")

    @staticmethod
    def _press_geometry(snapshot):
        return snapshot.windows, snapshot.output_views, snapshot.topology_revision, snapshot.presentation_mode

    def _resume_waiting_press(self):
        pending = getattr(self, "_waiting_press", None)
        if pending is None or self._preview.busy or self._native_drag.pending or getattr(self, "_projection_status", "presented") != "presented" or self.state.mode is not SpatialEditorMode.PERSISTENT:
            return
        press, x, y, hit, geometry, topology = pending
        snapshot = self.state.view.state
        if not snapshot.committed or snapshot.revision < getattr(self, "_settled_revision", 0):
            return
        self._waiting_press = None
        if topology != snapshot.topology_revision or self._press_geometry(snapshot) != geometry or self._interaction_at(x,y) != hit:
            trace_editor_drag("waiting-invalidated", press=press)
            return
        trace_editor_drag("waiting-resumed", press=press)
        if self._native_drag.begin(hit[3], snapshot, hit[1], press):
            self._native_press = press
            self.panel.set_cursor_from_name("grabbing")
            self.window.set_cursor_from_name("grabbing")

    def _native_started(self, accepted):
        if not accepted:
            self._native_press = None
            self._refresh_cursor()
        if getattr(self, "_waiting_press", None) is not None:
            self._resume_waiting_press()

    def _render_preview(self, preview):
        drag = self.state.edits.drag
        snapshot = self.state.view.state if self.state.mode is SpatialEditorMode.TRANSIENT_DRAG and self.state.view else drag.snapshot if drag else None
        if snapshot is None or not preview or not preview.accepted:
            if self._shown_preview is not None:
                self._revision += 1
                self._handshake.start()
            self._shown_preview = None
            self.preview_panel.set_visible(False)
            self.panel.set_opacity(1.0)
            for icon in self.panel.icon_widgets.values():
                icon.set_opacity(1.0)
            return
        if preview == self._shown_preview:
            return
        self._shown_preview = preview
        boards=dict(preview.boards)
        placements = {address: (x, y) for address, x, y, _mode in preview.windows}
        views = {output: SpatialView(x, y, columns, rows) for output, x, y, columns, rows in preview.output_views}
        candidate = replace(snapshot,
                            windows=tuple(replace(window, x=placements[window.address][0], y=placements[window.address][1], board_id=boards.get(window.address,window.board_id))
                                          for window in snapshot.windows if window.address in placements),
                            output_views=tuple(replace(output, rect=views[output.output_id])
                                               for output in snapshot.output_views if output.output_id in views))
        candidate=replace(candidate,selected_output_id=self._board_output or snapshot.target_output_id,editor_origin=self._editor_origin)
        self.preview_panel.update(candidate, self._windows)
        self.preview_panel.set_visible(True)
        self.panel.set_opacity(1.0)
        for icon in self.panel.icon_widgets.values():
            icon.set_opacity(0.0)
        if isinstance(getattr(self, "frame", None), EditorVisual):
            self.frame.refresh()
        self._revision += 1
        self._handshake.start()

    def _preview_changed(self, preview):
        if not self.visible:
            return
        self._render_preview(preview)
        self.status.set_label(self.state.edits.message)

    def _drag_update(self, _gesture, dx, dy):
        if not self.state.edits.active:
            return
        self._pointer_position = (self._drag_origin[0] + dx, self._drag_origin[1] + dy)
        self._refresh_cursor()
        point = self._drag_point(*self._pointer_position)
        self._preview.preview(point)
        if point is None:
            self.status.set_label("공간 안에 놓아 주세요")

    def _drag_end(self, _gesture, dx, dy):
        self._waiting_press = None
        if self.state.edits.active:
            point = self._drag_point(self._drag_origin[0] + dx, self._drag_origin[1] + dy)
            self._preview.release(point)

    def _drag_cancel(self, _gesture, _sequence):
        self._waiting_press = None
        if getattr(self, "_native_press", None) is not None:
            return  # Clearing GTK pointer focus is part of native ownership transfer.
        self._preview.cancel("이동을 취소했습니다")
        self._finish_drag()

    def _finish_drag(self):
        self._shown_preview = None
        message = self.state.edits.message
        self.preview_panel.set_visible(False)
        self.panel.set_opacity(1.0)
        self._drag_cells = {}
        self.update(self._windows)
        self.status.set_label(self.state.diagnostic or message or "Super+Space로 닫기")
        self._refresh_cursor()

    def _visible_cells(self):
        viewport = self.scroll.get_child()
        clip = self._bounds(viewport, self.window) if viewport else None
        if clip is None:
            return ()
        left, top = max(0.0, clip[0]), max(0.0, clip[1])
        right = min(float(self.window.get_width()), clip[0] + clip[2])
        bottom = min(float(self.window.get_height()), clip[1] + clip[3])
        panel_box = self._bounds(self.panel, self.window) if isinstance(getattr(self, "frame", None), EditorVisual) else None
        origin_x, origin_y = panel_box[:2] if panel_box else (0, 0)
        cells = []
        for (column, row), holder in sorted(self.panel.holders.items()):
            box = self._bounds(holder, self.window)
            if box is None:
                continue
            x, y = max(left, box[0]), max(top, box[1])
            end_x, end_y = min(right, box[0] + box[2]), min(bottom, box[1] + box[3])
            if end_x > x and end_y > y:
                cells.append((column, row, x-origin_x, y-origin_y, end_x - x, end_y - y))
        return tuple(cells)

    def _invalidate_grab_layout(self):
        layout = getattr(self, "_registered_layout", None)
        self._registered_layout = None
        self._projection_frame = None
        if layout is not None:
            try:
                self.client.spatial_grab_layout(replace(layout, cells=()))
            except HyprlandError:
                pass  # Never replay a registration across an uncertain lifetime.

    def _start_grab_geometry(self):
        self._invalidate_grab_layout()
        self._cell_geometry = self._visible_cells()
        if not getattr(self, "_geometry_tick_id", 0):
            self._geometry_tick_id = self.window.add_tick_callback(self._watch_grab_geometry)

    def _stop_grab_geometry(self):
        self._invalidate_grab_layout()
        if tick := getattr(self, "_geometry_tick_id", 0):
            self.window.remove_tick_callback(tick)
            self._geometry_tick_id = 0

    def _watch_grab_geometry(self, _widget, _clock):
        if self.state.mode is not SpatialEditorMode.TRANSIENT_DRAG:
            self._geometry_tick_id = 0
            return False
        cells = self._visible_cells()
        allocation = self.window.get_width(), self.window.get_height()
        if cells != self._cell_geometry or allocation != self._allocation:
            self._invalidate_grab_layout()
            self._cell_geometry = cells
            self._frame_revision()
            self._revision += 1
            self._handshake.start()
        return True

    def projection_presented(self, payload: str) -> bool:
        event = self.state.grab_event
        frame = getattr(self, "_projection_frame", None)
        if isinstance(getattr(self, "frame", None), EditorVisual) and self.frame.animating:
            return False
        if event is None or frame is None or self.state.mode is not SpatialEditorMode.TRANSIENT_DRAG:
            return False
        try:
            namespace, generation, revision, content_revision = payload.split(",")
            if not revision.isascii() or not revision.isdecimal() or not content_revision.isascii() or not content_revision.isdecimal():
                return False
            revision, content_revision = int(revision), int(content_revision)
        except ValueError:
            return False
        if (namespace, generation, content_revision) != ("luminophore-shell-spatial-editor", frame[0], frame[1]):
            return False
        if not 0 < revision < 2**64 or content_revision != self._revision:
            return False
        cells = self._visible_cells()
        if cells != frame[2]:
            self._invalidate_grab_layout()
            self._revision += 1
            self._handshake.start()
            return False
        layout = SpatialGrabLayout(event.generation, event.revision, event.topology_revision,
                                   generation, revision, cells,self._board_output if self.state.view and self.state.view.state.protocol_version==2 else 0, event.target_epoch)
        if layout == getattr(self, "_registered_layout", None):
            return True
        # Consume this presentation before IPC; a duplicate event cannot retry it.
        self._projection_frame = None
        try:
            accepted = self.client.spatial_grab_layout(layout)
        except HyprlandError:
            # The compositor may have accepted a timed-out request. Retain its
            # identity so geometry changes or close can explicitly invalidate it.
            self._registered_layout = layout
            return False
        if accepted:
            self._registered_layout = layout
        return accepted

    def _frame_revision(self) -> int:
        allocation = self.window.get_width(), self.window.get_height()
        if min(allocation) <= 0:
            return 0
        if allocation != self._allocation:
            if self.state.edits.active:
                self._preview.cancel("화면 배치가 변경되었습니다. 다시 드래그해 주세요")
                self.preview_panel.set_visible(False)
                self.panel.set_opacity(1.0)
                self._shown_preview = None
            self._allocation = allocation
            self._revision += 1
            if isinstance(getattr(self, "frame", None), EditorVisual):
                self.frame.refresh()
        return self._revision

    def _submit_projection(self) -> bool:
        if not self.visible:
            return True
        width, height = self.window.get_width(), self.window.get_height()
        if width <= 0 or height <= 0:
            return False
        generation = uuid.uuid4().hex
        cells = self._visible_cells() if self.state.mode is SpatialEditorMode.TRANSIENT_DRAG else ()
        self._projection_frame = (generation, self._revision, cells)
        box = self._bounds(self.panel, self.window) if isinstance(getattr(self, "frame", None), EditorVisual) else None
        box = box or (0.0, 0.0, float(width), float(height))
        blur = (0.0, 0.0, 0.0, 0.0)
        visual = getattr(self, "frame", None)
        if isinstance(visual, EditorVisual):
            _, views = visual.geometry()
            origin = self._bounds(visual, self.window)
            if views and origin:
                vx, vy, vw, vh = views[0]
                blur = (origin[0]+vx, origin[1]+vy, vw, vh)
        args = ("spatial-editor", True, generation, self._revision, {
            "panel_x": box[0], "panel_y": box[1], "panel_width": box[2], "panel_height": box[3],
            "blur_x": blur[0], "blur_y": blur[1], "blur_width": blur[2], "blur_height": blur[3],
            "bloom": False,
            "radius": 14.0, "outline": 1.0, "extent": 1.0, "intensity": 0.0,
        })
        return submit_surface_projection(self, self.client.shell_projection, args, self._projection_applied)

    def _projection_applied(self, projected):
        if projected and getattr(self, "_waiting_press", None) is not None:
            self._resume_waiting_press()
        width, height = self.window.get_width(), self.window.get_height()
        if not projected:
            self._waiting_press = None
            self._approved_input = None
            self._invalidate_grab_layout()
            self._preview.cancel("표시 상태를 다시 확인하고 있습니다")
        surface = self.window.get_surface()
        if surface:
            interactive = projected and self.state.mode is SpatialEditorMode.PERSISTENT
            visual = getattr(self, "frame", None)
            if isinstance(visual, EditorVisual) and visual.animating:
                interactive = False
            box = self._bounds(self.panel, self.window) if isinstance(visual, EditorVisual) else None
            box = box or (0, 0, width, height)
            if projected:
                self._approved_input = (surface, box)
            surface.set_input_region(cairo.Region(cairo.RectangleInt(*(int(v) for v in box))) if interactive else cairo.Region())
            # Presentation arrives after GTK's last frame. Wayland input state
            # is pending until a surface commit, even when no pixels changed.
            if getattr(self.state.edits, "drag", None):
                self._refresh_cursor()
            surface.queue_render()
        return projected

    def destroy(self) -> None:
        forget_surface_projection(self)
        self.close(animate=False)
        self._preview.close()
        self._native_drag.close()
        self.window.destroy()

    def _size_visual(self):
        geometry = self.monitor.get_geometry()
        cell = max(28, min(CELL_SIZE, (geometry.width-160)//12, (geometry.height-160)//10))
        self.panel.cell_size = self.preview_panel.cell_size = cell
        self.panel.icon_size = self.preview_panel.icon_size = min(ICON_SIZE, round(cell*.42))
        self.frame.set_size_request(min(geometry.width, cell*14), min(geometry.height, cell*12))

    def _appearance_ready(self):
        if not self.visible:
            return
        self._revision += 1
        self._handshake.start()
        self.frame.refresh()
