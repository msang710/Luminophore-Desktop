from __future__ import annotations

import json
import math
import re
import uuid
from types import SimpleNamespace
from dataclasses import dataclass
from .hyprland import HyprlandClient, HyprlandError
from .panel_geometry import anchored_panel_bounds

_TOKEN = re.compile(r"^[A-Za-z0-9_.:\-]{1,256}$")

class LivePipError(RuntimeError):
    pass

@dataclass(frozen=True)
class PipPlacement:
    output: str
    x: float
    y: float
    width: float
    height: float
    margin: float


def initial_placement(monitors, crop, edge_margin: int) -> PipPlacement:
    if not monitors or len(crop) != 4 or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in crop) or min(crop[2:]) <= 0:
        raise LivePipError('PiP 배치 정보를 확인할 수 없습니다')
    monitor = max(monitors, key=lambda m: (m.x, m.x + m.width, m.name))
    margin = min(max(0, edge_margin), max(0, min(monitor.width, monitor.height) // 4))
    ratio = min(360 / crop[2], max(1, monitor.width - 2 * margin) / crop[2], max(1, monitor.height - 2 * margin) / crop[3], max(1.0, 96 / crop[2], 64 / crop[3]))
    width, height = max(1, round(crop[2] * ratio)), max(1, round(crop[3] * ratio))
    x, y, width, height = anchored_panel_bounds('right', monitor.width, width, height, margin, 0, monitor.height, margin, 'bottom')
    return PipPlacement(monitor.name, monitor.x + x, monitor.y + y, width, height, margin)


def command_wire(action: str, request: str, instance: str, **fields) -> str:
    """Serialize the existing native PiP protocol without Lua evaluation."""
    required = {
        'begin': (), 'cancel': (), 'result': (),
        'resolve': ('x', 'y', 'width', 'height'),
        'create': ('output', 'x', 'y', 'width', 'height', 'margin'),
        'remove': ('revision',),
        'place': ('revision', 'output', 'x', 'y', 'width', 'height'),
    }
    if action not in required or set(fields) != set(required[action]):
        raise LivePipError('Invalid PiP fields')
    if any(type(value) is not str or not _TOKEN.fullmatch(value) for value in (request, instance)):
        raise LivePipError('Invalid PiP identity')
    if action in {'remove', 'place'}:
        if not request.isascii() or not request.isdecimal() or not 0 <= int(request) < 2**64:
            raise LivePipError('Invalid PiP window identity')
    words = [action, request, instance]
    for key in required[action]:
        value = fields[key]
        if key == 'output':
            if type(value) is not str or not _TOKEN.fullmatch(value): raise LivePipError('Invalid PiP output')
        elif key == 'revision':
            if type(value) is not str or not value.isascii() or not value.isdecimal() or not 0 <= int(value) < 2**64:
                raise LivePipError('Invalid PiP revision')
        else:
            if type(value) not in (int, float) or not math.isfinite(value): raise LivePipError('Invalid PiP coordinate')
            if key in {'width', 'height'} and not 0 < value < 1e6: raise LivePipError('Invalid PiP extent')
            if key in {'x', 'y'} and abs(value) >= 1e7: raise LivePipError('Invalid PiP position')
            if key == 'margin' and not 0 <= value <= 256: raise LivePipError('Invalid PiP margin')
        words.append(str(value))
    return ' '.join(words)


class LivePipClient:
    """One request per selection; uncertain create is queried, never replayed."""
    def __init__(self, client: HyprlandClient | None = None):
        self.client = client or HyprlandClient()
        self.request = uuid.uuid4().hex
        self.instance = ''
        self._create_sent = False

    def supported(self) -> bool:
        try:
            state = self.client.query('luminophorelivepip')
            if state.get('selection') is not True or not _TOKEN.fullmatch(state.get('instance', '')):
                return False
            self.instance = state['instance']
            return True
        except (HyprlandError, ValueError, TypeError, AttributeError):
            return False

    def call(self, action, **fields):
        raw = self.client._run('luminophorepipcommand', command_wire(action, self.request, self.instance, **fields), timeout=2)
        try:
            result = json.loads(raw)
            if result.get('request') != self.request or result.get('instance') != self.instance or not isinstance(result.get('status'), str):
                raise ValueError('response identity')
            return result
        except (ValueError, TypeError, AttributeError) as exc:
            raise LivePipError('PiP 응답을 확인할 수 없습니다') from exc

    def monitors(self):
        state = self.client.query('luminophorelivepip')
        if state.get('instance') != self.instance:
            raise LivePipError('출력 구성이 변경됐습니다')
        outputs = state.get('outputs', [])
        try:
            monitors = [SimpleNamespace(**{k: o[k] for k in ('name','x','y','width','height')}) for o in outputs]
            if not monitors or any(not _TOKEN.fullmatch(m.name) or not all(type(v) in (int,float) and math.isfinite(v) for v in (m.x,m.y,m.width,m.height)) or min(m.width,m.height) <= 0 for m in monitors):
                raise ValueError()
            return monitors
        except (KeyError, TypeError, ValueError):
            raise LivePipError('출력 배치 정보를 확인할 수 없습니다')

    def begin(self):
        if self.call('begin')['status'] != 'ready':
            raise LivePipError('PiP 영역 선택을 시작할 수 없습니다')

    def resolve(self, rect):
        result = self.call('resolve', **dict(zip(('x','y','width','height'), rect)))
        if result['status'] != 'ready' or 'crop' not in result:
            raise LivePipError('한 창 안의 영역을 다시 선택해 주세요. 선택 도중 창이 바뀌었을 수 있습니다.')
        return result['crop']

    def create(self, placement: PipPlacement):
        if self._create_sent:
            return self.call('result')
        self._create_sent = True
        try:
            result = self.call('create', **placement.__dict__)
        except (HyprlandError, LivePipError):
            result = self.call('result')
        if result['status'] != 'applied':
            raise LivePipError('PiP 생성 결과: ' + result['status'])
        return result

    def cancel(self):
        try:
            self.call('cancel')
        except (HyprlandError, LivePipError):
            pass
