"""All desktop settings are edited as one validated five-file candidate."""
import os
import json
import re
import tomllib
from .settings_bundle import decode_bundle, settings_store
from .settings_store import SettingsPaths, StoreError
from .owned_settings import SPECS, OptionSpec
from .generated_settings import SCHEMA
from .input_settings import decode_devices
from .monitor_settings import decode_monitors
from dataclasses import is_dataclass
from typing import get_type_hints
from .config import ShellConfig, COLLECTION_ENCODINGS, validate_collection
from .toml_edit import patch as patch_toml

# All currently declared scalar consumers use the same schema as validation.
# Panel height remains a Shell-only integration value until the full C1 schema
# owns the remaining Shell fields; structured domains are not enabled here.
CANDIDATE_SPECS = {field['key']: SPECS[field['key']] for field in SCHEMA['fields']
                   if field['visibility'] == 'setting'}
CANDIDATE_SPECS['layout.panel_height'] = OptionSpec('layout.panel_height', int, 24, 240)
_internal = {field['key'] for field in SCHEMA['fields'] if field['visibility'] != 'setting'}


def _shell_specs(model, prefix=''):
    for key, kind in get_type_hints(model).items():
        path = prefix + key
        if path in ('path', 'devices') or path in _internal or path in COLLECTION_ENCODINGS:
            continue
        if is_dataclass(kind):
            _shell_specs(kind, path + '.')
        elif kind in (str, bool, int, float):
            CANDIDATE_SPECS.setdefault(path, OptionSpec(path, kind))


_shell_specs(ShellConfig)
DOMAIN_PATHS = {'native': ('settings.toml', 'native'), 'bindings.actions': ('bindings.toml', 'actions'),
                'placement.rules': ('placement.toml', 'rules'), 'bundles.bundles': ('bundles.toml', 'bundles'),
                'monitors.positions': ('monitors.toml', 'positions')}


def fixture_enabled():
    return os.environ.get('LUMINOPHORE_SETTINGS_FIXTURE') == '1'


def fixture_store():
    if fixture_enabled():
        for name in ('XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME'):
            if not os.path.isabs(os.environ.get(name, '')):
                raise StoreError('fixture requires explicit absolute XDG paths')
    return settings_store(SettingsPaths.current())


def generation_config(store, generation):
    parsed = {name: tomllib.loads(text) for name, text in generation.documents.items()}
    config = decode_bundle(parsed, store.paths.config/'settings.toml').settings
    return config


def boot_config():
    store = fixture_store()
    generation = store.current()
    if generation is None:
        raise StoreError('start the compositor before the Shell')
    return generation_config(store, generation)


def edit_candidate(store, generation, changes):
    if not changes or set(changes) - (CANDIDATE_SPECS.keys() | COLLECTION_ENCODINGS.keys() | DOMAIN_PATHS.keys() | {'monitors.outputs', 'devices'}):
        raise StoreError('unknown or internal setting')
    texts = dict(generation.documents)
    changes = dict(changes)
    for path in tuple(changes):
        if path in DOMAIN_PATHS:
            name, key = DOMAIN_PATHS[path]
            texts[name] = patch_toml(texts[name], key, changes.pop(path))
        elif path in COLLECTION_ENCODINGS:
            value = changes.pop(path)
            validate_collection(value, path)
            texts['settings.toml'] = patch_toml(texts['settings.toml'], path, value)
    if 'monitors.outputs' in changes:
        raw = tomllib.loads(texts['monitors.toml']); raw.pop('schema_version')
        raw['outputs'] = changes['monitors.outputs']
        decode_monitors(raw)
        # Replace this structured domain as a whole. Other documents and scalar
        # comments are preserved; positions retain their existing meaning.
        rendered = ['schema_version = 1\n']
        if raw.get('positions'):
            rendered.append('\n[positions]\n')
            for name, value in sorted(raw['positions'].items()):
                rendered.append(f'{json.dumps(name)} = {json.dumps(value)}\n')
        for name, fields in sorted(raw['outputs'].items()):
            rendered.append(f'\n[outputs.{json.dumps(name)}]\n')
            for name, value in sorted(fields.items()):
                rendered.append(f'{name} = {json.dumps(value, allow_nan=False)}\n')
        texts['monitors.toml'] = ''.join(rendered)
    if 'devices' in changes:
        devices=decode_devices(changes['devices'])
        before_devices=tomllib.loads(texts['settings.toml'])
        # UI replies omit large internal snapshots. Preserve them when the
        # source path is unchanged, including after the original file is gone.
        for name, rule in devices.items():
            old = before_devices.get('devices', {}).get(name, {})
            if 'kb_file' in rule and 'kb_snapshot' not in rule and rule['kb_file'] == old.get('kb_file') and 'kb_snapshot' in old:
                rule['kb_snapshot'] = old['kb_snapshot']
        texts['settings.toml'] = patch_toml(texts['settings.toml'], 'devices', devices)
    for path, value in sorted(changes.items()):
        if path in ('monitors.outputs', 'devices'): continue
        try:
            CANDIDATE_SPECS[path].validate(value)
        except ValueError as error:
            raise StoreError(str(error)) from error
        texts['settings.toml'] = patch_toml(texts['settings.toml'], path, value)
    from .keymap_snapshot import import_keymaps
    refresh = {('input',)} if 'input.kb_file' in changes else set()
    texts = import_keymaps(texts, store.paths.config, refresh=refresh)
    return store._candidate(texts)
