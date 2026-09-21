"""Offline, non-executing import into a reviewable five-file TOML candidate.

Never changes the running session, source files, or completed generation. Lua
outside the small literal grammar is reported, not evaluated or discarded.
"""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import tomllib

from .lua_option_map import OPTION_MAP
from .legacy_bindings import BindingServiceError
from .settings_bundle import decode_bundle
from .settings_store import FILES, MAX_BYTES, StoreError, _digest, _sync


@dataclass(frozen=True)
class Migration:
    documents: dict[str, str]
    sources: dict[str, str]
    originals: dict[str, bytes]
    unsupported: tuple[str, ...]


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _read_sources(root):
    result, errors = {}, []
    total = 0
    if root.is_symlink() or not root.is_dir():
        raise StoreError('migration source must be a real directory')
    # Only configuration files belong to this import, never cache or assets.
    def walk(directory):
        nonlocal total
        for path in sorted(directory.iterdir()):
            name = path.relative_to(root).as_posix()
            if path.is_symlink():
                errors.append(name + ': symlink is not an import source')
            elif path.is_dir():
                if name == 'config' or name.startswith('config/'):
                    if len(path.relative_to(root).parts) > 16:
                        raise StoreError('configuration directory nesting limit')
                    walk(path)
            elif path.suffix in {'.lua', '.toml', '.json'}:
                if path.stat().st_size > MAX_BYTES:
                    raise StoreError('migration source exceeds size limit')
                data = path.read_bytes()
                total += len(data)
                if total > MAX_BYTES:
                    raise StoreError('migration sources exceed size limit')
                result[name] = data
    walk(root)
    return result, errors


class _LiteralLua:
    """Tokenizer consumes every byte; strings/comments cannot hide code."""
    token = re.compile(r'''\s+|--\[\[[\s\S]*?\]\]|--[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[+-]?(?:\d+\.\d+|\d+)(?:[eE][+-]?\d+)?|[A-Za-z_][A-Za-z_0-9]*|[{}()\[\],;.=]''')

    def __init__(self, text):
        self.tokens = []
        offset = 0
        while offset < len(text):
            match = self.token.match(text, offset)
            if not match:
                raise StoreError(f'unsupported Lua syntax at byte {offset}')
            word = match.group()
            if not word.isspace() and not word.startswith('--'):
                self.tokens.append(word)
            offset = match.end()
        self.index = 0

    def peek(self):
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self, expected=None):
        value = self.peek()
        if value is None or (expected is not None and value != expected):
            raise StoreError(f'expected {expected or "literal"}; found {value!r}')
        self.index += 1
        return value

    def value(self, depth=0):
        if depth > 16:
            raise StoreError('Lua literal nesting limit')
        word = self.take()
        if word in {'true', 'false'}:
            return word == 'true'
        if word == 'nil':
            return None
        if word.startswith(('"', "'")):
            # JSON escapes overlap with Lua except unicode/solidus escapes.
            # Reject ambiguous escapes rather than interpreting executable Lua.
            body = word[1:-1]
            if re.search(r'\\(?![\\"\'nrtbf])', body):
                raise StoreError('unsupported Lua string escape')
            escapes = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f', '\\': '\\', '"': '"', "'": "'"}
            return re.sub(r'\\(.)', lambda m: escapes[m[1]], body)
        if re.fullmatch(r'[+-]?\d+', word):
            return int(word)
        if re.fullmatch(r'[+-]?(?:\d+\.\d+|\d+)(?:[eE][+-]?\d+)?', word):
            return float(word)
        if word != '{':
            raise StoreError('only literal values can be imported')
        fields, values = {}, []
        while self.peek() != '}':
            if self.peek() == '[':
                self.take('['); key = self.value(depth+1); self.take(']'); self.take('=')
                if not isinstance(key, str): raise StoreError('only string table keys are supported')
            elif self.index + 1 < len(self.tokens) and self.tokens[self.index+1] == '=':
                key = self.take(); self.take('=')
            else:
                values.append(self.value(depth+1)); key = None
            if key is not None:
                if key in fields: raise StoreError('duplicate Lua table key')
                fields[key] = self.value(depth+1)
            if self.peek() in {',', ';'}: self.take()
            elif self.peek() != '}': raise StoreError('unsupported Lua expression')
        self.take('}')
        if fields and values: raise StoreError('mixed Lua table is unsupported')
        return values if values else fields

    def calls(self):
        calls = []
        while self.peek() is not None:
            self.take('hl'); self.take('.'); name = self.take(); self.take('(')
            value = self.value(); self.take(')')
            if self.peek() == ';': self.take(';')
            if name not in {'config', 'monitor', 'device'} or type(value) is not dict:
                raise StoreError('unsupported Lua call: hl.' + name)
            calls.append((name, value))
        return calls


