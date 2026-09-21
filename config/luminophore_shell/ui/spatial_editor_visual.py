"""Presentation-only editor: GTK lattice/icons and the existing glow shader."""
from __future__ import annotations

import math
import array
import ast
import ctypes
import os
from pathlib import Path
import re

import cairo
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
from gi.repository import Gtk, Gdk, Gsk, Graphene

from ..state import read_palette_state_details
from ..theme import Palette

from ..transition import OVERVIEW_ENTER_MS, OVERVIEW_EXIT_MS, ease_out_cubic, ease_in_cubic

CELL_SIZE = 68
ICON_SIZE = 28
BADGE_SIZE = 44
FACE_ALPHA = .10
DURATION = OVERVIEW_ENTER_MS / 1000


def wave_front(progress, exiting=False):
    return 1.65 * (ease_in_cubic(progress) if exiting else ease_out_cubic(progress))


def wave_sample(px, py, grid, step, progress, exiting=False):
    """Presentation displacement only; never used for hit testing."""
    x, y, w, h = grid
    dx, dy = px-x-w/2, py-y-h/2
    reach = math.hypot(w/2+step*.8, h/2+step*.8)
    radius = math.hypot(dx, dy)/max(1, reach)
    phase = (radius-wave_front(progress, exiting))/.16
    envelope = math.sin(math.pi*max(0, min(1, progress))) * math.exp(-phase*phase)
    height = step*.09*envelope*math.cos(phase*3)
    length = max(1, math.hypot(dx, dy))
    return px+dx/length*height*.45, py+dy/length*height*.45-height, height



def color(value):
    return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))


def union(boxes):
    if not boxes:
        return None
    x, y = min(b[0] for b in boxes), min(b[1] for b in boxes)
    return x, y, max(b[0]+b[2] for b in boxes)-x, max(b[1]+b[3] for b in boxes)-y


def grid_mask(width, height, grid, step):
    """Cached by the caller: a feathered rounded rectangle, not an ellipse."""
    x, y, w, h = grid
    pad, feather, corner = step*1.15, step*1.5, step*1.9
    surface = cairo.ImageSurface(cairo.FORMAT_A8, width, height)
    cr = cairo.Context(surface)
    cr.set_operator(cairo.OPERATOR_SOURCE)
    for i in range(65):
        t = i/64
        inset = feather*t
        left, top = x-pad+inset, y-pad+inset
        right, bottom = x+w+pad-inset, y+h+pad-inset
        radius = max(0.1, min(corner-inset, (right-left)/2, (bottom-top)/2))
        cr.new_sub_path()
        cr.arc(right-radius, top+radius, radius, -math.pi/2, 0)
        cr.arc(right-radius, bottom-radius, radius, 0, math.pi/2)
        cr.arc(left+radius, bottom-radius, radius, math.pi/2, math.pi)
        cr.arc(left+radius, top+radius, radius, math.pi, math.pi*1.5)
        cr.close_path()
        cr.set_source_rgba(1, 1, 1, t*t*(3-2*t))
        cr.fill()
    return surface


def grid_opacity(px, py, grid, step):
    x, y, w, h = grid
    pad, feather, corner = step*1.15, step*1.5, step*1.9
    dx = abs(px-x-w/2) - (w/2+pad-corner)
    dy = abs(py-y-h/2) - (h/2+pad-corner)
    distance = math.hypot(max(dx, 0), max(dy, 0)) + min(max(dx, dy), 0) - corner
    t = max(0, min(1, -distance/feather))
    return t*t*(3-2*t)


