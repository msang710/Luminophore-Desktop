"""Shared-host ABI and capability contract, separate from release integrity."""

from copy import deepcopy
from pathlib import Path

from . import inventory
from .common import ContractError, digest, identifier, identity, relative, within


SCHEMA = "luminophore-host-contract/v1"
STATUSES = ("compatible", "rebuild-required", "incompatible", "unknown")
CHANGE_POLICIES = {"compatible", "rebuild-required"}


def _seal(value):
    value = deepcopy(value)
    value.pop("digest", None)
    value["digest"] = identity(value)
    return value


def validate(value):
    if not isinstance(value, dict) or set(value) != {
        "schema", "providers", "protocols", "capabilities", "graphics_stack", "digest"
    } or value.get("schema") != SCHEMA:
        raise ContractError("invalid host contract schema")
    if identity({key: item for key, item in value.items() if key != "digest"}) != value.get("digest"):
        raise ContractError("host contract digest mismatch")
    if not isinstance(value["providers"], dict):
        raise ContractError("invalid host providers")
    for path, provider in value["providers"].items():
        relative(path)
        if not isinstance(provider, dict) or set(provider) != {
            "baseline_sha256", "soname", "required_versions", "role", "change_policy"
        }:
            raise ContractError("invalid host provider")
        identifier(provider["baseline_sha256"])
        if provider["soname"] is not None and (not isinstance(provider["soname"], str) or not provider["soname"]):
            raise ContractError("invalid host provider SONAME")
        versions = provider["required_versions"]
        if not isinstance(versions, list) or versions != sorted(set(versions)) or any(
            not isinstance(item, str) or not item for item in versions
        ):
            raise ContractError("invalid host provider versions")
        if provider["role"] not in {"abi", "loader", "graphics-loader", "graphics-vendor"}:
            raise ContractError("invalid host provider role")
        if provider["change_policy"] not in CHANGE_POLICIES:
            raise ContractError("invalid host provider change policy")
    for key in ("protocols", "capabilities", "graphics_stack"):
        if not isinstance(value[key], dict) or any(
            not isinstance(name, str) or not name for name in value[key]
        ):
            raise ContractError("invalid host " + key)
    if any(not isinstance(family, str) or not family for family in value["graphics_stack"].values()):
        raise ContractError("invalid graphics stack family")
    return value


def _role(path, info):
    name = Path(path).name.lower()
    if info.get("interpreter") is None and info.get("soname") is None and "ld-" in name:
        return "loader"
    if any(token in name for token in ("libegl", "libglx", "libgl.so", "libgbm", "libdrm")):
        return "graphics-loader"
    if any(token in path.lower() for token in ("/dri/", "nvidia", "radeon", "nouveau", "intel")):
        return "graphics-vendor"
    return "abi"


def _graphics_family(path):
    lowered = path.lower()
    if "nvidia" in lowered:
        return "nvidia"
    if any(token in lowered for token in ("/dri/", "mesa", "radeon", "nouveau", "intel")):
        return "mesa"
    return "neutral"


def create(system_files, release_elf, host_root, *, inspect_fn=inventory.inspect):
    if not isinstance(system_files, dict) or not isinstance(release_elf, dict):
        raise ContractError("host contract inputs must be objects")
    required = set()
    for info in release_elf.values():
        if not isinstance(info, dict):
            raise ContractError("invalid release ELF inventory")
        required.update(info.get("versions", ()))
    providers, graphics = {}, {}
    for path, baseline in sorted(system_files.items()):
        relative(path)
        identifier(baseline)
        target = within(host_root, path)
        if digest(target) != baseline:
            raise ContractError("host provider changed while creating contract: " + path)
        info = inspect_fn(target)
        if info is None:
            raise ContractError("shared host provider is not ELF: " + path)
        role = _role(path, info)
        providers[path] = {
            "baseline_sha256": baseline,
            "soname": info.get("soname"),
            "required_versions": sorted(required & set(info.get("versions", ()))),
            "role": role,
            "change_policy": "compatible",
        }
        if role.startswith("graphics-"):
            graphics[path] = _graphics_family(path)
    return _seal({
        "schema": SCHEMA,
        "providers": providers,
        "protocols": {},
        "capabilities": {},
        "graphics_stack": graphics,
    })


def with_requirements(contract, *, protocols=None, capabilities=None):
    result = deepcopy(validate(contract))
    result["protocols"] = deepcopy(protocols or {})
    result["capabilities"] = deepcopy(capabilities or {})
    return validate(_seal(result))


def with_change_policy(contract, policies):
    result = deepcopy(validate(contract))
    if not isinstance(policies, dict):
        raise ContractError("change policies must be an object")
    for path, policy in policies.items():
        if path not in result["providers"] or policy not in CHANGE_POLICIES:
            raise ContractError("invalid host change policy")
        result["providers"][path]["change_policy"] = policy
    return validate(_seal(result))


def with_graphics_stack(contract, stack):
    result = deepcopy(validate(contract))
    result["graphics_stack"] = deepcopy(stack)
    return validate(_seal(result))


def _satisfies(expected, actual):
    if isinstance(expected, bool):
        return actual is expected
    if type(expected) is int:
        return type(actual) is int and actual >= expected
    return actual == expected


def evaluate(contract, host_root, *, protocols=None, capabilities=None,
             inspect_fn=inventory.inspect):
    contract = validate(contract)
    incompatible, unknown, rebuild, notes = [], [], [], []
    for path, expected in contract["providers"].items():
        try:
            target = within(host_root, path)
            current_hash = digest(target)
            info = inspect_fn(target)
        except (OSError, ContractError) as exc:
            incompatible.append("provider-missing:" + path)
            continue
        if info is None:
            unknown.append("provider-not-elf:" + path)
            continue
        if info.get("soname") != expected["soname"]:
            incompatible.append("soname-missing:" + path)
        missing = sorted(set(expected["required_versions"]) - set(info.get("versions", ())))
        if missing:
            incompatible.append("symbol-version-missing:" + path + ":" + ",".join(missing))
        if current_hash != expected["baseline_sha256"]:
            if expected["change_policy"] == "rebuild-required":
                rebuild.append("provider-changed:" + path)
            else:
                notes.append("content-changed-compatible")
    for label, expected_set, actual_set in (
        ("protocol", contract["protocols"], protocols),
        ("capability", contract["capabilities"], capabilities),
    ):
        if expected_set and actual_set is None:
            unknown.append(label + "-inventory-unavailable")
            continue
        actual_set = actual_set or {}
        for name, expected in expected_set.items():
            if name not in actual_set or not _satisfies(expected, actual_set[name]):
                incompatible.append(label + "-missing:" + name)
    families = {family for family in contract["graphics_stack"].values() if family != "neutral"}
    if len(families) > 1:
        incompatible.append("graphics-stack-mixed:" + ",".join(sorted(families)))
    reasons = incompatible or unknown or rebuild or sorted(set(notes))
    status = ("incompatible" if incompatible else "unknown" if unknown else
              "rebuild-required" if rebuild else "compatible")
    return {
        "schema": "luminophore-host-evaluation/v1",
        "status": status,
        "reasons": reasons,
        "contract": contract["digest"],
    }


def require_compatible(contract, host_root, **kwargs):
    result = evaluate(contract, host_root, **kwargs)
    if result["status"] != "compatible":
        raise ContractError("host compatibility " + result["status"] + ": " + "; ".join(result["reasons"]))
    return result
