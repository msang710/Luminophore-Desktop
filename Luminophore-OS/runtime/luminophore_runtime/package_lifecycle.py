"""Package activation preserves prior releases; no service or login mutation."""
import argparse
from pathlib import Path
import sys
import os
import fcntl
from . import artifact
from .common import ContractError, identifier, locked, atomic_json
from .installed import InstalledStore
from .session import process_token


def prepare_removal(store, owner):
    with locked(store.lock):
        for path in store.leases.iterdir():
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise ContractError('Luminophore session is running; log out before removing its package') from error
            finally:
                os.close(fd)
        if not owner:
            raise ContractError('package transaction owner unavailable')
        atomic_json(store.root / 'removing.json', {'owner': owner}, mode=0o644)


def activate(release, store_root, host_root=Path('/')):
    release = Path(release)
    generation = identifier(release.name)
    # An incompatible first install must leave an empty manageable store,
    # rather than causing the installed hooks to reject every later transaction.
    store = InstalledStore.initialize(store_root)
    manifest = artifact.verify(release, host_root, expected=generation)
    artifact.preflight(release)
    previous = store.read()['selected']
    if previous and store._release(previous)['compatibility']['config'] != manifest['compatibility']['config']:
        raise ContractError('config schema transition requires explicit migration before activation')
    store.stage(release, generation)
    return store.select(generation, host_root, store.read()['revision'],
                        config_version=manifest['compatibility']['config'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('generation', nargs='?')
    parser.add_argument('--check-removal', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.check_removal:
            prepare_removal(InstalledStore('/var/lib/luminophore'), process_token(os.getppid()))
            return 0
        generation = identifier(args.generation)
        activate(Path('/usr/lib/luminophore/releases') / generation, Path('/var/lib/luminophore'))
        return 0
    except (ContractError, OSError, ValueError, KeyError) as error:
        print('Luminophore activation refused; prior selection retained: ' + str(error), file=sys.stderr)
        return 1