def _toml_value(value):
    if type(value) is dict:
        return '{' + ', '.join(json.dumps(k, ensure_ascii=False)+' = '+_toml_value(v) for k, v in value.items()) + '}'
    if type(value) is list:
        return '[' + ', '.join(_toml_value(v) for v in value) + ']'
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _render(value):
    return ''.join(json.dumps(key, ensure_ascii=False)+' = '+_toml_value(item)+'\n' for key, item in value.items())


def inspect(source: Path, *, trusted=None, packaged=False) -> Migration:
    source = Path(source)
    originals, errors = _read_sources(source)
    sources = {name: _hash(data) for name, data in originals.items()}
    docs = {name: {'schema_version': 1} for name in FILES}
    catalog = json.loads(Path(__file__).with_name('legacy_modules.json').read_text())['modules'] if packaged else {}
    native = None
    if packaged:
        from .desktop_defaults import native_defaults
        native = native_defaults()
    settings_text = None
    consumed = set()
    duplicate_domains = {}
    # An existing TOML is authoritative only if no conflicting legacy value is
    # present. Conflict resolution must be explicit; precedence loses data.
    for name in ('settings.toml', 'shell.toml', 'config.toml'):
        if name not in originals: continue
        try:
            text = originals[name].decode()
            raw = tomllib.loads(text)
            raw.setdefault('schema_version', 1)
            if settings_text is not None and raw != docs['settings.toml']:
                raise StoreError('conflicting settings TOML files')
            docs['settings.toml'] = raw
            settings_text = text if 'schema_version' in tomllib.loads(text) else 'schema_version = 1\n'+text
            consumed.add(name)
        except (ValueError, UnicodeError) as error: errors.append(name+': '+str(error))
    for name in FILES[1:]:
        if name in originals:
            try: docs[name] = tomllib.loads(originals[name].decode()); consumed.add(name)
            except (ValueError, UnicodeError) as error: errors.append(name+': '+str(error))

    def merge(table, key, value):
        if key in table and table[key] != value:
            raise StoreError('conflicting values for '+key)
        table[key] = value

    reverse = {}
    for key, option in OPTION_MAP.items(): reverse.setdefault(option, []).append(key)
    imported = {}
    if packaged:
        # These are the effective values previously set by reviewed modules.
        # Explicit user TOML wins over a packaged fallback, never vice versa.
        from .legacy_defaults import LEGACY_DEFAULTS
        for key, value in LEGACY_DEFAULTS.items():
            section, field = key.split('.')
            target = docs['settings.toml'].setdefault(section, {})
            if field not in target:
                target[field] = value
                imported[key] = value

    def binding_data(data):
        from .legacy_bindings import decode_registry
        registry = decode_registry(data)
        actions = {}
        for action in registry.actions:
            flags = {key: getattr(action.flags, key) for key in ('locked', 'non_consuming', 'release', 'repeating')}
            actions[action.action_id] = {'flags': flags, **({'disabled': True} if action.chord is None else {'chord': action.chord})}
        merge(docs['bindings.toml'], 'actions', actions)
        return registry

    def config_values(table, prefix=''):
        for key, value in table.items():
            name = (prefix+':'+key if prefix else key).replace('.', ':')
            if name not in reverse and type(value) is dict:
                config_values(value, name); continue
            targets = reverse.get(name, [])
            if len(targets) != 1:
                raise StoreError('unmapped or compound option: '+name)
            target = targets[0]
            section, field = target.split('.')
            dest = docs['settings.toml'].setdefault(section, {})
            merge(dest, field, value)
            imported[target] = value

    for name, data in originals.items():
        if name in consumed or name == '.luminophore-schema.json': continue
        try:
            base = Path(name).name
            if sources[name] in catalog.get(base, []):
                continue
            elif packaged and base == 'variables.lua':
                parser = _LiteralLua(data.decode())
                values = {}
                allowed = {'TERMINAL': 'terminal', 'FILE_MANAGER': 'files', 'BROWSER': 'browser', 'EDITOR': 'editor', 'CALCULATOR': 'calculator'}
                while parser.peek() is not None:
                    key = parser.take(); parser.take('=')
                    value = values[parser.take()] if parser.peek() in values else parser.value()
                    if key not in {*allowed, 'LEFT_MONITOR', 'RIGHT_MONITOR', 'PRIMARY_MONITOR'} or type(value) is not str:
                        raise StoreError('unknown application or output variable')
                    values[key] = value
                merge(duplicate_domains, 'variables.lua', values)
                policy = native_defaults(values.get('PRIMARY_MONITOR', 'DP-2'), values.get('LEFT_MONITOR', 'DP-2'), values.get('RIGHT_MONITOR', 'DP-1'))
                for key in ('window_rules', 'workspace_rules'): native[key] = policy[key]
                native['applications'].update({allowed[k]: v for k, v in values.items() if k in allowed})
            elif packaged and base == 'environment.lua':
                parser = _LiteralLua(data.decode())
                while parser.peek() is not None:
                    parser.take('hl'); parser.take('.'); parser.take('env'); parser.take('(')
                    key = parser.value(); parser.take(','); value = parser.value(); parser.take(')')
                    if parser.peek() == ';': parser.take(';')
                    merge(native['environment'], key, value)
            elif packaged and base == 'luminophore_theme.lua':
                parser = _LiteralLua(data.decode()); parser.take('return'); raw = parser.value()
                if parser.peek() is not None or type(raw) is not dict or raw.keys() - {'primary', 'surface_container', 'secondary', 'error', 'generation_id'}:
                    raise StoreError('unknown palette implementation')
                merge(duplicate_domains, 'luminophore_theme.lua', raw)
                for key, color in raw.items():
                    if key == 'generation_id': continue
                    match = re.fullmatch(r'(rgb|rgba)\(([0-9a-fA-F]+)\)', color)
                    if not match or len(match[2]) != (6 if match[1] == 'rgb' else 8):
                        raise StoreError('invalid palette color')
                    native['palette'][key] = 'ff'+match[2] if match[1] == 'rgb' else match[2][6:]+match[2][:6]
            elif base == 'luminophore_placements.lua':
                from .placement_rules import PREFIX, encode, validate
                value = validate(json.loads(data.decode().splitlines()[0].removeprefix(PREFIX)))
                if encode(value) != data: raise StoreError('generated placement body differs')
                merge(docs['placement.toml'], 'rules', value)
            elif base == 'luminophore_bundles.lua':
                from .app_bundles import PREFIX, encode, validate
                value = validate(json.loads(data.decode().splitlines()[0].removeprefix(PREFIX)))
                if encode(value) != data: raise StoreError('generated bundle body differs')
                merge(docs['bundles.toml'], 'bundles', value)
            elif base == 'luminophore_monitor_layout.lua':
                from .monitor_layout import decode
                merge(docs['monitors.toml'], 'positions', decode(data))
            elif base == 'bindings.json':
                from .generated_bindings import serialize_binding_lua
                registry = binding_data(data)
                for other in originals:
                    if Path(other).name == 'luminophore_bindings.lua':
                        if originals[other] != serialize_binding_lua(registry): raise StoreError('binding Lua and JSON disagree')
                        consumed.add(other)
            elif base == 'luminophore_bindings.lua':
                from .generated_bindings import serialize_binding_lua
                parser = _LiteralLua(data.decode())
                parser.take('return')
                raw = parser.value()
                if parser.peek() is not None: raise StoreError('unexpected code after binding data')
                registry = binding_data(json.dumps(raw).encode())
                if data != serialize_binding_lua(registry): raise StoreError('generated binding body differs')
            elif sources[name] in (trusted or {}).get(name, set()):
                continue  # exact implementation input, still backed up in full
            elif name.endswith('.lua'):
                for call, value in _LiteralLua(data.decode()).calls():
                    if call == 'config': config_values(value)
                    elif call == 'monitor':
                        if 'output' not in value: raise StoreError('monitor output missing')
                        value = dict(value); output = value.pop('output')
                        if 'position' in value:
                            match = re.fullmatch(r'(-?\d+)x(-?\d+)', str(value['position']))
                            if not match: raise StoreError('unsupported monitor position')
                            value['position'] = [int(match[1]), int(match[2])]
                        merge(docs['monitors.toml'].setdefault('outputs', {}), output, value)
                    else:
                        value = dict(value); device = value.pop('name', None)
                        if not device: raise StoreError('device name missing')
                        merge(docs['settings.toml'].setdefault('devices', {}), device, value)
            else:
                raise StoreError('unclassified configuration file')
        except (ValueError, KeyError, TypeError, IndexError, UnicodeError, BindingServiceError) as error:
            errors.append(name+': '+str(error))
    if native is not None:
        if 'native' in docs['settings.toml'] and docs['settings.toml']['native'] != native:
            errors.append('native: existing TOML differs from legacy policy')
        else:
            docs['settings.toml']['native'] = native
    try:
        decode_bundle(docs, source/'settings.toml')
    except (ValueError, TypeError) as error:
        errors.append(str(error))
    if errors:
        return Migration({}, sources, originals, tuple(errors))
    texts = {name: _render(raw) for name, raw in docs.items()}
    if settings_text is not None:
        from .config import render_config_patch
        preserved = render_config_patch(settings_text, imported, config_root=source) if imported else settings_text
        before = tomllib.loads(preserved)
        if native is not None and 'native' not in before:
            preserved = 'native = '+_toml_value(native)+'\n'+preserved
        if 'devices' not in before and 'devices' in docs['settings.toml']:
            for name, fields in docs['settings.toml']['devices'].items():
                preserved += '\n[devices.'+json.dumps(name)+']\n'+_render(fields)
        if tomllib.loads(preserved) != docs['settings.toml']:
            errors.append('settings.toml: cannot preserve existing document safely')
        texts['settings.toml'] = preserved
    return Migration(texts, sources, originals, tuple(errors))


