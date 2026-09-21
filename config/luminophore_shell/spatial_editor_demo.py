"""Disposable GTK grid + existing native shader preview; no spatial mutations."""
from __future__ import annotations

import ast
import ctypes
import re
import array
from pathlib import Path
import time
import os
import sys

from .bootstrap import LAYER_SHELL, preload_entries, with_preload, without_preload

# Match the Shell launcher: load layer-shell before GTK, then keep it out of children.
if __name__ == '__main__':
    preload = preload_entries(os.environ.get('LD_PRELOAD'))
    if LAYER_SHELL not in preload:
        environment = dict(os.environ)
        environment['LD_PRELOAD'] = with_preload(preload, LAYER_SHELL)
        os.execve(sys.executable, [sys.executable, '-m', 'luminophore_shell.spatial_editor_demo'], environment)
    child_preload = without_preload(preload, LAYER_SHELL)
    if child_preload is None:
        os.environ.pop('LD_PRELOAD', None)
    else:
        os.environ['LD_PRELOAD'] = child_preload

import cairo
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gtk4LayerShell', '1.0')
from gi.repository import Gdk, Gtk, Gtk4LayerShell

os.environ.setdefault('PYOPENGL_PLATFORM', 'egl')
from OpenGL import GL
from .state import read_palette_state
from .theme import Palette


ANIMATION_SPEED = 1.6
GRID_SCALE = .48
GRID_LINE_WIDTH = 3.1


def rgb(value):
    return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))


def shader_source(name):
    source = (Path(__file__).parents[1] / 'native/luminophore_glow_shader.h').read_text()
    block = source.split('static const char *' + name + ' =', 1)[1].split('\n\n', 1)[0]
    return ''.join(ast.literal_eval(token) for token in re.findall(r'"(?:[^"\\]|\\.)*"', block))


