import importlib.util
import os
from pathlib import Path
import subprocess

from test_artifact import ArtifactFixture


class InstalledStoreTests(ArtifactFixture):
    def test_root_store_uses_read_only_session_lease_and_rejects_writable_state(self):
        self.assertIsNotNone(importlib.util.find_spec('luminophore_runtime.installed'),
                             'installed store implementation required')
        self.recipe["entries"]["shell_cli"] = {"path": "bin/demo", "args": []}
        release = self.pack()
        code = '''
import os,sys
from pathlib import Path
from luminophore_runtime.installed import InstalledStore
from luminophore_runtime.common import ContractError
release, root, host = map(Path, sys.argv[1:])
store = InstalledStore.initialize(root)
store.stage(release, release.name)
store.select(release.name, host, store.read()['revision'], config_version=1)
assert store.state_path.stat().st_mode & 0o777 == 0o644
assert (store.leases / release.name).stat().st_mode & 0o777 == 0o644
with store.session(host, config_version=1) as session:
    import fcntl
    assert fcntl.fcntl(session.lease_fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY
    _, env = session.command('shell', environment={})
    assert '--system-store' in env['LUMINOPHORE_SHELL_COMMAND']
    assert not store.gc()
    from luminophore_runtime.session import process_token
    from unittest.mock import patch
    record = {'generation': release.name, 'owner': process_token(os.getpid())}
    with patch('luminophore_runtime.artifact.verify', side_effect=AssertionError('interactive full verify')), patch('luminophore_runtime.artifact.preflight', side_effect=AssertionError('interactive preflight')):
        with store.interactive_session(record) as fast:
            assert fast.root == session.root and fast.interactive
            fast.command('control', ['-j', 'monitors'], environment={})
            for command in ('ctl', 'launch', 'launch-bundle', 'capture', 'restart', 'validate-config'):
                fast.command('shell_cli', [command], environment={})
            for component, args in [('shell', []), ('compositor', []), ('settings', []),
                                    ('shell_cli', ['daemon']), ('shell_cli', ['settings']), ('shell_cli', [])]:
                try:
                    fast.command(component, args, environment={})
                except ContractError:
                    pass
                else:
                    raise AssertionError('interactive context bypassed full service admission')
    for invalid in ({**record, 'owner': '0:stale'}, {**record, 'owner': None}):
        try:
            with store.interactive_session(invalid):
                pass
        except ContractError:
            pass
        else:
            raise AssertionError('dead interactive owner accepted')
    revoked = store.read()
    revoked['revoked'] = [release.name]
    with patch.object(store, 'read', return_value=revoked):
        try:
            with store.interactive_session(record):
                pass
        except ContractError:
            pass
        else:
            raise AssertionError('revoked interactive generation accepted')
    from luminophore_runtime.common import atomic_json
    marker = store.root / 'removing.json'
    atomic_json(marker, {'owner': record['owner']})
    try:
        try:
            with store.interactive_session(record):
                pass
        except ContractError:
            pass
        else:
            raise AssertionError('removing interactive generation accepted')
    finally:
        marker.unlink()
    from luminophore_runtime.package_lifecycle import prepare_removal
    from luminophore_runtime.session import process_token
    try:
        prepare_removal(store, process_token(os.getpid()))
    except ContractError:
        pass
    else:
        raise AssertionError('active package removal allowed')
try:
    with store.interactive_session(record):
        pass
except ContractError:
    pass
else:
    raise AssertionError('interactive command accepted without a live lease')
store.state_path.chmod(0o666)
try:
    InstalledStore(root).read()
except ContractError:
    pass
else:
    raise AssertionError('writable selection accepted')
from luminophore_runtime.package_lifecycle import activate
(host / 'usr/lib/libc.so.6').write_bytes(b'incompatible host')
empty = root.parent / 'activation-refused'
try:
    activate(release, empty, host)
except ContractError:
    pass
else:
    raise AssertionError('incompatible host activated')
assert InstalledStore(empty).read()['selected'] is None
'''
        runtime = Path(__file__).resolve().parents[1]
        command = ['/usr/bin/bwrap', '--unshare-user', '--uid', '0', '--gid', '0',
                   '--ro-bind', '/', '/', '--bind', str(self.root), str(self.root),
                   '--proc', '/proc', '--dev', '/dev', '--setenv', 'PYTHONPATH', str(runtime),
                   '--', '/usr/bin/python3', '-B', '-c', code, str(release),
                   str(self.root / 'installed'), str(self.sysroot)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
