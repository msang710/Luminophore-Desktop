#!/usr/bin/python3
"""Build the pinned portal fork in a fresh output directory, without installation."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def prepare(source, target):
    source, target = Path(source).resolve(strict=True), Path(target).resolve()
    if target.exists() or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError('fresh, separate portal build directory required')
    lock = json.loads((ROOT / 'portal/upstream.json').read_text())
    target.mkdir(parents=True)
    try:
        for name, expected in lock['files'].items():
            path = source / name
            if path.is_symlink() or not path.resolve(strict=True).is_relative_to(source):
                raise ValueError('portal source escapes root: ' + name)
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != expected['sha256']:
                raise ValueError('portal source digest mismatch: ' + name)
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            destination.chmod(0o755 if expected['executable'] else 0o644)
        subprocess.run(['/usr/bin/patch', '--batch', '--fuzz=0', '-p1', '-i',
                        str(ROOT / 'portal/namespace.patch')], cwd=target, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except BaseException:
        shutil.rmtree(target)
        raise
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError('portal output already exists')
    source = prepare(args.source, output / 'source')
    if args.prepare_only:
        return
    build = output / 'build'
    subprocess.run(['/usr/bin/cmake', '-S', str(source), '-B', str(build), '-G', 'Ninja',
                    '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_INSTALL_PREFIX=/usr',
                    '-DSYSTEMD_SERVICES=OFF', '-DFETCHCONTENT_FULLY_DISCONNECTED=ON'], check=True)
    subprocess.run(['/usr/bin/cmake', '--build', str(build), '--parallel', '2'], check=True)
    prefix = output / 'package/usr/lib/luminophore'
    (prefix / 'bin').mkdir(parents=True)
    for name, built in [('xdg-desktop-portal-luminophore', 'xdg-desktop-portal-hyprland'),
                        ('luminophore-share-picker', 'hyprland-share-picker/hyprland-share-picker')]:
        shutil.copy2(build / built, prefix / 'bin' / name)
    license_dir = prefix / 'share/licenses/luminophore-portal'
    license_dir.mkdir(parents=True)
    shutil.copy2(source / 'LICENSE', license_dir / 'LICENSE')


if __name__ == '__main__':
    main()