class EditorGlow(Gtk.GLArea):
    def __init__(self, visual):
        super().__init__()
        self.visual = visual
        self.program = self.buffer = 0
        self.locations = {}
        self.set_allowed_apis(Gdk.GLAPI.GLES)
        self.set_required_version(3, 0)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_can_target(False)
        self.connect('render', self.render_gl)
        self.connect('unrealize', self.release)
        self.failed = False

    def initialize(self):
        os.environ.setdefault('PYOPENGL_PLATFORM', 'egl')
        from OpenGL import GL
        self.gl = GL
        header = (Path(__file__).parents[2] / 'native/luminophore_glow_shader.h').read_text()
        shaders = []
        try:
            for kind, name in ((GL.GL_VERTEX_SHADER, 'luminophore_glow_vertex_source'),
                               (GL.GL_FRAGMENT_SHADER, 'luminophore_glow_fragment_source')):
                block = header.split('static const char *' + name + ' =', 1)[1].split('\n\n', 1)[0]
                source = ''.join(ast.literal_eval(t) for t in re.findall(r'"(?:[^"\\]|\\.)*"', block))
                if kind == GL.GL_FRAGMENT_SHADER:
                    source = source.replace('uniform vec2 u_viewport;', 'uniform vec2 u_viewport; uniform float u_haze; uniform vec4 u_grid; uniform float u_wave; uniform float u_exit; uniform float u_progress; uniform float u_step; uniform vec2 u_grid_size; uniform float u_deform;')
                    source = source.replace('vec2 p=gl_FragCoord.xy-u_rect.xy;',
                        'vec2 delta=gl_FragCoord.xy-u_grid.xy;'
                        'float phase=(length(delta)/u_grid.z-u_wave)/0.16;'
                        'float lift=u_step*0.09*sin(3.14159265*u_progress)*exp(-phase*phase)*cos(phase*3.0);'
                        'vec2 displacement=(delta/max(1.0,length(delta))*0.45+vec2(0.0,1.0))*lift;'
                        'vec2 p=gl_FragCoord.xy-displacement*u_deform-u_rect.xy;')
                    source = source.replace('float core=0.0;', 'float core=(1.0-u_deform)*(1.0-smoothstep(0.6,1.4,abs(d)));')
                    source = source.replace('float a=(core+near+bloom)',
                        'float a=(core+near*mix(1.0,0.18,u_haze)+bloom*mix(1.0,2.4,u_haze))')
                    source = source.replace('gl_FragColor=vec4(c*a,a);',
                        'float r=length((gl_FragCoord.xy-u_grid.xy)/u_grid.zw);'
                        'float gate=smoothstep(0.0,0.18,u_wave-r);'
                        'vec2 q=abs(gl_FragCoord.xy-u_grid.xy)-(u_grid_size*0.5+u_step*1.15-u_step*1.9);'
                        'float maskDistance=length(max(q,vec2(0.0)))+min(max(q.x,q.y),0.0)-u_step*1.9;'
                        'a*=mix(gate,1.0-gate,u_exit)*smoothstep(0.0,1.0,clamp(-maskDistance/(u_step*1.5),0.0,1.0));'
                        'gl_FragColor=vec4(c*a,a);')
                shader = GL.glCreateShader(kind)
                shaders.append(shader)
                GL.glShaderSource(shader, source)
                GL.glCompileShader(shader)
                if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
                    raise RuntimeError(GL.glGetShaderInfoLog(shader))
            self.program = GL.glCreateProgram()
            for shader in shaders:
                GL.glAttachShader(self.program, shader)
            GL.glLinkProgram(self.program)
            if not GL.glGetProgramiv(self.program, GL.GL_LINK_STATUS):
                raise RuntimeError(GL.glGetProgramInfoLog(self.program))
        finally:
            for shader in shaders:
                GL.glDeleteShader(shader)
        self.buffer = GL.glGenBuffers(1)
        vertices = array.array('f', [-1,-1, 1,-1, -1,1, 1,1]).tobytes()
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.buffer)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, len(vertices), vertices, GL.GL_STATIC_DRAW)
        self.position = GL.glGetAttribLocation(self.program, 'a_position')
        self.locations = {name: GL.glGetUniformLocation(self.program, name) for name in (
            'u_viewport', 'u_time', 'u_age', 'u_phase', 'u_rect', 'u_radius',
            'u_outline', 'u_extent', 'u_intensity', 'u_base', 'u_core', 'u_haze', 'u_grid', 'u_wave', 'u_exit', 'u_progress', 'u_step', 'u_grid_size', 'u_deform')}

    def render_gl(self, _area, _context):
        if self.failed or self.get_error():
            return False
        try:
            if not self.program:
                self.initialize()
            GL = self.gl
            scale = self.get_scale_factor()
            width, height = self.get_width()*scale, self.get_height()*scale
            GL.glViewport(0, 0, width, height)
            GL.glDisable(GL.GL_SCISSOR_TEST)
            GL.glClearColor(0, 0, 0, 0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            geometry, views = self.visual.geometry()
            if geometry is None:
                return True
            x, y, w, h = geometry
            step = self.visual.owner.panel.cell_size
            def uniform(name, *v):
                getattr(GL, 'glUniform' + str(len(v)) + 'f')(self.locations[name], *v)
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glUseProgram(self.program)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.buffer)
            GL.glEnableVertexAttribArray(self.position)
            GL.glVertexAttribPointer(self.position, 2, GL.GL_FLOAT, False, 0, ctypes.c_void_p(0))
            uniform('u_time', 2.3)
            uniform('u_age', 1)
            uniform('u_phase', 0)
            uniform('u_viewport', width, height)
            uniform('u_grid', (x+w/2)*scale, height-(y+h/2)*scale, math.hypot(w/2+step*.8, h/2+step*.8)*scale, math.hypot(w/2+step*.8, h/2+step*.8)*scale)
            uniform('u_wave', wave_front(self.visual.progress, self.visual.exiting))
            uniform('u_exit', float(self.visual.exiting))
            uniform('u_progress', self.visual.progress)
            uniform('u_step', step*scale)
            uniform('u_grid_size', w*scale, h*scale)
            sources = [(box, step*.40, 1.40, self.visual.accent, 0, 1) for box in views]
            panel = self.visual.owner.preview_panel if self.visual.owner.preview_panel.get_visible() else self.visual.owner.panel
            for icon in panel.icon_widgets.values():
                bounds = self.visual.owner._bounds(icon, self.visual)
                if not bounds:
                    continue
                ix, iy, iw, ih = bounds
                lift = wave_sample(ix+iw/2, iy+ih/2, geometry, step, self.visual.progress, self.visual.exiting)[2]*.5
                zoom = 1+lift/max(1, step)*.3
                iy -= lift
                bw = min(BADGE_SIZE, step*.68)*zoom
                frame = (ix+iw/2-bw/2, iy+ih/2-bw/2-4, bw, bw)
                sources.append((frame, 7, .85, self.visual.primary, 0, 0))
                fx, fy, fw, fh = frame
                sources.append(((fx+2, fy+9, fw-4, 1), 4, .65, self.visual.primary, 0, 0))
            for box, extent, gain, tint, haze, deform in sources:
                uniform('u_deform', deform)
                bx, by, bw, bh = box
                pad = extent+step*.15
                left, bottom = max(0, int((bx-pad)*scale)), max(0, int(height-(by+bh+pad)*scale))
                right, top = min(width, math.ceil((bx+bw+pad)*scale)), min(height, math.ceil(height-(by-pad)*scale))
                GL.glEnable(GL.GL_SCISSOR_TEST)
                GL.glScissor(left, bottom, max(0, right-left), max(0, top-bottom))
                uniform('u_rect', (bx+bw/2)*scale, height-(by+bh/2)*scale, bw*scale, bh*scale)
                uniform('u_radius', (1 if haze else 3)*scale)
                uniform('u_outline', (1 if haze else 2)*scale)
                uniform('u_extent', extent*scale)
                uniform('u_intensity', gain)
                uniform('u_base', *tint)
                uniform('u_core', *tint)
                uniform('u_haze', haze)
                GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
            GL.glDisable(GL.GL_SCISSOR_TEST)
            GL.glDisableVertexAttribArray(self.position)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
            GL.glUseProgram(0)
            return True
        except Exception:
            import logging
            logging.getLogger('luminophore-shell').exception('spatial editor shader failed')
            self.failed = True
            return False

    def release(self, _area):
        self.make_current()
        if not self.get_error() and hasattr(self, 'gl'):
            if self.program:
                self.gl.glDeleteProgram(self.program)
            if self.buffer:
                self.gl.glDeleteBuffers(1, [self.buffer])
        self.program = self.buffer = 0
        self.failed = False


