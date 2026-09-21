"""Explicit greeter trial with a boot-persistent, session-independent rollback.

The guard is copied to root-owned state so recovery does not depend on a
candidate release, its Python runtime, or the terminal that started the trial.
Only an explicit confirmation cancels recovery; mapping a window is insufficient.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib


def systemctl(*args):
    subprocess.run(['/usr/bin/systemctl', *args], check=True, timeout=45)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, data, mode=0o644):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Trial:
    timer = 'luminophore-greeter-trial-recovery.timer'
    service = 'luminophore-greeter-trial-recovery.service'

    def __init__(self, root=Path('/'), control=systemctl):
        self.root = Path(root)
        self.control = control
        self.config = self.root / 'etc/greetd/config.toml'
        self.candidate = self.root / 'usr/lib/luminophore/greetd/luminophore.toml'
        self.state = self.root / 'var/lib/luminophore/greeter-trial'
        self.pending = self.state / 'pending.toml'
        self.units = self.root / 'etc/systemd/system'

    @contextmanager
    def locked(self):
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.state / 'lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def start(self):
        with self.locked():
            if self.pending.exists():
                raise RuntimeError('greeter trial already pending; restore or confirm it first')
            original = self.config.read_bytes()
            candidate = self.candidate.read_bytes()
            for content in (original, candidate):
                parsed = tomllib.loads(content.decode())
                session = parsed.get('default_session', {})
                if not session.get('command') or not session.get('user'):
                    raise RuntimeError('invalid greeter session configuration')
            if original == candidate:
                raise RuntimeError('candidate already active; no independent fallback available')
            atomic_write(self.state / 'guard.py', Path(__file__).read_bytes(), 0o600)
            atomic_write(self.pending, original, 0o600)
            self.units.mkdir(parents=True, exist_ok=True)
            service = '''[Unit]
Description=Restore the previous login configuration after an unconfirmed Luminophore trial
ConditionPathExists=/var/lib/luminophore/greeter-trial/pending.toml
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I /var/lib/luminophore/greeter-trial/guard.py restore
Restart=on-failure
RestartSec=5
'''
            timer = f'''[Unit]
Description=Bound the unconfirmed Luminophore greeter trial
[Timer]
OnActiveSec=120
AccuracySec=1
Unit={self.service}
[Install]
WantedBy=timers.target
'''
            atomic_write(self.units / self.service, service.encode())
            atomic_write(self.units / self.timer, timer.encode())
            self.control('daemon-reload')
            self.control('enable', '--now', self.timer)
            self.control('is-active', '--quiet', self.timer)
            # A caller killed from this point onward cannot cancel the guard.
            atomic_write(self.config, candidate)
            self.control('restart', 'greetd.service')

    def restore(self):
        with self.locked():
            if not self.pending.exists():
                return
            atomic_write(self.config, self.pending.read_bytes())
            self.control('restart', 'greetd.service')
            self.pending.unlink()
            sync_directory(self.state)
            self.control('disable', '--now', self.timer)

    def confirm(self):
        with self.locked():
            if not self.pending.exists():
                raise RuntimeError('no pending greeter trial')
            if self.config.read_bytes() != self.candidate.read_bytes():
                raise RuntimeError('candidate is no longer active')
            # Recovery and confirmation serialize on the same lock.
            self.control('disable', '--now', self.timer)
            self.pending.unlink()
            sync_directory(self.state)


def main(action):
    if os.geteuid() != 0:
        raise PermissionError('greeter trial requires administrator authentication')
    if action not in {'start', 'restore', 'confirm'}:
        raise ValueError('invalid greeter trial action')
    getattr(Trial(), action)()
    return 0


if __name__ == '__main__':
    import sys
    raise SystemExit(main(sys.argv[1] if len(sys.argv) == 2 else ''))
