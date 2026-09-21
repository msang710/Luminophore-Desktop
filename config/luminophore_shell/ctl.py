"""Interactive shell control without graphics or configuration initialization."""
from __future__ import annotations

import argparse
import json
import sys

from .ipc_client import IpcClient, IpcError


def add_parser(sub: argparse._SubParsersAction) -> None:
    ctl = sub.add_parser("ctl")
    ctl_sub = ctl.add_subparsers(dest="ctl_command", required=True)

    toggle = ctl_sub.add_parser("toggle")
    toggle.add_argument("panel", choices=("spatial-editor", "overview", "launcher", "notifications", "power", "weather", "system", "spotify"))

    open_cmd = ctl_sub.add_parser("open")
    open_cmd.add_argument("panel", choices=("launcher", "notifications", "power", "weather", "system", "spotify"))
    open_cmd.add_argument(
        "--provider",
        choices=("default", "clip", "file", "web", "command", "emoji", "wallpaper", "palette", "settings", "system-theme", "controls"),
        default="default",
    )

    wallpaper = ctl_sub.add_parser("wallpaper")
    wallpaper.add_argument("action", choices=("open", "list", "current", "status", "apply", "next", "previous"))
    wallpaper.add_argument("scene", nargs="?", default="")
    ctl_sub.add_parser("reload")
    refresh = ctl_sub.add_parser("refresh")
    refresh.add_argument("provider", choices=("weather",))
    ctl_sub.add_parser("status")
    glow_probe = ctl_sub.add_parser("glow-probe")
    glow_probe.add_argument("action", choices=("start", "reset", "stop"))

    hardware = ctl_sub.add_parser("hardware")
    hardware.add_argument(
        "action",
        choices=(
            "volume-up", "volume-down", "volume-mute", "mic-mute",
            "media-toggle", "media-next", "media-previous",
            "brightness-up", "brightness-down",
            "brightness-preview-up", "brightness-preview-down", "brightness-commit",
        ),
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="luminophore-shell")
    add_parser(result.add_subparsers(dest="command", required=True))
    return result


def request_payload(args: argparse.Namespace) -> dict[str, object]:
    command = args.ctl_command
    if command == "wallpaper": return {"command": "wallpaper", "action": args.action, "scene": args.scene}
    if command in {"toggle", "open"}:
        payload: dict[str, object] = {"command": command, "panel": args.panel}
        if command == "open":
            payload["provider"] = args.provider
        return payload
    if command == "refresh":
        return {"command": "refresh", "provider": args.provider}
    if command == "hardware":
        return {"command": command, "action": args.action}
    if command == "glow-probe":
        return {"command": command, "action": args.action}
    return {"command": command}



def execute(args: argparse.Namespace) -> int:
    try:
        response = IpcClient().request(request_payload(args))
    except IpcError as exc:
        print(f"luminophore-shell: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(response, ensure_ascii=False, indent=2 if args.ctl_command == "status" else None))
    return 0 if response.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    return execute(parser().parse_args(argv))
