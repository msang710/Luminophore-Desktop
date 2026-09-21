from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .hyprland import SpatialState
from .spatial_edit import SpatialEditPreview, SpatialEditRequest
from .spatial_feedback import SpatialFeedback
from .spatial_grab import SpatialGrabEvent


class SpatialEditorMode(Enum):
    HIDDEN = "hidden"
    PERSISTENT = "persistent"
    TRANSIENT_DRAG = "transient-drag"


@dataclass(frozen=True)
class SpatialEditorView:
    state: SpatialState

    @property
    def revision(self) -> tuple[int, int]:
        return self.state.topology_revision, self.state.revision


class SpatialEditorState:
    def __init__(self) -> None:
        self.mode = SpatialEditorMode.HIDDEN
        self.view: SpatialEditorView | None = None
        self.diagnostic = ""
        self.feedback: SpatialFeedback | None = None
        self.edits = SpatialEditSession()
        self.grab_event: SpatialGrabEvent | None = None
        self.grab_generation: int | None = None
        self._last_grab_generation = 0
        self.grab_source: str | None = None
        self._retired_grab_sources: set[str] = set()
        self._return_mode = SpatialEditorMode.HIDDEN

    @property
    def visible(self) -> bool:
        return self.mode is not SpatialEditorMode.HIDDEN

    def toggle(self) -> None:
        if self.visible:
            self.close()
        else:
            self.mode = SpatialEditorMode.PERSISTENT

    def close(self) -> None:
        self.mode = SpatialEditorMode.HIDDEN
        self.feedback = None
        self.edits.cancel()
        self.grab_event = None
        self.grab_generation = None
        self._return_mode = SpatialEditorMode.HIDDEN

    def connection_reset(self) -> None:
        if self.grab_generation is not None:
            self.finish_transient(self.grab_generation, self.grab_source)
        self.edits.cancel("연결이 갱신되어 진행 중인 이동을 취소했습니다")
        self.feedback = None
        self.view = None

    def begin_transient(self, generation: int, source: str | None = None) -> bool:
        if type(generation) is not int or generation <= 0:
            return False
        if source is not None and (not isinstance(source, str) or not source):
            return False
        if source is None and self.grab_source is not None:
            return False
        if source is not None and source != self.grab_source:
            if not source or source in self._retired_grab_sources:
                return False
            if self.grab_source is not None:
                self._retired_grab_sources.add(self.grab_source)
            self.grab_source = source
            self._last_grab_generation = 0
        if generation <= self._last_grab_generation:
            return False
        if self.mode is not SpatialEditorMode.TRANSIENT_DRAG:
            self._return_mode = self.mode
        self.edits.cancel()
        self.feedback = None
        self._last_grab_generation = generation
        self.grab_generation = generation
        self.mode = SpatialEditorMode.TRANSIENT_DRAG
        return True

    def finish_transient(self, generation: int, source: str | None = None) -> bool:
        if source != self.grab_source:
            return False
        if type(generation) is not int or self.mode is not SpatialEditorMode.TRANSIENT_DRAG or generation != self.grab_generation:
            return False
        self.edits.cancel()
        self.feedback = None
        self.grab_event = None
        self.grab_generation = None
        self.mode = self._return_mode
        self._return_mode = SpatialEditorMode.HIDDEN
        return True

    def accept_grab(self, event: SpatialGrabEvent, snapshot: SpatialState | None = None) -> bool:
        if event.phase == "begin":
            snapshot = snapshot or (self.view.state if self.view else None)
            if snapshot is None or not snapshot.active or not snapshot.committed or snapshot.diagnostics:
                return False
            if (snapshot.committed_topology_revision, snapshot.committed_model_revision) != (snapshot.topology_revision, snapshot.revision):
                return False
            if (snapshot.topology_revision, snapshot.revision) != (event.topology_revision, event.revision):
                return False
            if not any(output.output_id == event.output_id for output in snapshot.output_views):
                return False
            window = next((window for window in snapshot.windows if window.address == event.window), None)
            if window is None or (window.mode == "floating") != event.floating:
                return False
            if not self.begin_transient(event.generation, event.source):
                return False
            self.accept(snapshot)
            self.grab_event = event
            return True
        original = self.grab_event
        if original is None or self.mode is not SpatialEditorMode.TRANSIENT_DRAG:
            return False
        if (event.source, event.generation, event.revision, event.topology_revision,
            event.output_id, event.window, event.floating) != (
                original.source, original.generation, original.revision, original.topology_revision,
                original.output_id, original.window, original.floating):
            return False
        if event.target_epoch < original.target_epoch:
            return False
        if event.target_epoch == original.target_epoch and event.target_output_id != original.target_output_id:
            return False
        if event.phase in {"end", "cancel"}:
            return self.finish_transient(event.generation, event.source)
        if event.phase != "update":
            return False
        if event.result and event.result.accepted:
            expected = event.revision + (event.result.status == "applied")
            if (event.result.topology_revision, event.result.revision) != (event.topology_revision, expected):
                return False
        self.grab_event = event
        return True

    def accept(self, state: SpatialState) -> bool:
        self.edits.observe(state)
        if not state.active or not state.committed or state.diagnostics or (
            state.revision != state.committed_model_revision
            or state.topology_revision != state.committed_topology_revision
        ):
            self.diagnostic = "공간 정보를 다시 확인하고 있습니다"
            return False
        self.view = SpatialEditorView(state)
        self.diagnostic = ""
        if self.feedback and (self.feedback.topology_revision, self.feedback.revision) != self.view.revision:
            self.feedback = None
        return True

    def highlight(self, feedback: SpatialFeedback) -> bool:
        if not self.visible:
            return False
        # The surface consumes only feedback for the snapshot it displays.
        if self.view and (feedback.topology_revision, feedback.revision) == self.view.revision:
            self.feedback = feedback
        return True


