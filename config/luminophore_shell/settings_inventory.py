"""Static migration coverage guard. Never imports config code or executes Lua.

The checked-in inventory records legacy entry points, not runtime defaults.
It deliberately cannot generate settings: ownership must be assigned explicitly.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re


@dataclass(frozen=True)
class Entry:
    key: str
    source: str
    line: int
    fingerprint: str


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def discover(root: Path) -> dict[str, Entry]:
    """root contains the config and compositor source trees."""
    entries: dict[str, Entry] = {}

    def add(key: str, path: Path, line: int, value: str) -> None:
        if key in entries:
            raise ValueError(f"duplicate declaration: {key}")
        entries[key] = Entry(key, path.relative_to(root).as_posix(), line, fingerprint(value))

    # AST parsing is read-only and does not initialize GTK or touch XDG paths.
    for relative in ("config/luminophore_shell/config.py", "config/luminophore_shell/visual_settings.py"):
        path = root / relative
        tree = ast.parse(path.read_text(), filename=relative)
        for cls in tree.body:
            if not isinstance(cls, ast.ClassDef):
                continue
            if not any(isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                       and d.func.id == "dataclass" for d in cls.decorator_list):
                continue
            for field in cls.body:
                if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                    add(f"shell:{cls.name}.{field.target.id}", path, field.lineno,
                        ast.dump(field, include_attributes=False))

    path = root / "compositor/src/config/values/ConfigValues.cpp"
    source = path.read_text()
    matches = list(re.finditer(r'\bMS<(\w+)>\s*\(\s*"([^"]+)"', source))
    if not matches:
        raise ValueError("compositor option declarations not found; update scanner")
    for match in matches:
        # Type and public name are stable identities; file digest below also
        # detects default/validator edits without pretending to parse C++.
        add("compositor:" + match[2], path, source.count("\n", 0, match.start()) + 1, match[0])

    path = root / "compositor/src/config/lua/bindings/LuaBindingsDispatchers.cpp"
    source = path.read_text()
    matches = list(re.finditer(r'Internal::setFn\(L,\s*"([^"]+)",\s*(\w+)\)', source))
    if not matches:
        raise ValueError("dispatcher registrations not found; update scanner")
    for match in matches:
        add(f"command:{match[2]}:{match[1]}", path,
            source.count("\n", 0, match.start()) + 1, match[0])

    # Entire source digests catch changes to expression/default semantics and
    # new file-level writers that scalar extraction alone cannot understand.
    paths = set((root / "config").glob("*.lua"))
    paths.update((root / "config/luminophore_shell").glob("*.py"))
    paths.update((root / "compositor/src/config").rglob("*.cpp"))
    paths.update((root / "compositor/src/config").rglob("*.hpp"))
    # The packaged cold-start route is part of configuration ownership too.
    # Reviewing only the Shell/compositor misses a package that selects Lua.
    paths.update((root / "Luminophore-OS/runtime/luminophore_runtime").glob("*.py"))
    paths.update((root / "Luminophore-OS/runtime/defaults").rglob("*.lua"))
    paths.update((root / "Luminophore-OS/runtime/defaults").rglob("*.toml"))
    excluded = {"settings_inventory.py"}
    for path in sorted(paths):
        if path.name not in excluded:
            add("source:" + path.relative_to(root).as_posix(), path, 1, path.read_text())
    return entries


# These are evidence slots, not completion inferred from assigned ownership.
ROLES = ("loader", "consumer", "entry", "writer", "recovery", "migration_test")


def _reference(root: Path, ref: object) -> bool:
    if not isinstance(ref, dict) or set(ref) != {"path", "symbol"}:
        return False
    path, symbol = ref["path"], ref["symbol"]
    if not isinstance(path, str) or not isinstance(symbol, str) or not symbol.strip():
        return False
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        return False
    return symbol in target.read_text()


def migration_errors(root: Path, row: dict, routes: dict) -> list[str]:
    key = row["key"]
    status = row.get("migration_state")
    if status not in ("pending", "source-only", "connected"):
        return [f"missing migration state: {key}"]
    route = routes.get(row.get("route"))
    if not isinstance(route, dict) or set(route.get("evidence", {})) != set(ROLES):
        return [f"missing migration route: {key}"]
    errors = []
    for role, ref in route["evidence"].items():
        if ref is not None and not _reference(root, ref):
            errors.append(f"invalid {role} reference: {key}")
        if status == "connected" and ref is None:
            errors.append(f"unconnected {role}: {key}")
    if status == "connected":
        # Explicit production call-site witnesses are required in addition to
        # declarations. This is a static guard, never proof of runtime success.
        calls = route.get("calls", [])
        if not calls:
            errors.append(f"missing production call sites: {key}")
        for call in calls:
            if not _reference(root, call) or "/tests/" in call["path"]:
                errors.append(f"invalid production call site: {key}")
        if not route.get("verified_keys") or key not in route["verified_keys"]:
            errors.append(f"missing per-setting integration coverage: {key}")
        if route.get("legacy_writer_active", True):
            errors.append(f"legacy writer still active: {key}")
    return errors


def validate(root: Path, manifest: dict) -> list[str]:
    if manifest.get("version") != 2 or not isinstance(manifest.get("entries"), list):
        raise ValueError("unsupported inventory format")
    actual = discover(root)
    errors: list[str] = []
    seen: set[str] = set()
    for row in manifest["entries"]:
        key = row["key"]
        if key in seen:
            errors.append(f"duplicate mapping: {key}")
        seen.add(key)
        if row.get("owner") != "luminophore" or row.get("kind") not in ("setting", "policy", "command", "source"):
            errors.append(f"invalid ownership: {key}")
        if not row.get("destination") or not row.get("check"):
            errors.append(f"missing destination/check: {key}")
        errors.extend(migration_errors(root, row, manifest.get("routes", {})))
        entry = actual.get(key)
        if entry is None:
            errors.append(f"removed declaration: {key}")
        elif entry.fingerprint != row.get("fingerprint"):
            errors.append(f"changed declaration: {key}")
    errors.extend(f"unmapped declaration: {key}" for key in sorted(actual.keys() - seen))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    errors = validate(args.root, json.loads(args.manifest.read_text()))
    for error in errors:
        print(error)
    if not errors:
        print("settings inventory: PASS (static coverage only; not runtime acceptance)")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
