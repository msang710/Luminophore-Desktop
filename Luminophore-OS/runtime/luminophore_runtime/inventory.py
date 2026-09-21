"""Read ELF metadata without executing inspected binaries or invoking ldd."""
import os
from pathlib import Path
import re
import subprocess

from .common import ContractError, digest, relative, within


def inspect(path):
    with Path(path).open('rb') as stream:
        if stream.read(4) != b'\x7fELF':
            return None
    result = subprocess.run(['/usr/bin/readelf', '-dW', '-lW', '-VW', str(path)],
                            text=True, capture_output=True, env={'PATH': '/usr/bin', 'LC_ALL': 'C'})
    if result.returncode:
        raise ContractError('cannot inspect ELF: ' + str(path))
    text = result.stdout
    interpreter = re.search(r'Requesting program interpreter: ([^\]]+)', text)
    rpath = re.findall(r'\((?:RUNPATH|RPATH)\).*?\[([^\]]*)\]', text)
    return {'needed': re.findall(r'\(NEEDED\).*?\[([^\]]+)\]', text),
            'soname': next(iter(re.findall(r'\(SONAME\).*?\[([^\]]+)\]', text)), None),
            'interpreter': interpreter.group(1) if interpreter else None,
            'search': ':'.join(rpath).split(':') if rpath else [],
            'versions': sorted(set(re.findall(r'Name: ([A-Za-z][A-Za-z0-9_.]+)', text)))}


def classify(root, candidates, system, required_private=()):
    """Expand an explicit OS SONAME policy through its NEEDED closure.

    Candidates come from a prepared, pinned root, never the running host.
    Anything outside that closure remains private. A private requirement
    conflicting with the OS graph is an error, not an implicit promotion.
    Dynamic OS modules must be included as explicit policy seeds too.
    """
    if not isinstance(candidates, dict) or not candidates:
        raise ContractError('explicit provider candidates required')
    for values in (system, required_private):
        if not isinstance(values, (list, tuple)) or any(not isinstance(v, str) for v in values):
            raise ContractError('provider policy must be a list of SONAME strings')
    providers = {}
    for name, path in candidates.items():
        if not isinstance(name, str) or not name or '/' in name:
            raise ContractError('invalid provider name')
        info = inspect(within(root, path))
        if info is None or info['soname'] not in (None, name):
            raise ContractError('provider SONAME mismatch: ' + name)
        providers[name] = {'path': path, 'owner': 'private'}
    system, required_private = set(system), set(required_private)
    for name in system | required_private:
        if name not in providers:
            raise ContractError('missing provider for policy: ' + str(name))
    queue = list(system)
    visited = set()
    while queue:
        name = queue.pop()
        if name in visited:
            continue
        if name not in providers:
            raise ContractError('missing provider: ' + name)
        if name in required_private:
            raise ContractError('required private provider conflicts with system closure: ' + name)
        visited.add(name)
        providers[name]['owner'] = 'system'
        info = inspect(within(root, providers[name]['path']))
        queue.extend(info['needed'])
        if info['interpreter']:
            queue.append(Path(info['interpreter']).name)
    return providers


def scan(root, roots, providers, dynamic=()):
    """Resolve every NEEDED edge against explicit providers in a pinned sysroot."""
    # Large Python/GI graphs share hundreds of edges. Reuse metadata within
    # this scan only; changed files invalidate the entry and copies still
    # undergo the independent source-digest checks in the packer.
    metadata = {}
    def inspect_file(path):
        info = path.stat()
        key = (path, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if key not in metadata:
            metadata[key] = inspect(path)
        return metadata[key]
    if not isinstance(providers, dict):
        raise ContractError('providers must be an object')
    for name, provider in providers.items():
        if '/' in name or not name or set(provider) != {'path', 'owner'}:
            raise ContractError('invalid provider')
        relative(provider['path'])
        if provider['owner'] not in {'private', 'system'}:
            raise ContractError('invalid provider owner')
    nodes, private, system = {}, {}, {}
    queue = [(relative(p), 'private') for p in [*roots, *dynamic]]
    seen = set()
    while queue:
        name, owner = queue.pop()
        if (name, owner) in seen:
            continue
        seen.add((name, owner))
        path = within(root, name)
        info = inspect_file(path)
        if info is None:
            if name in dynamic:
                raise ContractError('dynamic provider is not ELF: ' + name)
            continue
        nodes[name] = {**info, 'owner': owner, 'sha256': digest(path)}
        if owner == 'system':
            system[name] = digest(path)
        if info['interpreter']:
            loader = info['interpreter']
            match = providers.get(Path(loader).name)
            if not match or match['owner'] != 'system':
                raise ContractError('explicit system interpreter provider required: ' + loader)
            loader_name = loader.lstrip('/')
            loader_hash = digest(within(root, loader_name))
            if loader_hash != digest(within(root, match['path'])):
                raise ContractError('system interpreter provider mismatch: ' + loader)
            system[loader_name] = loader_hash
            system[match['path']] = digest(within(root, match['path']))
            queue.append((match['path'], 'system'))
        for needed in info['needed']:
            provider = providers.get(needed)
            if not provider:
                raise ContractError('missing provider: ' + needed + ' needed by ' + name)
            child = within(root, provider['path'])
            child_info = inspect_file(child)
            if child_info is None or child_info['soname'] not in (None, needed):
                raise ContractError('provider SONAME mismatch: ' + needed)
            if owner == 'system' and provider['owner'] == 'private':
                raise ContractError('system provider depends on private library: ' + needed)
            if provider['owner'] == 'private':
                private[needed] = provider['path']
            else:
                system[provider['path']] = digest(child)
            queue.append((provider['path'], provider['owner']))
    for name in dynamic:
        info = nodes[name]
        soname = info['soname'] or Path(name).name
        if soname in private and private[soname] != name:
            raise ContractError('dynamic provider collision: ' + soname)
        private[soname] = name
    return {'elf': nodes, 'private': private, 'system_files': system}


def validate_search(path, info, library_dir):
    """Only relocation-safe, bundle-contained search directories are acceptable."""
    expected = os.path.relpath(library_dir, Path(path).parent)
    expected = '$ORIGIN' if expected == '.' else '$ORIGIN/' + expected
    if expected not in info['search']:
        raise ContractError('private ELF requires RUNPATH ' + expected + ': ' + str(path))
    for entry in info['search']:
        if not entry.startswith('$ORIGIN') or entry not in (expected, '$ORIGIN'):
            raise ContractError('unexpected ELF search path: ' + entry)


def validate_capability_inventory(manifest, commands):
    """Return ownership for every externally invoked command, failing closed."""
    from .capabilities import validate

    if not isinstance(commands, (set, frozenset, list, tuple)) or any(
            not isinstance(name, str) or not name for name in commands):
        raise ContractError('command inventory must be a collection of names')
    classified = {}
    for row in validate(manifest)['capabilities']:
        if row['kind'] != 'command':
            continue
        for name in row['names']:
            classified[name] = row['ownership']
    missing = sorted(set(commands) - set(classified))
    if missing:
        raise ContractError('unclassified executable: ' + ', '.join(missing))
    return {name: classified[name] for name in sorted(set(commands))}
