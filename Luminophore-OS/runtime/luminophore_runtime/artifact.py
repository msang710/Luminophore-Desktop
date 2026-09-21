"""Create content-addressed whole-DE artifacts; no install or activation here."""
import os
from pathlib import Path
import shutil
import tempfile
import re
import subprocess

from . import host_contract, inventory
from .common import (ContractError, atomic_json, digest, files, fsync_dir,
                     identifier, identity, load, relative, within)


SCHEMA = 'luminophore-release/v2'


def expand(root, mappings):
    result = {}
    for target, source in mappings.items():
        relative(target)
        path = within(root, source, regular=False)
        members = sorted(path.rglob('*')) if path.is_dir() else [path]
        for member in members:
            if member.is_dir() and not member.is_symlink():
                continue
            suffix = member.relative_to(path).as_posix() if path.is_dir() else ''
            dest = target + ('/' + suffix if suffix else '')
            rel_source = member.relative_to(Path(root).resolve()).as_posix()
            within(root, rel_source)
            if dest in result:
                raise ContractError('duplicate artifact destination: ' + dest)
            result[dest] = rel_source
    return result


def validate_recipe(recipe):
    required = {'schema', 'providers', 'files', 'dynamic', 'entries', 'compatibility',
                'provenance', 'system_files', 'runtime_env'}
    if not isinstance(recipe, dict) or set(recipe) != required or recipe['schema'] != 1:
        raise ContractError('invalid release recipe schema')
    if not {'compositor', 'control', 'shell'} <= set(recipe['entries']):
        raise ContractError('whole-DE entries compositor/control/shell required')
    for name, entry in recipe['entries'].items():
        if not name.isidentifier() or set(entry) != {'path', 'args'}:
            raise ContractError('invalid entry')
        relative(entry['path'])
        if not isinstance(entry['args'], list) or any(not isinstance(v, str) or '\x00' in v for v in entry['args']):
            raise ContractError('invalid entry arguments')
    if recipe['entries']['control']['args']:
        raise ContractError('control entry must be a directly executable client without prefix arguments')
    if set(recipe['compatibility']) != {'config', 'ipc'} or any(
            type(v) is not int or v < 1 for v in recipe['compatibility'].values()):
        raise ContractError('invalid compatibility versions')
    if set(recipe['provenance']) != {'source', 'build_lock', 'license'}:
        raise ContractError('source/build lock/license provenance required')
    identifier(recipe['provenance']['build_lock'])
    if not recipe['provenance']['source'] or not recipe['provenance']['license']:
        raise ContractError('empty provenance')
    allowed = {'PYTHONHOME', 'GI_TYPELIB_PATH', 'GSETTINGS_SCHEMA_DIR', 'GIO_MODULE_DIR', 'FONTCONFIG_FILE', 'QT_PLUGIN_PATH'}
    if not isinstance(recipe['runtime_env'], dict) or not set(recipe['runtime_env']) <= allowed:
        raise ContractError('invalid runtime environment')
    for value in recipe['runtime_env'].values():
        relative(value)


