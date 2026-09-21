from __future__ import annotations
from pathlib import Path
from dataclasses import replace
from uuid import uuid4
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib
from ..background_scene import SceneAsset, WallpaperScene
from ..background_store import BackgroundStore
from ..scene_profile import SceneProfileDraft, ProfileLayer
from ..scene_profile_service import SceneProfileService
from .scene_profile_canvas import SceneProfileCanvas


class SceneProfileWorkbench:
    def __init__(self, application, parent=None, saved=lambda scene: None, scene=None):
        self.scene_id = scene.id if scene else uuid4().hex
        self.store = BackgroundStore()
        self.service = SceneProfileService()
        self.saved = saved
        self.assets = {}
        self.drafts = {}
        self.role = "left"
        self.draft = None
        self.syncing = False
        self.autosave = 0
        self.preview_timer = 0
        self.preview_index = 0
        self.window = Gtk.Window(application=application, title="배경화면 제작")
        if parent:
            self.window.set_transient_for(parent)
        self.window.set_default_size(900, 720)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for edge in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{edge}")(16)
        self.window.set_child(root)
        self.name = Gtk.Entry(placeholder_text="장면 이름")
        root.append(self.name)
        self.order = Gtk.SpinButton.new_with_range(0, 999, 1)
        self.order.set_value(len(self.store.scenes()))
        root.append(Gtk.Label(label="장면 순서", xalign=0))
        root.append(self.order)
        roles = Gtk.Box(spacing=8)
        for role, label in (("left", "왼쪽 화면"), ("right", "오른쪽 화면")):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _b, r=role: self.select_role(r))
            roles.append(button)
        source = Gtk.Button(label="원본 이미지 선택")
        source.connect("clicked", self.choose)
        roles.append(source)
        resume = Gtk.Button(label="이전 초안 열기")
        resume.connect("clicked", self.resume)
        roles.append(resume)
        root.append(roles)
        self.stage = Gtk.Stack()
        self.stage.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.stage)
        root.append(switcher)
        source_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.source_label = Gtk.Label(
            label="양쪽 화면의 원본을 선택하고 프로필을 제작하세요", wrap=True
        )
        source_page.append(self.source_label)
        self.fit = Gtk.DropDown.new_from_strings(["화면 채우기", "이미지 전체 보기"])
        self.fit.connect("notify::selected", self.viewport)
        source_page.append(self.fit)
        self.focal_x = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.01)
        self.focal_y = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.01)
        for label, control in (
            ("가로 중심", self.focal_x),
            ("세로 중심", self.focal_y),
        ):
            control.set_value(0.5)
            control.connect("value-changed", self.viewport)
            source_page.append(Gtk.Label(label=label))
            source_page.append(control)
        self.stage.add_titled(source_page, "source", "Source")
        layers = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        tools = Gtk.Box(spacing=6)
        self.selector = Gtk.DropDown.new_from_strings(
            ["background", "midground", "foreground"]
        )
        self.selector.connect("notify::selected", self.select_layer)
        tools.append(self.selector)
        for label, inc, erase in (
            ("전경 포함", True, False),
            ("배경 제외", False, False),
            ("지우기", True, True),
        ):
            button = Gtk.Button(label=label)
            button.connect("clicked", lambda _b, i=inc, e=erase: self.tool(i, e))
            tools.append(button)
        fill = Gtk.Button(label="영역 채우기")
        fill.connect("clicked", lambda _b: self.fill_tool())
        tools.append(fill)
        add = Gtk.Button(label="레이어 추가")
        add.connect("clicked", self.add_layer)
        tools.append(add)
        layers.append(tools)
        self.canvas = SceneProfileCanvas(self.changed)
        self.canvas.set_vexpand(True)
        layers.append(self.canvas)
        self.feather = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 32, 1)
        self.feather.set_value(3)
        self.feather.connect("value-changed", self.parameters)
        layers.append(Gtk.Label(label="경계 부드러움"))
        layers.append(self.feather)
        self.stage.add_titled(layers, "layers", "Layers")
        motion = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        motion.append(Gtk.Label(label="선택 레이어 깊이 · 배경 레이어는 0으로 고정"))
        self.depth = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.05)
        self.depth.connect("value-changed", self.parameters)
        motion.append(self.depth)
        motion.append(Gtk.Label(label="장면 움직임 크기"))
        self.amplitude = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 0.05, 0.001
        )
        self.amplitude.set_value(0.015)
        self.amplitude.connect("value-changed", self.parameters)
        motion.append(self.amplitude)
        self.stage.add_titled(motion, "motion", "Motion")
        preview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.picture = Gtk.Picture()
        self.picture.set_can_shrink(True)
        self.picture.set_vexpand(True)
        preview.append(self.picture)
        self.stage.add_titled(preview, "preview", "Preview")
        self.stage.set_vexpand(True)
        root.append(self.stage)
        controls = Gtk.Box(spacing=8)
        compile_button = Gtk.Button(label="미리보기 생성")
        compile_button.connect("clicked", self.compile)
        controls.append(compile_button)
        cancel = Gtk.Button(label="생성 취소")
        cancel.connect("clicked", lambda _b: self.cancel())
        controls.append(cancel)
        self.save_button = Gtk.Button(label="이 화면 프로필 저장")
        self.save_button.set_sensitive(False)
        self.save_button.connect("clicked", self.save_profile)
        controls.append(self.save_button)
        self.scene_button = Gtk.Button(label="양쪽 장면 등록")
        self.scene_button.set_sensitive(False)
        self.scene_button.connect("clicked", self.save_scene)
        controls.append(self.scene_button)
        root.append(controls)
        self.status = Gtk.Label(
            label="편집 내용은 자동 저장됩니다. 장면 등록은 현재 배경을 바꾸지 않습니다.",
            wrap=True,
        )
        root.append(self.status)
        self.window.connect("close-request", self.close)
        if scene:
            try:
                from ..scene_profile_compiler import validate_package

                self.name.set_text(scene.name)
                self.order.set_value(scene.order)
                for role in ("left", "right"):
                    asset = getattr(scene, role)
                    data = validate_package(Path(asset.path))
                    draft = SceneProfileDraft.parse(data["draft"])
                    draft.source = replace(
                        draft.source,
                        fit=asset.fit,
                        focal_x=asset.focal_x,
                        focal_y=asset.focal_y,
                    )
                    self.drafts[role] = draft
                    self.assets[role] = asset
                self.select_role("left")
                self.scene_button.set_sensitive(True)
            except (ValueError, OSError, KeyError) as exc:
                self.status.set_label(f"편집할 프로필을 열 수 없습니다: {exc}")
        self.window.present()

    def tool(self, include, erase):
        self.canvas.include = include
        self.canvas.erase = erase
        self.canvas.fill = False

    def fill_tool(self):
        self.canvas.fill = True
        self.canvas.erase = False
        self.canvas.include = True

    def cancel(self):
        self.service.cancel()
        self.save_button.set_sensitive(False)
        self.status.set_label("생성을 취소했습니다. 초안은 유지됩니다.")

    def flush(self):
        if self.autosave:
            GLib.source_remove(self.autosave)
            self.autosave = 0
        for draft in self.drafts.values():
            self.service.store.save(draft)
        return False

    def close(self, *_args):
        if self.preview_timer:
            GLib.source_remove(self.preview_timer)
            self.preview_timer = 0
        self.flush()
        self.service.cancel()
        self.canvas.close()
        return False

    def choose(self, _button):
        dialog = Gtk.FileChooserNative(
            title="원본 이미지",
            transient_for=self.window,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="편집",
            cancel_label="취소",
        )

        def selected(chooser, response):
            file = chooser.get_file() if response == Gtk.ResponseType.ACCEPT else None
            chooser.destroy()
            if not file or not file.get_path():
                return
            try:
                draft = SceneProfileDraft(
                    uuid4().hex,
                    SceneAsset.capture(Path(file.get_path())),
                    [
                        ProfileLayer("background", 0),
                        ProfileLayer("midground", 0.5),
                        ProfileLayer("foreground", 1),
                    ],
                )
                self.load(draft)
            except (ValueError, OSError) as exc:
                self.status.set_label(str(exc))

        dialog.connect("response", selected)
        dialog.show()

    def load(self, draft):
        self.service.cancel()
        self.draft = draft
        self.drafts[self.role] = draft
        self.assets.pop(self.role, None)
        self.service.store.save(draft)
        self.canvas.source(draft.source.path)
        self.source_label.set_label(Path(draft.source.path).name)
        self.selector.set_model(Gtk.StringList.new([l.name for l in draft.layers]))
        self.selector.set_selected(min(1, len(draft.layers) - 1))
        self.select_layer()
        self.stage.set_visible_child_name("layers")
        self.save_button.set_sensitive(False)
        self.scene_button.set_sensitive(False)

    def select_role(self, role):
        self.flush()
        self.service.cancel()
        self.role = role
        self.draft = self.drafts.get(role)
        self.save_button.set_sensitive(False)
        if self.draft:
            self.canvas.source(self.draft.source.path)
            self.source_label.set_label(Path(self.draft.source.path).name)
            self.selector.set_model(
                Gtk.StringList.new([l.name for l in self.draft.layers])
            )
            self.select_layer()
        else:
            self.canvas.clear()
            self.source_label.set_label("이 화면의 원본 이미지를 선택하세요")
        self.status.set_label("왼쪽 화면" if role == "left" else "오른쪽 화면")

    def select_layer(self, *_args):
        if not self.draft:
            return
        index = self.selector.get_selected()
        if index >= len(self.draft.layers):
            return
        self.syncing = True
        self.fit.set_selected(0 if self.draft.source.fit == "cover" else 1)
        self.focal_x.set_value(self.draft.source.focal_x)
        self.focal_y.set_value(self.draft.source.focal_y)
        layer = self.draft.layers[index]
        self.canvas.layer = layer if index else None
        self.depth.set_value(layer.depth)
        self.depth.set_sensitive(index > 0)
        self.feather.set_value(layer.feather)
        self.amplitude.set_value(self.draft.motion)
        self.syncing = False
        self.canvas.queue_draw()

    def viewport(self, *_args):
        if self.syncing or not self.draft:
            return
        self.draft.source = replace(
            self.draft.source,
            fit="cover" if self.fit.get_selected() == 0 else "contain",
            focal_x=self.focal_x.get_value(),
            focal_y=self.focal_y.get_value(),
        )
        self.changed()

    def parameters(self, *_args):
        if self.syncing or not self.draft:
            return
        index = self.selector.get_selected()
        if index >= len(self.draft.layers):
            return
        layer = self.draft.layers[index]
        layer.depth = self.depth.get_value() if index else 0
        layer.feather = round(self.feather.get_value())
        self.draft.motion = self.amplitude.get_value()
        self.changed()

    def changed(self):
        if not self.draft:
            return
        self.service.cancel()
        self.draft.revision += 1
        if self.autosave:
            GLib.source_remove(self.autosave)
        self.autosave = GLib.timeout_add(500, self.flush)
        self.assets.pop(self.role, None)
        self.save_button.set_sensitive(False)
        self.scene_button.set_sensitive(False)

    def add_layer(self, _button):
        if not self.draft or len(self.draft.layers) >= 8:
            return
        self.draft.layers.append(ProfileLayer(f"layer {len(self.draft.layers)}", 0.5))
        self.changed()
        self.selector.set_model(Gtk.StringList.new([l.name for l in self.draft.layers]))
        self.selector.set_selected(len(self.draft.layers) - 1)

    def compile(self, _button):
        if not self.draft:
            return
        self.status.set_label("레이어를 생성하고 있습니다…")
        self.save_button.set_sensitive(False)
        self.service.compile(self.draft, lambda: GLib.idle_add(self.compiled))

    def compiled(self):
        if self.service.phase == "preview_ready":
            self.picture.set_filename(self.service.result["preview"])
            if self.preview_timer:
                GLib.source_remove(self.preview_timer)
            self.preview_index = 0
            self.preview_timer = GLib.timeout_add(83, self.preview_frame)
            self.stage.set_visible_child_name("preview")
            self.save_button.set_sensitive(True)
            self.status.set_label(
                "native 렌더러 미리보기입니다. 확인 후 프로필을 저장하세요."
            )
        elif self.service.phase == "error":
            labels = {
                "compiler_unavailable": "이미지 제작 구성 요소가 설치되지 않았습니다",
                "FileNotFoundError": "원본 또는 미리보기 실행 파일을 찾을 수 없습니다",
                "source_changed": "원본 이미지가 변경되었습니다. 다시 선택하세요",
                "native_preview_failed": "장면 미리보기를 생성하지 못했습니다",
                "profile_pixel_budget": "이미지는 800만 픽셀 이하로 준비하세요",
                "background_seed_required": "남겨둘 배경을 제외 도구로 표시하세요",
                "profile_disk_budget": "이 프로필의 저장 공간 한도를 초과했습니다",
            }
            self.status.set_label(
                "생성 실패: " + labels.get(self.service.error, self.service.error)
            )
        return False

    def preview_frame(self):
        result = self.service.result
        if not result or self.service.phase not in {"preview_ready", "saved"}:
            self.preview_timer = 0
            return False
        frames = result.get("frames", [result["preview"]])
        self.preview_index = (self.preview_index + 1) % len(frames)
        self.picture.set_filename(frames[self.preview_index])
        return True

    def save_profile(self, _button):
        try:
            self.assets[self.role] = self.service.save(self.draft)
            self.save_button.set_sensitive(False)
            self.scene_button.set_sensitive(set(self.assets) == {"left", "right"})
            self.status.set_label("프로필 저장 완료. 반대쪽 화면도 준비하세요.")
        except (ValueError, OSError) as exc:
            self.status.set_label(str(exc))

    def save_scene(self, _button):
        try:
            scene = WallpaperScene(
                self.scene_id,
                self.name.get_text().strip() or "새 장면",
                self.assets["left"],
                self.assets["right"],
                self.order.get_value_as_int(),
            )
            self.store.save(scene)
            self.saved(scene)
            self.status.set_label("장면을 등록했습니다. 선택기에서 적용할 수 있습니다.")
        except (ValueError, OSError, KeyError) as exc:
            self.status.set_label(str(exc))

    def resume(self, _button):
        dialog = Gtk.FileChooserNative(
            title="저장된 draft.json 선택",
            transient_for=self.window,
            action=Gtk.FileChooserAction.OPEN,
            accept_label="열기",
            cancel_label="취소",
        )

        def selected(chooser, response):
            file = chooser.get_file() if response == Gtk.ResponseType.ACCEPT else None
            chooser.destroy()
            if file and file.get_path():
                try:
                    import json

                    self.load(
                        SceneProfileDraft.parse(
                            json.loads(Path(file.get_path()).read_text())
                        )
                    )
                except (ValueError, OSError) as exc:
                    self.status.set_label(str(exc))

        dialog.connect("response", selected)
        dialog.show()
