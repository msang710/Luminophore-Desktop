"""Start and supervise both greeter components from the greetd process."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import pwd
import re
import select
import signal
import stat
import subprocess
import sys
import time
from typing import Callable, Sequence

from .bootstrap import internal_python_environment

DEFAULT_THEME_CONFIG = "/etc/luminophore-shell/greeter-theme.json"
DEFAULT_LOCAL_CONFIG = "/etc/luminophore-shell/greeter.json"
RUNTIME = "/usr/lib/luminophore/runtime/luminophore-runtime"
STORE = "/var/lib/luminophore"
START_TIMEOUT = 20.0


def _record_status(event: str) -> None:
    """Leave a short greeter-owned trace without logging credentials."""
    try:
        path = _log_directory() / "luminophore-greeter-status.log"
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, f"{int(time.time())} {event}\n".encode("ascii"))
        finally:
            os.close(fd)
    except OSError:
        pass


def _open_log(directory: Path, name: str) -> int:
    return os.open(_log_directory() / name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)


def _log_directory() -> Path:
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    if home.is_symlink() or not home.is_dir() or home.stat().st_uid != os.getuid():
        raise OSError("invalid greeter log directory")
    return home


@dataclass(frozen=True)
class SupervisorResult:
    greeter_returncode: int
    compositor_exit_returncode: int


def _disable_core_dumps() -> None:
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, OSError, ValueError):
        pass


def terminate_process(process: subprocess.Popen, timeout_seconds: float = 1.5) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout_seconds)


def _private_file(release_root: str, relative: str) -> str:
    root = Path(release_root)
    if root.is_symlink() or not root.is_dir():
        raise OSError("invalid release root")
    path = root / relative
    target = path.resolve(strict=True)
    if path.is_symlink() or not target.is_relative_to(root.resolve()) or not target.is_file():
        raise OSError("invalid private greeter component")
    return str(target)


def _runtime_directory() -> Path:
    expected = Path(f"/run/user/{os.getuid()}")
    if (os.environ.get("XDG_RUNTIME_DIR") != str(expected) or expected.is_symlink()
            or not expected.is_dir()):
        raise OSError("invalid greeter runtime directory")
    return expected


def _wayland_sockets(directory: Path) -> set[str]:
    result = set()
    for path in directory.glob("wayland-*"):
        if not re.fullmatch(r"wayland-[0-9]+", path.name):
            continue
        try:
            if stat.S_ISSOCK(path.lstat().st_mode):
                result.add(path.name)
        except FileNotFoundError:
            continue
    return result


def _wait_for_socket(compositor: subprocess.Popen, directory: Path,
                     before: set[str], timeout: float = START_TIMEOUT) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if compositor.poll() is not None:
            raise OSError("greeter compositor exited before Wayland socket")
        new = _wayland_sockets(directory) - before
        if len(new) == 1:
            return new.pop()
        if len(new) > 1:
            raise OSError("ambiguous greeter Wayland socket")
        time.sleep(0.1)
    raise TimeoutError("greeter compositor did not create a Wayland socket")


def _wait_for_ui(greeter: subprocess.Popen, compositor: subprocess.Popen,
                 ready_fd: int, timeout: float = START_TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if greeter.poll() is not None or compositor.poll() is not None:
            raise OSError("greeter exited before presenting login UI")
        readable, _, _ = select.select([ready_fd], [], [],
                                        min(0.1, max(0.0, deadline - time.monotonic())))
        if readable:
            if os.read(ready_fd, 1) == b"1":
                return
            raise OSError("greeter did not report a presented login UI")
    raise TimeoutError("greeter login UI did not appear")


def _runtime_command(generation: str, component: str) -> tuple[str, ...]:
    return (RUNTIME, "run", "--system-store", "--store", STORE,
            "--host-root", "/", "--config-version", "1", "--generation", generation,
            "--component", component)


def supervise(theme_config: str, local_config: str, *, release_root: str | None = None,
              popen: Callable[..., subprocess.Popen] = subprocess.Popen,
              socket_waiter: Callable[..., str] = _wait_for_socket,
              ready_waiter: Callable[..., None] = _wait_for_ui) -> SupervisorResult:
    release_root = release_root or os.environ.get("LUMINOPHORE_RELEASE_ROOT", "")
    generation = Path(release_root).name
    if not re.fullmatch(r"[0-9a-f]{64}", generation):
        raise OSError("invalid selected release")
    python = _private_file(release_root, "python/bin/python3")
    shell = _private_file(release_root, "shell/luminophore-shell")
    directory = _runtime_directory()
    before = _wayland_sockets(directory)
    compositor_env = os.environ.copy()
    compositor_env.pop("WAYLAND_DISPLAY", None)
    compositor_env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
    compositor_log = _open_log(directory, "luminophore-greeter-compositor.log")
    try:
        compositor = popen(_runtime_command(generation, "greeter_compositor"),
                           env=compositor_env, stderr=compositor_log)
    finally:
        os.close(compositor_log)
    _record_status("compositor_started")
    greeter = None
    ready_read = ready_write = -1
    handlers = {}

    def stop_child(_signum: int, _frame: object) -> None:
        if greeter is not None:
            terminate_process(greeter)
        terminate_process(compositor)

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            handlers[signum] = signal.signal(signum, stop_child)
        socket_name = socket_waiter(compositor, directory, before)
        _record_status("wayland_socket_ready")
        environment = internal_python_environment()
        environment["WAYLAND_DISPLAY"] = socket_name
        environment.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
        ready_read, ready_write = os.pipe()
        environment["LUMINOPHORE_GREETER_READY_FD"] = str(ready_write)
        greeter_log = _open_log(directory, "luminophore-greeter-ui.log")
        try:
            greeter = popen((python, "-B", "-s", shell, "greeter", "--theme", theme_config,
                             "--config", local_config), env=environment,
                            pass_fds=(ready_write,), preexec_fn=_disable_core_dumps,
                            stderr=greeter_log)
        finally:
            os.close(greeter_log)
        os.close(ready_write)
        ready_write = -1
        ready_waiter(greeter, compositor, ready_read)
        _record_status("login_ui_mapped")
        while True:
            try:
                result = greeter.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if compositor.poll() is not None:
                    raise OSError("greeter compositor exited while login UI was running")
        terminate_process(compositor)
        _record_status("greeter_exited")
        return SupervisorResult(result, compositor.returncode)
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
        if ready_read >= 0:
            os.close(ready_read)
        if ready_write >= 0:
            os.close(ready_write)
        if greeter is not None:
            terminate_process(greeter)
        terminate_process(compositor)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="luminophore-greeter-session")
    parser.add_argument("--theme", default=DEFAULT_THEME_CONFIG)
    parser.add_argument("--config", default=DEFAULT_LOCAL_CONFIG)
    parser.add_argument("--release-root")
    args = parser.parse_args(argv)
    try:
        result = supervise(args.theme, args.config, release_root=args.release_root)
    except (OSError, TimeoutError, subprocess.TimeoutExpired) as exc:
        _record_status("startup_failed")
        print(f"luminophore-greeter: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0 if result.greeter_returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
