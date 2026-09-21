"""Single-user graphical-session ownership, separate from release selection.

The environment file is private to Luminophore units. Only public display
variables enter the shared D-Bus activation environment.
"""
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import tempfile
import re

from .common import ContractError, atomic_json, locked, digest, load
from .session import application_environment, owner_alive, process_token


TARGET = 'luminophore-session.target'
PUBLIC = ('WAYLAND_DISPLAY', 'DISPLAY', 'XDG_CURRENT_DESKTOP', 'XDG_SESSION_TYPE',
          'XDG_SESSION_DESKTOP', 'LUMINOPHORE_INSTANCE_SIGNATURE', 'XMODIFIERS', 'QT_IM_MODULE')
PRIVATE = ('LUMINOPHORE_RELEASE_ID', 'LUMINOPHORE_RELEASE_ROOT', 'LUMINOPHORE_SESSION_TOKEN',
           'LUMINOPHORE_SESSION_OWNER', 'LUMINOPHORE_STORE', 'LUMINOPHORE_CONFIG_ROOT',
           'LUMINOPHORE_SETTINGS_STATE_ROOT', 'LUMINOPHORE_SETTINGS_RECOVERY',
           'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME')


def _active_autostart(environment, runner):
    rows = _run(runner, ['/usr/bin/systemctl', '--user', 'list-units', '--type=service',
                         '--state=active', '--plain', '--no-legend',
                         'app-*@autostart.service'], environment)
    units = set()
    for row in rows.splitlines():
        name = row.split(None, 1)[0] if row.split() else ''
        if re.fullmatch(r'app-[A-Za-z0-9_.:@\\x2d]+@autostart\.service', name):
            units.add(name)
    return units


def _stop_autostart(environment, runner, baseline=()):
    # Stop only generated units that became active during this Luminophore
    # session. Units already active before entry may belong to another user
    # service and are never selected by a wildcard stop.
    started = sorted(_active_autostart(environment, runner) - set(baseline))
    _run(runner, ['/usr/bin/systemctl', '--user', 'stop', *started,
                  'xdg-desktop-autostart.target'], environment)


def runtime_root(environment):
    base = Path(environment.get('XDG_RUNTIME_DIR', ''))
    if not base.is_absolute() or base.is_symlink():
        raise ContractError('absolute owned XDG_RUNTIME_DIR required')
    info = base.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ContractError('unsafe XDG_RUNTIME_DIR permissions')
    root = base / 'luminophore'
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ContractError('unsafe Luminophore runtime directory')
    return root


