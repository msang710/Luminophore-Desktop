"""Locked, networkless builds from an explicit sysroot, never host /usr."""
import os
from pathlib import Path
import stat
import subprocess
import sys

from .common import ContractError, digest, identity, relative, within


def tree(root):
    root = Path(root).resolve(strict=True)
    if root == Path('/'):
        raise ContractError('an explicit inactive root is required')
    result = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            target = os.readlink(path)
            # Absolute symlinks are interpreted inside the build root, not on
            # the machine running this tool. Bind mounts never follow them.
            virtual = root / target.lstrip('/') if target.startswith('/') else path.parent / target
            normalized = Path(os.path.normpath(virtual))
            if not normalized.is_relative_to(root):
                raise ContractError('symlink escapes root: ' + name)
            result[name] = {'link': target}
        elif stat.S_ISDIR(mode):
            result[name] = {'directory': True, 'mode': stat.S_IMODE(mode)}
        elif stat.S_ISREG(mode):
            result[name] = {'sha256': digest(path), 'mode': stat.S_IMODE(mode)}
        else:
            raise ContractError('special files must not be in a build/deployment root: ' + name)
    return result


def create_lock(sysroot, source, argv, epoch):
    if (not isinstance(argv, list) or not argv or any(not isinstance(v, str) or '\x00' in v for v in argv)
            or type(epoch) is not int or epoch < 0):
        raise ContractError('invalid build command or SOURCE_DATE_EPOCH')
    result = {'schema': 1, 'sysroot': tree(sysroot), 'source': tree(source),
              'argv': argv, 'source_date_epoch': epoch}
    result['digest'] = identity(result)
    return result


def verify_lock(sysroot, source, lock):
    if (set(lock) != {'schema', 'sysroot', 'source', 'argv', 'source_date_epoch', 'digest'}
            or lock['schema'] != 1
            or identity({k: v for k, v in lock.items() if k != 'digest'}) != lock['digest']):
        raise ContractError('invalid build lock')
    for label, path in [('sysroot', sysroot), ('source', source)]:
        if tree(path) != lock[label]:
            raise ContractError(label + ' differs from build lock')


def command(sysroot, source, output, lock):
    sysroot, source, output = map(lambda p: Path(p).resolve(), (sysroot, source, output))
    for left, right in [(sysroot, source), (output, source), (output, sysroot)]:
        if left.is_relative_to(right) or right.is_relative_to(left):
            raise ContractError('build roots and output must not overlap')
    verify_lock(sysroot, source, lock)
    return ['/usr/bin/bwrap', '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc',
            '--unshare-uts', '--die-with-parent', '--new-session', '--clearenv',
            '--ro-bind', str(sysroot), '/', '--ro-bind', str(source), '/src',
            '--bind', str(output), '/build', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
            '--chdir', '/build', '--setenv', 'PATH', '/usr/bin:/bin',
            '--setenv', 'HOME', '/tmp', '--setenv', 'LC_ALL', 'C', '--setenv', 'TZ', 'UTC',
            '--setenv', 'SOURCE_DATE_EPOCH', str(lock['source_date_epoch']), '--', *lock['argv']]


def run(sysroot, source, output, lock):
    args = command(sysroot, source, output, lock)
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ContractError('build output must be empty')
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run(args, check=True, stdout=sys.stderr, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
    # This catches accidental concurrent mutation; the caller must keep the
    # pinned inputs immutable for the entire build (read-only snapshot/image).
    verify_lock(sysroot, source, lock)
    return {'build_lock': lock['digest'], 'output': identity(tree(output)),
            'reproducibility': 'requires-independent-repeat'}
