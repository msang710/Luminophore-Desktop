"""Session-time native configuration initialization. Never executes legacy Lua."""
import argparse
import ctypes
import os
from pathlib import Path
import shutil
import tempfile
import tomllib

from .settings_bundle import decode_bundle
from .settings_migration import inspect, stage
from .settings_store import FILES, StoreError, _sync


def prepare(target, defaults):
    target, defaults = Path(target), Path(defaults)
    if not target.is_absolute() or target.is_symlink():
        raise StoreError('absolute non-symlink user config directory required')
    if (target/'settings.toml').exists():
        docs = {}
        for name in FILES:
            path = target/name
            if path.is_symlink() or not path.is_file():
                raise StoreError('incomplete native settings bundle: '+name)
            docs[name] = tomllib.loads(path.read_text())
        decode_bundle(docs, target/'settings.toml')
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    migration = None
    if target.exists():
        migration = inspect(target, packaged=True)
        if migration.unsupported:
            raise StoreError('legacy migration requires resolution: '+'; '.join(migration.unsupported))
        from .settings_store import _digest
        backup = target.parent/('.luminophore-migration-'+_digest(migration.documents))
        stage(migration, target, backup)
        documents = migration.documents
    else:
        documents = {name: (defaults/name).read_text() for name in FILES}
    decode_bundle({name: tomllib.loads(value) for name, value in documents.items()}, target/'settings.toml')
    incoming = Path(tempfile.mkdtemp(prefix='.luminophore-native-', dir=target.parent))
    exchanged = False
    try:
        # Preserve referenced resources and unrelated settings, including exact
        # legacy originals. Only the five native inputs become authoritative.
        if target.exists():
            shutil.copytree(target, incoming, dirs_exist_ok=True, symlinks=True)
        for name, value in documents.items():
            path = incoming/name
            if path.exists() or path.is_symlink():
                if path.is_symlink() or not migration or name not in migration.originals or path.read_bytes() != migration.originals[name]:
                    raise StoreError('native input appeared during migration: '+name)
                # Only the staging copy is replaced. Both migration.sources and
                # the retained source tree still preserve the inspected input.
                path.unlink()
            with path.open('x') as stream:
                os.chmod(path, 0o600)
                stream.write(value); stream.flush(); os.fsync(stream.fileno())
        _sync(incoming)
        if target.exists():
            # Re-inspect after copying: user changes invalidate this candidate.
            current = inspect(target, packaged=True)
            if current.unsupported or current.sources != migration.sources:
                raise StoreError('legacy configuration changed during migration')
            libc = ctypes.CDLL(None, use_errno=True)
            exchange = libc.renameat2
            exchange.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            exchange.restype = ctypes.c_int
            if exchange(-100, os.fsencode(incoming), -100, os.fsencode(target), 2):
                number = ctypes.get_errno()
                raise OSError(number, os.strerror(number))
            exchanged = True
            _sync(target.parent)
            # Exchange leaves the full old directory here, never deletes it.
            retained = backup/'source-tree'
            if retained.exists():
                retained = backup/('source-tree-'+incoming.name)
            os.rename(incoming, retained)
            _sync(backup)
        else:
            libc = ctypes.CDLL(None, use_errno=True)
            rename = libc.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            if rename(-100, os.fsencode(incoming), -100, os.fsencode(target), 1):
                number = ctypes.get_errno()
                raise OSError(number, os.strerror(number))
        _sync(target.parent)
        return target
    finally:
        if incoming.exists() and not exchanged:
            shutil.rmtree(incoming)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--defaults', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.target, args.defaults)


if __name__ == '__main__':
    main()