class Glow(Gtk.GLArea):
    def __init__(self, demo, primary, accent):
        super().__init__()
        self.demo, self.primary, self.accent = demo, primary, accent
        self.program = self.buffer = 0
        self.frame_time = time.monotonic()
        self.set_allowed_apis(Gdk.GLAPI.GLES)
        self.set_required_version(3, 0)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.connect('render', self.render_glow)
        self.connect('unrealize', self.release_gl)

    def initialize_gl(self):
        shaders = []
        for kind, name in ((GL.GL_VERTEX_SHADER, 'luminophore_glow_vertex_source'),
                           (GL.GL_FRAGMENT_SHADER, 'luminophore_glow_fragment_source')):
            shader = GL.glCreateShader(kind)
            source = shader_source(name)
            if kind == GL.GL_FRAGMENT_SHADER:
                # Reuse the existing emission kernel with a shared spatial reveal.
                source = source.replace('uniform vec2 u_viewport;',
                    'uniform vec2 u_viewport; uniform vec2 u_grid_size; '
                    'uniform float u_wave; uniform float u_exit; uniform float u_haze;')
                source = source.replace('float a=(core+near+bloom)',
                    'float a=(core+near*mix(1.0,0.18,u_haze)+bloom*mix(1.0,2.4,u_haze))')
                source = source.replace('gl_FragColor=vec4(c*a,a);',
                    'float r=length((gl_FragCoord.xy-u_viewport*0.5)/u_grid_size);'
                    'float gate=smoothstep(0.0,0.18,u_wave-r);'
                    'gate=mix(gate,1.0-gate,u_exit);'
                    'a*=gate; gl_FragColor=vec4(c*a,a);')
            GL.glShaderSource(shader, source)
            GL.glCompileShader(shader)
            if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
                raise RuntimeError(GL.glGetShaderInfoLog(shader))
            shaders.append(shader)
        self.program = GL.glCreateProgram()
        for shader in shaders:
            GL.glAttachShader(self.program, shader)
        GL.glLinkProgram(self.program)
        for shader in shaders:
            GL.glDeleteShader(shader)
        if not GL.glGetProgramiv(self.program, GL.GL_LINK_STATUS):
            raise RuntimeError(GL.glGetProgramInfoLog(self.program))
        self.buffer = GL.glGenBuffers(1)
        vertices = array.array('f', [-1,-1, 1,-1, -1,1, 1,1]).tobytes()
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.buffer)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, len(vertices), vertices, GL.GL_STATIC_DRAW)
        print('Grid demo: existing luminophore_glow_shader compiled', flush=True)

    def render_glow(self, area, context):
        if self.get_error():
            print(self.get_error(), flush=True)
            return False
        try:
            if not self.program:
                self.initialize_gl()
            scale = self.get_scale_factor()
            w, h = self.get_width() * scale, self.get_height() * scale
            step = min(w / 14, h / 9) * GRID_SCALE
            GL.glViewport(0, 0, w, h)
            GL.glDisable(GL.GL_SCISSOR_TEST)
            GL.glClearColor(0, 0, 0, 0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glUseProgram(self.program)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.buffer)
            pos = GL.glGetAttribLocation(self.program, 'a_position')
            GL.glEnableVertexAttribArray(pos)
            GL.glVertexAttribPointer(pos, 2, GL.GL_FLOAT, False, 0, ctypes.c_void_p(0))
            def uniform(name, *values):
                location = GL.glGetUniformLocation(self.program, name)
                getattr(GL, 'glUniform' + str(len(values)) + 'f')(location, *values)
            age = max(0, (self.frame_time - self.demo.started) * ANIMATION_SPEED)
            uniform('u_time', 2.3)
            uniform('u_age', 1)
            uniform('u_phase', 0)
            uniform('u_viewport', w, h)
            uniform('u_grid_size', step * 6.1, step * 4.15)
            uniform('u_wave', min(1.5, age / .85 * 1.5))
            uniform('u_exit', float(self.demo.exiting))
            # Same shader, two sources: broad central haze and accent view bloom.
            for size, radius, outline, extent, gain, color, haze in (
                (2 * scale, scale, scale, step * 5.1, .72, self.primary, 1),
                (step * 2, 3 * scale, 2 * scale, step * .34, 1.05, self.accent, 0),
            ):
                uniform('u_rect', w / 2, h / 2, size, size)
                uniform('u_radius', radius)
                uniform('u_outline', outline)
                uniform('u_extent', extent)
                uniform('u_intensity', gain)
                uniform('u_haze', haze)
                uniform('u_base', *color)
                uniform('u_core', *color)
                GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
            GL.glDisableVertexAttribArray(pos)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
            GL.glUseProgram(0)
            return True
        except Exception as exc:
            print('Grid shader error:', exc, flush=True)
            return False

    def release_gl(self, area):
        self.make_current()
        if not self.get_error():
            if self.program:
                GL.glDeleteProgram(self.program)
            if self.buffer:
                GL.glDeleteBuffers(1, [self.buffer])
        self.program = self.buffer = 0


