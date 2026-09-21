"""Position-only display preview with compositor-owned timeout and durable fallback."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from .hyprland import HyprlandClient

PREFIX = '-- luminophore-monitor-layout-v1 '


def encode(positions):
    if not isinstance(positions, dict) or len(positions) > 32:
        raise ValueError('모니터 배치가 올바르지 않습니다')
    tokens = []
    for key, point in sorted(positions.items()):
        if not isinstance(key, str) or not re.fullmatch('[A-Za-z0-9_-]{1,1024}', key) or not isinstance(point, (list, tuple)) or len(point) != 2 or any(type(v) is not int or abs(v)>100000 for v in point):
            raise ValueError('모니터 위치가 올바르지 않습니다')
        tokens.extend([key, str(point[0]), str(point[1])])
    return (PREFIX + json.dumps(positions, sort_keys=True) + '\nreturn ' + json.dumps(' '.join(tokens)) + '\n').encode()


def decode(data):
    try:
        values = json.loads(data.decode().splitlines()[0].removeprefix(PREFIX))
        if encode(values) != data:
            raise ValueError()
        return values
    except (ValueError, UnicodeError, IndexError, TypeError) as exc:
        raise ValueError('모니터 배치 파일이 손상되었거나 외부에서 변경되었습니다') from exc


def positions(snapshot):
    return {o['key']: [int(o['x']), int(o['y'])] for o in snapshot['outputs']}


def validate(snapshot, candidate):
    encode(candidate)
    if not snapshot['supported']:
        raise ValueError('현재 배치의 미러링 또는 혼합 배율은 아직 지원하지 않습니다')
    if set(candidate) != {o['key'] for o in snapshot['outputs']} or not candidate:
        raise ValueError('모니터 구성이 변경되었습니다')
    boxes=[]
    for o in snapshot['outputs']:
        x,y=candidate[o['key']]; w,h=o['width'],o['height']
        if w<=0 or h<=0: raise ValueError('잘못된 화면 크기')
        if any(x<a+c and x+w>a and y<b+d and y+h>b for a,b,c,d in boxes):
            raise ValueError('모니터가 겹칩니다. 가장자리를 맞춰주세요')
        boxes.append((x,y,w,h))


def snapped(snapshot, candidate, key, x, y, distance=35):
    own=next(o for o in snapshot['outputs'] if o['key']==key)
    xs,ys=[],[]
    for o in snapshot['outputs']:
        if o['key']==key: continue
        a,b=candidate[o['key']]
        xs.extend((a, a+o['width'], a-own['width'], a+o['width']-own['width']))
        ys.extend((b, b+o['height'], b-own['height'], b+o['height']-own['height']))
    def snap(v, choices):
        near=min(choices,key=lambda p:abs(p-v),default=v)
        return round(near if abs(near-v)<=distance else v)
    return snap(x,xs),snap(y,ys)


class MonitorLayout:
    def __init__(self, path=None, client=None, *, service=None):
        from .domain_client import DomainClient
        self.client = client or HyprlandClient()
        self.service = service or DomainClient()
        self.identifier = ''

    def snapshot(self):
        raw = json.loads(self.client._run('luminophoremonitorlayout', 'snapshot', timeout=3))
        if not isinstance(raw, dict) or not isinstance(raw.get('outputs'), list) or not isinstance(raw.get('topology'), str):
            raise ValueError('모니터 상태 응답이 올바르지 않습니다')
        return raw

    def release(self):
        # Native coordinator owns timeout and rollback even after UI exit.
        pass

    def recover(self):
        self.service.request('settings-snapshot')

    def begin(self, snapshot, candidate):
        validate(snapshot, candidate)
        if self.snapshot()['topology'] != snapshot['topology']:
            raise ValueError('모니터 구성이 변경되었습니다')
        docs, digest = self.service.snapshot()
        # Native monitor keys in the preview are stable identities, whereas
        # the TOML output selector uses the connector name.
        names = {row['key']: row['name'] for row in snapshot['outputs']}
        rules = docs['monitors.toml'].get('outputs', {})
        saved = docs['monitors.toml'].get('positions', {})
        for key, point in candidate.items():
            name = names[key]
            rules.setdefault(name, {})['position'] = list(point)
            saved.pop(key, None)
            saved.pop(name, None)
        reply = self.service.start({'monitors.outputs': rules, 'monitors.positions': saved}, digest)
        self.identifier = reply['request_id']
        self.service.wait(reply, preview=True)

    def status(self):
        reply = self.service.request('settings-status', request_id=self.identifier)
        if reply.get('awaiting_confirmation'): return 'preview'
        if reply.get('category') == 'completion_unknown': return 'confirming'
        return 'committed' if reply.get('category') in ('ok', 'saved_pending_next_start') else 'rolled_back'

    def _decide(self, keep):
        self.service.request('settings-confirm', request_id=self.identifier, keep=keep)
        reply = self.service.request('settings-status', request_id=self.identifier)
        if keep:
            self.service.wait(reply)
        else:
            import time
            end = time.monotonic() + 30
            while reply.get('category') == 'completion_unknown' and time.monotonic() < end:
                time.sleep(.025)
                reply = self.service.request('settings-status', request_id=self.identifier)
            if reply.get('category') != 'runtime_apply_failed_rolled_back':
                raise ValueError('이전 모니터 배치 복구를 확인하지 못했습니다')

    def cancel(self): self._decide(False)
    def confirm(self): self._decide(True)
