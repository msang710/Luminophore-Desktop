"""Concrete desktop layout on top of the generic v2 artifact format.

Consumes a prepared sysroot, never discovers packages from the running host.
The package inventory is a publisher declaration, not a build attestation.
"""
from pathlib import Path
import re

from . import artifact, capabilities, inventory
from .common import ContractError, digest, identifier, relative, within


PREFIX = 'usr/lib/luminophore'
SCHEMA = 'luminophore-desktop-input/v1'
GREETER_ENTRIES = ('greeter_compositor', 'greeter_control', 'greeter_shell', 'greeter_session')
GREETER_ASSETS = tuple('config/greeter/'+name for name in ('settings.toml', 'monitors.toml', 'bindings.toml', 'placement.toml', 'bundles.toml')) + ('config/greeter-theme.json', 'share/fonts/luminophore/fallback.ttf')


def desktop_entries():
    return {
        'compositor': {'path': 'bin/start-hyprland', 'args': [
            '--path', '{release}/bin/Hyprland', '--no-nixgl', '--',
            '--config', '{release}/config/desktop/settings.toml']},
        'control': {'path': 'bin/hyprctl', 'args': []},
        'portal': {'path': 'bin/xdg-desktop-portal-luminophore', 'args': []},
        'shell': {'path': 'python/bin/python3', 'args': ['-B', '-s', '{release}/shell/luminophore-shell', 'daemon']},
        'shell_cli': {'path': 'python/bin/python3', 'args': ['-B', '-s', '{release}/shell/luminophore-shell']},
        'settings': {'path': 'python/bin/python3', 'args': ['-B', '-s', '{release}/shell/luminophore-shell', 'settings']},
        'greeter_compositor': {'path': 'bin/start-hyprland', 'args': [
            '--path', '{release}/bin/Hyprland', '--no-nixgl', '--',
            '--config', '{release}/config/greeter/settings.toml']},
        'greeter_control': {'path': 'bin/hyprctl', 'args': []},
        'greeter_shell': {'path': 'python/bin/python3', 'args': [
            '-B', '-s', '{release}/shell/luminophore-shell', 'greeter']},
        'greeter_session': {'path': 'python/bin/python3', 'args': [
            '-B', '-s', '{release}/shell/luminophore-shell', 'greeter-session']},
    }


def validate_greeter_contract(entries, files):
    if not set(GREETER_ENTRIES) <= set(entries):
        raise ContractError('whole greeter entry set required')
    files = set(files)
    for asset in GREETER_ASSETS:
        if asset not in files:
            raise ContractError('greeter asset missing: ' + asset)
    result = {}
    forbidden = ('/usr/bin/start-hyprland', '/usr/bin/hyprctl', '/usr/bin/hyprpaper',
                 'hyprland.desktop', '/.config/hypr')
    for name in GREETER_ENTRIES:
        entry = entries[name]
        relative(entry['path'])
        text = repr(entry)
        if any(value in text for value in forbidden) or entry['path'] not in files:
            raise ContractError('greeter entry escapes private release: ' + name)
        result[name] = entry
    return result


def capability_metadata():
    manifest = capabilities.load_manifest()
    return {
        'desktop-capabilities.json': manifest,
        'desktop-capabilities.sha256': capabilities.manifest_digest(manifest),
    }


def _ownership(root, packages):
    if not isinstance(packages, list) or not packages:
        raise ContractError('package ownership inventory required')
    owners, hashes, names = {}, {}, set()
    for package in packages:
        if not isinstance(package, dict) or set(package) != {
                'name', 'version', 'owner', 'license', 'license_files', 'files'}:
            raise ContractError('invalid package ownership record')
        name = package['name']
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*', name)
                or name in names):
            raise ContractError('invalid or duplicate package name')
        names.add(name)
        if package['owner'] not in {'private', 'system'}:
            raise ContractError('invalid package owner')
        if any(not isinstance(package[k], str) or not package[k].strip() for k in ('version', 'license')):
            raise ContractError('package version and license required')
        if not isinstance(package['files'], dict) or not package['files']:
            raise ContractError('package files required')
        for path, expected in package['files'].items():
            relative(path)
            identifier(expected)
            if path in owners:
                raise ContractError('duplicate file ownership: ' + path)
            if digest(within(root, path)) != expected:
                raise ContractError('package file digest mismatch: ' + path)
            owners[path] = package['owner']
            hashes[path] = expected
        licenses = package['license_files']
        if not isinstance(licenses, list) or (package['owner'] == 'private' and not licenses):
            raise ContractError('private package license files required')
        if any(not isinstance(p, str) or p not in package['files'] for p in licenses):
            raise ContractError('license file must belong to its package')
    return owners, hashes


