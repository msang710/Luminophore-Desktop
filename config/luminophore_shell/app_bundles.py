"""Explicit app launch recipes. They never own or restore running windows."""
from __future__ import annotations

import fcntl
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from .binding_registry import normalize_chord

PREFIX = '-- luminophore-bundles-v1 '


def validate(bundles):
    if not isinstance(bundles, list) or len(bundles) > 64:
        raise ValueError('묶음은 최대 64개입니다')
    ids, chords = set(), set()
    result = []
    for bundle in bundles:
        if not isinstance(bundle, dict) or set(bundle) != {'id', 'name', 'chord', 'items'}:
            raise ValueError('올바르지 않은 묶음 형식')
        identifier, name = bundle['id'], bundle['name']
        if not isinstance(identifier, str) or not re.fullmatch('[a-z0-9-]{1,64}', identifier) or identifier in ids:
            raise ValueError('묶음 ID가 잘못되었거나 중복됩니다')
        if not isinstance(name, str) or not name.strip() or len(name) > 100 or any(ord(c) < 32 for c in name):
            raise ValueError('묶음 이름을 입력하세요')
        chord = bundle['chord']
        if not isinstance(chord, str):
            raise ValueError('잘못된 단축키')
        if chord:
            chord = normalize_chord(chord)
            if not re.fullmatch(r'(?:(?:SUPER|CTRL|ALT|SHIFT) \+ )+[A-Z0-9_]+', chord) or chord in chords:
                raise ValueError('보조키를 포함한 서로 다른 단축키를 지정하세요')
            chords.add(chord)
        items = bundle['items']
        if not isinstance(items, list) or not 1 <= len(items) <= 32:
            raise ValueError('묶음에는 앱 1~32개를 지정하세요')
        apps = set()
        for item in items:
            if not isinstance(item, dict) or set(item) != {'desktop_id', 'new_instance'}:
                raise ValueError('잘못된 앱 항목')
            app = item['desktop_id']
            if not isinstance(app, str) or not app.endswith('.desktop') or len(app) > 256 or any(ord(c) < 32 or c in '/\\:' for c in app) or app.startswith('-') or app in apps:
                raise ValueError('서로 다른 설치 앱을 선택하세요')
            if type(item['new_instance']) is not bool:
                raise ValueError('잘못된 새 인스턴스 설정')
            apps.add(app)
        ids.add(identifier)
        result.append(dict(bundle, name=name.strip(), chord=chord, items=[dict(i) for i in items]))
    return result


def encode(bundles):
    bundles = validate(bundles)
    quote = lambda text: json.dumps(text, ensure_ascii=False)
    rows = [PREFIX + json.dumps(bundles, ensure_ascii=False, sort_keys=True), 'return {']
    rows += ['    { id = ' + quote(b['id']) + ', chord = ' + quote(b['chord']) + ' },' for b in bundles if b['chord']]
    return ('\n'.join(rows) + '\n}\n').encode()


class BundleStore:
    def __init__(self, path=None, *, service=None):
        from .domain_client import DomainClient
        self.service = service or DomainClient()

    def snapshot(self):
        docs, digest = self.service.snapshot()
        return validate(docs['bundles.toml'].get('bundles', [])), digest

    def save(self, bundles, expected_digest):
        bundles = validate(bundles)
        self.service.commit({'bundles.bundles': bundles}, expected_digest)
        return self.snapshot()


def running(app, windows):
    # Exact desktop or declared StartupWMClass only. Substrings and process
    # proximity are not evidence that the requested app is already running.
    identities = {app.desktop_id.removesuffix('.desktop')}
    if app.window_class:
        identities.add(app.window_class)
    return any(w.app_class in identities or w.initial_class in identities for w in windows)


def execute(bundle, apps, windows, launcher):
    validate([bundle])
    installed = {app.desktop_id: app for app in apps}
    result = {'launched': [], 'skipped': [], 'failed': []}
    for item in bundle['items']:
        key = item['desktop_id']
        app = installed.get(key)
        if app is None:
            result['failed'].append(key)
            continue
        try:
            if not item['new_instance'] and running(app, windows()):
                result['skipped'].append(key)
            elif launcher.launch_desktop(key):
                result['launched'].append(key)
            else:
                result['failed'].append(key)
        except (RuntimeError, OSError):
            # Unknown result is not permission to retry or undo other launches.
            result['failed'].append(key)
    return result


def launch_bundle(identifier, store=None):
    from .applications import ApplicationCatalog
    from .external_launch import UwsmApplicationLauncher
    from .hyprland import HyprlandClient
    from .state import state_dir
    store = store or BundleStore()
    bundles, _ = store.snapshot()
    bundle = next((b for b in bundles if b['id'] == identifier), None)
    if bundle is None:
        raise ValueError('묶음을 찾을 수 없습니다')
    # One batch at a time across shortcut and settings processes. Keep the lock
    # outside the configuration directory so it survives a config replacement.
    with (state_dir() / 'bundle-launch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('다른 묶음을 실행 중입니다') from exc
        launcher = UwsmApplicationLauncher(history_group=str(uuid4()))
        return execute(bundle, ApplicationCatalog().apps, HyprlandClient().windows, launcher)
