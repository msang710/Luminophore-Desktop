"""Root-owned release storage with read-only, process-held user leases."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat

from .common import ContractError, atomic_json, identifier, identity, load
from .session import Session, owner_alive
from .state import Store


def trusted(path, *, directory=False):
    info = Path(path).lstat()
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not correct_type or info.st_uid != 0 or info.st_mode & 0o022:
        raise ContractError('installed store requires root-owned, non-writable paths: ' + str(path))


class InstalledStore(Store):
    def __init__(self, root):
        raw = Path(root)
        if not raw.is_absolute() or raw.is_symlink() or raw.resolve(strict=True) != raw:
            raise ContractError('canonical installed store path required')
        self.root = raw
        self.generations = raw / 'generations'
        self.leases = raw / 'leases'
        self.lock = raw / '.lock'
        self.state_path = raw / 'state.json'
        for path in (raw, self.generations, self.leases):
            trusted(path, directory=True)
        trusted(self.lock)

    @classmethod
    def initialize(cls, root):
        if os.geteuid() != 0:
            raise ContractError('root is required to initialize the installed store')
        root = Path(root)
        if not root.is_absolute() or root.is_symlink():
            raise ContractError('absolute non-symlink installed store required')
        trusted(root.parent, directory=True)
        root.mkdir(mode=0o755, exist_ok=True)
        trusted(root, directory=True)
        for path in (root / 'generations', root / 'leases'):
            path.mkdir(mode=0o755, exist_ok=True)
            trusted(path, directory=True)
        fd = os.open(root / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644)
        os.close(fd)
        return cls(root)

    def read(self):
        if self.state_path.exists():
            trusted(self.state_path)
        return super().read()

    def _write(self, state):
        if os.geteuid() != 0:
            raise ContractError('root is required to change installed selection')
        state['revision'] += 1
        atomic_json(self.state_path, state, mode=0o644)

    def _release(self, generation, host_root=None):
        trusted(self.generations / identifier(generation), directory=True)
        return super()._release(generation, host_root)

    def _eligible(self, state, generation, host_root, config_version):
        marker = self.root / 'removing.json'
        if marker.exists():
            trusted(marker)
            if owner_alive(load(marker)['owner']):
                raise ContractError('package removal is in progress')
        return super()._eligible(state, generation, host_root, config_version)

    def stage(self, source, trusted_generation):
        if os.geteuid() != 0:
            raise ContractError('root is required to stage an installed release')
        return super().stage(source, trusted_generation)

    def _published(self, target):
        for path in (target, *[p for p in target.rglob('*') if p.is_dir()]):
            path.chmod(0o755)
        fd = os.open(self.leases / target.name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644)
        os.close(fd)
        trusted(self.leases / target.name)

    @contextmanager
    def interactive_session(self, record):
        """Borrow an admitted live generation without repeating its full scan.

        Only public control clients use this path. Root-owned immutable release
        metadata, a live owner and an already-held lease must all agree. Admission,
        activation, services and package updates still perform full verification.
        """
        generation = identifier(record.get('generation'))
        if not record.get('owner') or not owner_alive(record['owner']):
            raise ContractError('interactive session owner is no longer alive')
        lock_fd = os.open(self.lock, os.O_RDONLY | os.O_NOFOLLOW)
        lease_fd = None
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_SH)
            state = self.read()
            if generation in state['revoked']:
                raise ContractError('release is revoked')
            marker = self.root / 'removing.json'
            if marker.exists():
                trusted(marker)
                if owner_alive(load(marker)['owner']):
                    raise ContractError('package removal is in progress')
            root = self.generations / generation
            trusted(root, directory=True)
            trusted(root / 'release.json')
            manifest = load(root / 'release.json')
            if manifest.get('generation') != generation or identity({k: v for k, v in manifest.items() if k != 'generation'}) != generation:
                raise ContractError('interactive generation metadata mismatch')
            trusted(self.leases / generation)
            lease_fd = os.open(self.leases / generation, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                fcntl.flock(lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                raise ContractError('interactive generation has no active session lease')
            fcntl.flock(lease_fd, fcntl.LOCK_SH)
            if not owner_alive(record['owner']):
                raise ContractError('interactive session ended during admission')
        except BaseException:
            if lease_fd is not None:
                os.close(lease_fd)
            raise
        finally:
            os.close(lock_fd)
        try:
            session = Session(root, manifest, lease_fd, self.root, Path('/'), system_store=True)
            session.interactive = True
            yield session
        finally:
            os.close(lease_fd)

    @contextmanager
    def session(self, host_root, *, config_version, generation=None):
        lease_fd = None
        lock_fd = os.open(self.lock, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_SH)
            state = self.read()
            chosen = generation or state['selected']
            if chosen is None:
                raise ContractError('no selected release')
            manifest = self._eligible(state, chosen, host_root, config_version)
            trusted(self.leases / chosen)
            lease_fd = os.open(self.leases / chosen, os.O_RDONLY | os.O_NOFOLLOW)
            fcntl.flock(lease_fd, fcntl.LOCK_SH)
        except BaseException:
            if lease_fd is not None:
                os.close(lease_fd)
            raise
        finally:
            os.close(lock_fd)
        try:
            yield Session(self.generations / chosen, manifest, lease_fd, self.root, host_root, system_store=True)
        finally:
            os.close(lease_fd)
