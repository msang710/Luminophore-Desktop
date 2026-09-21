"""Strict input/device data; keymap compilation belongs to native prepare."""
import re
import math
from decimal import Decimal

from .owned_settings import SPECS, DEFAULTS, validate
from .settings_store import StoreError


def validate_input(values, *, device=False):
    if type(values) is not dict:
        raise StoreError('input: expected table')
    for key, value in values.items():
        if type(key) is not str:
            raise StoreError('input: expected field name')
        spec = SPECS.get('input.' + key)
        if spec is None and device:
            spec = next((SPECS[prefix + key] for prefix in ('touchpad.', 'touchdevice.', 'tablet.', 'tablettool.') if prefix + key in SPECS), None)
        if spec is None:
            raise StoreError('unsupported input field: ' + key)
        try:
            spec.validate(value)
        except ValueError as error:
            raise StoreError(str(error)) from error
        if key in ('accel_profile', 'scroll_points'):
            acceleration_curve(value, profile=key == 'accel_profile')
        if key.startswith('kb_') and key not in ('kb_file', 'kb_snapshot') and (len(value) > 512 or re.fullmatch(r'[A-Za-z0-9_:+.,() -]*', value) is None):
            raise StoreError('invalid keymap field: ' + key)
    return dict(values)


GLOBAL_INTERACTION_FIELDS = frozenset(('drag_threshold', 'scroll_event_delay', 'cursor_inactive_timeout', 'cursor_no_warps', 'cursor_persistent_warps', 'cursor_hide_on_key_press', 'cursor_hide_on_touch', 'cursor_hide_on_tablet', 'cursor_warp_back_after_non_mouse_input', 'resize_on_border', 'extend_border_grab_area', 'resize_on_border_inner_area', 'hover_icon_on_border', 'resize_corner', 'close_gesture_timeout'))


def decode_devices(raw):
    if type(raw) is not dict or len(raw) > 32:
        raise StoreError('devices: expected at most 32 named rules')
    result = {}
    for name, values in raw.items():
        if type(name) is not str or re.fullmatch(r'[a-z0-9_-]{1,128}', name) is None:
            raise StoreError('devices: expected normalized device name')
        if type(values) is not dict:
            raise StoreError('devices: expected settings table')
        if set(values) & {'emulate_discrete_scroll', 'mouse_refocus', 'follow_mouse_threshold', 'off_window_axis_events', 'follow_mouse', 'follow_mouse_shrink'}:
            raise StoreError("pointer focus and axis policies are global-only")
        if set(values) & {'focus_on_close', 'float_switch_override_focus'}:
            raise StoreError('focus transition policies are global-only')
        if set(values) & GLOBAL_INTERACTION_FIELDS:
            raise StoreError('interaction settings are global-only')
        if 'enabled' in values:
            raise StoreError('enabled is touchscreen-global-only; shared selectors cannot disable other device classes')
        if 'force_no_accel' in values:
            raise StoreError('force_no_accel is global-only')
        result[name] = validate_input(values, device=True)
    return result


def validate_absolute_inputs(raw, devices):
    values = validate({group + '.' + key: value for group, fields in raw.items() for key, value in fields.items()})
    def output(value):
        if value not in ('', '[[Auto]]') and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) is None:
            raise StoreError('invalid input output selector')
    def pressure(lo, hi):
        if lo >= 0 and hi >= 0 and lo > hi:
            raise StoreError('tablet pressure minimum exceeds maximum')
    output(values['touchdevice.output'])
    output(values['tablet.output'])
    lo, hi = values['tablettool.pressure_range_min'], values['tablettool.pressure_range_max']
    pressure(lo, hi)
    for rule in devices.values():
        if 'output' in rule: output(rule['output'])
        pressure(rule.get('pressure_range_min', lo), rule.get('pressure_range_max', hi))


_NUMBER = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')


def acceleration_curve(text, *, profile=False):
    if type(text) is not str or len(text) > 4096:
        raise StoreError('invalid acceleration curve text')
    if profile and text in ('', 'adaptive', 'flat'):
        return None
    if not profile and text == '':
        return None
    # Match the native consumer's ASCII-space tokenization exactly.
    if any(c.isspace() and c != ' ' for c in text):
        raise StoreError('acceleration curve requires space-separated numbers')
    tokens = text.split()
    if profile:
        if not text.startswith('custom ') or not tokens or tokens.pop(0) != 'custom':
            raise StoreError('unknown acceleration profile')
    if not 3 <= len(tokens) <= 65 or any(not _NUMBER.fullmatch(t) for t in tokens):
        raise StoreError('acceleration curve needs a step and 2..64 points')
    values = [float(t) for t in tokens]
    if any(not math.isfinite(v) or v < 0 or (v == 0 and Decimal(t) != 0) for v,t in zip(values,tokens)) or values[0] <= 0:
        raise StoreError('acceleration step must be positive and points finite/nonnegative')
    return values


def validate_acceleration(raw, devices):
    base = {k: raw.get(k, DEFAULTS['input.' + k]) for k in ('accel_profile', 'scroll_points')}
    for values in [base] + [{**base, **rule} for rule in devices.values()]:
        custom = acceleration_curve(values['accel_profile'], profile=True)
        scroll = acceleration_curve(values['scroll_points'])
        if scroll is not None and custom is None:
            raise StoreError('scroll_points requires a custom acceleration profile')
