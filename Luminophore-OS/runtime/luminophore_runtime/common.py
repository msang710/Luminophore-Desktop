"""Bounded input, path containment and durable file operations."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile


class ContractError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def identity(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ContractError('invalid generation identifier')
    return value


def relative(value):
    if not isinstance(value, str) or not value or '\x00' in value or '\\' in value:
        raise ContractError('invalid relative path')
    p = PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or str(p) != value or value == '.':
        raise ContractError('path must be canonical and relative: ' + value)
    return value


def within(root, name, *, regular=True):
    root = Path(root).resolve(strict=True)
    path = root / relative(name)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ContractError('path escapes root: ' + name)
    if regular and not resolved.is_file():
        raise ContractError('not a regular file: ' + name)
    return resolved


def load(path, *, expected=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ContractError('JSON input must be a bounded regular file')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ContractError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    try:
        # Hash and decode the same bytes; a caller may bind this read to a
        # manifest verified earlier without a second, racy file read.
        data = path.read_bytes()
        if len(data) > 16 * 1024 * 1024:
            raise ContractError('JSON input exceeds size limit')
        if expected is not None and hashlib.sha256(data).hexdigest() != identifier(expected):
            raise ContractError('JSON digest differs from verified manifest')
        value = json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as error:
        raise ContractError('invalid JSON') from error
    if not isinstance(value, dict):
        raise ContractError('JSON object required')
    return value


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value, *, mode=0o600):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def locked(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def files(root):
    """Enumerate only ordinary files. Artifact trees never contain symlinks."""
    result = {}
    for path in sorted(Path(root).rglob('*')):
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ContractError('non-regular artifact member: ' + str(path))
        result[path.relative_to(root).as_posix()] = {
            'sha256': digest(path), 'executable': bool(mode & 0o111)}
    return result
