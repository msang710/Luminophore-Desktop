"""Typed data for compositor-owned desktop domains.

Rule effects are validated again by the native rule engine during prepare.
Commands are shell commands only in explicit startup/shutdown/application fields;
no configuration document is executable.
"""
from copy import deepcopy
import math
import re

WINDOW_EFFECTS = frozenset('''float tile fullscreen maximize fullscreen_state move size center pseudo monitor workspace
no_initial_focus pin suppress_event content no_close_for scrolling_width initial_placement rounding rounding_power
persistent_size animation border_color idle_inhibit opacity tag max_size min_size border_size allows_input dim_around
decorate focus_on_activate keep_aspect_ratio nearest_neighbor no_anim no_blur no_dim no_focus no_follow_mouse
no_max_size no_shadow no_shortcuts_inhibit opaque force_rgbx sync_fullscreen immediate xray render_unfocused
no_screen_share no_vrr no_auto_hdr tonemap scroll_mouse scroll_touchpad stay_focused confine_pointer'''.split())
LAYER_EFFECTS = frozenset('no_anim blur blur_popups ignore_alpha dim_around xray animation order above_lock no_screen_share'.split())
MATCH_FIELDS = frozenset('class title initial_class initial_title float tag xwayland fullscreen pin focus modal fullscreen_state_internal fullscreen_state_client workspace content xdg_tag namespace'.split())
APPLICATIONS = frozenset('terminal files browser editor calculator mission_center'.split())


def _table(value, allowed, path):
    if type(value) is not dict or value.keys() - allowed:
        raise ValueError(path + ': unknown fields or expected table')


def _text(value, path, *, empty=False):
    if type(value) is not str or (not empty and not value.strip()) or len(value) > 8192 or any(ord(c) < 32 for c in value):
        raise ValueError(path + ': invalid string')


def _rows(value, path):
    if type(value) is not list or len(value) > 1024:
        raise ValueError(path + ': expected bounded array')


def decode_native(raw):
    _table(raw, {'profile', 'palette', 'environment', 'startup', 'shutdown', 'applications', 'gestures', 'window_rules', 'layer_rules', 'workspace_rules'}, 'native')
    value = deepcopy(raw)
    if value.get('profile', 'desktop') not in ('desktop', 'greeter'):
        raise ValueError('native.profile: unsupported profile')
    palette = value.get('palette', {})
    _table(palette, {'primary', 'surface_container', 'secondary', 'error'}, 'native.palette')
    for color in palette.values():
        if type(color) is not str or not re.fullmatch(r'[0-9a-fA-F]{8}', color):
            raise ValueError('native.palette: expected AARRGGBB color')
    for key in ('startup', 'shutdown'):
        _rows(value.get(key, []), key)
        for command in value.get(key, []):
            _text(command, key)
    environment = value.get('environment', {})
    if type(environment) is not dict or len(environment) > 128:
        raise ValueError('native.environment: expected bounded table')
    for key, entry in environment.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
            raise ValueError('native.environment: invalid variable')
        _text(entry, 'native.environment.' + key, empty=True)
    apps = value.get('applications', {})
    _table(apps, APPLICATIONS, 'native.applications')
    for key, entry in apps.items():
        _text(entry, 'native.applications.' + key)
    gestures = value.get('gestures', [])
    _rows(gestures, 'native.gestures')
    triggers = set()
    for row in gestures:
        _table(row, {'fingers', 'direction', 'action', 'modifiers', 'scale', 'disable_inhibit'}, 'gesture')
        if type(row.get('fingers')) is not int or not 2 <= row['fingers'] <= 5:
            raise ValueError('gesture: fingers must be 2..5')
        if row.get('direction') not in ('up', 'down', 'left', 'right', 'horizontal', 'vertical', 'swipe', 'pinch', 'pinchin', 'pinchout'):
            raise ValueError('gesture: unsupported direction')
        if row.get('action') not in ('close', 'fullscreen', 'maximize', 'float', 'move', 'resize', 'zoom'):
            raise ValueError('gesture: unsupported action')
        modifiers = row.get('modifiers', '')
        if type(modifiers) is not str or any(m not in ('SUPER', 'CTRL', 'ALT', 'SHIFT') for m in modifiers.split()):
            raise ValueError('gesture: invalid modifiers')
        scale = row.get('scale', 1.0)
        if type(scale) not in (int, float) or not math.isfinite(scale) or not 0 < scale <= 100:
            raise ValueError('gesture: invalid scale')
        if type(row.get('disable_inhibit', False)) is not bool:
            raise ValueError('gesture: invalid inhibit flag')
        trigger = row['fingers'], row['direction'], tuple(sorted(set(modifiers.split())))
        if trigger in triggers:
            raise ValueError('gesture: duplicate trigger')
        triggers.add(trigger)
    for key, effects in (('window_rules', WINDOW_EFFECTS), ('layer_rules', LAYER_EFFECTS)):
        rows = value.get(key, [])
        _rows(rows, 'native.' + key)
        names = set()
        for row in rows:
            _table(row, {'name', 'enabled', 'match', 'effects'}, key)
            if 'name' in row:
                _text(row['name'], key + '.name')
                if row['name'] in names:
                    raise ValueError(key + ': duplicate name')
                names.add(row['name'])
            if type(row.get('enabled', True)) is not bool:
                raise ValueError(key + ': expected enabled boolean')
            _table(row.get('match'), MATCH_FIELDS if key == 'window_rules' else {'namespace'}, key + '.match')
            _table(row.get('effects'), effects, key + '.effects')
            if not row['match'] or not row['effects']:
                raise ValueError(key + ': match and effects required')
            for field, item in (*row['match'].items(), *row['effects'].items()):
                if type(item) is list and field in ('size', 'move', 'min_size', 'max_size'):
                    if len(item) != 2:
                        raise ValueError(key + ': expected two coordinates')
                    parts = item
                else:
                    parts = [item]
                for part in parts:
                    if type(part) not in (str, bool, int, float) or (type(part) is float and not math.isfinite(part)):
                        raise ValueError(key + ': invalid rule value')
                    if type(part) is str:
                        _text(part, key + '.' + field)
    rows = value.get('workspace_rules', [])
    _rows(rows, 'native.workspace_rules')
    for row in rows:
        _table(row, {'workspace', 'monitor', 'persistent'}, 'workspace rule')
        _text(row.get('workspace'), 'workspace rule.workspace')
        _text(row.get('monitor'), 'workspace rule.monitor')
        if not row['workspace'].startswith('name:') or not row['workspace'][5:]:
            raise ValueError('workspace rule: explicit workspace name required')
        if type(row.get('persistent', False)) is not bool:
            raise ValueError('workspace rule: invalid persistent flag')
    return value
