"""Prepare a relocatable makepkg input without installation or source rebuilding."""
import argparse
from pathlib import Path
import shutil
import re
import tarfile
import sys
from . import artifact
from .common import ContractError, digest


WHOLE_DESKTOP_COMPONENTS = {'compositor', 'control', 'shell', 'greeter', 'portal'}
USER_UNITS = (
    'luminophore-session.target', 'luminophore-shell.service',
    'luminophore-spotify.service', 'luminophore-polkit-agent.service',
    'luminophore-portal.service', 'luminophore-shell-failure.service',
    'luminophore-input-method.service', 'luminophore-wallpaper.service',
    'luminophore-session-lock.service', 'luminophore-idle-lock.service',
    'luminophore-secret-service.service',
)


def package_files(migration, assets, units):
    """Return the static package payload without generating or installing it."""
    migration, assets, units = map(Path, (migration, assets, units))
    runtime = migration / 'Luminophore-OS/runtime'
    guardian = migration / 'Luminophore-OS/update-guardian'
    files = []

    def add(source, destination, mode=0o644):
        files.append((Path(source), destination, mode))

    for module in sorted((runtime / 'luminophore_runtime').glob('*.py')):
        add(module, 'usr/lib/luminophore/runtime/luminophore_runtime/' + module.name)
    add(runtime / 'luminophore-runtime',
        'usr/lib/luminophore/runtime/luminophore-runtime', 0o755)
    add(assets / 'luminophore-session', 'usr/bin/luminophore-session', 0o755)
    add(assets / 'luminophore-shell', 'usr/bin/luminophore-shell', 0o755)
    add(assets / 'luminophore-admin', 'usr/lib/luminophore/luminophore-admin', 0o755)
    for name, destination in (
        ('luminophore.desktop', 'usr/share/wayland-sessions/luminophore.desktop'),
        ('luminophore-portals.conf', 'usr/share/xdg-desktop-portal/luminophore-portals.conf'),
        ('luminophore.portal', 'usr/share/xdg-desktop-portal/portals/luminophore.portal'),
        ('org.freedesktop.impl.portal.desktop.luminophore.service',
         'usr/share/dbus-1/services/org.freedesktop.impl.portal.desktop.luminophore.service'),
        ('04-luminophore-remove.hook', 'usr/share/libalpm/hooks/04-luminophore-remove.hook'),
    ):
        add(assets / name, destination)
    for name in USER_UNITS:
        add(units / name, 'usr/lib/systemd/user/' + name)
    add(units / 'luminophore-lock.ini', 'usr/share/luminophore/gtklock.ini')
    add(units / 'luminophore-lock.css', 'usr/share/luminophore/gtklock.css')
    add(units / 'app-org.fcitx.Fcitx5@autostart.service.d/luminophore.conf',
        'usr/lib/systemd/user/app-org.fcitx.Fcitx5@autostart.service.d/luminophore.conf')

    add(runtime / 'files/luminophore-greeter-session',
        'usr/lib/luminophore/luminophore-greeter-session', 0o755)
    add(runtime / 'files/greetd-luminophore.toml',
        'usr/lib/luminophore/greetd/luminophore.toml')
    add(runtime / 'profiles/desktop-capabilities.json',
        'usr/share/luminophore/desktop-capabilities.json')

    add(guardian / 'hooks/95-luminophore-update-observer.hook',
        'usr/share/libalpm/hooks/95-luminophore-update-observer.hook')
    add(guardian / 'libexec/luminophore-update-observe',
        'usr/lib/luminophore-update-guardian/luminophore-update-observe', 0o755)
    for source, destination in (
        ('desktop-follow.py', 'desktop-follow'),
        ('desktop-follow.py', 'desktop-follow.py'),
        ('build-next.py', 'build-next'),
        ('install-release.py', 'install-release'),
    ):
        add(guardian / source, 'usr/lib/luminophore-update-guardian/' + destination, 0o755)
    for name in (
        'luminophore-update-guardian.path', 'luminophore-update-guardian.service',
        'luminophore-release-install@.service',
    ):
        add(guardian / 'systemd' / name, 'usr/lib/systemd/system/' + name)
    add(guardian / 'sysusers/luminophore-update-guardian.conf',
        'usr/lib/sysusers.d/luminophore-update-guardian.conf')
    add(guardian / 'tmpfiles/luminophore-update-guardian.conf',
        'usr/lib/tmpfiles.d/luminophore-update-guardian.conf')
    return files


