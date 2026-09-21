from __future__ import annotations

import logging
import time
from typing import Callable

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk


LOG = logging.getLogger("luminophore-shell")


class FrameProjectionHandshake:
    """Retry a projection transaction only on real GTK frame boundaries."""

    def __init__(
        self,
        widget: Gtk.Widget,
        namespace: str,
        submit: Callable[[], bool],
        revision: Callable[[], int] | None = None,
        stable_frames: int = 2,
    ) -> None:
        self.widget = widget
        self.namespace = namespace
        self.submit = submit
        self._tick_id = 0
        self._attempts = 0
        self._revision = revision
        self._stable_frames = max(1, stable_frames)
        self._last_revision: int | None = None
        self._stable_count = 0

    @property
    def pending(self) -> bool:
        return self._tick_id != 0

    def start(self) -> None:
        if self._tick_id:
            return
        self._attempts = 0
        self._last_revision = None
        self._stable_count = 0
        self._tick_id = self.widget.add_tick_callback(self._on_frame)

    def cancel(self) -> None:
        if not self._tick_id:
            return
        self.widget.remove_tick_callback(self._tick_id)
        self._tick_id = 0

    def _on_frame(self, _widget: Gtk.Widget, _frame_clock: Gdk.FrameClock) -> bool:
        self._attempts += 1
        if self._revision is not None:
            revision = self._revision()
            if revision != self._last_revision:
                self._last_revision = revision
                self._stable_count = 1
            else:
                self._stable_count += 1
            if revision <= 0 or self._stable_count < self._stable_frames:
                return True
        if not self.submit():
            if self._attempts == 1 or self._attempts % 60 == 0:
                LOG.debug(
                    "projection handshake pending namespace=%s attempts=%d",
                    self.namespace,
                    self._attempts,
                )
            return True
        LOG.debug(
            "projection handshake committed namespace=%s attempts=%d",
            self.namespace,
            self._attempts,
        )
        self._tick_id = 0
        return False


__all__ = ["FrameProjectionHandshake"]


def submit_surface_projection(owner, submit, args, applied):
    """Return queue admission; only matching presentation transfers ownership."""
    from ..hyprland import HyprlandClient
    from gi.repository import GLib
    client = getattr(submit, "__self__", None)
    if not isinstance(client, HyprlandClient):
        accepted = bool(submit and submit(*args))
        applied(accepted)
        return accepted
    token = object()
    owner._projection_request_token = token
    generation = args[2]
    owner._projection_generation = generation
    owner._projection_client = client
    owner._projection_name = args[0]
    owner._projection_status = "queued"
    owner._projection_requested_at = time.monotonic()

    recovery_attempts = 0
    recovery_pending = False

    def result(status):
        nonlocal recovery_attempts, recovery_pending
        if getattr(owner, "_projection_dead", False) or owner._projection_generation != generation or owner._projection_request_token is not token:
            return
        # Presentation may beat the command reply; never regress its state.
        if owner._projection_status == "presented":
            return
        if owner._projection_status == "unavailable" and status != "presented":
            return
        if status == "accepted" and owner._projection_status == "unknown":
            return  # A late command reply must not cancel scheduled readback.
        owner._projection_status = status
        if status == "presented":
            owner._projection_presented_ms = (time.monotonic() - owner._projection_requested_at) * 1000
            if getattr(owner, "_osd_waiting", False) and getattr(owner, "_reveal_to", 0) == 1:
                owner._osd_latency_ms = (time.monotonic() - owner._osd_requested_at) * 1000
                owner._osd_waiting = False
        LOG.debug("surface projection namespace=%s state=%s generation=%s", args[0], status, generation)
        if status == "presented":
            applied(True)
            # Readback uses the same native grab-layout acknowledgement path
            # as the event, including the exact compositor revision.
            payload = client.consume_projection_receipt(args[0], generation, args[3])
            if payload and callable(getattr(owner, "projection_presented", None)):
                owner.projection_presented(payload)
        elif status == "unknown":
            if recovery_pending:
                return
            if recovery_attempts >= 3:
                owner._projection_status = "unavailable"
                applied(False)
                return
            recovery_attempts += 1
            recovery_pending = True
            attempt = recovery_attempts
            def reconcile():
                nonlocal recovery_pending
                recovery_pending = False
                if (not getattr(owner, "_projection_dead", False) and owner._projection_generation == generation and owner._projection_request_token is token
                        and owner._projection_status == "unknown"):
                    if not client.reconcile_projection(args[0], generation):
                        result("unsupported")
                    else:
                        # A bounded watchdog also covers a busy transport queue.
                        def check_deadline():
                            if (owner._projection_request_token is token and not getattr(owner, "_projection_dead", False)
                                    and owner._projection_generation == generation and owner._projection_status == "unknown"
                                    and recovery_attempts == attempt and not recovery_pending):
                                result("unknown")
                            return False
                        GLib.timeout_add(2500, check_deadline)
                return False
            GLib.timeout_add(200 * 2 ** (recovery_attempts - 1), reconcile)
        elif status == "unsupported":
            owner._projection_status = "unavailable"
            applied(False)
        elif status == "failed":
            # Explicit prepare rejection may mean the initial map is not ready.
            # This is a new request, never a replay after unknown completion.
            attempts = getattr(owner, "_projection_rejections", 0) + 1
            owner._projection_rejections = attempts
            if attempts <= 3:
                def retry():
                    if not getattr(owner, "_projection_dead", False) and owner._projection_generation == generation and owner._projection_request_token is token:
                        handshake = getattr(owner, "_projection_handshake", None) or getattr(owner, "_handshake", None)
                        if handshake:
                            handshake.start()
                    return False
                GLib.timeout_add(32, retry)
            else:
                applied(False)
        if status == "presented":
            owner._projection_rejections = 0

    queued = client.shell_projection_async(*args, result)
    if not queued:
        applied(False)
    else:
        def deadline():
            if (not getattr(owner, "_projection_dead", False) and owner._projection_generation == generation and owner._projection_request_token is token
                    and owner._projection_status in {"queued", "accepted"}):
                result("unknown")
            return False
        GLib.timeout_add(1200, deadline)
    return queued


def forget_surface_projection(owner):
    owner._projection_dead = True
    client = getattr(owner, "_projection_client", None)
    if client:
        client.forget_projection(owner._projection_name)
