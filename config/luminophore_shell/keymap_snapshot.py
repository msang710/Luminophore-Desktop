"""Import keymaps once; immutable generations never reopen the source file."""
import ctypes
import json
import os
from pathlib import Path
import re
import stat
import tomllib
from copy import deepcopy
from .settings_store import StoreError

LIMIT = 1024 * 1024


def compile_keymap(text, *, includes=True):
    if type(text) is not str or '\0' in text or len(text.encode('utf-8')) > LIMIT:
        raise StoreError('invalid keymap text')
    lib = ctypes.CDLL('libxkbcommon.so.0')
    ptr = ctypes.c_void_p
    lib.xkb_context_new.argtypes = [ctypes.c_int]; lib.xkb_context_new.restype = ptr
    lib.xkb_context_unref.argtypes = [ptr]
    lib.xkb_keymap_new_from_string.argtypes = [ptr, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    lib.xkb_keymap_new_from_string.restype = ptr
    lib.xkb_keymap_get_as_string.argtypes = [ptr, ctypes.c_int]; lib.xkb_keymap_get_as_string.restype = ptr
    lib.xkb_keymap_unref.argtypes = [ptr]
    libc = ctypes.CDLL(None); libc.free.argtypes = [ptr]
    context = lib.xkb_context_new(0 if includes else 1)
    if not context: raise StoreError('keymap context unavailable')
    keymap = buffer = None
    try:
        keymap = lib.xkb_keymap_new_from_string(context, text.encode('utf-8'), 2, 0)
        if not keymap: raise StoreError('keymap compilation failed')
        buffer = lib.xkb_keymap_get_as_string(keymap, 2)
        if not buffer: raise StoreError('keymap serialization failed')
        result = ctypes.string_at(buffer).decode('utf-8')
        if len(result.encode('utf-8')) > LIMIT: raise StoreError('compiled keymap too large')
        return result
    finally:
        if buffer: libc.free(buffer)
        if keymap: lib.xkb_keymap_unref(keymap)
        lib.xkb_context_unref(context)


def snapshot_text(path, snapshot):
    if type(path) is not str or type(snapshot) is not str:
        raise StoreError('keymap file and snapshot must be text')
    if not path:
        if snapshot: raise StoreError('keymap snapshot without file')
        return ''
    if not os.path.isabs(path) or len(path.encode('utf-8')) > 4096 or any(ord(c) < 32 for c in path):
        raise StoreError('keymap file must be an absolute path without control characters')
    if not snapshot.startswith(path + '\n'):
        raise StoreError('keymap file must be imported before applying')
    text = snapshot[len(path) + 1:]
    if not text or '\0' in text or len(text.encode('utf-8')) > LIMIT:
        raise StoreError('invalid keymap snapshot')
    return text


def validate_keymaps(raw, devices):
    base = {k: raw.get(k, '') for k in ('kb_file', 'kb_snapshot')}
    for values in [base] + [{**base, **v} for v in devices.values()]:
        snapshot_text(values['kb_file'], values['kb_snapshot'])


def read_keymap(path, root):
    if type(path) is not str or len(path.encode('utf-8')) > 4096 or any(ord(c) < 32 for c in path):
        raise StoreError('invalid keymap source path')
    source = Path(path).expanduser()
    if not source.is_absolute(): source = root / source
    source = source.absolute()
    try:
        # Do not block on FIFO/device inputs. Read one opened regular file.
        fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): raise StoreError('keymap must be a regular file')
            data = stream.read(LIMIT + 1)
        if len(data) > LIMIT: raise StoreError('keymap file too large')
        compiled = compile_keymap(data.decode('utf-8'))
    except (OSError, UnicodeError) as error:
        raise StoreError('cannot import keymap file') from error
    path = str(source)
    value = path + '\n' + compiled
    snapshot_text(path, value)
    return path, value


def _table(text, section, values):
    """Rewrite scalar fields only when a parse proves unrelated values survived."""
    before = tomllib.loads(text); expected = deepcopy(before)
    target = expected
    for key in section: target = target.setdefault(key, {})
    target.update(values)
    # Replace the structured root with semantic comparison, including inline
    # device maps produced by generation edits. Device names are literal keys.
    from .toml_edit import patch
    return patch(text, section[0], expected[section[0]])


def import_keymaps(documents, root, *, refresh=()):
    result=dict(documents); text=result['settings.toml']; raw=tomllib.loads(text)
    tables=[(('input',),raw.get('input',{}))]+[(('devices',name),v) for name,v in raw.get('devices',{}).items()]
    for section,values in tables:
        if 'kb_file' not in values: continue
        path=values['kb_file']
        if type(path) is not str: raise StoreError('keymap file must be text')
        snapshot=values.get('kb_snapshot','')
        if path and (section in refresh or not isinstance(snapshot,str) or not snapshot.startswith(path+'\n')):
            path,snapshot=read_keymap(path,root)
        elif not path: snapshot=''
        else: continue
        text=_table(text,section,{'kb_file':path,'kb_snapshot':snapshot})
    result['settings.toml']=text
    return result
