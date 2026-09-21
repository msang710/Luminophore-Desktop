#!/usr/bin/env python3
"""Narrow root installer for a verified Luminophore follow-build receipt."""

from __future__ import annotations

import argparse
from importlib.util import module_from_spec, spec_from_file_location
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


class InstallError(RuntimeError):
    pass


def _follow():
    spec = spec_from_file_location("luminophore_desktop_follow", Path(__file__).with_name("desktop-follow.py"))
    module = module_from_spec(spec); spec.loader.exec_module(module); return module


def _contained(root, value):
    root = Path(root).resolve(strict=True)
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or path.is_symlink():
        raise InstallError("path-outside-trusted-root")
    return resolved


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _vercmp(left, right):
    result = subprocess.run(["/usr/bin/vercmp", left, right], capture_output=True,
                            text=True, check=True, timeout=5,
                            env={"PATH": "/usr/bin:/bin", "LANG": "C"})
    return int(result.stdout.strip())


def install(transaction, *, queue, receipts, packages, dbpath, generations,
            runner=subprocess.run, compare=None, expected_uid=0):
    if not re.fullmatch(r"[0-9a-f]{64}", transaction):
        raise InstallError("invalid-transaction")
    follow = _follow()
    record_path = _contained(queue, "transaction-" + transaction + ".json")
    record = follow._validate_record(record_path, follow.load_json(record_path))
    if record["state"] != "staged":
        raise InstallError("transaction-not-staged")
    receipt_path = _contained(receipts, record["detail"].get("receipt", ""))
    receipt = follow.load_json(receipt_path)
    if (receipt.get("schema") != "luminophore-follow-build/v1"
            or receipt.get("transaction") != transaction
            or receipt.get("host_digest") != record["package_set_digest"]):
        raise InstallError("receipt-identity-mismatch")
    current_digest = follow.identity(follow.package_set(dbpath))
    if current_digest != receipt["host_digest"]:
        raise InstallError("host-package-set-changed")
    package = _contained(packages, receipt.get("package", ""))
    metadata = package.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != expected_uid:
        raise InstallError("package-ownership-invalid")
    if _digest(package) != receipt.get("package_sha256"):
        raise InstallError("package-hash-mismatch")
    installed = follow.package_set(dbpath).get("luminophore-compositor")
    compare = compare or _vercmp
    if installed and compare(receipt.get("version", ""), installed) < 0:
        raise InstallError("downgrade-policy-violation")
    process = runner(
        ["/usr/bin/pacman", "-U", "--noconfirm", "--", str(package)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, timeout=600,
    )
    if process.returncode:
        raise InstallError("pacman-install-failed")
    generation = receipt.get("generation")
    target = _contained(generations, generation)
    if not target.is_dir():
        raise InstallError("generation-admission-missing")
    follow.transition(record_path, "installed", {"generation": generation, "package": package.name})
    return generation


def parser():
    p = argparse.ArgumentParser(); p.add_argument("transaction")
    p.add_argument("--queue", type=Path, default=Path("/var/lib/luminophore-update-guardian/queue"))
    p.add_argument("--receipts", type=Path, default=Path("/var/lib/luminophore-update-guardian/work"))
    p.add_argument("--packages", type=Path, default=Path("/var/lib/luminophore-update-guardian/packages"))
    p.add_argument("--dbpath", type=Path, default=Path("/var/lib/pacman/local"))
    p.add_argument("--generations", type=Path, default=Path("/var/lib/luminophore/generations")); return p


def main():
    args = parser().parse_args()
    try:
        install(args.transaction, queue=args.queue, receipts=args.receipts, packages=args.packages,
                dbpath=args.dbpath, generations=args.generations)
    except (InstallError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main())