class Demo(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='io.github.msang710.LuminophoreSpatialEditorDemo')
        self.connect('activate', self.activate_demo)
        self.connect('shutdown', self.cleanup)
        self.glows = {}
        self.canvases = []
        self.started = time.monotonic()
        self.exiting = False
        self.ticks = {}

    def failed(self, reason):
        print('native glow:', reason, flush=True)

    def layer(self, monitor, suffix, layer):
        window = Gtk.ApplicationWindow(application=self)
        window.set_decorated(False)
        window.add_css_class('luminophore-grid-demo')
        Gtk4LayerShell.init_for_window(window)
        Gtk4LayerShell.set_namespace(window, 'luminophore-grid-demo-' + suffix)
        Gtk4LayerShell.set_monitor(window, monitor)
        Gtk4LayerShell.set_layer(window, layer)
        Gtk4LayerShell.set_exclusive_zone(window, 0)
        for edge in (Gtk4LayerShell.Edge.TOP, Gtk4LayerShell.Edge.BOTTOM,
                     Gtk4LayerShell.Edge.LEFT, Gtk4LayerShell.Edge.RIGHT):
            Gtk4LayerShell.set_anchor(window, edge, True)
        return window

    def activate_demo(self, app):
        provider = Gtk.CssProvider()
        provider.load_from_data(b'window.luminophore-grid-demo { background: transparent; }')
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        palettes = read_palette_state()[1]
        monitors = display.get_monitors()
        for index in range(monitors.get_n_items()):
            monitor = monitors.get_item(index)
            palette = palettes.get(monitor.get_connector(), Palette('#63D8FF', '#F6BD69'))
            primary, accent = rgb(palette.primary), rgb(palette.secondary)
            window = self.layer(monitor, 'grid', Gtk4LayerShell.Layer.OVERLAY)
            Gtk4LayerShell.set_keyboard_mode(window, Gtk4LayerShell.KeyboardMode.EXCLUSIVE)
            canvas = Gtk.DrawingArea()
            canvas._frame_time = self.started
            canvas.set_draw_func(self.draw, (primary, accent))
            glow = Glow(self, primary, accent)
            overlay = Gtk.Overlay()
            overlay.set_child(glow)
            overlay.add_overlay(canvas)
            canvas.set_hexpand(True)
            canvas.set_vexpand(True)
            canvas.set_halign(Gtk.Align.FILL)
            canvas.set_valign(Gtk.Align.FILL)
            canvas.set_can_target(False)
            window.set_child(overlay)
            keys = Gtk.EventControllerKey()
            keys.connect('key-pressed', self.key)
            window.add_controller(keys)
            window.present()
            self.canvases.append(canvas)
            self.glows[monitor.get_connector()] = glow
        self.animate()
        print('Grid demo: Escape closes; Space replays the center-out grid reveal.', flush=True)

    def draw(self, area, cr, width, height, colors):
        primary, accent = colors
        cx, cy = width / 2, height / 2
        step = min(width / 14, height / 9) * GRID_SCALE
        rx, ry = step * 6.1, step * 4.15
        cr.save()
        cr.translate(cx, cy)
        cr.scale(rx, ry)
        cr.rectangle(-1.18, -1.18, 2.36, 2.36)
        cr.clip()
        cr.push_group()
        # Draw a crisp GTK/Cairo lattice, then mask its complete outer extent.
        cr.save()
        cr.scale(1 / rx, 1 / ry)
        for x in range(-7, 8):
            cr.move_to(x * step, -height)
            cr.line_to(x * step, height)
        for y in range(-5, 6):
            cr.move_to(-width, y * step)
            cr.line_to(width, y * step)
        cr.set_source_rgba(*primary, .58)
        cr.set_line_width(GRID_LINE_WIDTH)
        cr.stroke()
        cr.restore()
        content = cr.pop_group()
        elapsed = max(0, (area._frame_time - self.started) * ANIMATION_SPEED)
        wave = min(1.5, elapsed / .85 * 1.5)
        mask = cairo.RadialGradient(0, 0, 0, 0, 0, 1.3)
        for i in range(131):
            r = i / 100
            outer = max(0, min(1, (1.10 - r) / .56))
            outer = outer * outer * (3 - 2 * outer)
            reveal = max(0, min(1, (wave - r) / .18))
            reveal = reveal * reveal * (3 - 2 * reveal)
            if self.exiting:
                reveal = 1 - reveal
            mask.add_color_stop_rgba(r / 1.3, 1, 1, 1, outer * reveal)
        cr.set_source(content)
        cr.mask(mask)
        cr.restore()

    def key(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            if not self.exiting:
                self.exiting = True
                self.started = time.monotonic()
                self.animate()
            return True
        if keyval == Gdk.KEY_space:
            self.exiting = False
            self.started = time.monotonic()
            self.animate()
            return True
        return False

    def animate(self):
        for canvas, glow in zip(self.canvases, self.glows.values()):
            if self.ticks.get(canvas):
                canvas.remove_tick_callback(self.ticks[canvas])
            self.ticks[canvas] = canvas.add_tick_callback(self.tick, glow)

    def tick(self, canvas, clock, glow):
        now = clock.get_frame_time() / 1_000_000
        canvas._frame_time = glow.frame_time = now
        canvas.queue_draw()
        glow.queue_render()
        if (now - self.started) * ANIMATION_SPEED >= 1.0:
            self.ticks[canvas] = 0
            if self.exiting:
                self.quit()
            return False
        return True

    def cleanup(self, app):
        for canvas, tick in self.ticks.items():
            if tick:
                canvas.remove_tick_callback(tick)
        self.ticks.clear()


if __name__ == '__main__':
    raise SystemExit(Demo().run([]))
