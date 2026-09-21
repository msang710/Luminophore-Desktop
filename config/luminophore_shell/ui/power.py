from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .effects import attach_luminophore_state


def power_expanded(
    on_lock: Callable[[], None],
    on_suspend: Callable[[], None],
    on_logout: Callable[[], None],
    on_restart: Callable[[], None],
    on_shutdown: Callable[[], None],
    on_firmware_setup: Callable[[], None],
) -> Gtk.Widget:
    actions = (
        ("lock", "잠금", on_lock),
        ("suspend", "절전", on_suspend),
        ("logout", "로그아웃", on_logout),
        ("reboot", "재시작", on_restart),
        ("poweroff", "종료", on_shutdown),
        ("firmware-setup", "BIOS/UEFI 진입", on_firmware_setup),
    )
    grid = Gtk.Grid(column_spacing=12, row_spacing=12)
    grid.set_halign(Gtk.Align.CENTER)
    for index, (_action, label, callback) in enumerate(actions):
        button = Gtk.Button(label=label)
        button.add_css_class("luminophore-button")
        button.set_can_focus(False)
        button.connect("clicked", lambda _button, call=callback: call())
        attach_luminophore_state(button)
        grid.attach(button, index % 3, index // 3, 1, 1)
    return grid
