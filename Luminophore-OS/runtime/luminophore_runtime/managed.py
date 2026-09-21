"""Admission contracts for an inactive OS root + boot assets + DE release.

This module deliberately cannot change a bootloader or update an active root.
It supplies a content-bound admission result for a separately installed host
adapter, which must supply its own trial-boot/fallback implementation.
"""
import os
from pathlib import Path
import shutil
import tempfile

from .build import tree
from .common import (ContractError, atomic_json, files, fsync_dir, identifier,
                     identity, locked, relative, within)


CHECKS = {'startup', 'input', 'output', 'shell', 'ipc', 'lock', 'resume', 'portal',
          'rollback', 'interrupted_update'}


def describe(root, boot, generation, profile, assets):
    root, boot = Path(root).resolve(strict=True), Path(boot).resolve(strict=True)
    if root == Path('/') or boot == Path('/boot') or root.is_relative_to(boot) or boot.is_relative_to(root):
        raise ContractError('separate inactive OS root and boot staging directory required')
    if set(assets) != {'kernel', 'initramfs', 'entry'}:
        raise ContractError('kernel/initramfs/entry assets required')
    for path in assets.values():
        within(boot, path)
    for required in ['usr/lib/modules', 'var/lib/pacman/local', 'etc/os-release']:
        within(root, required, regular=False)
    result = {'schema': 1, 'generation': identifier(generation), 'profile': identifier(profile),
              'root_files': tree(root), 'boot_files': tree(boot), 'assets': assets}
    result['deployment'] = identity(result)
    return result


def verify(root, boot, descriptor):
    if (set(descriptor) != {'schema', 'generation', 'profile', 'root_files', 'boot_files', 'assets', 'deployment'}
            or descriptor['schema'] != 1
            or identity({k: v for k, v in descriptor.items() if k != 'deployment'}) != descriptor['deployment']):
        raise ContractError('invalid deployment descriptor')
    actual = describe(root, boot, descriptor['generation'], descriptor['profile'], descriptor['assets'])
    if actual['root_files'] != descriptor['root_files']:
        raise ContractError('candidate root changed (STALE)')
    if actual['boot_files'] != descriptor['boot_files']:
        raise ContractError('candidate boot assets changed (STALE)')
    return actual['deployment']


def admit(root, boot, descriptor, evidence, status):
    deployment = verify(root, boot, descriptor)
    if status not in {'trial', 'confirmed'}:
        raise ContractError('invalid admission status')
    required = {'vm'} if status == 'trial' else {'vm', 'physical'}
    passed = set()
    for receipt in evidence:
        if (not isinstance(receipt, dict) or receipt.get('deployment') != deployment
                or receipt.get('boundary') not in {'vm', 'physical'}
                or set(receipt.get('checks', {})) != CHECKS
                or any(v is not True for v in receipt['checks'].values())):
            raise ContractError('candidate evidence mismatch or incomplete')
        if receipt['boundary'] in passed:
            raise ContractError('duplicate/conflicting candidate evidence')
        passed.add(receipt['boundary'])
    if not required <= passed:
        raise ContractError('candidate evidence pending: ' + ','.join(sorted(required - passed)))
    return {'deployment': deployment, 'status': status, 'evidence_origin': 'external_report',
            'boot_activation': 'NOT_RUN', 'root_update': 'NOT_RUN'}


def copy_config(source, state_root, generation, schema):
    """Prepare an independent settings copy; never migrate shared data in place."""
    identifier(generation)
    if type(schema) is not int or schema < 1:
        raise ContractError('invalid config schema')
    source, state_root = Path(source).resolve(strict=True), Path(state_root).resolve()
    if state_root.is_relative_to(source) or source.is_relative_to(state_root):
        raise ContractError('config source and state must not overlap')
    members = files(source)
    state_root.mkdir(parents=True, exist_ok=True)
    target = state_root / generation
    with locked(state_root / '.config.lock'):
        if target.exists():
            raise ContractError('config generation already exists; explicit migration required')
        staging = Path(tempfile.mkdtemp(prefix='.config-', dir=state_root))
        try:
            shutil.copytree(source, staging, dirs_exist_ok=True, symlinks=True)
            if files(staging) != members:
                raise ContractError('config changed during snapshot')
            for path in staging.rglob('*'):
                if path.is_file():
                    with path.open('rb') as stream:
                        os.fsync(stream.fileno())
            atomic_json(staging / 'luminophore-config-generation.json',
                        {'generation': generation, 'schema': schema, 'source_digest': identity(members)})
            for path in sorted((p for p in staging.rglob('*') if p.is_dir()), reverse=True):
                fsync_dir(path)
            fsync_dir(staging)
            os.rename(staging, target)
            fsync_dir(state_root)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return target
