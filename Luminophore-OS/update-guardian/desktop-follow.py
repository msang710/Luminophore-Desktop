#!/usr/bin/env python3
"""Fail-open pacman observation and durable Luminophore follow-up state."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from datetime import datetime, timezone


CAPABILITY_SCHEMA = "luminophore-desktop-capabilities/v1"
RECORD_SCHEMA = "luminophore-desktop-follow/v1"
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+:~-]{0,95}")
GENERATION = re.compile(r"[0-9a-f]{64}")
MAX_PACKAGES = 8192
MAX_PENDING = 64
TRANSITIONS = {
    "observed": {"evaluating", "failed"},
    "evaluating": {"compatible", "building", "failed"},
    "building": {"staged", "failed"},
    "staged": {"installed", "failed"},
    "compatible": set(),
    "installed": set(),
    "failed": {"observed"},
}


class FollowError(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def identity(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_json(path, value, mode=0o640):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_json(path, limit=1024 * 1024):
    metadata = Path(path).lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
        raise FollowError("JSON must be a bounded regular file")
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FollowError("invalid JSON") from exc
    if not isinstance(value, dict):
        raise FollowError("JSON object required")
    return value


def watched_packages(manifest_path):
    value = load_json(manifest_path)
    if value.get("schema") != CAPABILITY_SCHEMA or not isinstance(value.get("packages"), dict):
        raise FollowError("invalid capability manifest")
    required = value["packages"].get("required")
    if not isinstance(required, list) or any(not isinstance(item, str) or not item for item in required):
        raise FollowError("invalid required package set")
    watched = set(required)
    watched.add("linux-cachyos")
    for row in value.get("capabilities", []):
        if isinstance(row, dict) and row.get("ownership") == "shared-host":
            watched.update(row.get("packages", []))
    return watched


def _field(lines, marker):
    try:
        position = lines.index(marker)
        return lines[position + 1]
    except (ValueError, IndexError):
        raise FollowError("invalid pacman desc")


def package_set(dbpath):
    result = {}
    entries = sorted(Path(dbpath).glob("*/desc"))
    if len(entries) > MAX_PACKAGES:
        raise FollowError("package set exceeds bound")
    for desc in entries:
        if desc.is_symlink() or desc.stat().st_size > 64 * 1024:
            raise FollowError("invalid pacman desc file")
        lines = desc.read_text(encoding="utf-8", errors="strict").splitlines()
        name, version = _field(lines, "%NAME%"), _field(lines, "%VERSION%")
        if not name or "/" in name or VERSION.fullmatch(version) is None or name in result:
            raise FollowError("invalid installed package identity")
        result[name] = version
    return dict(sorted(result.items()))


def selected_release(path):
    try:
        value = load_json(path)
    except (OSError, FollowError):
        return None
    selected = value.get("selected")
    return selected if isinstance(selected, str) and GENERATION.fullmatch(selected) else None


def precheck(targets, manifest_path):
    watched = watched_packages(manifest_path)
    relevant = sorted({item.strip() for item in targets if item.strip()} & watched)
    return {
        "schema": "luminophore-desktop-precheck/v1",
        "relevant": relevant,
        "action": "evaluate-after-transaction" if relevant else "none",
        "pacman_policy": "nonblocking",
    }


def observe(targets, *, dbpath, queue, manifest_path, release_state,
            observed_at=None, max_pending=MAX_PENDING):
    queue = Path(queue)
    if queue.is_symlink() or not queue.is_dir():
        raise FollowError("queue unavailable")
    watched = watched_packages(manifest_path)
    changed_names = sorted({item.strip() for item in targets if item.strip()} & watched)
    if not changed_names:
        return None
    installed = package_set(dbpath)
    changes = [{"name": name, "version": installed[name]} for name in changed_names if name in installed]
    if not changes:
        return None
    package_digest = identity(installed)
    release_digest = selected_release(release_state)
    transaction = identity({
        "package_set_digest": package_digest,
        "changes": changes,
        "release_digest": release_digest,
    })
    final = queue / ("transaction-" + transaction + ".json")
    lock_path = queue / ".observer.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if final.exists():
            return final
        if len(list(queue.glob("transaction-*.json"))) >= min(max_pending, MAX_PENDING):
            raise FollowError("queue full")
        timestamp = observed_at or now()
        record = {
            "schema": RECORD_SCHEMA,
            "transaction": transaction,
            "state": "observed",
            "observed_at": timestamp,
            "updated_at": timestamp,
            "package_set_digest": package_digest,
            "changes": changes,
            "release_digest": release_digest,
            "detail": {},
            "last_error": None,
            "history": [{"state": "observed", "at": timestamp}],
        }
        atomic_json(final, record)
    return final


def _validate_record(path, record):
    required = {"schema", "transaction", "state", "observed_at", "updated_at",
                "package_set_digest", "changes", "release_digest", "detail",
                "last_error", "history"}
    if set(record) != required or record.get("schema") != RECORD_SCHEMA:
        raise FollowError("invalid follow record")
    if record["state"] not in TRANSITIONS or not GENERATION.fullmatch(record["transaction"]):
        raise FollowError("invalid follow state")
    if Path(path).name != "transaction-" + record["transaction"] + ".json":
        raise FollowError("transaction filename mismatch")
    return record


def transition(path, target, detail, *, at=None):
    path = Path(path)
    record = _validate_record(path, load_json(path))
    if target not in TRANSITIONS[record["state"]]:
        raise FollowError("invalid state transition")
    if not isinstance(detail, dict):
        raise FollowError("transition detail must be an object")
    timestamp = at or now()
    record["state"] = target
    record["updated_at"] = timestamp
    record["detail"] = detail
    record["last_error"] = detail.get("reason") if target == "failed" else None
    record["history"].append({"state": target, "at": timestamp})
    atomic_json(path, record)
    return record


def resume(queue):
    resumed = 0
    for path in sorted(Path(queue).glob("transaction-*.json")):
        record = _validate_record(path, load_json(path))
        if record["state"] not in {"evaluating", "building"}:
            continue
        prior = record["state"]
        record["state"] = "observed"
        record["updated_at"] = now()
        record["last_error"] = "interrupted:" + prior
        record["detail"] = {"resume_from": prior}
        record["history"].append({"state": "observed", "at": record["updated_at"]})
        atomic_json(path, record)
        resumed += 1
    return resumed


def parser():
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    observe_parser = sub.add_parser("observe")
    observe_parser.add_argument("--dbpath", type=Path, default=Path("/var/lib/pacman/local"))
    observe_parser.add_argument("--queue", type=Path, default=Path("/var/lib/luminophore-update-guardian/queue"))
    observe_parser.add_argument("--manifest", type=Path, default=Path("/usr/share/luminophore/desktop-capabilities.json"))
    observe_parser.add_argument("--release-state", type=Path, default=Path("/var/lib/luminophore/state.json"))
    observe_parser.add_argument("--max-pending", type=int, default=16)
    resume_parser = sub.add_parser("resume")
    resume_parser.add_argument("--queue", type=Path, default=Path("/var/lib/luminophore-update-guardian/queue"))
    precheck_parser = sub.add_parser("precheck")
    precheck_parser.add_argument("targets", nargs="*")
    precheck_parser.add_argument("--manifest", type=Path, default=Path("/usr/share/luminophore/desktop-capabilities.json"))
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "observe":
            observe(sys.stdin, dbpath=args.dbpath, queue=args.queue, manifest_path=args.manifest,
                    release_state=args.release_state, max_pending=args.max_pending)
        elif args.command == "resume":
            resume(args.queue)
        else:
            print(json.dumps(precheck(args.targets, args.manifest), sort_keys=True))
    except (FollowError, OSError, UnicodeError, ValueError):
        # This process is a PostTransaction observer. Its failure must never
        # alter the already completed CachyOS transaction result.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