def stage(migration: Migration, source: Path, destination: Path) -> Path:
    """Atomic candidate+backup publication. This is not session activation."""
    source, destination = Path(source), Path(destination)
    if migration.unsupported:
        raise StoreError('migration requires explicit resolution: '+'; '.join(migration.unsupported))
    if {name: _hash(data) for name, data in migration.originals.items()} != migration.sources:
        raise StoreError('migration backup differs from inspected sources')
    if destination.resolve().is_relative_to(source.resolve()):
        raise StoreError('candidate must be outside the source directory')
    current, errors = _read_sources(source)
    if errors or {k: _hash(v) for k, v in current.items()} != migration.sources:
        raise StoreError('migration source changed since inspection')
    decode_bundle({k: tomllib.loads(v) for k, v in migration.documents.items()}, destination/'settings.toml')
    report = {'version': 1, 'sources': migration.sources, 'generation': _digest(migration.documents), 'activated': False}
    expected = {**{k: v.encode() for k, v in migration.documents.items()},
                **{'originals/'+k: v for k, v in migration.originals.items()},
                'migration.json': (json.dumps(report, sort_keys=True, indent=2)+'\n').encode()}
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir(): raise StoreError('candidate destination is not a directory')
        actual = {p.relative_to(destination).as_posix(): p.read_bytes() for p in destination.rglob('*') if p.is_file() and not p.is_symlink()}
        if actual != expected or any(p.is_symlink() for p in destination.rglob('*')):
            raise StoreError('existing migration candidate differs')
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.migration-', dir=destination.parent))
    try:
        for name, data in expected.items():
            target = temporary/name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                os.chmod(target, 0o600)
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
        for directory in sorted((p for p in temporary.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            _sync(directory)
        _sync(temporary)
        current, errors = _read_sources(source)
        if errors or {k: _hash(v) for k, v in current.items()} != migration.sources:
            raise StoreError('migration source changed while staging')
        # rename() may replace a concurrently created empty directory. Linux's
        # NOREPLACE makes publication atomic without taking ownership of it.
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(temporary), -100, os.fsencode(destination), 1):
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number), str(destination))
        _sync(destination.parent)
        return destination
    finally:
        if temporary.exists(): shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--stage', type=Path, help='write a candidate and originals; never activate')
    parser.add_argument('--packaged', action='store_true', help='import reviewed packaged desktop modules as native policy')
    args = parser.parse_args()
    result = inspect(args.source, packaged=args.packaged)
    output = {'status': 'UNSUPPORTED' if result.unsupported else 'READY', 'sources': result.sources,
              'unsupported': result.unsupported, 'activated': False}
    if args.stage and not result.unsupported:
        output['candidate'] = str(stage(result, args.source, args.stage))
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return int(bool(result.unsupported))


if __name__ == '__main__':
    raise SystemExit(main())
