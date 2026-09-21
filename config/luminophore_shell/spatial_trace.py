"""Opt-in, bounded spatial IPC trace. Never records titles, classes or raw payloads."""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import select
import socket
import time

NUMBERS = set('generation revision topologyRevision output targetOutput targetEpoch expectedRevision actualRevision expectedTopology actualTopology x y board columns rows model topology'.split())
FLAGS = set('applied visible floating active committed shellLost targetLost'.split())
TEXT = {
    'phase': {'begin', 'update', 'end', 'cancel'},
    'action': {'focus', 'window-move', 'view-move', 'view-resize', 'desktop', 'wide'},
    'direction': {'left', 'right', 'up', 'down'},
    'mode': {'floating', 'tiled'},
    'status': {'applied', 'no-change', 'no-capacity', 'stale-revision', 'stale-topology', 'invalid-command'},
}
ARRAYS = {'pointer', 'fromPoint', 'toPoint', 'fromRect', 'toRect'}
EVENTS = {'luminophoredirectgrab', 'luminophorespatialgrab', 'luminophorespatialgrabdiagnostic', 'luminophorespatialaction'}


class Sanitizer:
    def __init__(self):
        self.windows = {}

    def clean(self, data):
        if not isinstance(data, dict):
            return {}
        result = {}
        for key, value in data.items():
            if key in NUMBERS and type(value) is int:
                result[key] = value
            elif key in FLAGS and type(value) is bool:
                result[key] = value
            elif key in TEXT and isinstance(value, str) and value in TEXT[key]:
                result[key] = value
            elif key in {'window', 'address'} and isinstance(value, str):
                identity = value.lower()
                if identity not in {'', '0x0'}:
                    result[key] = self.windows.setdefault(identity, f'w{len(self.windows)+1}')
            elif key in ARRAYS and isinstance(value, list) and len(value) in {2, 4} and all(type(n) in {int, float} and math.isfinite(n) for n in value):
                result[key] = value
            elif key in {'result', 'committedRevision'} and isinstance(value, dict):
                result[key] = self.clean(value)
            elif key in {'windows', 'outputViews'} and isinstance(value, list):
                result[key] = [self.clean(item) for item in value if isinstance(item, dict)]
        return result


def snapshot(directory):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(0.5)
        connection.connect(str(directory / '.socket.sock'))
        connection.sendall(b'j/luminophorespatialstate2')
        data = bytearray()
        deadline = time.monotonic() + 0.5
        while True:
            connection.settimeout(max(.001, deadline-time.monotonic()))
            block = connection.recv(65536)
            if not block:
                return json.loads(data)
            data.extend(block)
            if len(data) > 4*1024*1024 or time.monotonic() >= deadline:
                raise ValueError('snapshot limit')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=300)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 1800:
        parser.error('--seconds must be between 1 and 1800')
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'hypr'
    started = time.monotonic()
    deadline = started + args.seconds
    # Exclusive creation prevents truncating old evidence or following symlinks.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as log:
        count = 0
        def emit(kind, data):
            nonlocal count
            log.write(json.dumps({'t': round(time.monotonic()-started, 4), 'kind': kind, **data})+'\n')
            log.flush()
            count += 1
        emit('start', {'seconds': args.seconds})
        print('READY: waiting for compositor; Ctrl+C stops capture', flush=True)
        try:
            while time.monotonic() < deadline and count < 20000:
                paths = sorted(runtime.glob('*/.socket2.sock'), key=lambda p: p.stat().st_mtime, reverse=True)
                connection = None
                for path in paths:
                    candidate = socket.socket(socket.AF_UNIX)
                    candidate.settimeout(.5)
                    try:
                        candidate.connect(str(path))
                        connection, directory = candidate, path.parent
                        break
                    except OSError:
                        candidate.close()
                if connection is None:
                    time.sleep(.5)
                    continue
                sanitizer = Sanitizer()
                emit('session', {})
                buffer = b''
                pending, last_query, previous = True, 0., None
                with connection:
                    while time.monotonic() < deadline and count < 20000:
                        if pending and time.monotonic()-last_query >= .2:
                            last_query, pending = time.monotonic(), False
                            try:
                                state = sanitizer.clean(snapshot(directory))
                                if state != previous:
                                    emit('state', state)
                                    previous = state
                            except (OSError, ValueError):
                                emit('state-unavailable', {})
                        if not select.select([connection], [], [], .1)[0]:
                            continue
                        try:
                            block = connection.recv(65536)
                        except OSError:
                            break
                        if not block:
                            break
                        buffer += block
                        if len(buffer) > 4*1024*1024:
                            emit('buffer-limit', {})
                            break
                        while b'\n' in buffer:
                            line, buffer = buffer.split(b'\n', 1)
                            name, _, payload = line.partition(b'>>')
                            name = name.decode('ascii', errors='replace')
                            if name in EVENTS:
                                try:
                                    emit(name, sanitizer.clean(json.loads(payload)))
                                except ValueError:
                                    emit('invalid-event', {})
                                pending = True
                            elif name == 'luminophorespatial':
                                pending = True
                emit('disconnected', {})
        except KeyboardInterrupt:
            pass
        finally:
            emit('stop', {})


if __name__ == '__main__':
    main()
