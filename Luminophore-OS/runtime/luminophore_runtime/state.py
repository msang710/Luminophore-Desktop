"""Durable generation selection, rollback and process-held GC leases."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import shutil
import tempfile

from . import artifact, host_contract
from .common import (ContractError, atomic_json, fsync_dir, identifier, identity,
                     load, locked)
from .session import Session


HEALTH_CHECKS = {'compositor', 'shell', 'ipc', 'output', 'input', 'lock', 'resume', 'portal'}


def health(evidence, generation, profile, selection_revision):
    if (not isinstance(evidence, dict) or evidence.get('schema') != 1
            or evidence.get('generation') != generation
            or evidence.get('profile') != profile
            or evidence.get('boundary') != 'physical'
            or evidence.get('selection_revision') != selection_revision
            or evidence.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            or set(evidence.get('checks', {})) != HEALTH_CHECKS
            or any(value is not True for value in evidence['checks'].values())):
        raise ContractError('health evidence incomplete, stale or wrong environment')


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.generations = self.root / 'generations'
        self.leases = self.root / 'leases'
        for path in [self.generations, self.leases]:
            if path.is_symlink():
                raise ContractError('store directory must not be a symlink')
            path.mkdir(exist_ok=True)
        self.lock = self.root / '.lock'
        self.state_path = self.root / 'state.json'

    def read(self):
        if not self.state_path.exists():
            return {'schema': 1, 'revision': 0, 'candidate': None, 'selected': None,
                    'previous': None, 'confirmed': None, 'revoked': []}
        state = load(self.state_path)
        if (set(state) != {'schema', 'revision', 'candidate', 'selected', 'previous', 'confirmed', 'revoked'}
                or state['schema'] != 1 or type(state['revision']) is not int or state['revision'] < 0
                or not isinstance(state['revoked'], list)):
            raise ContractError('invalid store state')
        for key in ['candidate', 'selected', 'previous', 'confirmed']:
            if state[key] is not None:
                identifier(state[key])
        for value in state['revoked']:
            identifier(value)
        return state

    def _write(self, state):
        state['revision'] += 1
        atomic_json(self.state_path, state)

    def _cas(self, revision):
        state = self.read()
        if type(revision) is not int or state['revision'] != revision:
            raise ContractError('STALE selection revision')
        return state

    def _release(self, generation, host_root=None):
        target = self.generations / identifier(generation)
        if target.is_symlink():
            raise ContractError('release root must not be a symlink')
        return artifact.verify(target, host_root, expected=generation)

    def compatibility(self, generation, host_root):
        """Reassess an immutable generation without selecting or deleting it."""
        manifest = self._release(generation)
        return host_contract.evaluate(manifest['host_contract'], host_root)

    def compatibility_inventory(self, host_root):
        """Report every generation, including leased and rollback releases."""
        result = {}
        with locked(self.lock):
            for target in sorted(self.generations.iterdir()):
                if target.name.startswith('.'):
                    continue
                identifier(target.name)
                if target.is_symlink() or not target.is_dir():
                    raise ContractError('invalid generation directory')
                result[target.name] = self.compatibility(target.name, host_root)
        return result

    def stage(self, source, trusted_generation):
        """Caller supplies a digest obtained from a separately trusted channel."""
        identifier(trusted_generation)
        artifact.verify(source, expected=trusted_generation)
        with locked(self.lock):
            target = self.generations / trusted_generation
            if not target.exists():
                staging = Path(tempfile.mkdtemp(prefix='.staging-', dir=self.generations))
                try:
                    shutil.copytree(source, staging, dirs_exist_ok=True, symlinks=True)
                    artifact.verify(staging, expected=trusted_generation)
                    for path in staging.rglob('*'):
                        if path.is_file():
                            path.chmod(0o555 if path.stat().st_mode & 0o111 else 0o444)
                            with path.open('rb') as stream:
                                os.fsync(stream.fileno())
                    for path in sorted((p for p in staging.rglob('*') if p.is_dir()), reverse=True):
                        fsync_dir(path)
                    fsync_dir(staging)
                    os.rename(staging, target)
                    fsync_dir(self.generations)
                finally:
                    if staging.exists():
                        shutil.rmtree(staging)
            self._release(trusted_generation)
            self._published(target)
            state = self.read()
            state['candidate'] = trusted_generation
            self._write(state)
        return trusted_generation

    def _published(self, target):
        """Subclass preparation runs under the store lock, before publication."""

    def _eligible(self, state, generation, host_root, config_version):
        if generation in state['revoked']:
            raise ContractError('release is revoked')
        manifest = self._release(generation, host_root)
        if type(config_version) is not int or manifest['compatibility']['config'] != config_version:
            raise ContractError('incompatible config schema')
        artifact.preflight(self.generations / generation)
        return manifest

    def select(self, generation, host_root, revision, *, config_version):
        with locked(self.lock):
            state = self._cas(revision)
            self._eligible(state, generation, host_root, config_version)
            if state['selected'] != generation:
                state['previous'] = state['selected']
                state['selected'] = generation
            state['candidate'] = None
            self._write(state)
            return state

    def rollback(self, host_root, revision, *, config_version):
        with locked(self.lock):
            state = self._cas(revision)
            if not state['previous']:
                raise ContractError('no previous generation')
            self._eligible(state, state['previous'], host_root, config_version)
            state['selected'], state['previous'] = state['previous'], state['selected']
            self._write(state)
            return state

    def confirm(self, generation, evidence, revision, host_root):
        with locked(self.lock):
            state = self._cas(revision)
            if state['selected'] != generation or generation in state['revoked']:
                raise ContractError('health evidence does not target selected release')
            manifest = self._release(generation, host_root)
            artifact.preflight(self.generations / generation)
            health(evidence, generation, identity(manifest['host_contract']), revision)
            state['confirmed'] = generation
            self._write(state)
            return state

    def revoke(self, generation, revision):
        with locked(self.lock):
            state = self._cas(revision)
            identifier(generation)
            if generation not in state['revoked']:
                state['revoked'].append(generation)
            self._write(state)

    @contextmanager
    def session(self, host_root, *, config_version, generation=None):
        fd = None
        try:
            with locked(self.lock):
                state = self.read()
                chosen = generation or state['selected']
                if chosen is None:
                    raise ContractError('no selected release')
                manifest = self._eligible(state, chosen, host_root, config_version)
                fd = os.open(self.leases / chosen, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                fcntl.flock(fd, fcntl.LOCK_SH)
            yield Session(self.generations / chosen, manifest, fd, self.root, host_root)
        finally:
            # Do not LOCK_UN: a child may share this open-file description.
            # Last close releases the lock, including inherited child FDs.
            if fd is not None:
                os.close(fd)

    def gc(self):
        removed = []
        with locked(self.lock):
            state = self.read()
            protected = {state[key] for key in ['candidate', 'selected', 'previous', 'confirmed']}
            for target in sorted(self.generations.iterdir()):
                if target.name.startswith('.') or target.name in protected:
                    continue
                identifier(target.name)
                if target.is_symlink() or not target.is_dir():
                    raise ContractError('invalid generation directory')
                fd = os.open(self.leases / target.name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                try:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    shutil.rmtree(target)
                    removed.append(target.name)
                finally:
                    os.close(fd)
            fsync_dir(self.generations)
        return removed
