"""Typed physical-output rules; parsing never queries connected hardware."""
from dataclasses import dataclass
import math
import re
from .settings_store import StoreError


@dataclass(frozen=True)
class MonitorSettings:
    width: int = 0
    height: int = 0
    refresh: float = 60.0
    scale: float = -1.0
    transform: int = 0
    position: tuple[int, int] | None = None
    disabled: bool = False
    vrr: int | None = None


def decode_monitors(raw):
    def fail():
        raise StoreError('monitors.toml: invalid or unsupported output rule')
    def table(value):
        if type(value) is not dict or any(type(k) is not str for k in value): fail()
        return value
    def point(value):
        if type(value) is not list or len(value) != 2 or any(type(v) is not int or abs(v) > 100000 for v in value): fail()
        return tuple(value)
    table(raw)
    if raw.keys() - {'positions', 'outputs'}: fail()
    positions, outputs = table(raw.get('positions', {})), table(raw.get('outputs', {}))
    names = positions.keys() | outputs.keys()
    if len(names) > 32: fail()
    result = {}
    for name in sorted(names):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,1024}', name): fail()
        value = table(outputs.get(name, {}))
        if value.keys() - {'mode', 'scale', 'transform', 'position', 'disabled', 'vrr'}: fail()
        mode = value.get('mode', 'preferred')
        width, height, refresh = 0, 0, 60.0
        if type(mode) is not str: fail()
        if mode != 'preferred':
            match = re.fullmatch(r'([1-9][0-9]{0,4})x([1-9][0-9]{0,4})@([1-9][0-9]{0,3}(?:\.[0-9]{1,3})?)', mode)
            if not match: fail()
            width, height, refresh = int(match[1]), int(match[2]), float(match[3])
            if width > 32768 or height > 32768 or refresh > 1000: fail()
        scale = value.get('scale', -1.0)
        if 'scale' in value and (type(scale) not in (int, float) or not math.isfinite(scale) or not .25 <= scale <= 8): fail()
        transform = value.get('transform', 0)
        if type(transform) is not int or not 0 <= transform <= 7: fail()
        disabled = value.get('disabled', False)
        if type(disabled) is not bool: fail()
        vrr = value.get('vrr')
        if 'vrr' in value and (type(vrr) is not int or not 0 <= vrr <= 3): fail()
        if name in positions and 'position' in value: fail()
        position = point(positions[name]) if name in positions else point(value['position']) if 'position' in value else None
        result[name] = MonitorSettings(width, height, refresh, float(scale), transform, position, disabled, vrr)
    return result