def _prepare(root, spec):
    root = Path(root).resolve(strict=True)
    if root == Path('/'):
        raise ContractError('an explicit staged sysroot is required')
    if not isinstance(spec, dict) or set(spec) != {
            'schema', 'python_version', 'providers', 'dynamic', 'system_files',
            'compatibility', 'provenance', 'packages'} or spec['schema'] != SCHEMA:
        raise ContractError('invalid desktop input schema')
    version = spec['python_version']
    if not isinstance(version, str) or not re.fullmatch(r'3\.[0-9]+', version):
        raise ContractError('explicit Python major.minor required')
    python = 'python/lib/python' + version
    required = [
        'bin/Hyprland', 'bin/hyprctl', 'bin/start-hyprland', 'python/bin/python3',
        'bin/xdg-desktop-portal-luminophore', 'bin/luminophore-share-picker',
        'etc/fonts/fonts.conf', 'share/fonts/luminophore/fallback.ttf',
        'lib/qt6/plugins/platforms/libqwayland.so',
        'shell/luminophore-shell', 'shell/luminophore_shell/__main__.py',
        'shell/luminophore_shell/bootstrap.py', 'config/desktop/settings.toml', 'config/greeter/settings.toml',
        'config/greeter-theme.json',
        'shell/luminophore_shell/settings_bootstrap.py',
        *('config/'+profile+'/'+name for profile in ('desktop', 'greeter')
          for name in ('monitors.toml', 'bindings.toml', 'placement.toml', 'bundles.toml')),
        'shell/native/luminophore_glow_shader.h',
        python + '/encodings/__init__.py', python + '/site-packages/gi/__init__.py',
        python + '/site-packages/PIL/__init__.py', python + '/site-packages/requests/__init__.py',
        python + '/site-packages/cairo/__init__.py', python + '/site-packages/dbus/__init__.py',
        python + '/site-packages/numpy/__init__.py', python + '/site-packages/cv2/__init__.py',
        python + '/site-packages/OpenGL/__init__.py',
        'lib/libgtk4-layer-shell.so.0', 'lib/girepository-1.0/Gtk-4.0.typelib',
        'lib/girepository-1.0/Gtk4LayerShell-1.0.typelib',
        'share/glib-2.0/schemas/gschemas.compiled',
    ]
    for name in required:
        within(root, PREFIX + '/' + name)
    prefix = within(root, PREFIX, regular=False)
    for directory in ['shell/luminophore_shell/assets', 'shell/luminophore_shell/templates',
                      'lib/gio/modules']:
        path = within(root, PREFIX + '/' + directory, regular=False)
        if not path.is_dir() or not any(p.is_file() for p in path.rglob('*')):
            raise ContractError('required desktop resource directory is empty: ' + directory)
    for pattern in ['gi/_gi*.so', 'cairo/_cairo*.so', '_dbus_bindings*.so',
                    'numpy/_core/_multiarray_umath*.so', 'cv2/cv2*.so']:
        extensions = list((prefix / python / 'site-packages').glob(pattern))
        if not extensions or any(inventory.inspect(p) is None for p in extensions):
            raise ContractError('private Python ELF extension required: ' + pattern)
    # Map the installed layout exactly; pack's reserved support/ remains generated.
    mappings = {p.name: PREFIX + '/' + p.name for p in sorted(prefix.iterdir())}
    if set(mappings) & {'metadata', 'support', 'release.json'}:
        raise ContractError('reserved desktop artifact path')
    owners, hashes = _ownership(root, spec['packages'])
    for package in spec['packages']:
        if package['owner'] == 'private':
            for index, path in enumerate(package['license_files']):
                mappings[f'licenses/{package["name"]}/{index}-{Path(path).name}'] = path
    members = artifact.expand(root, mappings)
    if not isinstance(spec['dynamic'], list) or any(not isinstance(v, str) for v in spec['dynamic']):
        raise ContractError('dynamic paths must be a list')
    # All installed ELF modules participate in closure inspection, including GI
    # and Python extensions, even if no executable has a NEEDED edge to them.
    # Modules already mapped retain their location instead of being flattened.
    report = inventory.scan(root, members.values(), spec['providers'], spec['dynamic'])
    private = set(members.values()) | set(report['private'].values()) | set(spec['dynamic'])
    system = set(report['system_files']) | set(spec['system_files'])
    for group, expected in [(private, 'private'), (system, 'system')]:
        for path in group:
            if path not in owners:
                raise ContractError('missing package ownership: ' + path)
            if owners[path] != expected:
                raise ContractError('package owner mismatch: ' + path)
    if private & system:
        raise ContractError('a path cannot be both private and system owned')
    # Explicit OS resource pins must agree with the package inventory too.
    for path, expected in spec['system_files'].items():
        if expected != hashes[path]:
            raise ContractError('system file digest differs from package inventory: ' + path)
    entries = desktop_entries()
    validate_greeter_contract(entries, {
        'bin/start-hyprland', 'bin/hyprctl', 'python/bin/python3', *GREETER_ASSETS})
    result = {'schema': 1, 'files': mappings, 'providers': spec['providers'],
              'dynamic': spec['dynamic'], 'entries': entries,
              'compatibility': spec['compatibility'], 'provenance': spec['provenance'],
              'system_files': spec['system_files'],
              'runtime_env': {'PYTHONHOME': 'python', 'GI_TYPELIB_PATH': 'lib/girepository-1.0',
                              'GIO_MODULE_DIR': 'lib/gio/modules',
                              'FONTCONFIG_FILE': 'etc/fonts/fonts.conf',
                              'QT_PLUGIN_PATH': 'lib/qt6/plugins',
                              'GSETTINGS_SCHEMA_DIR': 'share/glib-2.0/schemas'}}
    artifact.validate_recipe(result)
    return result, hashes


def recipe(root, spec):
    """Return a reviewed generic recipe; no write or activation."""
    return _prepare(root, spec)[0]


def pack(root, spec, output, *, patchelf=None, build_evidence=None, expected_files=None):
    result, hashes = _prepare(root, spec)
    modes = None
    if expected_files is not None:
        if hashes != {name: item['sha256'] for name, item in expected_files.items()}:
            raise ContractError('desktop source digests differ from verified assembly')
        modes = {name: item['executable'] for name, item in expected_files.items()}
    metadata = {'desktop-inventory.json': {
        'schema': 'luminophore-desktop-inventory/v1', 'input': spec,
        'recipe': result, 'acceptance': 'NOT_RUN',
        'provenance': 'publisher-declaration',
        'generated_files': ['support/*', 'metadata/*']}, **capability_metadata()}
    if build_evidence is not None:
        metadata['build-assembly.json'] = build_evidence
    return artifact.pack(root, result, output, patchelf=patchelf, expected_sources=hashes,
                         metadata=metadata, expected_modes=modes)