def follow_receipt(release, package, *, transaction, host_digest, version,
                   components, provenance, sbom_digest):
    """Bind a whole-DE package to one observed host transaction."""
    manifest = artifact.verify(release)
    package = Path(package)
    if package.is_symlink() or not package.is_file():
        raise ContractError('package must be a regular file')
    if set(components) != WHOLE_DESKTOP_COMPONENTS:
        raise ContractError('whole desktop component set required')
    for value in (transaction, host_digest, sbom_digest):
        if not re.fullmatch(r'[0-9a-f]{64}', value):
            raise ContractError('invalid follow-build digest')
    if not isinstance(provenance, dict) or not provenance:
        raise ContractError('build provenance required')
    return {
        'schema': 'luminophore-follow-build/v1', 'transaction': transaction,
        'host_digest': host_digest, 'generation': manifest['generation'],
        'version': version, 'package': package.name, 'package_sha256': digest(package),
        'components': sorted(components), 'provenance': provenance,
        'sbom_digest': sbom_digest,
    }


def prepare(release, output, *, version="0.1.0", assets=None, units=None):
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*(?:[a-z][a-z0-9.]*)?", version):
        raise ContractError("explicit package version required")
    manifest = artifact.verify(release)
    runtime = Path(__file__).resolve().parent
    migration = runtime.parents[2]
    assets = Path(assets) if assets else migration / 'compositor/luminophore/packaging/arch'
    units = Path(units) if units else migration / 'config/systemd'
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    payload = output / 'payload'
    def copy(source, name, mode=0o644):
        target = payload / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(mode)
    for source, destination, mode in package_files(migration, assets, units):
        copy(source, destination, mode)
    for name, args in [('luminophore', ''), ('luminophore-compositor', ''),
                       ('luminophorectl', 'component control')]:
        path = payload / 'usr/bin' / name
        path.write_text('#!/bin/sh\nexec /usr/bin/luminophore-session ' + args + ' "$@"\n')
        path.chmod(0o755)
    settings_name = 'io.github.msang710.LuminophoreSettings.desktop'
    settings = payload / 'usr/share/applications' / settings_name
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text((units.parent / 'desktop' / settings_name).read_text().replace(
        '@LUMINOPHORE_SETTINGS_EXEC@', '/usr/bin/luminophore-shell'))
    for profile in ('eco', 'balanced', 'gaming'):
        name = f'luminophore-{profile}-capped'
        copy(units.parent / 'power-profiles' / name / 'tuned.conf',
             f'usr/lib/tuned/profiles/{name}/tuned.conf')
    target = payload / 'usr/lib/luminophore/releases' / manifest['generation']
    shutil.copytree(release, target)
    artifact.verify(target, expected=manifest['generation'])
    archive = output / 'payload.tar'
    with tarfile.open(archive, 'w', format=tarfile.PAX_FORMAT) as tar:
        for path in [payload, *sorted(payload.rglob('*'))]:
            info = tar.gettarinfo(str(path), arcname=path.relative_to(output).as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mtime = 0
            if path.is_file():
                with path.open('rb') as stream:
                    tar.addfile(info, stream)
            else:
                tar.addfile(info)
    template = (assets / 'PKGBUILD.in').read_text()
    (output / 'PKGBUILD').write_text(template.replace('@VERSION@', version)
                                   .replace('@PAYLOAD_SHA256@', digest(archive)))
    (output / 'luminophore.install').write_text(
        'post_install() {\n    /usr/lib/luminophore/luminophore-admin ' + manifest['generation']
        + '\n}\npost_upgrade() { post_install; }\n'
        '# Removal intentionally retains /var/lib/luminophore and user configuration.\n')
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(prepare(args.release, args.output, version=args.version))
        return 0
    except (ContractError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
