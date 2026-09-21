"""Bind locked build outputs to an atomically published desktop sysroot."""
import copy
import os
from pathlib import Path
import shutil
import stat
import tempfile

from . import build, desktop
from .common import (ContractError, atomic_json, digest, files, fsync_dir,
                     identity, load, relative, within)


def _separate(*roots):
    paths = [Path(p).resolve() for p in roots]
    for index, left in enumerate(paths):
        for right in paths[index + 1:]:
            if left.is_relative_to(right) or right.is_relative_to(left):
                raise ContractError('assembly input/output roots must not overlap')


def _inputs(output, source, lock, receipt, layout_name):
    if (not isinstance(lock, dict) or set(lock) != {
            'schema', 'sysroot', 'source', 'argv', 'source_date_epoch', 'digest'}
            or lock['schema'] != 1
            or identity({k: v for k, v in lock.items() if k != 'digest'}) != lock['digest']):
        raise ContractError('invalid build lock')
    if build.tree(source) != lock['source']:
        raise ContractError('source differs from build lock')
    if (not isinstance(receipt, dict) or set(receipt) != {'build_lock', 'output', 'reproducibility'}
            or receipt['build_lock'] != lock['digest']
            or receipt['reproducibility'] != 'requires-independent-repeat'):
        raise ContractError('receipt differs from build lock or schema')
    if identity(build.tree(output)) != receipt['output']:
        raise ContractError('build output differs from receipt')
    layout = load(within(source, layout_name))
    if (set(layout) != {'schema', 'desktop', 'packages'}
            or layout['schema'] != 'luminophore-build-layout/v1'
            or not isinstance(layout['desktop'], dict)
            or not isinstance(layout['packages'], list) or not layout['packages']):
        raise ContractError('invalid build layout')
    template = layout['desktop']
    if (set(template) != {'schema', 'python_version', 'providers', 'dynamic', 'system_files',
                         'compatibility', 'provenance'}
            or not isinstance(template['provenance'], dict)
            or set(template['provenance']) != {'source', 'license'}
            or not isinstance(template['system_files'], list)):
        raise ContractError('invalid desktop layout template')
    directories = []
    for package in layout['packages']:
        if not isinstance(package, dict) or set(package) != {
                'root', 'name', 'version', 'owner', 'license', 'license_files'}:
            raise ContractError('invalid package layout')
        relative(package['root'])
        path = within(output, package['root'], regular=False)
        if not path.is_dir() or (Path(output) / package['root']).is_symlink():
            raise ContractError('package root must be an ordinary directory')
        directories.append(path)
    _separate(*directories)
    return layout, directories


def assemble(output, source, lock, receipt, layout_name, destination):
    output, source, destination = map(lambda p: Path(p).resolve(), (output, source, destination))
    _separate(output, source, destination)
    if destination.exists():
        raise ContractError('assembly destination already exists')
    layout, directories = _inputs(output, source, lock, receipt, layout_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.assembly-', dir=destination.parent))
    try:
        root = staging / 'root'
        root.mkdir()
        spec = copy.deepcopy(layout['desktop'])
        spec['provenance']['build_lock'] = lock['digest']
        spec['packages'] = []
        occupied = set()
        for package, directory in zip(layout['packages'], directories):
            record = {k: copy.deepcopy(v) for k, v in package.items() if k != 'root'}
            record['files'] = {}
            for member in sorted(directory.rglob('*')):
                name = member.relative_to(directory).as_posix()
                mode = member.lstat().st_mode
                if stat.S_ISDIR(mode):
                    continue
                original = within(directory, name)
                if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                    raise ContractError('unsupported package member: ' + name)
                if name in occupied:
                    raise ContractError('package file collision: ' + name)
                occupied.add(name)
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                expected = digest(original)
                shutil.copyfile(original, target)
                target.chmod(0o755 if original.stat().st_mode & 0o111 else 0o644)
                if digest(target) != expected:
                    raise ContractError('package changed during copy: ' + name)
                record['files'][name] = expected
            spec['packages'].append(record)
        spec['system_files'] = {relative(name): digest(within(root, name))
                                for name in spec['system_files']}
        # Fail before publication if anything needed by the desktop is missing.
        desktop.recipe(root, spec)
        atomic_json(staging / 'desktop-input.json', spec)
        atomic_json(staging / 'build-receipt.json', receipt)
        atomic_json(staging / 'build-layout.json', layout)
        manifest = {'schema': 'luminophore-assembly/v1', 'build_lock': lock['digest'],
                    'build_output': receipt['output'], 'layout': identity(layout),
                    'files': files(staging), 'acceptance': 'NOT_RUN',
                    'provenance': 'local-build-receipt-not-attestation'}
        manifest['assembly'] = identity(manifest)
        atomic_json(staging / 'assembly.json', manifest)
        # Recheck both inputs after all copies, before publishing any assembly.
        _inputs(output, source, lock, receipt, layout_name)
        for member in staging.rglob('*'):
            if member.is_file():
                with member.open('rb') as stream:
                    os.fsync(stream.fileno())
        for member in sorted((p for p in staging.rglob('*') if p.is_dir()), reverse=True):
            fsync_dir(member)
        fsync_dir(staging)
        if destination.exists():
            raise ContractError('assembly destination already exists')
        os.rename(staging, destination)
        fsync_dir(destination.parent)
        return {'assembly': manifest['assembly'], 'sysroot': str(destination / 'root'),
                'input': str(destination / 'desktop-input.json'), 'session': 'NOT_RUN'}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def verify(path):
    path = Path(path)
    manifest = load(path / 'assembly.json')
    if (manifest.get('schema') != 'luminophore-assembly/v1'
            or identity({k: v for k, v in manifest.items() if k != 'assembly'}) != manifest.get('assembly')):
        raise ContractError('assembly manifest digest mismatch')
    actual = files(path)
    actual.pop('assembly.json')
    if actual != manifest['files']:
        raise ContractError('assembly file digest/mode mismatch')
    return manifest


def run(sysroot, source, lock, layout_name, output, destination):
    _separate(sysroot, source, output, destination)
    if Path(destination).exists():
        raise ContractError('assembly destination already exists')
    # Resolve the layout from the locked source; do not accept a separately
    # mutable host-side recipe for this combined path.
    within(source, layout_name)
    receipt = build.run(sysroot, source, output, lock)
    return assemble(output, source, lock, receipt, layout_name, destination)


def pack(path, destination, *, patchelf=None):
    path = Path(path).resolve()
    _separate(path, destination)
    manifest = verify(path)
    def read(name):
        return load(path / name, expected=manifest['files'][name]['sha256'])
    return desktop.pack(path / 'root', read('desktop-input.json'), destination,
                        expected_files={name[5:]: item for name, item in manifest['files'].items()
                                        if name.startswith('root/')},
                        patchelf=patchelf, build_evidence={
                            'assembly': manifest, 'receipt': read('build-receipt.json'),
                            'layout': read('build-layout.json')})
