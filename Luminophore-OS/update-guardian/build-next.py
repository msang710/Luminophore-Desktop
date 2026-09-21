#!/usr/bin/env python3
"""Evaluate one observed transaction and build a whole-DE package only when required."""

from __future__ import annotations

import argparse
import fcntl
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import re
import stat
import subprocess
import sys


class ConsumerError(RuntimeError):
    pass


def _follow_module():
    source = Path(__file__).with_name("desktop-follow.py")
    spec = spec_from_file_location("luminophore_desktop_follow", source)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load(path, limit=128 * 1024):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ConsumerError("unbounded-or-nonregular-result")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ConsumerError("result-object-required")
    return value


def _run(program, record, output, timeout):
    result = subprocess.run(
        [str(program), str(record), str(output)], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": "/var/empty", "LANG": "C.UTF-8"},
    )
    if result.returncode:
        raise ConsumerError(f"worker-failed-{result.returncode}")
    return _load(output)


def _validate_receipt(receipt, record, package_root):
    required = {"schema", "transaction", "host_digest", "generation", "version", "package",
                "package_sha256", "components", "provenance", "sbom_digest"}
    if set(receipt) != required or receipt["schema"] != "luminophore-follow-build/v1":
        raise ConsumerError("build-receipt-schema-invalid")
    if receipt["transaction"] != record["transaction"] or receipt["host_digest"] != record["package_set_digest"]:
        raise ConsumerError("build-receipt-host-mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", receipt["generation"]):
        raise ConsumerError("build-generation-invalid")
    if not isinstance(receipt["version"], str) or not receipt["version"]:
        raise ConsumerError("build-version-invalid")
    components = set(receipt["components"]) if isinstance(receipt["components"], list) else set()
    if not {"compositor", "control", "shell", "greeter", "portal"} <= components:
        raise ConsumerError("whole-desktop-components-missing")
    package = (package_root / receipt["package"]).resolve(strict=True)
    if not package.is_relative_to(package_root.resolve()) or package.is_symlink() or not package.is_file():
        raise ConsumerError("package-path-invalid")
    import hashlib
    with package.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != receipt["package_sha256"]:
        raise ConsumerError("package-hash-mismatch")
    return package


def run(args):
    follow = _follow_module()
    for directory in (args.queue, args.work, args.packages):
        if directory.is_symlink() or not directory.is_dir():
            raise ConsumerError("state-directory-invalid")
    with (args.work / "consumer.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        candidates = []
        for path in sorted(args.queue.glob("transaction-*.json")):
            record = follow._validate_record(path, follow.load_json(path))
            if record["state"] == "observed":
                candidates.append((path, record))
        if not candidates:
            return 0
        path, record = candidates[0]
        try:
            follow.transition(path, "evaluating", {"evaluator": str(args.evaluator)})
            evaluation_path = args.work / (record["transaction"] + ".evaluation.json")
            evaluation = _run(args.evaluator, path, evaluation_path, args.timeout)
            status = evaluation.get("status")
            if status == "compatible":
                follow.transition(path, "compatible", evaluation)
                return 0
            if status != "rebuild-required":
                raise ConsumerError("host-" + str(status))
            follow.transition(path, "building", evaluation)
            receipt_path = args.work / (record["transaction"] + ".build.json")
            receipt = _run(args.builder, path, receipt_path, args.timeout)
            package = _validate_receipt(receipt, record, args.packages)
            follow.transition(path, "staged", {
                "receipt": str(receipt_path), "package": str(package),
                "generation": receipt["generation"], "host_digest": receipt["host_digest"],
            })
        except (ConsumerError, OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
            current = follow._validate_record(path, follow.load_json(path))["state"]
            if current in {"evaluating", "building", "staged"}:
                follow.transition(path, "failed", {"reason": str(exc), "retry": "desktop-follow retry " + record["transaction"]})
        return 0


def parser():
    result = argparse.ArgumentParser()
    result.add_argument("--queue", type=Path, default=Path("/var/lib/luminophore-update-guardian/queue"))
    result.add_argument("--work", type=Path, default=Path("/var/lib/luminophore-update-guardian/work"))
    result.add_argument("--packages", type=Path, default=Path("/var/lib/luminophore-update-guardian/packages"))
    result.add_argument("--evaluator", type=Path, default=Path("/usr/lib/luminophore-update-guardian/evaluate-host"))
    result.add_argument("--builder", type=Path, default=Path("/usr/lib/luminophore-update-guardian/build-desktop"))
    result.add_argument("--timeout", type=int, default=43200)
    return result


def main():
    try:
        return run(parser().parse_args())
    except (ConsumerError, OSError, ValueError) as exc:
        print(json.dumps({"state": "failed", "reason": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
