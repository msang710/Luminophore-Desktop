"""Explicit, non-destructive NEON -> Luminophore user-data migration.

Run after stopping both shells. Runtime sockets and old release bundles are never
copied. Existing state/cache destinations are preserved. Icon recovery only adds missing
files; differing existing icon data aborts the whole plan before copying.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import tomllib


@dataclass(frozen=True)
class Migration:
    source: Path
    destination: Path
    policy: str = "strict"


def plan(home: Path, env: dict[str, str]) -> list[Migration]:
    state = Path(env.get('XDG_STATE_HOME') or home / '.local/state')
    cache = Path(env.get('XDG_CACHE_HOME') or home / '.cache')
    data = Path(env.get('XDG_DATA_HOME') or home / '.local/share')
    pairs = [
        (state / 'neon-shell', state / 'luminophore-shell'),
        (state / 'neon', state / 'luminophore'),
        (cache / 'neon-shell', cache / 'luminophore-shell'),
        (data / 'icons/neon-shell-arcticons', data / 'icons/luminophore-shell-arcticons'),
    ]
    entries = [Migration(a, b, "icons" if b.name == "luminophore-shell-arcticons"
                         else "preserve" if b.exists() else "strict") for a, b in pairs if a.exists()]
    if (data / 'neon-shell/generated-icons.toml').exists():
        entries.append(Migration(data / 'neon-shell', data / 'luminophore-shell', 'icon-state'))
    return entries


def _regular_bytes(path: Path) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f'Expected a regular icon file: {path}')
    return path.read_bytes()


def _icon_files(entry: Migration) -> dict[Path, bytes]:
    if entry.policy == 'icons':
        files = {}
        for source in sorted(entry.source.rglob('*')):
            if source.is_symlink():
                raise ValueError(f'Symbolic icon paths are not migrated: {source}')
            if source.is_file():
                raw = _regular_bytes(source)
                if source.name == 'index.theme':
                    raw = raw.replace(b'Name=Neon Shell Arcticons', b'Name=Luminophore Shell Arcticons')
                files[source.relative_to(entry.source)] = raw
        return files
    manifest = _regular_bytes(entry.source / 'generated-icons.toml')
    data = tomllib.loads(manifest.decode('utf-8'))
    rows = data.get('icons', {})
    if data.get('version') != 1 or not isinstance(rows, dict):
        raise ValueError('Invalid generated icon approval manifest')
    files = {}
    targets = set()
    for desktop_id, row in rows.items():
        if not isinstance(row, dict):
            raise ValueError(f'Invalid icon approval: {desktop_id}')
        digest, target = row.get('object_sha256'), row.get('target_name')
        if (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]*', target)
                or target in targets or type(row.get('approved_at')) is not int or row['approved_at'] <= 0
                or row.get('strategy') not in {'silhouette', 'edge', 'combined', 'manual'}
                or not isinstance(row.get('source_sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', row['source_sha256'])):
            raise ValueError(f'Invalid icon approval: {desktop_id}')
        targets.add(target)
        relative = Path('icon-objects') / f'{digest}.svg'
        object_path = entry.source / relative
        if object_path.parent.is_symlink():
            raise ValueError(f'Symbolic icon object directory: {object_path.parent}')
        raw = _regular_bytes(object_path)
        overlay = entry.source.parent / 'icons/neon-shell-arcticons/scalable/apps' / f'{target}.svg'
        if hashlib.sha256(raw).hexdigest() != digest or hashlib.sha256(_regular_bytes(overlay)).hexdigest() != digest:
            raise ValueError(f'Approved icon hash mismatch: {desktop_id}')
        files[relative] = raw
    # Publish the approval manifest after all approved objects are available.
    files[Path('generated-icons.toml')] = manifest
    return files


def _validate_destination(destination: Path, files: dict[Path, bytes]) -> None:
    for relative, raw in files.items():
        target = destination / relative
        for parent in (target.parent, *target.parents):
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise FileExistsError(f'Preserving conflicting destination: {parent}')
            if parent == destination.parent:
                break
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_file() or target.read_bytes() != raw:
                raise FileExistsError(f'Preserving conflicting destination: {target}')


def _copy_missing_icons(destination: Path, files: dict[Path, bytes]) -> None:
    for relative, raw in files.items():
        target = destination / relative
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.luminophore-migrate-', dir=target.parent)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            # Hard-link publication never replaces an existing destination.
            os.link(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)



def apply(entries: list[Migration], runtime: Path | None = None) -> None:
    if runtime:
        for name in ('neon-shell', 'luminophore-shell'):
            if (runtime / name / 'control.sock').exists():
                raise RuntimeError('Stop both shells and remove stale runtime sockets before migration')
    # Validate the whole plan before making any destination.
    icon_files = {}
    for entry in entries:
        if not entry.source.is_dir() or entry.source.is_symlink():
            raise ValueError(f'Expected a real source directory: {entry.source}')
        if entry.policy in {'icons', 'icon-state'}:
            icon_files[entry] = _icon_files(entry)
            _validate_destination(entry.destination, icon_files[entry])
        elif entry.policy == 'preserve' and entry.destination.is_dir() and not entry.destination.is_symlink():
            continue
        elif entry.destination.exists() or entry.destination.is_symlink():
            raise FileExistsError(f'Preserving existing destination: {entry.destination}')
    for entry in entries:
        if entry in icon_files:
            _copy_missing_icons(entry.destination, icon_files[entry])
            continue
        if entry.policy == 'preserve' and entry.destination.exists():
            continue
        entry.destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.luminophore-migrate-', dir=entry.destination.parent))
        try:
            shutil.copytree(entry.source, temporary / 'data', symlinks=True)
            if entry.destination.name == 'luminophore-shell-arcticons':
                index = temporary / 'data/index.theme'
                if index.is_file():
                    index.write_text(index.read_text().replace('Name=Neon Shell Arcticons', 'Name=Luminophore Shell Arcticons'))
            # No source deletion: older generations retain their own data paths.
            (temporary / 'data').rename(entry.destination)
        finally:
            shutil.rmtree(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Copy data after both shells are stopped')
    args = parser.parse_args()
    entries = plan(Path.home(), dict(os.environ))
    if args.apply:
        runtime = os.environ.get('XDG_RUNTIME_DIR')
        if not runtime:
            parser.error('XDG_RUNTIME_DIR is required to check shell sockets')
        apply(entries, Path(runtime))
    print(json.dumps({'applied': args.apply, 'entries': [dict(source=str(e.source), destination=str(e.destination), policy=e.policy) for e in entries]}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
