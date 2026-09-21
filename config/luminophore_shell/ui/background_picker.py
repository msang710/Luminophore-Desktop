from __future__ import annotations
import time
from uuid import uuid4
import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk, Gdk, Gsk, Graphene, GLib, Gtk4LayerShell
from ..background_picker import ScenePickerModel
from ..background_thumbnails import BackgroundThumbnails
from ..transition import TimedTransition
from .projection import FrameProjectionHandshake, submit_surface_projection
from .scene_profile_workbench import SceneProfileWorkbench


class BackgroundPickerSurface:
    def __init__(
        self, application, controller, client, monitor, duration=180, palette_index=0
    ):
        self.monitor = monitor
        self.controller = controller
        self.client = client
        self.application = application
        self.model = ScenePickerModel()
        self.thumbnails = BackgroundThumbnails()
        self.window = Gtk.ApplicationWindow(application=application)
        self.window.set_decorated(False)
        self.window.add_css_class("luminophore-surface")
        self.window.add_css_class(f"palette-{palette_index}")
        Gtk4LayerShell.init_for_window(self.window)
        Gtk4LayerShell.set_namespace(self.window, "luminophore-shell-background-picker")
        Gtk4LayerShell.set_monitor(self.window, monitor)
        Gtk4LayerShell.set_layer(self.window, Gtk4LayerShell.Layer.BOTTOM)
        Gtk4LayerShell.set_exclusive_zone(self.window, 0)
        Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.NONE)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.add_css_class("luminophore-panel")
        root.set_size_request(1000, 360)
        self.window.set_child(root)
        self.rail = Gtk.Fixed()
        self.rail.set_size_request(1000, 260)
        root.append(self.rail)
        controls = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        for label, action in (
            ("←", lambda: self.navigate(-1)),
            ("적용", self.apply),
            ("→", lambda: self.navigate(1)),
            ("새 장면 제작", self.edit),
            ("선택 장면 편집", self.edit_selected),
            ("닫기", self.close),
        ):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, f=action: f())
            controls.append(b)
        root.append(controls)
        self.status = Gtk.Label(wrap=True)
        root.append(self.status)
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self.key)
        self.window.add_controller(key)
        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
            | Gtk.EventControllerScrollFlags.DISCRETE
        )
        scroll.connect(
            "scroll",
            lambda _c, _dx, dy: (
                (self.navigate(1 if dy > 0 else -1) or True) if dy else False
            ),
        )
        self.window.add_controller(scroll)
        self.cards = []
        self.revision = 1
        self.token = 0
        self.timer = 0
        self.tick = 0
        self.target_visible = False
        self.fade = TimedTransition(0, duration)
        self.offset = TimedTransition(0, duration)
        self._handshake = FrameProjectionHandshake(
            self.window,
            "luminophore-shell-background-picker",
            self.project,
            lambda: self.revision,
        )
        self.window.connect("notify::is-active", self.activation_changed)
        self.ever_active = False
        self.window.connect(
            "realize",
            lambda _w: self.window.get_surface().set_input_region(cairo.Region()),
        )
        self.window.connect("close-request", lambda _w: self.close() or True)

    def activation_changed(self, window, _property):
        if window.is_active():
            self.ever_active = True
        elif self.ever_active and self.target_visible:
            self.close()

    def key(self, _controller, keyval, *_args):
        if keyval == Gdk.KEY_Escape:
            self.close()
        elif keyval == Gdk.KEY_Left:
            self.navigate(-1)
        elif keyval == Gdk.KEY_Right:
            self.navigate(1)
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.apply()
        else:
            return False
        return True

    def show(self):
        self.model = ScenePickerModel(
            self.controller.store.scenes(), self.controller.active
        )
        self.target_visible = True
        self.ever_active = False
        self.refresh()
        self.window.present()
        self.window.set_opacity(0)
        self._handshake.start()
        self.fade.retarget(1, time.monotonic() * 1000)
        self.animate()
        if not self.timer:
            self.timer = GLib.timeout_add(200, self.poll)

    def close(self):
        Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.NONE)
        self.target_visible = False
        self.fade.retarget(0, time.monotonic() * 1000)
        self.animate()

    def animate(self):
        if not self.tick:
            self.tick = self.window.add_tick_callback(self.frame)

    def frame(self, _window, clock):
        ms = clock.get_frame_time() / 1000
        fade = self.fade.sample(ms)
        offset = self.offset.sample(ms)
        self.window.set_opacity(fade.value)
        for relative, card in self.cards:
            x = 350 + relative * 330 + offset.value
            distance = min(1, abs(relative + offset.value / 330))
            scale = 1 - 0.10 * distance
            self.rail.set_child_transform(
                card,
                Gsk.Transform()
                .translate(
                    Graphene.Point().init(x + 150 * (1 - scale), 15 + 120 * (1 - scale))
                )
                .scale(scale, scale),
            )
            card.set_opacity(1 - 0.45 * distance)
        if fade.active or offset.active:
            return True
        self.tick = 0
        if not self.target_visible:
            self._handshake.cancel()
            self.client.forget_projection("background-picker")
            self.window.set_visible(False)
        return False

    def project(self):
        if not self.target_visible:
            return True
        w, h = self.window.get_width(), self.window.get_height()
        if w <= 0 or h <= 0:
            return False
        return submit_surface_projection(
            self,
            self.client.shell_projection,
            (
                "background-picker",
                True,
                uuid4().hex,
                self.revision,
                {
                    "panel_x": 350.0,
                    "panel_y": 15.0,
                    "panel_width": 300.0,
                    "panel_height": 240.0,
                    "radius": 14.0,
                    "outline": 1.0,
                    "extent": 48.0,
                    "intensity": 1.0,
                },
            ),
            self.presented,
        )

    def presented(self, ok):
        if ok and self.target_visible:
            Gtk4LayerShell.set_keyboard_mode(
                self.window, Gtk4LayerShell.KeyboardMode.EXCLUSIVE
            )
        surface = self.window.get_surface()
        if surface:
            surface.set_input_region(
                cairo.Region(
                    cairo.RectangleInt(
                        0, 0, self.window.get_width(), self.window.get_height()
                    )
                )
                if ok and self.target_visible
                else cairo.Region()
            )
            surface.queue_render()
        return ok

    def contains_global_point(self, x, y):
        geometry = self.monitor.get_geometry()
        width, height = self.window.get_width(), self.window.get_height()
        left = geometry.x + (geometry.width - width) / 2
        top = geometry.y + (geometry.height - height) / 2
        return left <= x < left + width and top <= y < top + height

    def refresh(self):
        self.token += 1
        token = self.token
        for _, card in self.cards:
            self.rail.remove(card)
        self.cards = []
        rows = self.model.scenes
        if not rows:
            self.status.set_label(
                "장면이 없습니다. 새 장면 제작에서 두 화면을 준비하세요."
            )
            return
        for relative in (-1, 0, 1):
            scene = rows[(self.model.index + relative) % len(rows)]
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            card.set_size_request(300, 240)
            click = Gtk.GestureClick.new()
            click.set_button(1)
            click.connect(
                "released",
                lambda *_args, r=relative: self.navigate(r) if r else self.apply(),
            )
            card.add_controller(click)
            pair = Gtk.Box(spacing=2)
            for asset in (scene.left, scene.right):
                picture = Gtk.Picture()
                picture.set_size_request(148, 190)
                picture.set_can_shrink(True)
                pair.append(picture)

                def loaded(data, pic=picture, t=token):
                    def finish():
                        if t == self.token and data:
                            pic.set_paintable(
                                Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
                            )
                        return False

                    GLib.idle_add(finish)

                self.thumbnails.request(asset, loaded)
            card.append(pair)
            card.append(Gtk.Label(label=scene.name))
            self.cards.append((relative, card))
            self.rail.put(card, 350 + relative * 330, 15)
        self.revision += 1
        self._handshake.start()

    def navigate(self, delta):
        self.model.navigate(delta)
        self.refresh()
        self.offset.snap(max(-330, min(330, self.offset.value + delta * 330)))
        self.offset.retarget(0, time.monotonic() * 1000)
        self.animate()

    def apply(self):
        if self.model.selected:
            self.controller.request(self.model.selected.id)

    def edit(self):
        self.workbench = SceneProfileWorkbench(
            self.application, self.window, lambda _scene: self.reload()
        )

    def edit_selected(self):
        if self.model.selected:
            self.workbench = SceneProfileWorkbench(
                self.application,
                self.window,
                lambda _scene: self.reload(),
                scene=self.model.selected,
            )

    def reload(self):
        self.model = ScenePickerModel(
            self.controller.store.scenes(), self.controller.active
        )
        self.refresh()

    def poll(self):
        if not self.target_visible:
            self.timer = 0
            return False
        state = self.controller.status()
        labels = {
            "idle": "장면을 선택하세요",
            "preparing": "장면 준비 중",
            "transitioning": "두 화면 전환 중",
            "committed": "적용 완료",
            "error": "장면을 적용하지 못했습니다",
            "degraded": "배경 복구가 필요합니다",
        }
        if self.model.scenes:
            self.status.set_label(
                labels.get(state["phase"], state["phase"])
                + (f" · {state['error']}" if state["error"] else "")
            )
        return True

    def destroy(self):
        self.token += 1
        self.target_visible = False
        self._projection_dead = True
        self._handshake.cancel()
        self.client.forget_projection("background-picker")
        if self.tick:
            self.window.remove_tick_callback(self.tick)
        if self.timer:
            GLib.source_remove(self.timer)
        self.thumbnails.close()
        self.window.destroy()
