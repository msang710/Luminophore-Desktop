from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
from typing import Sequence

from .power_backend import PowerBackendError, PowerBackendInstaller
from .power_profiles import PowerProfileError, validate_profile_source


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="luminophore-power-backend")
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install")
    install.add_argument("--source", type=Path, default=Path("power-profiles"))
    install.add_argument("--root", type=Path, default=Path("/"))
    mode = install.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--backup", type=Path, required=True)
    rollback.add_argument("--root", type=Path, default=Path("/"))
    rollback_mode = rollback.add_mutually_exclusive_group(required=True)
    rollback_mode.add_argument("--check", action="store_true")
    rollback_mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            validate_profile_source(args.source)
            missing = [name for name in ("tuned-adm", "systemctl") if shutil.which(name) is None]
            if args.check:
                if missing:
                    raise PowerBackendError("missing_commands:" + ",".join(missing))
                print("CHECK_OK profiles=3 boost_policy=firmware-owned ppd_policy=dbus-masked")
                return 0
            if args.root == Path("/") and os.geteuid() != 0:
                raise PowerBackendError("root_required")
            backup = PowerBackendInstaller(args.root).install(args.source)
            print(f"APPLIED backup={backup.path}")
            return 0
        if args.check:
            if not (args.backup / "rollback.json").is_file():
                raise PowerBackendError("invalid_backup")
            print("CHECK_OK rollback")
            return 0
        if args.root == Path("/") and os.geteuid() != 0:
            raise PowerBackendError("root_required")
        PowerBackendInstaller(args.root).rollback(args.backup)
        print("ROLLED_BACK")
        return 0
    except (PowerBackendError, PowerProfileError) as exc:
        print(f"ERROR category={exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
