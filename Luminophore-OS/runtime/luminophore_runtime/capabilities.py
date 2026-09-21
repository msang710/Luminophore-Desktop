"""Validated desktop capability and package ownership contract."""

from collections import namedtuple
import json
from pathlib import Path
import re
import shlex

from .common import ContractError, identity


SCHEMA = "luminophore-desktop-capabilities/v1"
OWNERS = {"private-release", "shared-host", "required-package", "optional-provider", "user-application"}
REQUIREMENTS = {"required", "optional", "one-of"}
KINDS = {"command", "dbus-service", "resource"}
DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "profiles/desktop-capabilities.json"
CommandGroups = namedtuple("CommandGroups", "required optional")


def validate(value):
    if not isinstance(value, dict) or set(value) != {"schema", "packages", "capabilities"} or value.get("schema") != SCHEMA:
        raise ContractError("invalid capability manifest schema")
    packages = value["packages"]
    if not isinstance(packages, dict) or set(packages) != {"required", "private", "optional"}:
        raise ContractError("invalid capability package contract")
    for group in ("required", "private"):
        rows = packages[group]
        if not isinstance(rows, list) or any(not isinstance(x, str) or not x for x in rows) or len(rows) != len(set(rows)):
            raise ContractError("invalid capability package list")
    optional = packages["optional"]
    if not isinstance(optional, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in optional.items()):
        raise ContractError("invalid optional package contract")
    if set(packages["required"]) & set(packages["private"]) or set(packages["required"]) & set(optional):
        raise ContractError("package ownership overlap")
    rows = value["capabilities"]
    if not isinstance(rows, list) or not rows:
        raise ContractError("capability entries required")
    ids, commands = set(), set()
    required_packages_set = set(packages["required"])
    known_packages = required_packages_set | set(packages["private"]) | set(optional)
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "kind", "names", "feature", "ownership", "requirement", "packages"}:
            raise ContractError("invalid capability entry")
        if not isinstance(row["id"], str) or not row["id"] or row["id"] in ids:
            raise ContractError("invalid or duplicate capability id")
        ids.add(row["id"])
        if row["kind"] not in KINDS or row["ownership"] not in OWNERS or row["requirement"] not in REQUIREMENTS:
            raise ContractError("invalid capability classification")
        if not isinstance(row["feature"], str) or not row["feature"]:
            raise ContractError("capability feature required")
        if not isinstance(row["names"], list) or not row["names"] or any(not isinstance(x, str) or not x for x in row["names"]):
            raise ContractError("capability names required")
        if row["kind"] == "command":
            overlap = commands & set(row["names"])
            if overlap:
                raise ContractError("command duplicate: " + sorted(overlap)[0])
            commands.update(row["names"])
        if not isinstance(row["packages"], list) or any(x not in known_packages for x in row["packages"]):
            raise ContractError("capability references unknown package")
        if row["ownership"] in {"shared-host", "required-package"} and row["requirement"] == "required":
            if not row["packages"] or not set(row["packages"]) <= required_packages_set:
                raise ContractError("required capability package is not required")
        if row["ownership"] in {"optional-provider", "user-application"} and row["requirement"] == "required":
            raise ContractError("optional capability cannot be required")
    return value


def load_manifest(path=None):
    path = Path(path) if path is not None else DEFAULT_MANIFEST
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError("cannot read capability manifest: " + str(path)) from exc
    return validate(value)


def manifest_digest(manifest=None):
    value = validate(manifest) if manifest is not None else load_manifest()
    return identity(value)


def required_packages(manifest):
    return tuple(sorted(validate(manifest)["packages"]["required"]))


def private_packages(manifest):
    return tuple(sorted(validate(manifest)["packages"]["private"]))


def optional_packages(manifest):
    return dict(sorted(validate(manifest)["packages"]["optional"].items()))


def command_groups(manifest):
    required, optional = set(), set()
    for row in validate(manifest)["capabilities"]:
        if row["kind"] != "command" or row["ownership"] == "private-release":
            continue
        target = required if row["requirement"] == "required" else optional
        target.update(row["names"])
    return CommandGroups(tuple(sorted(required)), tuple(sorted(optional)))


def pkgbuild_arrays(text):
    result = {}
    for name in ("depends", "optdepends"):
        match = re.search(r"(?ms)^" + name + r"=\((.*?)\)", text)
        if not match:
            raise ContractError("PKGBUILD lacks " + name)
        try:
            entries = shlex.split(match.group(1), comments=True, posix=True)
        except ValueError as exc:
            raise ContractError("invalid PKGBUILD " + name) from exc
        result[name] = tuple(sorted(entry.split(":", 1)[0].strip() for entry in entries))
    return result


def validate_pkgbuild(text, manifest):
    arrays = pkgbuild_arrays(text)
    if set(arrays["depends"]) != set(required_packages(manifest)) - set(private_packages(manifest)):
        raise ContractError("PKGBUILD required dependencies differ from capability manifest")
    if set(arrays["optdepends"]) != set(optional_packages(manifest)):
        raise ContractError("PKGBUILD optional dependencies differ from capability manifest")
    return arrays