@dataclass(frozen=True)
class SpatialDrag:
    snapshot: SpatialState
    action: str
    output_id: int
    start: tuple[int, int]
    origin: tuple[int, int]
    size: tuple[int, int] = (0, 0)
    window: str = ""
    edges: tuple[str, ...] = ()

    def request_at(self, point: tuple[int, int]) -> SpatialEditRequest:
        dx, dy = point[0] - self.start[0], point[1] - self.start[1]
        x, y = self.origin
        columns, rows = self.size
        if self.action == "resize-view":
            if "left" in self.edges:
                x += dx
                columns -= dx
            if "right" in self.edges:
                columns += dx
            if "top" in self.edges:
                y += dy
                rows -= dy
            if "bottom" in self.edges:
                rows += dy
        else:
            x += dx
            y += dy
        return SpatialEditRequest(self.action, self.snapshot.revision, self.snapshot.topology_revision,
                                  self.output_id, x, y, self.window, columns, rows)


class SpatialEditSession:
    """A pointer gesture owns one immutable base and at most one commit attempt."""

    def __init__(self) -> None:
        self.generation = 0
        self.drag: SpatialDrag | None = None
        self.request: SpatialEditRequest | None = None
        self.preview: SpatialEditPreview | None = None
        self.message = ""
        self.refresh_required = False

    @property
    def active(self) -> bool:
        return self.drag is not None

    def cancel(self, message: str = "") -> None:
        self.generation += 1
        self.drag = None
        self.request = None
        self.preview = None
        self.message = message

    def observe(self, snapshot: SpatialState) -> None:
        if self.drag and (not self._valid(snapshot) or
                          (snapshot.revision, snapshot.topology_revision) !=
                          (self.drag.snapshot.revision, self.drag.snapshot.topology_revision)):
            self.cancel("공간이 변경되었습니다. 다시 드래그해 주세요")
            self.refresh_required = True

    @staticmethod
    def _valid(snapshot: SpatialState) -> bool:
        return (snapshot.active and snapshot.committed and not snapshot.diagnostics
                and snapshot.revision == snapshot.committed_model_revision
                and snapshot.topology_revision == snapshot.committed_topology_revision)

    def begin(self, snapshot: SpatialState, action: str, output_id: int, start: tuple[int, int],
              *, window: str = "", edges: tuple[str, ...] = ()) -> bool:
        self.cancel()
        self.refresh_required = False
        if not self._valid(snapshot) or action not in {"move-window", "move-view", "resize-view"}:
            return False
        output = next((view for view in snapshot.output_views if view.output_id == output_id), None)
        if output is None:
            return False
        if action == "move-window":
            target = next((item for item in snapshot.windows if item.address == window), None)
            if target is None:
                return False
            origin, size = (target.x, target.y), (0, 0)
        else:
            if snapshot.presentation_mode == "wide":
                return False
            rect = output.rect
            origin, size = (rect.x, rect.y), (rect.columns, rect.rows)
        if action == "resize-view" and (not edges or len(set(edges)) != len(edges)
                or not set(edges) <= {"left", "right", "top", "bottom"}
                or {"left", "right"} <= set(edges) or {"top", "bottom"} <= set(edges)):
            return False
        self.drag = SpatialDrag(snapshot, action, output_id, start, origin, size, window, edges)
        return True

    def accept_preview(self, request: SpatialEditRequest, preview: SpatialEditPreview) -> SpatialEditPreview | None:
        if self.drag is None:
            return None
        if preview.status in {"stale-revision", "stale-topology"}:
            self.cancel("공간이 변경되었습니다. 다시 드래그해 주세요")
            self.refresh_required = True
            return preview
        if preview.accepted and (preview.topology_revision != request.topology_revision or
                                 preview.revision != request.revision + (preview.status == "applied")):
            self.cancel("미리보기의 공간 정보가 일치하지 않습니다")
            self.refresh_required = True
            return None
        self.request, self.preview = request, preview
        self.message = "놓아서 적용" if preview.accepted else "이 위치에는 놓을 수 없습니다"
        return preview
