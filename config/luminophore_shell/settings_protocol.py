"""Read-only native scalar validation; never an apply acknowledgement.

Version 1 transports the generated scalar schema. Tag h encodes UTF-8 strings
as hex (a dash means empty); legacy s enum tokens remain readable.
Complex domain snapshots and commit/epoch semantics are not this endpoint.
"""
from __future__ import annotations

import re
from uuid import uuid4

from .owned_settings import DEFAULTS, validate

ENDPOINT = 'luminophoresettingsvalidate'
_LIMIT = 8192


class SettingsProtocolError(ValueError):
    pass


def encode(values, request_id):
    if type(request_id) is not str or not re.fullmatch('[0-9a-f]{32}', request_id):
        raise SettingsProtocolError('invalid request id')
    normalized = validate(values)
    tokens = ['1', request_id, str(len(values))]
    for key, value in sorted(values.items()):
        tag = {int: 'i', float: 'f', bool: 'b', str: 'h'}[type(value)]
        text = (value.encode('utf-8').hex() or '-') if type(value) is str else str(value).lower()
        if not re.fullmatch('[!-~]+', text):
            raise SettingsProtocolError('scalar token cannot contain whitespace')
        tokens.extend((key, tag, text))
    wire = ' '.join(tokens)
    if len(wire) > _LIMIT:
        raise SettingsProtocolError('request exceeds limit')
    return wire, normalized


def decode(response, request_id, expected):
    if type(response) is not str or len(response) > _LIMIT or any(ord(c) < 32 or ord(c) > 126 for c in response):
        raise SettingsProtocolError('invalid response framing')
    tokens = response.split(' ')
    if len(tokens) < 3 or tokens[:2] != ['1', request_id]:
        raise SettingsProtocolError('response version or request mismatch')
    if tokens[2] != 'validated':
        raise SettingsProtocolError('native settings validation rejected')
    if len(tokens) < 4 or tokens[3] != str(len(DEFAULTS)) or len(tokens) != 4 + 3 * len(DEFAULTS):
        raise SettingsProtocolError('incomplete native settings response')
    values = {}
    try:
        for index in range(4, len(tokens), 3):
            key, tag, text = tokens[index:index+3]
            if key in values or key not in DEFAULTS:
                raise ValueError('duplicate or unknown key')
            if tag == 'i':
                if not re.fullmatch('-?[0-9]+', text): raise ValueError('invalid integer')
                value = int(text)
            elif tag == 'f': value = float(text)
            elif tag == 's': value = text
            elif tag == 'h':
                if text == '-': value = ''
                elif re.fullmatch('(?:[0-9a-f]{2})+', text): value = bytes.fromhex(text).decode('utf-8')
                else: raise ValueError('invalid encoded string')
            elif tag == 'b' and text in ('true', 'false'): value = text == 'true'
            else: raise ValueError('invalid value type')
            values[key] = value
        validate(values)
    except ValueError as error:
        raise SettingsProtocolError(f'invalid native settings: {error}') from error
    if values != expected:
        raise SettingsProtocolError('native and Shell settings disagree')
    return values


def validate_remote(client, values):
    """Use from a worker. Transport failure is propagated without Lua fallback."""
    request_id = uuid4().hex
    request, expected = encode(values, request_id)
    response = client._run(ENDPOINT, request).removesuffix("\n")
    return decode(response, request_id, expected)