class EditorVisual(Gtk.Overlay):
    def __init__(self, owner, content):
        super().__init__()
        self.owner = owner
        self.progress = 1.0
        self.exiting = False
        self.tick = 0
        self.finished = None
        self._running = False
        self._animation_generation = 0
        self._resume_progress = 0.0
        self.started = None
        self.primary, self.accent = color('#63D8FF'), color('#F6BD69')
        self.glow = EditorGlow(self)
        self.lines = Gtk.DrawingArea()
        self.lines.set_can_target(False)
        self.lines.set_hexpand(True)
        self.lines.set_vexpand(True)
        self.lines.set_halign(Gtk.Align.FILL)
        self.lines.set_valign(Gtk.Align.FILL)
        self.lines.set_draw_func(self.draw_lines)
        self.set_child(self.lines)
        self.add_overlay(self.glow)
        content.set_halign(Gtk.Align.CENTER)
        content.set_valign(Gtk.Align.CENTER)
        self.add_overlay(content)
        self.set_measure_overlay(content, True)
        self.content = content
        self.connect('unmap', lambda *_: self.suspend())
        self.connect('unrealize', lambda *_: self.suspend())
        self.connect('map', lambda *_: self.resume())
        css = Gtk.CssProvider()
        css.load_from_string('''
window.spatial-editor-light, .spatial-editor-light scrolledwindow,
.spatial-editor-light viewport, .spatial-editor-light .spatial-map-grid,
.spatial-editor-light .spatial-map-cell {
 background: transparent; background-image: none; border: none;
 box-shadow: none; padding: 0; margin: 0; border-radius: 0;
}
.spatial-editor-light .spatial-map-icon { opacity: 1; }
''')
        Gtk.StyleContext.add_provider_for_display(self.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION+1)
        self.css = css
        self.color_scope = f"spatial-editor-colors-{id(self):x}"
        self.owner.window.add_css_class(self.color_scope)
        self.color_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(self.get_display(), self.color_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION+2)
        self.refresh_palette()

    def refresh_palette(self):
        entry = read_palette_state_details().entries.get(self.owner.monitor.get_connector())
        palette = entry.palette if entry else Palette('#63D8FF', '#F6BD69')
        tokens = entry.scheme.modes['dark'].colors if entry and entry.scheme else {}
        self.primary = color(tokens.get('outline', '#909398'))
        self.accent = color(tokens.get('primary', palette.primary))
        foreground = tokens.get('on_surface', '#E4E4E8')
        self.color_css.load_from_string(
            f'.{self.color_scope} .spatial-map-icon, .{self.color_scope} .spatial-map-icon * {{ color: {foreground}; }}')
        self.refresh()

    def geometry(self):
        panel = self.owner.preview_panel if self.owner.preview_panel.get_visible() else self.owner.panel
        boxes = []
        selected = []
        # Preview widgets are rebuilt before GTK allocates them. Use the
        # retained input lattice for pixel coordinates, and only take the view
        # membership from preview. This also keeps backdrop and grid aligned.
        for point, holder in self.owner.panel.holders.items():
            box = self.owner._bounds(holder, self)
            if box and min(box[2:]) > 0:
                boxes.append(box)
                view_holder = panel.holders.get(point)
                if view_holder and view_holder.has_css_class('spatial-map-cell-view'):
                    selected.append(box)
        grid = union(boxes)
        view = union(selected)
        return grid, (view,) if view else ()

    def draw_lines(self, _area, cr, width, height):
        grid, views = self.geometry()
        if not grid:
            return
        x, y, w, h = grid
        step = self.owner.panel.cell_size
        reach = math.hypot(w/2+step*.8, h/2+step*.8)
        rx = ry = reach
        front = wave_front(self.progress, self.exiting)
        amplitude = step*.09*math.sin(math.pi*self.progress)
        points = {}
        def point(px, py):
            key = (px, py)
            if key not in points:
                dx, dy = px-x-w/2, py-y-h/2
                length = math.hypot(dx, dy)
                phase = (length/reach-front)/.16
                height = amplitude*math.exp(-phase*phase)*math.cos(phase*3)
                length = max(1, length)
                points[key] = (px+dx/length*height*.45, py+dy/length*height*.45-height)
            return points[key]
        def edge(ax, ay, bx, by, first=False):
            count = max(1, math.ceil(math.hypot(bx-ax, by-ay)/(step/3)))
            if first:
                cr.move_to(*point(ax, ay))
            for n in range(1, count+1):
                t = n/count
                cr.line_to(*point(ax+(bx-ax)*t, ay+(by-ay)*t))
        cr.push_group()
        # Disjoint cell faces: the view replaces the lattice tint, never stacks it.
        for row in range(-2, round(h/step)+2):
            for col in range(-2, round(w/step)+2):
                ax, ay = x+col*step, y+row*step
                cx, cy = ax+step/2, ay+step/2
                in_view = any(vx <= cx < vx+vw and vy <= cy < vy+vh for vx,vy,vw,vh in views)
                tint = self.accent if in_view else self.primary
                edge(ax, ay, ax+step, ay, True)
                edge(ax+step, ay, ax+step, ay+step)
                edge(ax+step, ay+step, ax, ay+step)
                edge(ax, ay+step, ax, ay)
                cr.close_path()
                phase = (math.hypot(cx-x-w/2, cy-y-h/2)/reach-wave_front(self.progress, self.exiting))/.16
                light = .20*math.sin(math.pi*self.progress)*math.exp(-phase*phase)*math.sin(phase*3)
                shaded = tuple(max(0, min(1, channel+light)) for channel in tint)
                cr.set_source_rgba(*shaded, FACE_ALPHA)
                cr.fill()
        cr.set_line_width(3.1)
        for vertical, count in ((True, round(w/step)+3), (False, round(h/step)+3)):
            for i in range(-2, count):
                ax, ay = (x+i*step, y-step*2) if vertical else (x-step*2, y+i*step)
                bx, by = (ax, y+h+step*2) if vertical else (x+w+step*2, ay)
                edge(ax, ay, bx, by, True)
        cr.set_source_rgba(*self.primary, .72)
        cr.stroke()
        content = cr.pop_group()
        key = (width, height, grid, step)
        cached = getattr(self, '_grid_mask_cache', None)
        if cached is None or cached[0] != key:
            cached = self._grid_mask_cache = (key, grid_mask(width, height, grid, step))
        mask = cairo.RadialGradient(0, 0, 0, 0, 0, 1.3)
        mask.set_matrix(cairo.Matrix(xx=1/rx, yy=1/ry, x0=-(x+w/2)/rx, y0=-(y+h/2)/ry))
        for i in range(33):
            radius = 1.3*i/32
            mask.add_color_stop_rgba(radius/1.3, 1, 1, 1, self.opacity_at(radius))
        combined = getattr(self, '_wave_mask_cache', None)
        if combined is None or combined.get_width() != width or combined.get_height() != height:
            combined = self._wave_mask_cache = cairo.ImageSurface(cairo.FORMAT_A8, width, height)
        mask_cr = cairo.Context(combined)
        mask_cr.set_operator(cairo.OPERATOR_CLEAR)
        mask_cr.paint()
        mask_cr.set_operator(cairo.OPERATOR_SOURCE)
        mask_cr.set_source_surface(cached[1], 0, 0)
        mask_cr.mask(mask)
        cr.set_source(content)
        cr.mask_surface(combined, 0, 0)
        state = self.owner.state
        if state.view and state.mode.value == "persistent":
            snapshot = state.view.state
            output = next((v for v in snapshot.output_views if v.output_id == (self.owner._board_output or snapshot.target_output_id)), None)
            if output:
                cells = {p: b for p, holder in self.owner.panel.holders.items() if (b := self.owner._bounds(holder, self)) is not None}
                for action, (hx,hy,hw,hh), edges in self.owner._view_handles(cells, output.rect):
                    radius = math.hypot(hx+hw/2-x-w/2, hy+hh/2-y-h/2)/reach
                    alpha = self.opacity_at(radius)*grid_opacity(hx+hw/2, hy+hh/2, grid, step)
                    cr.set_source_rgba(*self.accent, alpha*.95)
                    if action == "move-view":
                        cr.rectangle(hx,hy+2,hw,2)
                        cr.rectangle(hx,hy+6,hw,2)
                    else:
                        cr.rectangle(hx+1,hy+1,max(2,hw-2),max(2,hh-2))
                    cr.fill()

    def opacity_at(self, radius):
        def smooth(value):
            value = max(0, min(1, value))
            return value*value*(3-2*value)
        reveal = smooth((wave_front(self.progress, self.exiting)-radius)/.18)
        return 1-reveal if self.exiting else reveal

    def update_icons(self):
        grid, _ = self.geometry()
        if not grid:
            return
        x, y, w, h = grid
        step = self.owner.panel.cell_size
        for panel in (self.owner.panel, self.owner.preview_panel):
            for holder in panel.holders.values():
                box = self.owner._bounds(holder, self)
                if box:
                    bx, by, bw, bh = box
                    radius = math.hypot(bx+bw/2-x-w/2, by+bh/2-y-h/2)/math.hypot(w/2+step*.8, h/2+step*.8)
                    stack = holder.get_first_child()
                    if stack is not None and hasattr(stack, 'lift'):
                        stack.lift = wave_sample(bx+bw/2, by+bh/2, grid, step, self.progress, self.exiting)[2]*.5
                        stack.zoom = 1+stack.lift/max(1, step)*.3
                        stack.queue_draw()
                    holder.set_opacity(grid_opacity(bx+bw/2, by+bh/2, grid, step) * self.opacity_at(radius))

    def refresh(self):
        self.update_icons()
        self.lines.queue_draw()
        self.glow.queue_render()
        self.queue_draw()

    @property
    def animating(self):
        return self._running

    def suspend(self):
        if self.tick:
            self.remove_tick_callback(self.tick)
        self.tick = 0
        self._resume_progress = self.progress
        self.started = None

    def resume(self):
        if not self._running or self.tick or not self.get_mapped():
            return
        generation = self._animation_generation
        self.tick = self.add_tick_callback(lambda widget, clock: self.advance(widget, clock, generation))
        self.refresh()

    def cancel(self):
        self.suspend()
        self._animation_generation += 1
        self._running = False
        self.finished = None

    def animate(self, entering, finished=None):
        was_running = self._running
        previous_exiting, previous_progress = self.exiting, self.progress
        self.cancel()
        self._duration_ms = OVERVIEW_ENTER_MS if entering else OVERVIEW_EXIT_MS
        if was_running:
            # Reverse the current wave in place; do not flash a fresh mask.
            self.exiting = previous_exiting
            self.progress = self._resume_progress = previous_progress
            self._direction = -1 if previous_exiting == entering else 1
        else:
            self.exiting = not entering
            self.progress = self._resume_progress = 0.0
            self._direction = 1
        self.finished = finished
        self._running = True
        self.resume()
        self.refresh()

    def advance(self, _widget, clock, generation=None):
        if not self._running or (generation is not None and generation != self._animation_generation):
            return False
        now = clock.get_frame_time()/1_000_000
        if self.started is None:
            self.started = now
        direction = getattr(self, '_direction', 1)
        duration = getattr(self, '_duration_ms', OVERVIEW_EXIT_MS if self.exiting else OVERVIEW_ENTER_MS)/1000
        self.progress = max(0, min(1, self._resume_progress + direction*max(0, now-self.started)/duration))
        # Apply the same wave to Cairo, the emission shader, and app icons.
        self.refresh()
        if (direction > 0 and self.progress < 1) or (direction < 0 and self.progress > 0):
            return True
        self.tick = 0
        self._running = False
        done, self.finished = self.finished, None
        if done:
            done()
        return False
