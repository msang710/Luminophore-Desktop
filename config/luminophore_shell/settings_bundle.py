"""Pure candidate decoding. No Lua evaluation, device queries or disk writes.

This is the Python validation boundary; native participant parity and live
monitor feasibility are separate requirements before enabling the new loader.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
import re
import math
from typing import Mapping, get_type_hints

from .input_settings import decode_devices
from .monitor_settings import MonitorSettings, decode_monitors
from .app_bundles import validate as validate_bundles
from .binding_registry import BindingFlags, BindingRegistry, default_registry
from .config import COLLECTION_ENCODINGS, ShellConfig, _config_from_raw, validate_collection
from .owned_settings import validate as validate_scalars
from .placement_rules import validate as validate_placement
from .settings_store import FILES, SettingsPaths, SettingsStore, StoreError
from .native_domains import decode_native


@dataclass(frozen=True)
class SettingsBundle:
    settings: ShellConfig
    monitors: dict[str, tuple[int, int]]
    bindings: BindingRegistry
    placement: dict[str, str]
    bundles: tuple[dict, ...]
    monitor_rules: dict[str, MonitorSettings]
    native: dict


def _table(value, path):
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise StoreError(f'{path}: expected table')
    return value


def _keys(value, allowed, path):
    _table(value, path)
    extra = value.keys() - allowed
    if extra:
        raise StoreError(f'{path}: unknown keys: {", ".join(sorted(extra))}')


def _declared_types(raw, model, path):
    hints = get_type_hints(model)
    _keys(raw, set(hints) - {'path'}, path)
    for key, value in raw.items():
        kind = hints[key]
        location = f'{path}.{key}'
        if location == "settings.toml.devices":
            decode_devices(value)
        elif is_dataclass(kind):
            _declared_types(value, kind, location)
        elif kind in (str, bool, int):
            if type(value) is not kind:
                raise StoreError(f'{location}: expected {kind.__name__}')
        elif kind is float:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise StoreError(f'{location}: expected finite number')
        elif location.removeprefix('settings.toml.') in COLLECTION_ENCODINGS:
            validate_collection(value, location.removeprefix('settings.toml.'))
        else:
            raise StoreError(f'{location}: unsupported declared wire type')


def _settings(raw, path):
    # Persisted candidates must reject invalid visual values, not silently
    # recover to defaults as the legacy boot reader does.
    _declared_types(raw, ShellConfig, 'settings.toml')
    scalar = {}
    for section in ('compositor', 'motion', 'visual', 'input', 'touchpad', 'touchdevice', 'virtualkeyboard', 'tablet', 'tablettool'):
        for key, value in _table(raw.get(section, {}), f'settings.toml.{section}').items():
            scalar[f'{section}.{key}'] = value
    validate_scalars(scalar)
    return _config_from_raw(raw, path)


def _monitors(raw):
    return {name: rule.position for name, rule in decode_monitors(raw).items() if rule.position is not None}


def _bindings(raw):
    _keys(raw, {'actions'}, 'bindings.toml')
    actions = _table(raw.get('actions', {}), 'bindings.toml.actions')
    changes = {}
    flag_names = {field.name for field in fields(BindingFlags)}
    registry = default_registry()
    defaults = {action.action_id: action for action in registry.actions}
    for key, value in actions.items():
        path = f'bindings.toml.actions.{key}'
        _keys(value, {'chord', 'disabled', 'flags'}, path)
        if key not in defaults:
            raise StoreError(f'{path}: unknown action')
        if type(value.get('disabled', False)) is not bool:
            raise StoreError(f'{path}.disabled: expected boolean')
        if 'chord' in value and (type(value['chord']) is not str or not value['chord'].strip()):
            raise StoreError(f'{path}.chord: expected nonempty string')
        if value.get('disabled') and 'chord' in value:
            raise StoreError(f'{path}: disabled and chord are mutually exclusive')
        flags = value.get('flags', {})
        _keys(flags, flag_names, path + '.flags')
        if any(type(flag) is not bool for flag in flags.values()):
            raise StoreError(f'{path}.flags: expected booleans')
        original = defaults[key]
        merged = {name: getattr(original.flags, name) for name in flag_names}
        merged.update(flags)
        changes[key] = (None if value.get('disabled') else value.get('chord', original.chord), BindingFlags(**merged))
    return registry.update(changes)


def decode_bundle(documents: Mapping[str, dict], config_path: Path) -> SettingsBundle:
    """Validate all five parsed TOML documents as one candidate.

    Paths for relative assets are resolved against the editable settings file,
    never a temporary generation directory. Caller owns the returned data.
    """
    if set(documents) != set(FILES):
        raise StoreError('a complete five-file settings bundle is required')
    raw = deepcopy(dict(documents))
    for name in FILES:
        _table(raw[name], name)
        version = raw[name].pop('schema_version', None)
        if type(version) is not int or version != 1:
            raise StoreError(f'{name}: unsupported schema_version')
    native = decode_native(raw['settings.toml'].pop('native', {}))
    decoded = {}
    readers = {
        'settings.toml': lambda value: _settings(value, config_path),
        'monitors.toml': _monitors,
        'bindings.toml': _bindings,
        'placement.toml': lambda value: _placement(value),
        'bundles.toml': lambda value: _bundles(value),
    }
    for name, reader in readers.items():
        try:
            decoded[name] = reader(raw[name])
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise StoreError(f'{name}: {error}') from error
    occupied = {action.chord for action in decoded['bindings.toml'].actions if action.chord}
    for bundle in decoded['bundles.toml']:
        if bundle['chord'] and bundle['chord'] in occupied:
            raise StoreError(f'bundles.toml.{bundle["id"]}.chord: conflicts with bindings.toml')
    return SettingsBundle(decoded['settings.toml'], decoded['monitors.toml'], decoded['bindings.toml'],
                          decoded['placement.toml'], tuple(decoded['bundles.toml']), decode_monitors(raw['monitors.toml']), native)


def _placement(raw):
    _keys(raw, {'rules'}, 'placement.toml')
    return validate_placement(raw.get('rules', {}))


def _bundles(raw):
    _keys(raw, {'bundles'}, 'bundles.toml')
    return validate_bundles(raw.get('bundles', []))


def settings_store(paths: SettingsPaths) -> SettingsStore:
    """Construct a store that cannot prepare an unvalidated product bundle."""
    from .keymap_snapshot import import_keymaps
    return SettingsStore(paths, lambda documents: decode_bundle(documents, paths.config / 'settings.toml'),
                         lambda documents: import_keymaps(documents, paths.config))
