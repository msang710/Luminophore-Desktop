#!/usr/bin/python3
"""Copy to /fixtures/check-desktop.py in the disposable package-test VM."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

if 'luminophore_test=1' not in Path('/proc/cmdline').read_text().split():
    raise SystemExit('disposable VM required')

sys.path.insert(0, '/usr/lib/luminophore/runtime')
from luminophore_runtime.common import ContractError
from luminophore_runtime.installed import InstalledStore
from luminophore_runtime.package_lifecycle import prepare_removal
from luminophore_runtime.session import process_token

store = InstalledStore('/var/lib/luminophore')
with store.session('/', config_version=1) as session:
    release = store.generations / store.read()['selected']
    with tempfile.TemporaryDirectory(prefix='luminophore-identity-') as runtime_dir:
        command, env = session.command('compositor', environment={'XDG_RUNTIME_DIR': runtime_dir})
        assert command[0] == str(release / 'bin/start-hyprland'), command
        assert command[command.index('--path') + 1] == str(release / 'bin/Hyprland'), command
        version = json.loads(subprocess.check_output(
            [str(release / 'bin/Hyprland'), '--version-json'], env=env, text=True))
        assert version['compositor'] == 'Luminophore', version
    try:
        prepare_removal(store, process_token(os.getpid()))
    except ContractError:
        pass
    else:
        raise AssertionError('live package removal accepted')
    subprocess.run(['/usr/bin/python3', '-B', '/fixtures/probe-release.py', str(release)],
                   check=True, timeout=300,
                   env={'PATH': '/usr/bin:/bin', 'PYTHONPATH': '/usr/lib/luminophore/runtime'})
print('INSTALLED_DESKTOP_RUNTIME_PASS')