def _run(runner, argv, environment):
    result = runner(argv, env=application_environment(environment),
                    capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise ContractError('session command failed: ' + argv[0] + ': ' + result.stderr.strip())
    return result.stdout


def _no_other_graphical_session(environment, runner):
    rows = _run(runner, ['/usr/bin/loginctl', 'show-user', str(os.getuid()), '-p', 'Sessions'], environment)
    sessions = dict(line.split('=', 1) for line in rows.splitlines() if '=' in line).get('Sessions')
    if sessions is None:
        raise ContractError('logind session inventory unavailable')
    for session in sessions.split():
        if session == environment.get('XDG_SESSION_ID'):
            continue
        rows = _run(runner, ['/usr/bin/loginctl', 'show-session', session,
                            '-p', 'Type', '-p', 'State', '-p', 'User'], environment)
        values = dict(line.split('=', 1) for line in rows.splitlines() if '=' in line)
        if values.get('User') != str(os.getuid()):
            raise ContractError('logind session owner mismatch')
        if values.get('Type') in {'wayland', 'x11'} and values.get('State') != 'closing':
            raise ContractError('another graphical session for this user is running: ' + session)


def _record(root):
    path = root / 'session.json'
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ContractError('unsafe session record')
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not data.get('owner') or not owner_alive(data['owner']):
        raise ContractError('session owner is no longer alive')
    return data


def ready(environment=None, *, runner=subprocess.run):
    environment = os.environ if environment is None else environment
    root = runtime_root(environment)
    with locked(root / 'record.lock'):
        record = _record(root)
        if (record['token'] != environment.get('LUMINOPHORE_SESSION_TOKEN')
                or record['generation'] != environment.get('LUMINOPHORE_RELEASE_ID')):
            raise ContractError('stale session readiness token')
        signature = environment.get('LUMINOPHORE_INSTANCE_SIGNATURE', '')
        if (not signature or signature in {'.', '..'} or '/' in signature or '\\' in signature
                or not (root / signature).is_dir() or (root / signature).is_symlink()):
            raise ContractError('invalid compositor instance')
        if not environment.get('WAYLAND_DISPLAY'):
            raise ContractError('Wayland display is not ready')
        if record.get('instance') and record['instance'] != signature:
            _stop_autostart(environment, runner, record.get('autostart_baseline', ()))
            _run(runner, ['/usr/bin/systemctl', '--user', 'stop', TARGET], environment)
        record['instance'] = signature
        record['config_root'] = environment.get('LUMINOPHORE_CONFIG_ROOT')
        record['settings_environment'] = {key: environment[key] for key in (
            'LUMINOPHORE_SETTINGS_STATE_ROOT', 'LUMINOPHORE_SETTINGS_RECOVERY',
            'XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'XDG_CACHE_HOME') if key in environment}
        atomic_json(root / 'session.json', record)
        values = {key: environment[key] for key in (*PUBLIC, *PRIVATE) if key in environment}
        if any(any(ord(c) < 32 for c in value) for value in values.values()):
            raise ContractError('invalid session environment value')
        # JSON strings are deliberately not shell input. systemd EnvironmentFile
        # understands double quotes and escaped backslash/double-quote values.
        text = ''.join(key + '=' + json.dumps(value, ensure_ascii=False) + '\n'
                       for key, value in sorted(values.items()))
        path = root / 'session.env'
        temporary = root / ('.environment-' + secrets.token_hex(12))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        # Pass assignments, since application_environment intentionally removes
        # private variables. No private Python/loader paths reach user services.
        _run(runner, ['/usr/bin/dbus-update-activation-environment', '--systemd',
                      *[key + '=' + values[key] for key in PUBLIC if key in values]], environment)
        _run(runner, ['/usr/bin/systemctl', '--user', 'daemon-reload'], environment)
        _run(runner, ['/usr/bin/systemctl', '--user', 'start', TARGET], environment)
        # Generic portals cache desktop selection; this UID has no other graphical session.
        _run(runner, ['/usr/bin/systemctl', '--user', 'restart',
                      'xdg-desktop-portal-gtk.service', 'xdg-desktop-portal.service'], environment)


def check(environment=None, *, public_control=False):
    environment = os.environ if environment is None else environment
    record = _record(runtime_root(environment))
    if public_control:
        if not record.get('instance') or record['instance'] != environment.get('LUMINOPHORE_INSTANCE_SIGNATURE'):
            raise ContractError('stale public compositor instance')
        return record
    if (record['token'] != environment.get('LUMINOPHORE_SESSION_TOKEN')
            or record['generation'] != environment.get('LUMINOPHORE_RELEASE_ID')):
        raise ContractError('stale session environment')
    return record


# These are user values or explicit overrides. Every other file below config/
# is immutable release implementation and must never be sourced from a mutable
# user copy.
def prepare_config(release, environment, *, config_version=1):
    base = Path(environment.get('XDG_CONFIG_HOME') or (Path(environment['HOME']) / '.config'))
    target = base / 'luminophore'
    if not base.is_absolute() or target.is_symlink() or (target.exists() and not target.is_dir()):
        raise ContractError('absolute real config directory required')
    if config_version != 1:
        raise ContractError('unsupported native config schema')
    return target


def compile_user_config(session, environment, *, runner=subprocess.run):
    """Initialize/import a five-file TOML bundle with the release's Python."""
    root = session.root
    target = Path(environment['LUMINOPHORE_CONFIG_ROOT'])
    if not (root / 'shell/luminophore_shell/settings_bootstrap.py').is_file():
        raise ContractError('release has no native settings bootstrap')
    env = application_environment(environment)
    env.update({key: str(root / value) for key, value in session.manifest['runtime_env'].items()})
    env['PYTHONPATH'] = str(root / 'shell')
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTHONNOUSERSITE'] = '1'
    result = runner([str(root / session.manifest['entries']['shell']['path']), '-B', '-s',
                     '-m', 'luminophore_shell.settings_bootstrap', '--target', str(target),
                     '--defaults', str(root/'config/desktop')], env=env, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ContractError('native settings initialization failed: ' + result.stderr.strip())


class Lifecycle:
    def __init__(self, environment, *, runner=subprocess.run):
        self.source = dict(environment)
        self.runner = runner
        self.fd = None
        self.environment = {}

    def __enter__(self):
        self.root = runtime_root(self.source)
        self.record_path = self.root / 'session.json'
        self.environment_path = self.root / 'session.env'
        self.fd = os.open(self.root / 'session.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ContractError('another Luminophore session is running') from error
            _no_other_graphical_session(self.source, self.runner)
            autostart_baseline = sorted(_active_autostart(self.source, self.runner))
            token = secrets.token_hex(32)
            owner = process_token(os.getpid())
            self.environment = {'LUMINOPHORE_SESSION_TOKEN': token, 'LUMINOPHORE_SESSION_OWNER': owner}
            with locked(self.root / 'record.lock'):
                # Any prior record is stale: the lifetime lock is now ours.
                if self.record_path.exists():
                    _stop_autostart(self.source, self.runner, autostart_baseline)
                    _run(self.runner, ['/usr/bin/systemctl', '--user', 'stop', TARGET], self.source)
                self.environment_path.unlink(missing_ok=True)
                atomic_json(self.record_path, {'token': token, 'owner': owner,
                            'generation': self.source['LUMINOPHORE_RELEASE_ID'],
                            'autostart_baseline': autostart_baseline})
            return self
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    def __exit__(self, exc_type, exc, traceback):
        try:
            with locked(self.root / 'record.lock'):
                record = _record(self.root)
                if record['token'] != self.environment['LUMINOPHORE_SESSION_TOKEN']:
                    raise ContractError('session ownership changed during cleanup')
                if self.environment_path.exists():
                    _stop_autostart(self.source, self.runner, record.get('autostart_baseline', ()))
                    _run(self.runner, ['/usr/bin/systemctl', '--user', 'stop', TARGET], self.source)
                    _run(self.runner, ['/usr/bin/systemctl', '--user', 'stop',
                                       'xdg-desktop-portal.service', 'xdg-desktop-portal-gtk.service'], self.source)
                    _run(self.runner, ['/usr/bin/systemctl', '--user', 'unset-environment', *PUBLIC], self.source)
                    _run(self.runner, ['/usr/bin/dbus-update-activation-environment',
                                      *[key + '=' for key in PUBLIC]], self.source)
                self.environment_path.unlink(missing_ok=True)
                self.record_path.unlink()
        finally:
            os.close(self.fd)
            self.fd = None
