from __future__ import annotations
import io
import math
from concurrent.futures import ThreadPoolExecutor
import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib
from PIL import Image, ImageOps


class SceneProfileCanvas(Gtk.DrawingArea):
    def __init__(self, changed):
        super().__init__()
        self.set_content_width(640)
        self.set_content_height(360)
        self.changed = changed
        self.loader = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="profile-source"
        )
        self.load_generation = 0
        self.load_future = None
        self.error = ""
        self.layer = None
        self.image = None
        self.include = True
        self.erase = False
        self.radius = 0.025
        self.fill = False
        self.set_draw_func(self._draw)
        gesture = Gtk.GestureDrag.new()
        gesture.set_button(1)
        gesture.connect("drag-begin", self._begin)
        gesture.connect("drag-update", self._move)
        self.add_controller(gesture)
        self.origin = (0, 0)
        self.last = None

    def source(self, path):
        self.load_generation += 1
        generation = self.load_generation
        self.image = None
        self.error = ""
        self.queue_draw()
        if self.load_future:
            self.load_future.cancel()

        def load():
            try:
                with Image.open(path) as image:
                    if image.width * image.height > 8_000_000:
                        raise ValueError("profile_pixel_budget")
                    image = ImageOps.exif_transpose(image).convert("RGBA")
                    image.thumbnail((1280, 720))
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                    pixels = buffer.getvalue()
                error = ""
            except (ValueError, OSError) as exc:
                pixels = None
                error = str(exc)

            def ready():
                if generation != self.load_generation:
                    return False
                self.error = error
                if pixels:
                    self.image = cairo.ImageSurface.create_from_png(io.BytesIO(pixels))
                self.queue_draw()
                return False

            GLib.idle_add(ready)

        self.load_future = self.loader.submit(load)

    def clear(self):
        self.load_generation += 1
        if self.load_future:
            self.load_future.cancel()
        self.image = None
        self.layer = None
        self.queue_draw()

    def close(self):
        self.load_generation += 1
        self.loader.shutdown(wait=False, cancel_futures=True)

    def box(self):
        if not self.image:
            return (0, 0, 1, 1)
        scale = min(
            self.get_width() / self.image.get_width(),
            self.get_height() / self.image.get_height(),
        )
        w, h = self.image.get_width() * scale, self.image.get_height() * scale
        return ((self.get_width() - w) / 2, (self.get_height() - h) / 2, w, h)

    def _draw(self, _area, cr, width, height):
        cr.set_source_rgb(0.06, 0.07, 0.09)
        cr.paint()
        if not self.image:
            return
        x, y, w, h = self.box()
        cr.save()
        cr.translate(x, y)
        cr.scale(w / self.image.get_width(), h / self.image.get_height())
        cr.set_source_surface(self.image)
        cr.paint()
        cr.restore()
        if self.layer:
            for stroke in self.layer.strokes:
                cr.set_source_rgba(
                    *(
                        (0.2, 0.9, 0.6, 0.45)
                        if stroke["include"]
                        else (1, 0.3, 0.3, 0.45)
                    )
                )
                cr.arc(
                    x + stroke["x"] * w,
                    y + stroke["y"] * h,
                    stroke["radius"] * min(w, h),
                    0,
                    math.tau,
                )
                cr.fill()

    def _begin(self, _gesture, x, y):
        self.origin = (x, y)
        self.last = None
        self.paint(x, y)

    def _move(self, _gesture, dx, dy):
        if not self.fill:
            self.paint(self.origin[0] + dx, self.origin[1] + dy)

    def paint(self, px, py):
        if self.layer is None or self.image is None:
            return
        x, y, w, h = self.box()
        if w <= 0 or h <= 0 or not (x <= px <= x + w and y <= py <= y + h):
            return
        u, v = (px - x) / w, (py - y) / h
        if (
            self.last
            and math.hypot(u - self.last[0], v - self.last[1]) < self.radius / 3
        ):
            return
        self.last = (u, v)
        if self.erase:
            self.layer.strokes[:] = [
                s
                for s in self.layer.strokes
                if math.hypot(s["x"] - u, s["y"] - v) > self.radius + s["radius"]
            ]
        elif len(self.layer.strokes) < 10000:
            self.layer.strokes.append(
                {
                    "x": u,
                    "y": v,
                    "radius": self.radius,
                    "include": self.include,
                    "kind": "fill" if self.fill else "brush",
                }
            )
        self.changed()
        self.queue_draw()
