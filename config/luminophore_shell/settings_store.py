"""TOML bundle storage for the Luminophore configuration coordinator.

Preparing a generation never changes the completed pointer. Only the coordinator
may publish after runtime participants have confirmed the same generation. This
module implements disk consistency, not a cross-process commit protocol.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import tomllib
from types import MappingProxyType
from typing import Callable, Mapping


FILES = ('settings.toml', 'monitors.toml', 'bindings.toml', 'placement.toml', 'bundles.toml')
MAX_BYTES = 4 * 1024 * 1024
_GENERATION = re.compile(r'[0-9a-f]{64}')


class StoreError(ValueError):
    pass


class ConflictError(StoreError):
    pass


@dataclass(frozen=True)
class SettingsPaths:
    config: Path
    state: Path
    cache: Path

    @classmethod
    def current(cls, environ: Mapping[str, str] | None = None, home: Path | None = None):
        env = os.environ if environ is None else environ
        home = Path.home() if home is None else home
        def root(variable, fallback):
            value = env.get(variable, '')
            path = Path(value) if value else home / fallback
            if not path.is_absolute():
                raise StoreError(f'{variable} must be absolute')
            return path / 'luminophore'
        config = Path(env['LUMINOPHORE_CONFIG_ROOT']) if env.get('LUMINOPHORE_CONFIG_ROOT') else root('XDG_CONFIG_HOME', '.config')
        if not config.is_absolute():
            raise StoreError('LUMINOPHORE_CONFIG_ROOT must be absolute')
        state = Path(env['LUMINOPHORE_SETTINGS_STATE_ROOT']) if env.get('LUMINOPHORE_SETTINGS_STATE_ROOT') else root('XDG_STATE_HOME', '.local/state')
        if not state.is_absolute():
            raise StoreError('LUMINOPHORE_SETTINGS_STATE_ROOT must be absolute')
        return cls(config, state, root('XDG_CACHE_HOME', '.cache'))


def _digest(documents: Mapping[str, str]) -> str:
    # Length-prefixed bytes: boundaries cannot collide and comments survive.
    digest = hashlib.sha256(b'luminophore-settings-v1\0')
    for name in FILES:
        data = documents[name].encode('utf-8')
        digest.update(name.encode() + b'\0' + str(len(data)).encode() + b'\0' + data)
    return digest.hexdigest()


@dataclass(frozen=True)
class Generation:
    id: str
    documents: Mapping[str, str]


def _sync(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic(path: Path, value: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        _sync(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


class SettingsStore:
    def __init__(self, paths: SettingsPaths, validate: Callable[[dict], None], import_documents=None):
        self.paths = paths
        self.validate = validate
        self.import_documents = import_documents or (lambda documents: documents)
        self.epoch = None
        self.root = paths.state / 'settings'
        self.generations = self.root / 'generations'

    def _candidate(self, documents: Mapping[str, str]) -> Generation:
        if set(documents) != set(FILES):
            raise StoreError('a complete five-file TOML bundle is required')
        documents = dict(documents)
        if any(type(text) is not str for text in documents.values()):
            raise StoreError('TOML documents must be text')
        if sum(len(text.encode('utf-8')) for text in documents.values()) > MAX_BYTES:
            raise StoreError('settings bundle exceeds size limit')
        parsed = {}
        for name in FILES:
            try:
                parsed[name] = tomllib.loads(documents[name])
            except tomllib.TOMLDecodeError as error:
                raise StoreError(f'{name}: {error}') from error
            version = parsed[name].get('schema_version')
            if type(version) is not int or version != 1:
                raise StoreError(f'{name}: unsupported schema_version')
        self.validate(parsed)
        return Generation(_digest(documents), MappingProxyType(documents))

    @contextmanager
    def _locked(self):
        self.paths.state.mkdir(parents=True, mode=0o700, exist_ok=True)
        _sync(self.paths.state.parent)
        self.generations.mkdir(parents=True, mode=0o700, exist_ok=True)
        # Persist creation before a pointer can reference any generation.
        _sync(self.generations.parent)
        _sync(self.root.parent)
        with (self.root / 'write.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                if self.epoch is not None and self._read_json('coordinator.json') != {'epoch': self.epoch}:
                    raise ConflictError('coordinator epoch changed')
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read_json(self, name: str) -> dict | None:
        path = self.root / name
        if path.is_symlink():
            raise StoreError(f'invalid {name}')
        if not path.exists():
            return None
        if path.stat().st_size > 4096:
            raise StoreError(f'invalid {name}')
        try:
            value = json.loads(path.read_text())
        except (ValueError, OSError) as error:
            raise StoreError(f'invalid {name}') from error
        if type(value) is not dict:
            raise StoreError(f'invalid {name}')
        return value

    def load(self, generation: str) -> Generation:
        if not isinstance(generation, str) or not _GENERATION.fullmatch(generation):
            raise StoreError('invalid generation')
        directory = self.generations / generation
        if directory.is_symlink():
            raise StoreError('invalid generation directory')
        documents = {}
        try:
            for name in FILES:
                path = directory / name
                if path.is_symlink() or path.stat().st_size > MAX_BYTES:
                    raise StoreError('invalid generation document')
                documents[name] = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as error:
            raise StoreError('generation is incomplete or unreadable') from error
        candidate = self._candidate(documents)
        if candidate.id != generation:
            raise StoreError('generation digest mismatch')
        return candidate

    def current(self) -> Generation | None:
        pointer = self._read_json('completed.json')
        if pointer is None:
            return None
        if set(pointer) != {'generation'}:
            raise StoreError('invalid completed pointer')
        return self.load(pointer['generation'])

    def _expect(self, expected: str) -> None:
        current = self.current()
        if (current.id if current else '') != expected:
            raise ConflictError('completed settings changed')

    def read_candidate(self) -> Generation:
        """Read editable inputs without making them the boot-time authority."""
        try:
            def read():
                documents = {}
                for name in FILES:
                    path = self.paths.config / name
                    if path.stat().st_size > MAX_BYTES:
                        raise StoreError('settings document exceeds size limit')
                    documents[name] = path.read_text(encoding='utf-8')
                return documents
            documents = read()
            candidate = self._candidate(self.import_documents(documents))
            if read() != documents:
                raise ConflictError('editable settings changed while reading')
            return candidate
        except (OSError, UnicodeError) as error:
            raise StoreError('cannot read settings candidate') from error

    def prepare(self, documents: Mapping[str, str], expected: str) -> Generation:
        candidate = self._candidate(documents)
        with self._locked():
            self._expect(expected)
            pending = self._read_json('pending.json')
            request = {'base': expected, 'generation': candidate.id}
            if pending is not None and pending != request:
                raise ConflictError('another settings candidate is pending')
            destination = self.generations / candidate.id
            if destination.exists():
                self.load(candidate.id)
            else:
                temporary = Path(tempfile.mkdtemp(prefix='.candidate-', dir=self.generations))
                try:
                    for name, text in candidate.documents.items():
                        with (temporary / name).open('w', encoding='utf-8', newline='') as stream:
                            stream.write(text)
                            stream.flush()
                            os.fsync(stream.fileno())
                    _sync(temporary)
                    os.rename(temporary, destination)
                    _sync(self.generations)
                finally:
                    if temporary.exists():
                        shutil.rmtree(temporary)
            _atomic(self.root / 'pending.json', request)
        return candidate

    def publish(self, generation: str, expected: str) -> Generation:
        """Coordinator calls only after all runtime acknowledgements.

        If an I/O exception follows rename, query current() to resolve the
        outcome. Do not automatically repeat runtime application.
        """
        with self._locked():
            candidate = self.load(generation)
            current = self.current()
            if current is not None and current.id == generation:
                # A repeated completion must never clear a newer candidate.
                if self._read_json('pending.json') == {'base': expected, 'generation': generation}:
                    (self.root / 'pending.json').unlink()
                    _sync(self.root)
                return current
            self._expect(expected)
            if self._read_json('pending.json') != {'base': expected, 'generation': generation}:
                raise ConflictError('candidate was not prepared against this generation')
            _atomic(self.root / 'completed.json', {'generation': generation})
            (self.root / 'pending.json').unlink(missing_ok=True)
            _sync(self.root)
            return candidate

    def abandon(self, generation: str, *, expected: str | None = None) -> None:
        with self._locked():
            if expected is not None:
                self._expect(expected)
            pending = self._read_json('pending.json')
            if pending is not None:
                if pending.get('generation') != generation or (expected is not None and pending.get('base') != expected):
                    raise ConflictError('different candidate is pending')
                (self.root / 'pending.json').unlink()
                _sync(self.root)

    def recover(self) -> Generation | None:
        """Cold-start recovery, before starting participants, discards intent."""
        with self._locked():
            completed = self.current()
            (self.root / 'pending.json').unlink(missing_ok=True)
            _sync(self.root)
            return completed

    def bind_epoch(self, epoch):
        if not isinstance(epoch, str) or len(epoch) != 32 or any(c not in '0123456789abcdef' for c in epoch):
            raise StoreError('invalid coordinator epoch')
        self.epoch = epoch

    def record_request(self, request, candidate):
        with self._locked():
            self._expect(request['expected_digest'])
            _atomic(self.root / 'request.json', {
                'request_id': request['request_id'], 'expected_digest': request['expected_digest'],
                'candidate': candidate, 'changes': request['changes']})

    def recovery_request(self):
        value = self._read_json('request.json')
        if value is None: return None
        if (set(value) != {'request_id', 'expected_digest', 'candidate', 'changes'}
                or not isinstance(value['request_id'], str) or not value['request_id']
                or not isinstance(value['changes'], dict)
                or any(not isinstance(value[k], str) or len(value[k]) != 64
                       or any(c not in '0123456789abcdef' for c in value[k])
                       for k in ('expected_digest', 'candidate'))):
            raise StoreError('invalid recovery request')
        return value