def pack(sysroot, recipe, destination, *, patchelf=None, expected_sources=None, metadata=None,
         expected_modes=None):
    validate_recipe(recipe)
    if patchelf:
        # Older relocators can pass --list yet corrupt dynamic module loading
        # for current distro ELF files. Pin the tested minimum and tool bytes.
        tool = Path(patchelf).resolve(strict=True)
        result = subprocess.run([str(tool), '--version'], capture_output=True, text=True,
                                check=True, timeout=10, env={'PATH': '/usr/bin', 'LC_ALL': 'C'})
        version = re.fullmatch(r'patchelf (\d+)\.(\d+)\.(\d+)\s*', result.stdout)
        if not version or tuple(map(int, version.groups())) < (0, 19, 1):
            raise ContractError('patchelf 0.19.1 or newer is required for private runtime relocation')
        metadata = dict(metadata or {})
        if 'pack-tool.json' in metadata:
            raise ContractError('pack-tool.json is reserved')
        metadata['pack-tool.json'] = {'patchelf': result.stdout.strip(), 'sha256': digest(tool)}
        patchelf = tool
    sysroot = Path(sysroot).resolve(strict=True)
    members = expand(sysroot, recipe['files'])
    report = inventory.scan(sysroot, members.values(), recipe['providers'], recipe['dynamic'])
    for name, source in report['private'].items():
        target = 'lib/' + relative(name)
        if target in members and members[target] != source:
            raise ContractError('private library destination collision')
        members[target] = source
    for name, expected in recipe['system_files'].items():
        identifier(expected)
        if digest(within(sysroot, name)) != expected:
            raise ContractError('pinned system file changed: ' + name)
        report['system_files'][name] = expected
    if expected_sources is not None:
        for name, current in report['system_files'].items():
            if expected_sources.get(name) != current:
                raise ContractError('system source changed or untracked: ' + name)
    for entry in recipe['entries'].values():
        if entry['path'] not in members:
            raise ContractError('missing entry file: ' + entry['path'])
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.staging-', dir=destination))
    try:
        for name, source in sorted(members.items()):
            original = within(sysroot, source)
            executable = bool(original.stat().st_mode & 0o111)
            if expected_modes is not None and expected_modes.get(source) != executable:
                raise ContractError('source mode changed or untracked: ' + source)
            expected = None
            if expected_sources is not None:
                expected = expected_sources.get(source)
                if expected is None or digest(original) != expected:
                    raise ContractError('source changed or untracked before copy: ' + source)
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(within(sysroot, source), target)
            if expected is not None and digest(target) != expected:
                raise ContractError('source changed during copy: ' + source)
            target.chmod(0o755 if executable else 0o644)
        if metadata:
            if (staging / 'metadata').exists():
                raise ContractError('metadata is reserved for generated release records')
            (staging / 'metadata').mkdir()
            for name, value in metadata.items():
                relative(name)
                if '/' in name:
                    raise ContractError('metadata must use direct filenames')
                atomic_json(staging / 'metadata' / name, value)
        # Carry the dispatcher with the release rather than resolving an
        # independently updated management checkout during shell restart.
        support = staging / 'support'
        if support.exists():
            raise ContractError('support is reserved for the release dispatcher')
        support.mkdir()
        package = Path(__file__).resolve().parent
        (support / 'luminophore_runtime').mkdir()
        for module in package.glob('*.py'):
            shutil.copyfile(module, support / 'luminophore_runtime' / module.name)
        shutil.copyfile(package.parent / 'luminophore-runtime', support / 'luminophore-runtime')
        elf = {}
        for name in members:
            path = staging / name
            info = inventory.inspect(path)
            if info is None:
                continue
            if patchelf and (info['needed'] or info['soname']):
                rel = os.path.relpath(staging / 'lib', path.parent)
                search = '$ORIGIN' if rel == '.' else '$ORIGIN/' + rel
                subprocess.run([str(patchelf), '--set-rpath', search, str(path)], check=True,
                               env={'PATH': '/usr/bin', 'LC_ALL': 'C'})
                info = inventory.inspect(path)
            if info['needed'] or info['soname']:
                inventory.validate_search(path, info, staging / 'lib')
            elf[name] = info
        for entry in recipe['entries'].values():
            if entry['path'] not in elf or not os.access(staging / entry['path'], os.X_OK):
                raise ContractError('entries must use an explicit ELF interpreter or executable')
        for key, value in recipe['runtime_env'].items():
            if key == 'FONTCONFIG_FILE':
                if not (staging / value).is_file():
                    raise ContractError('fontconfig file missing: ' + value)
            elif not (staging / value).is_dir():
                raise ContractError('runtime environment directory missing: ' + value)
        shared_host = host_contract.create(report['system_files'], elf, sysroot)
        manifest = {'schema': SCHEMA, 'entries': recipe['entries'], 'elf': elf,
                    'compatibility': recipe['compatibility'], 'provenance': recipe['provenance'],
                    'system_files': report['system_files'], 'runtime_env': recipe['runtime_env'],
                    'host_contract': shared_host,
                    'dynamic': recipe['dynamic'], 'files': files(staging),
                    'acceptance': 'NOT_RUN'}
        manifest['generation'] = identity(manifest)
        atomic_json(staging / 'release.json', manifest)
        verify(staging, sysroot, expected=manifest['generation'])
        target = destination / manifest['generation']
        if target.exists():
            verify(target, expected=manifest['generation'])
            return target
        for path in staging.rglob('*'):
            if path.is_file():
                with path.open('rb') as stream:
                    os.fsync(stream.fileno())
        for path in sorted((p for p in staging.rglob('*') if p.is_dir()), reverse=True):
            fsync_dir(path)
        fsync_dir(staging)
        os.rename(staging, target)
        fsync_dir(destination)
        return target
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def verify(root, host_root=None, *, expected=None):
    root = Path(root)
    manifest = load(root / 'release.json')
    if manifest.get('schema') != SCHEMA:
        raise ContractError('unsupported release schema')
    generation = identifier(manifest.get('generation'))
    if identity({k: v for k, v in manifest.items() if k != 'generation'}) != generation:
        raise ContractError('generation digest mismatch')
    if expected is not None and generation != identifier(expected):
        raise ContractError('unexpected generation')
    if expected is None and root.name != generation:
        raise ContractError('generation directory mismatch')
    actual = files(root)
    actual.pop('release.json')
    if set(actual) != set(manifest['files']):
        raise ContractError('artifact file set mismatch')
    for name, info in actual.items():
        if info != manifest['files'][name]:
            raise ContractError('artifact digest/mode mismatch: ' + name)
    if 'host_contract' not in manifest:
        raise ContractError('release lacks shared-host compatibility contract')
    host_contract.validate(manifest['host_contract'])
    if host_root is not None:
        host_contract.require_compatible(manifest['host_contract'], host_root)
    return manifest


