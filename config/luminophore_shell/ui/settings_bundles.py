from __future__ import annotations

from threading import Thread
from uuid import uuid4

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib

from ..app_bundles import BundleStore, launch_bundle
from ..applications import ApplicationCatalog
from ..hyprland import HyprlandClient


class BundleSettings(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.store = BundleStore()
        self.values, self.digest, self.apps, self.rows = [], '', [], []
        self.identifier = str(uuid4())
        self.append(Gtk.Label(label='앱 묶음 실행', xalign=0))
        self.append(Gtk.Label(label='앱별 초기 배치 사용 · 실행 중인 앱은 기본 건너뛰기', xalign=0))
        self.saved = Gtk.DropDown.new_from_strings(['새 묶음'])
        self.saved.connect('notify::selected', self.select)
        self.append(self.saved)
        self.name = Gtk.Entry(placeholder_text='묶음 이름')
        self.chord = Gtk.Entry(placeholder_text='단축키 (선택, 예: SUPER + CTRL + 1)')
        self.append(self.name)
        self.append(self.chord)
        self.items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.append(self.items)
        self.buttons = Gtk.Box(spacing=6)
        for label, callback in [('앱 추가', lambda *_: self.add()), ('저장 및 적용', self.save), ('지금 실행', self.run), ('삭제', self.delete), ('새로고침', self.refresh)]:
            button = Gtk.Button(label=label)
            button.connect('clicked', callback)
            self.buttons.append(button)
        self.append(self.buttons)
        self.status = Gtk.Label(xalign=0, wrap=True)
        self.append(self.status)
        self.refresh()

    def worker(self, operation, complete):
        self.buttons.set_sensitive(False)
        def run():
            try:
                result, error = operation(), None
            except Exception as exc:
                result, error = None, str(exc)
            def finish():
                self.buttons.set_sensitive(True)
                if error:
                    self.status.set_text(error)
                else:
                    complete(result)
                return False
            GLib.idle_add(finish)
        Thread(target=run, daemon=True).start()

    def refresh(self, *_):
        def complete(result):
            (self.values, self.digest), self.apps = result
            self.saved.set_model(Gtk.StringList.new(['새 묶음'] + [b['name'] for b in self.values]))
            self.saved.set_selected(0)
            self.select()
            self.status.set_text('새 인스턴스는 재실행 요청입니다. 실제 새 창 생성 여부는 앱이 결정합니다.')
        self.worker(lambda: (self.store.snapshot(), ApplicationCatalog().apps), complete)

    def select(self, *_):
        index = self.saved.get_selected()
        bundle = self.values[index - 1] if 0 < index <= len(self.values) else None
        self.identifier = bundle['id'] if bundle else str(uuid4())
        self.name.set_text(bundle['name'] if bundle else '')
        self.chord.set_text(bundle['chord'] if bundle else '')
        for row, *_ in self.rows:
            self.items.remove(row)
        self.rows.clear()
        for item in bundle['items'] if bundle else []:
            self.add(item)

    def add(self, item=None):
        item = item or {}
        row = Gtk.Box(spacing=6)
        # Preserve unavailable saved apps so editing cannot silently replace them.
        ids = [a.desktop_id for a in self.apps]
        labels = [a.name + ' · ' + a.desktop_id for a in self.apps]
        old = item.get('desktop_id')
        if old and old not in ids:
            ids.append(old)
            labels.append(old + ' (현재 설치 목록에 없음)')
        picker = Gtk.DropDown.new_from_strings(labels or ['설치된 앱 없음'])
        picker.set_hexpand(True)
        picker.set_selected(ids.index(old) if old in ids else 0)
        new = Gtk.CheckButton(label='새 인스턴스 요청')
        new.set_active(item.get('new_instance', False))
        remove = Gtk.Button(label='제거')
        entry = (row, picker, new, ids)
        def discard(*_):
            self.items.remove(row)
            self.rows.remove(entry)
        remove.connect('clicked', discard)
        for child in (picker, new, remove):
            row.append(child)
        self.rows.append(entry)
        self.items.append(row)

    def save(self, *_):
        bundle = dict(id=self.identifier, name=self.name.get_text(), chord=self.chord.get_text(), items=[
            dict(desktop_id=ids[picker.get_selected()], new_instance=new.get_active())
            for _, picker, new, ids in self.rows if picker.get_selected() < len(ids)])
        values = [b for b in self.values if b['id'] != self.identifier] + [bundle]
        self.persist(values)

    def delete(self, *_):
        self.persist([b for b in self.values if b['id'] != self.identifier])

    def persist(self, values):
        digest = self.digest
        def operation():
            result = self.store.save(values, digest)
            return result, '저장·적용 완료'
        identifier = self.identifier
        def complete(result):
            (self.values, self.digest), message = result
            self.saved.set_model(Gtk.StringList.new(['새 묶음'] + [b['name'] for b in self.values]))
            selected = next((i + 1 for i, b in enumerate(self.values) if b['id'] == identifier), 0)
            self.saved.set_selected(selected)
            self.select()
            self.status.set_text(message)
        self.worker(operation, complete)

    def run(self, *_):
        identifier = self.identifier
        def complete(result):
            self.status.set_text(f"실행 요청 {len(result['launched'])} · 건너뜀 {len(result['skipped'])} · 실패 {len(result['failed'])}" + ('\n' + ', '.join(result['failed']) if result['failed'] else ''))
        self.worker(lambda: launch_bundle(identifier, self.store), complete)
