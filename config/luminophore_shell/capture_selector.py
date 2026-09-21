"""Disposable layer-shell region selector. Emits one result after destroying overlays."""
from __future__ import annotations
import json
import os
import sys
from .bootstrap import LAYER_SHELL, configure_release, preload_entries, with_preload

if __name__ == '__main__' and LAYER_SHELL not in preload_entries(os.environ.get('LD_PRELOAD')):
    env = dict(os.environ)
    env['LD_PRELOAD'] = with_preload(preload_entries(env.get('LD_PRELOAD')), LAYER_SHELL)
    os.execve(sys.executable, [sys.executable, '-m', 'luminophore_shell.capture_selector'], env)

configure_release()

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gtk4LayerShell', '1.0')
from gi.repository import Gtk, Gdk, GLib, Gtk4LayerShell
from .capture_selection import CaptureSelection

class Selector:
    def __init__(self):
        self.loop = GLib.MainLoop()
        self.selection = CaptureSelection()
        self.windows = []
        self.areas = []
        self.result = None
        self.done = False
        display = Gdk.Display.get_default()
        if display is None or not Gtk4LayerShell.is_supported():
            raise RuntimeError('Wayland layer-shell is unavailable')
        self.monitors = display.get_monitors()
        self.monitors.connect('items-changed', lambda *_: self.finish(None))
        for i in range(self.monitors.get_n_items()):
            monitor = self.monitors.get_item(i)
            geo = monitor.get_geometry()
            monitor.connect('notify::geometry', lambda *_: self.finish(None))
            window = Gtk.Window()
            window.add_css_class('luminophore-capture-selector')
            Gtk4LayerShell.init_for_window(window)
            Gtk4LayerShell.set_namespace(window, 'luminophore-capture-selector')
            Gtk4LayerShell.set_monitor(window, monitor)
            Gtk4LayerShell.set_layer(window, Gtk4LayerShell.Layer.OVERLAY)
            Gtk4LayerShell.set_keyboard_mode(window, Gtk4LayerShell.KeyboardMode.EXCLUSIVE)
            Gtk4LayerShell.set_exclusive_zone(window, -1)
            for edge in (Gtk4LayerShell.Edge.TOP, Gtk4LayerShell.Edge.BOTTOM, Gtk4LayerShell.Edge.LEFT, Gtk4LayerShell.Edge.RIGHT):
                Gtk4LayerShell.set_anchor(window, edge, True)
            area = Gtk.DrawingArea()
            area.set_cursor_from_name('crosshair')
            area.set_draw_func(self.draw, geo)
            drag = Gtk.GestureDrag.new()
            drag.set_button(0)
            drag.connect('drag-begin', self.begin, geo)
            drag.connect('drag-update', self.update)
            drag.connect('drag-end', self.end)
            drag.connect('cancel', lambda *_: self.finish(None))
            area.add_controller(drag)
            buttons = Gtk.EventControllerLegacy.new()
            buttons.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            buttons.connect('event', self.extra_button)
            area.add_controller(buttons)
            keys = Gtk.EventControllerKey.new()
            keys.connect('key-pressed', self.key)
            window.add_controller(keys)
            window.set_child(area)
            self.windows.append(window)
            self.areas.append(area)
        css = Gtk.CssProvider()
        css.load_from_data(b'window.luminophore-capture-selector { background: transparent; }')
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for window in self.windows:
            window.present()
        GLib.timeout_add_seconds(150, lambda: self.finish(None))

    def key(self, _controller, keyval, *_):
        if keyval == Gdk.KEY_Escape:
            self.finish(None)
        return True

    def extra_button(self, _controller, event):
        if event.get_event_type() == Gdk.EventType.BUTTON_PRESS and self.selection.origin is not None:
            self.finish(None)
            return True
        return False

    def begin(self, gesture, x, y, geo):
        if not self.selection.begin(gesture.get_current_button(), geo.x + x, geo.y + y):
            self.finish(None)

    def update(self, _gesture, dx, dy):
        if self.selection.origin:
            ox, oy = self.selection.origin
            self.selection.update(ox + dx, oy + dy)
            for area in self.areas:
                area.queue_draw()

    def end(self, _gesture, dx, dy):
        if self.selection.origin:
            ox, oy = self.selection.origin
            self.finish(self.selection.finish(ox + dx, oy + dy))

    def draw(self, _area, cr, width, height, geo):
        import cairo
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(0, 0, width, height)
        rect = self.selection.rect
        if rect:
            x, y, w, h = rect
            cr.rectangle(x - geo.x, y - geo.y, w, h)
        cr.set_source_rgba(0.02, 0.03, 0.04, .28)
        cr.fill()
        if rect:
            cr.rectangle(x - geo.x + .5, y - geo.y + .5, max(0, w - 1), max(0, h - 1))
            cr.set_source_rgba(.38, .88, .92, .95)
            cr.set_line_width(1)
            cr.stroke()

    def finish(self, result):
        if self.done:
            return False
        self.done, self.result = True, result
        for window in self.windows:
            window.destroy()
        self.loop.quit()
        return False

    def run(self):
        if not self.windows:
            raise RuntimeError('No display outputs')
        if not self.done:
            self.loop.run()
        return self.result

if __name__ == '__main__':
    try:
        Gtk.init()
        print(json.dumps(Selector().run()), flush=True)
    except Exception as exc:
        print(f'capture selector: {exc}', file=sys.stderr)
        sys.exit(1)