def preflight(root, host_root=Path('/')):
    """Resolve trusted ELF files with their real loader without running entrypoints.

    This is a loader check, not a graphics/session acceptance test. Use only
    after validating an artifact obtained through a trusted digest channel.
    """
    root = Path(root).resolve()
    manifest = verify(root)
    host_contract.require_compatible(manifest['host_contract'], host_root)
    allowed = {(root / name).resolve(): item['sha256'] for name, item in manifest['files'].items()}
    for name in manifest['system_files']:
        path = within(host_root, name)
        allowed[path.resolve()] = digest(path)
    interpreters = {info['interpreter'] for info in manifest['elf'].values() if info['interpreter']}
    if len(interpreters) != 1:
        raise ContractError('loader preflight requires one target ELF interpreter')
    verified = {}
    def pinned(path):
        if path not in allowed:
            return False
        # Hundreds of ELF closures share the same large libraries. Cache only
        # within this invocation and invalidate on metadata changes, including
        # ctime (even when a writer restores the original size and mtime).
        def fingerprint():
            info = path.stat()
            return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns)
        before = fingerprint()
        if verified.get(path) == before:
            return True
        if digest(path) != allowed[path] or fingerprint() != before:
            return False
        verified[path] = before
        return True
    loader = within(host_root, next(iter(interpreters)).lstrip('/'))
    if not pinned(loader.resolve()):
        raise ContractError('loader changed or unpinned')
    for name, info in manifest['elf'].items():
        if not info['needed']:
            continue
        result = subprocess.run([str(loader), '--list', str(root / name)], capture_output=True,
                                text=True, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}, timeout=30)
        if result.returncode or 'not found' in result.stdout:
            raise ContractError('loader cannot resolve ' + name + ': ' + result.stderr.strip())
        for line in result.stdout.splitlines():
            match = re.match(r'\s*(?:\S+\s+=>\s+)?(/\S+)\s+\(', line)
            if not match:
                if line.strip().startswith('linux-vdso.'):
                    continue
                raise ContractError('unexpected loader output: ' + line)
            path = Path(match.group(1)).resolve()
            if not pinned(path):
                raise ContractError('loader selected unpinned provider: ' + str(path))
    return {'generation': manifest['generation'], 'loader': 'PASS', 'session': 'NOT_RUN'}
