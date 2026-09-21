from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
from typing import Sequence

from .config import ConfigError, load_config
from .hyprland import HyprlandClient, HyprlandError
from .session_theme import (
    LuminophoreGreeterRenderer,
    PlymouthInstaller,
    RenderedTheme,
    RetainSplashInstaller,
    SessionStackInstaller,
    SessionThemeError,
    stage_session_theme,
    validate_rendered,
)
from .state import read_palette_state_details, state_dir
from .wallpaper_backends import STATIC_PROVIDERS, WallpaperBackendError, discover_wallpaper_snapshot


def _rendered(path: Path) -> RenderedTheme:
    if path.is_symlink() or not path.is_dir():
        raise SessionThemeError("unsafe_source")
    raw = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    generation = raw.get("generation_id") if isinstance(raw, dict) else None
    files = raw.get("files") if isinstance(raw, dict) else None
    if not isinstance(generation, str) or not isinstance(files, dict):
        raise SessionThemeError("invalid_manifest")
    return RenderedTheme(path, generation, files)


def _runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=180)


def _preview_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, text=True)


def _require_apply_authority(root: Path, apply: bool) -> None:
    if not apply:
        return
    if root == Path("/") and os.geteuid() != 0:
        raise SessionThemeError("root_required")


def _stage_current(output_root: Path, config_path: Path | None) -> RenderedTheme:
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        raise SessionThemeError("invalid_config") from exc
    try:
        monitors = HyprlandClient().monitors()
    except HyprlandError as exc:
        raise SessionThemeError("monitor_query_failed") from exc
    preferred = config.theme.palette_source if config.theme.palette_source in STATIC_PROVIDERS else ""
    try:
        snapshot = discover_wallpaper_snapshot(monitors, preferred)
    except WallpaperBackendError as exc:
        raise SessionThemeError(exc.category) from exc
    return stage_session_theme(
        output_root,
        config.system_theme.mode,
        monitors,
        snapshot,
        read_palette_state_details(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="luminophore-session-theme")
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage-session")
    stage.add_argument("--output-root", type=Path)
    stage.add_argument("--config", type=Path)
    preview = sub.add_parser("preview-session")
    preview.add_argument("--staged", type=Path, required=True)
    for name in ("install-session", "install-boot"):
        command = sub.add_parser(name)
        command.add_argument("--staged", type=Path, required=True)
        command.add_argument("--root", type=Path, default=Path("/"))
        if name == "install-session":
            command.add_argument("--login-user", required=True)
        mode = command.add_mutually_exclusive_group(required=True)
        mode.add_argument("--check", action="store_true")
        mode.add_argument("--apply", action="store_true")
    for name in ("rollback-session", "rollback-boot"):
        command = sub.add_parser(name)
        command.add_argument("--backup", type=Path, required=True)
        command.add_argument("--root", type=Path, default=Path("/"))
        mode = command.add_mutually_exclusive_group(required=True)
        mode.add_argument("--check", action="store_true")
        mode.add_argument("--apply", action="store_true")
    install_handoff = sub.add_parser("install-handoff")
    install_handoff.add_argument("--root", type=Path, default=Path("/"))
    handoff_mode = install_handoff.add_mutually_exclusive_group(required=True)
    handoff_mode.add_argument("--check", action="store_true")
    handoff_mode.add_argument("--apply", action="store_true")
    rollback_handoff = sub.add_parser("rollback-handoff")
    rollback_handoff.add_argument("--backup", type=Path, required=True)
    rollback_handoff.add_argument("--root", type=Path, default=Path("/"))
    rollback_handoff_mode = rollback_handoff.add_mutually_exclusive_group(required=True)
    rollback_handoff_mode.add_argument("--check", action="store_true")
    rollback_handoff_mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "stage-session":
            output_root = args.output_root or state_dir() / "session-theme-staging"
            rendered = _stage_current(output_root, args.config)
            print(f"STAGED path={rendered.root} generation={rendered.generation_id[:12]}")
            return 0
        if args.command == "preview-session":
            rendered = _rendered(args.staged)
            preview_argv = LuminophoreGreeterRenderer.preview_argv(rendered)
            result = _preview_runner(preview_argv)
            if result.returncode:
                raise SessionThemeError("preview_failed")
            print(f"PREVIEW_OK generation={rendered.generation_id[:12]}")
            return 0
        _require_apply_authority(args.root, args.apply)
        if args.command == "install-handoff":
            installer = RetainSplashInstaller(args.root, _runner)
            installer.check()
            if not args.apply:
                print("CHECK_OK retain-splash")
                return 0
            backup = installer.install()
            print(f"APPLIED backup={backup}")
            return 0
        if args.command == "rollback-handoff":
            if not args.apply:
                raw = json.loads((args.backup / "rollback.json").read_text(encoding="utf-8"))
                if raw.get("version") != 1:
                    raise SessionThemeError("invalid_backup")
                print("CHECK_OK rollback")
                return 0
            RetainSplashInstaller(args.root, _runner).rollback(args.backup)
            print("ROLLED_BACK")
            return 0
        if args.command.startswith("install"):
            rendered = _rendered(args.staged)
            validate_rendered(rendered)
            if not args.apply:
                if args.command == "install-session":
                    SessionStackInstaller(args.root, args.login_user).check(rendered)
                print(f"CHECK_OK generation={rendered.generation_id[:12]}")
                return 0
            installer = (
                SessionStackInstaller(args.root, args.login_user)
                if args.command == "install-session"
                else PlymouthInstaller(args.root, _runner)
            )
            backup = installer.install(rendered)
            print(f"APPLIED backup={backup}")
            return 0
        if not args.apply:
            raw = json.loads((args.backup / "rollback.json").read_text(encoding="utf-8"))
            if raw.get("version") != 1:
                raise SessionThemeError("invalid_backup")
            print("CHECK_OK rollback")
            return 0
        installer = SessionStackInstaller(args.root, "") if args.command == "rollback-session" else PlymouthInstaller(args.root, _runner)
        installer.rollback(args.backup)
        print("ROLLED_BACK")
        return 0
    except (OSError, json.JSONDecodeError, SessionThemeError) as exc:
        category = exc.category if isinstance(exc, SessionThemeError) else "invalid_input"
        print(f"ERROR category={category}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
