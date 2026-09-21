from __future__ import annotations

import argparse
import json
import sys
from typing import Mapping

# Module invocation gets the same fast path as the executable script.
if __name__ == "__main__" and sys.argv[1:2] == ["ctl"]:
    from luminophore_shell.ctl import main as ctl_main
    raise SystemExit(ctl_main())

from .ctl import add_parser as add_ctl_parser, request_payload as _ctl_request
from .config import ConfigError, load_config
from .ipc import IpcClient, IpcError
from .settings_contract import SettingsMutationError
from .settings_contract import (
    SettingsApplyPhase,
    SettingsApplyRequest,
    SettingsApplyResult,
    SettingsResultCategory,
    SettingsRoute,
    SettingsSnapshot,
    SettingsTransportCategory,
)


class _SettingsIpcFacade:
    def __init__(self, client: IpcClient | None = None) -> None:
        self.client = client or IpcClient()
        self._completion_unknown: set[str] = set()

    @staticmethod
    def _remote_rejection(response: Mapping[str, object], fallback: str) -> IpcError:
        return IpcError(
            fallback,
            SettingsTransportCategory.REMOTE_REJECTION,
        )

    @staticmethod
    def _result(response: Mapping[str, object]) -> SettingsApplyResult:
        try:
            return SettingsApplyResult(
                str(response["request_id"]),
                SettingsResultCategory(str(response["category"])),
                SettingsApplyPhase(str(response["phase"])),
                str(response["digest"]),
                tuple(response.get("changed_paths", ())),
                str(response.get("message", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise IpcError("invalid settings response", SettingsTransportCategory.PROTOCOL_ERROR) from exc

    def settings_status(self, request_id: str) -> SettingsApplyResult | None:
        response = self.client.request({"command": "settings-status", "request_id": request_id})
        if not response.get("ok"):
            raise self._remote_rejection(response, "settings status failed")
        if not response.get("found"):
            return None
        return self._result(response)

    def recover_settings(self, request_id: str) -> SettingsApplyResult | None:
        response = self.client.request({"command": "settings-recover", "request_id": request_id})
        if not response.get("ok"):
            raise self._remote_rejection(response, "settings recovery failed")
        return self._result(response) if response.get("found") else None

    def snapshot(self) -> SettingsSnapshot:
        try:
            response = self.client.request({"command": "settings-snapshot"})
        except IpcError as exc:
            if exc.category is not SettingsTransportCategory.CONNECTION_UNAVAILABLE:
                raise
            raise
        if not response.get("ok"):
            raise self._remote_rejection(response, "settings snapshot failed")
        if not isinstance(response.get("values"), dict):
            raise IpcError("invalid settings snapshot", SettingsTransportCategory.PROTOCOL_ERROR)
        return SettingsSnapshot.build(str(response["digest"]), response["values"], bool(response.get("online")))

    def capabilities(self) -> Mapping[str, bool]:
        try:
            response = self.client.request({"command": "settings-capabilities"})
        except IpcError as exc:
            if exc.category is not SettingsTransportCategory.CONNECTION_UNAVAILABLE:
                raise
            return {}
        if not response.get("ok"):
            raise self._remote_rejection(response, "settings capabilities failed")
        values = response.get("capabilities")
        if not isinstance(values, dict):
            raise IpcError("invalid settings capabilities", SettingsTransportCategory.PROTOCOL_ERROR)
        return {str(key): bool(value) for key, value in values.items()}

    def apply(self, request: SettingsApplyRequest) -> SettingsApplyResult:
        if request.request_id in self._completion_unknown:
            try:
                status = self.settings_status(request.request_id)
            except IpcError as exc:
                if exc.category not in {
                    SettingsTransportCategory.CONNECTION_UNAVAILABLE,
                    SettingsTransportCategory.COMPLETION_UNKNOWN,
                }:
                    raise
                status = None
            if status is not None:
                return status
            return SettingsApplyResult(
                request.request_id, SettingsResultCategory.COMPLETION_UNKNOWN,
                SettingsApplyPhase.COMPLETION_UNKNOWN, request.expected_digest,
                tuple(key for key, _value in request.changes),
                "completion is unknown; status must be confirmed before retry",
            )
        try:
            response = self.client.request({
                "command": "settings-apply",
                "request_id": request.request_id,
                "expected_digest": request.expected_digest,
                "changes": dict(request.changes),
            })
        except IpcError as exc:
            if exc.category is SettingsTransportCategory.COMPLETION_UNKNOWN:
                self._completion_unknown.add(request.request_id)
                return SettingsApplyResult(
                    request.request_id, SettingsResultCategory.COMPLETION_UNKNOWN,
                    SettingsApplyPhase.COMPLETION_UNKNOWN, request.expected_digest,
                    tuple(key for key, _value in request.changes),
                    "completion is unknown; inspect request status before retry",
                )
            if exc.category is not SettingsTransportCategory.CONNECTION_UNAVAILABLE:
                raise
            raise
        if not response.get("ok"):
            raise self._remote_rejection(response, "settings apply failed")
        return self._result(response)

    def binding_snapshot(self) -> Mapping[str, object]:
        response = self.client.request({"command": "bindings-snapshot"})
        if not response.get("ok") or not isinstance(response.get("bindings"), list):
            raise IpcError(str(response.get("error", "invalid binding snapshot")))
        return response

    def apply_bindings(
        self, expected_digest: str, changes: Mapping[str, Mapping[str, object]],
    ) -> Mapping[str, object]:
        response = self.client.request({
            "command": "bindings-apply",
            "expected_digest": expected_digest,
            "changes": {str(key): dict(value) for key, value in changes.items()},
        })
        if not response.get("ok"):
            raise IpcError(str(response.get("error", "binding apply failed")))
        from .domain_client import DomainClient
        completed = DomainClient(client=self.client).wait(response)
        return {**completed, "generation_id": completed['digest'], "reload_required": False,
                "changed_actions": sorted(changes)}

    def _system_theme_request(self, command: str, **fields: object) -> Mapping[str, object]:
        response = self.client.request({"command": command, **fields})
        if not response.get("ok"):
            raise IpcError(str(response.get("message") or response.get("error", "system theme request failed")))
        return response

    def system_theme_status(self) -> Mapping[str, object]:
        return self._system_theme_request("system-theme-status")

    def preview_system_theme(self, mode: str) -> Mapping[str, object]:
        return self._system_theme_request("system-theme-preview", mode=mode)

    def apply_system_theme(self, preview_id: str) -> Mapping[str, object]:
        return self._system_theme_request("system-theme-apply", preview_id=preview_id)

    def rollback_system_theme(self) -> Mapping[str, object]:
        return self._system_theme_request("system-theme-rollback")

    def preview_appearance(
        self, request_id: str, sources: Mapping[str, str], mode: str,
    ) -> Mapping[str, object]:
        response = self.client.request({
            "command": "appearance-preview", "request_id": request_id,
            "sources": dict(sources), "mode": mode,
        })
        if not response.get("ok"):
            raise IpcError(str(response.get("error", "appearance preview failed")))
        return response

    def apply_appearance(self, request_id: str, preview_id: str) -> Mapping[str, object]:
        response = self.client.request({
            "command": "appearance-apply", "request_id": request_id, "preview_id": preview_id,
        })
        if not response.get("ok"):
            raise IpcError(str(response.get("error", "appearance apply failed")))
        return response

    def appearance_status(self, request_id: str) -> Mapping[str, object]:
        response = self.client.request({"command": "appearance-status", "request_id": request_id})
        if not response.get("ok"):
            raise IpcError(str(response.get("error", "appearance status failed")))
        return response

    def appearance_context(self) -> Mapping[str, object]:
        response = self.client.request({"command": "appearance-context"})
        if not response.get("ok") or not isinstance(response.get("monitors"), list):
            raise IpcError(str(response.get("error", "appearance context failed")))
        return response


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="luminophore-shell")
    sub = parser.add_subparsers(dest="command", required=True)
    bundle = sub.add_parser("launch-bundle")
    bundle.add_argument("identifier")
    launch = sub.add_parser("launch")
    launch.add_argument("argv", nargs=argparse.REMAINDER)
    sub.add_parser("daemon")
    sub.add_parser("greeter")
    sub.add_parser("greeter-session")
    sub.add_parser("validate-config")
    settings = sub.add_parser("settings")
    settings.add_argument("--page", choices=tuple(route.value for route in SettingsRoute), default=SettingsRoute.HOME.value)
    capture = sub.add_parser("capture")
    capture.add_argument("mode", choices=("region",))
    destination = capture.add_mutually_exclusive_group(required=True)
    destination.add_argument("--clipboard-only", action="store_true")
    destination.add_argument("--save", action="store_true")
    capture.add_argument("--freeze", action="store_true")
    restart = sub.add_parser("restart")
    restart.add_argument("--timeout", type=float, default=20.0)
    add_ctl_parser(sub)
    return parser




def main(argv: list[str] | None = None) -> int:
    command_argv = sys.argv[1:] if argv is None else argv
    if command_argv[:1] == ["greeter"]:
        from .luminophore_greeter import main as greeter_main
        return greeter_main(command_argv[1:])
    if command_argv[:1] == ["greeter-session"]:
        from .greeter_supervisor import main as supervisor_main
        return supervisor_main(command_argv[1:])
    args = _parser().parse_args(argv)
    if args.command == "launch-bundle":
        from .app_bundles import launch_bundle
        try:
            result = launch_bundle(args.identifier)
            print(json.dumps(result, ensure_ascii=False))
            if result['failed']:
                import subprocess
                try:
                    subprocess.run(['notify-send', '묶음 실행', '실행을 확인하지 못한 앱: ' + ', '.join(result['failed'])], timeout=3, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            return 1 if result['failed'] else 0
        except (ValueError, OSError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if args.command == "launch":
        from .external_launch import UwsmApplicationLauncher
        command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        return 0 if UwsmApplicationLauncher().launch_argv(command) else 1
    if args.command == "validate-config":
        try:
            from .settings_generation import fixture_enabled, boot_config
            config = boot_config()
        except ConfigError as exc:
            print(f"configuration error: {exc}", file=sys.stderr)
            return 2
        print(f"valid: {config.path}")
        return 0

    if args.command == "daemon":
        try:
            from .app import run

            return run()
        except ConfigError as exc:
            print(f"configuration error: {exc}", file=sys.stderr)
            return 2

    if args.command == "settings":
        from .settings_app import LuminophoreSettingsApplication

        application = LuminophoreSettingsApplication(_SettingsIpcFacade(), args.page)
        return application.run(["luminophore-settings"])

    if args.command == "capture":
        from .capture import run_capture

        return run_capture(save=bool(args.save), freeze=bool(args.freeze))

    if args.command == "restart":
        from .restart import RestartError, ShellRestarter

        try:
            result = ShellRestarter().restart(args.timeout)
        except RestartError as exc:
            print(f"luminophore-shell: {exc}", file=sys.stderr)
            return 1
        print(f"luminophore-shell restarted: {result.old_pid} -> {result.new_pid}")
        return 0

    try:
        response = IpcClient().request(_ctl_request(args))
    except IpcError as exc:
        print(f"luminophore-shell: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(response, ensure_ascii=False, indent=2 if args.ctl_command == "status" else None))
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
