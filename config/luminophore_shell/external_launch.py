from __future__ import annotations

import logging
import os
import re
from pathlib import Path
import subprocess
import threading
from typing import Callable, Mapping, Sequence


LOG = logging.getLogger("luminophore-shell")

RunCommand = Callable[..., subprocess.CompletedProcess[str]]


class AsyncLaunchController:
    """One bounded launch at a time; completion belongs to the UI dispatcher.

    Operations must themselves be bounded (the launcher uses subprocess timeouts).
    Keeping the slot until completion is delivered prevents rapid clicks from
    starting duplicate applications or an older callback closing a newer panel.
    """

    def __init__(self, dispatch: Callable[[Callable[[], bool]], object]) -> None:
        self.dispatch = dispatch
        self._pending = False

    def submit(self, operation: Callable[[], bool], complete: Callable[[bool], None]) -> bool:
        # submit and finish are UI-thread operations. Only operation runs off-thread.
        if self._pending:
            return False
        self._pending = True

        def finish(success: bool) -> bool:
            self._pending = False
            complete(success)
            return False

        def work() -> None:
            try:
                success = bool(operation())
            except Exception:
                LOG.exception("asynchronous application launch failed")
                success = False

            self.dispatch(lambda: finish(success))

        try:
            threading.Thread(target=work, name="luminophore-app-launch", daemon=True).start()
        except RuntimeError:
            self.dispatch(lambda: finish(False))
        return True


class UwsmApplicationLauncher:
    """Launch user-facing applications outside the luminophore-shell service cgroup."""

    def __init__(
        self,
        uwsm: Path = Path("/usr/bin/uwsm"),
        gio: Path = Path("/usr/bin/gio"),
        timeout_seconds: float = 5.0,
        runner: RunCommand = subprocess.run,
        environment: Mapping[str, str] | None = None,
        history_group: str = "",
    ) -> None:
        self.history_group = history_group
        self.uwsm = uwsm
        self.gio = gio
        self.timeout_seconds = timeout_seconds
        self.runner = runner
        self.environment = dict(environment) if environment is not None else None

    @property
    def available(self) -> bool:
        return self.uwsm.is_file() and os.access(self.uwsm, os.X_OK)

    def launch_desktop(self, desktop_id: str, action_id: str | None = None) -> bool:
        if not desktop_id or ":" in desktop_id:
            LOG.warning("external desktop launch rejected: invalid desktop id")
            return False
        if action_id is not None and (not action_id or ":" in action_id):
            LOG.warning("external desktop launch rejected: invalid action id")
            return False
        target = desktop_id if action_id is None else f"{desktop_id}:{action_id}"
        return self._launch((target,), "desktop")

    def launch_argv(self, argv: Sequence[str], *, app_name: str = "") -> bool:
        command = tuple(str(item) for item in argv)
        if not command or not command[0]:
            LOG.warning("external argv launch rejected: empty command")
            return False
        options: tuple[str, ...] = ("-a", app_name) if app_name else ()
        return self._launch((*options, "--", *command), "command")

    def open_uri(self, uri: str) -> bool:
        if not uri or not self.gio.is_file() or not os.access(self.gio, os.X_OK):
            LOG.warning("external URI launch rejected: handler unavailable")
            return False
        return self.launch_argv((str(self.gio), "open", uri), app_name="uri-handler")

    def _launch(self, arguments: Sequence[str], kind: str) -> bool:
        if not self.available:
            LOG.warning("external launch failed: kind=%s reason=uwsm-unavailable", kind)
            return False
        environment = dict(self.environment) if self.environment is not None else os.environ.copy()
        if environment.get("LUMINOPHORE_RELEASE_ROOT"):
            environment["PATH"] = "/usr/bin:/bin"
        # UWSM interprets DEBUG itself as a boolean. Build tools commonly export
        # DEBUG=release, which is irrelevant to the launched application and
        # otherwise produces a warning before every interactive launch.
        environment.pop("DEBUG", None)
        properties = ()
        if environment.get("LUMINOPHORE_COMPOSITOR") == "1":
            try:
                token = self._direct_launch_token(self.history_group) if self.history_group else self._direct_launch_token()
                if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", token):
                    properties = ("-p", "Environment=LUMINOPHORE_DIRECT_LAUNCH_TOKEN=" + token)
            except RuntimeError:
                LOG.warning("direct launch origin unavailable; using default placement")
        # Never inherit a prior launch's token into an unrelated application.
        environment.pop("LUMINOPHORE_DIRECT_LAUNCH_TOKEN", None)
        private_paths = {"PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "GI_TYPELIB_PATH",
                         "GIO_MODULE_DIR", "GIO_EXTRA_MODULES", "GSETTINGS_SCHEMA_DIR",
                         "GTK_PATH", "GTK_EXE_PREFIX", "GTK_DATA_PREFIX", "GDK_PIXBUF_MODULE_FILE",
                         "QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML_IMPORT_PATH", "FONTCONFIG_FILE", "FONTCONFIG_PATH"}
        environment = {key: value for key, value in environment.items()
                       if key not in private_paths and not key.startswith("LD_")
                       and (not key.startswith("LUMINOPHORE_") or key == "LUMINOPHORE_INSTANCE_SIGNATURE")}
        try:
            result = self.runner(
                (str(self.uwsm), "app", "-t", "service", *properties, *arguments),
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            LOG.warning("external launch failed: kind=%s error=%s", kind, type(exc).__name__)
            return False
        if result.returncode:
            LOG.warning("external launch failed: kind=%s exit=%s", kind, result.returncode)
            return False
        return True

    @staticmethod
    def _direct_launch_token(group: str = "") -> str:
        from .hyprland import HyprlandClient
        if group and not re.fullmatch(r"[a-z0-9-]{1,64}", group):
            raise RuntimeError("Invalid launch group")
        return HyprlandClient()._run("luminophorelaunchtoken", *([group] if group else [])).strip()
