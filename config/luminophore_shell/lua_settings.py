"""Compile validated TOML into the production Lua loader's typed API.

No shell evaluation, executable config values, or partial TOML parser. The final
module must be required after the source-owned Lua defaults.
"""
import argparse
import json
import tomllib
import struct
from pathlib import Path
from .config import load_config, render_config_patch, _write_text_atomic
from .config_paths import lua_config_path
from .owned_settings import DEFAULTS, validate
from .lua_option_map import OPTION_MAP
from .binding_service import _write_atomic


def owned_values(config):
    result = {}
    for key in DEFAULTS:
        section, field = key.split('.')
        result[key] = getattr(getattr(config, section), field)
    return validate(result)


def lua(value):
    if isinstance(value, dict):
        return '{' + ', '.join('[' + lua(k) + '] = ' + lua(v) for k, v in sorted(value.items())) + '}'
    if isinstance(value, (tuple, list)):
        return '{' + ', '.join(map(lua, value)) + '}'
    if isinstance(value, str):
        # Lua does not understand JSON's Unicode escapes. Encode control bytes
        # with fixed-width decimal escapes, retaining valid UTF-8 literally.
        return '"' + ''.join('\\' + format(ord(c), '03d') if ord(c) < 32 or c in '\\"' else c for c in value) + '"'
    return json.dumps(value, allow_nan=False)


def color(argb):
    return 'rgba(' + argb[2:] + argb[:2] + ')'


def options(values):
    result = {}
    groups = {}
    for key, option in OPTION_MAP.items():
        groups.setdefault(option, []).append(key)
    for option, keys in groups.items():
        if len(keys) > 1:
            if option == 'general:float_gaps':
                value = {edge: values['compositor.float_gap_' + edge] for edge in ('top','right','bottom','left')}
            else:
                prefix = keys[0].rsplit('_',1)[0]
                value = [struct.unpack('f', struct.pack('f', values[prefix+axis]))[0] for axis in ('_x', '_y')]
        else:
            key = keys[0]; value = values[key]
            if key == 'compositor.background_color': value = color(value)
            if key in ('compositor.shadow_color', 'compositor.shadow_inactive_color'):
                if value == 'inherit': continue
                parts = value.split(); value = {'colors': [color(c) for c in parts[:-1]], 'angle': int(parts[-1][:-3])}
        result[option.replace(':','.').replace('-','_')] = value
    return result


def device_options(name, fields, values=None):
    values = values or DEFAULTS
    result = {'name': name}
    for key, value in fields.items():
        if key.endswith(('_x','_y')) and any(key.startswith(p) for p in ('region_', 'active_area_')):
            base = key[:-2]
            result[base] = [fields.get(base+axis, values['tablet.'+base+axis]) for axis in ('_x','_y')]
        else:
            result[key] = value
    return result


def render(config):
    values = owned_values(config)
    data = {section: {key.split('.', 1)[1]: value for key, value in values.items() if key.startswith(section + '.')}
            for section in ('compositor', 'motion')}
    lines = ['-- Generated from validated shell.toml; apply after source-owned defaults.',
             'local M = ' + lua(data), 'function M.apply()', 'hl.config({']
    lines += ['    ['+lua(key)+'] = '+lua(value)+',' for key,value in sorted(options(values).items())]
    lines += ['})']
    lines += ['hl.device('+lua(device_options(name, fields, values))+')' for name,fields in sorted(config.devices.items())]
    motion = config.motion
    speed = {'fast': 2, 'balanced': 3, 'smooth': 5, 'custom': 3}[motion.preset]
    curve = 'easeInOutCubic' if motion.preset == 'smooth' else 'quick'
    for name, amount, style in [('global',speed,''),('windows',speed,'slide'),('specialWorkspaceIn',2,'slide top'),('specialWorkspaceOut',2,'slide bottom')]:
        node = {'leaf': name, 'enabled': motion.enabled, 'speed': amount * motion.speed if motion.enabled else 1,
                'style': style if motion.enabled else ''}
        node['spring' if name == 'windows' and motion.enabled else 'bezier'] = 'easy' if name == 'windows' and motion.enabled else curve if motion.enabled else 'default'
        lines.append('hl.animation(' + lua(node) + ')')
    lines += ['end', 'return M']
    return '\n'.join(lines)+'\n'


from .legacy_defaults import LEGACY_DEFAULTS


def migrate_legacy_defaults(path):
    path = Path(path); text = path.read_text(); parsed = tomllib.loads(text)
    missing = {key: value for key, value in LEGACY_DEFAULTS.items()
               if key.split('.')[1] not in parsed.get(key.split('.')[0], {})}
    if missing:
        candidate = render_config_patch(text, missing)
        from .config import load_config_text
        load_config_text(candidate, path)
        _write_text_atomic(path, candidate)
    return bool(missing)


def compile_config(path):
    config=load_config(Path(path)); target=lua_config_path('owned',config.path)
    _write_atomic(target,render(config).encode())
    return target


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--compile', required=True, type=Path)
    parser.add_argument('--migrate', action='store_true')
    args=parser.parse_args()
    if args.migrate: migrate_legacy_defaults(args.compile)
    compile_config(args.compile)

if __name__ == '__main__': main()
