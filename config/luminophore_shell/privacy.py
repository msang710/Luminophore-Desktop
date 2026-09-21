from __future__ import annotations

from dataclasses import dataclass
import json
import subprocess
import threading
from typing import Callable, Sequence


class PrivacyError(RuntimeError):
    pass


@dataclass(frozen=True)
class PrivacySession:
    kind: str
    application: str
    node_id: int


@dataclass(frozen=True)
class PrivacySnapshot:
    available: bool
    sessions: tuple[PrivacySession, ...] = ()
    error: str = ""

    @property
    def active_kinds(self) -> frozenset[str]:
        return frozenset(item.kind for item in self.sessions)


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def run_command(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=5)


def parse_pw_dump(payload: object) -> PrivacySnapshot:
    if not isinstance(payload, list):
        raise PrivacyError("decode_failed:pw-dump")
    sessions: list[PrivacySession] = []
    for item in payload:
        if not isinstance(item, dict) or item.get("type") != "PipeWire:Interface:Node":
            continue
        info = item.get("info")
        if not isinstance(info, dict) or str(info.get("state", "")).casefold() != "running":
            continue
        props = info.get("props")
        if not isinstance(props, dict):
            continue
        media_class = str(props.get("media.class", ""))
        if not media_class.startswith("Stream/"):
            continue
        media_type = str(props.get("media.type", "")).casefold()
        category = str(props.get("media.category", "")).casefold()
        role = str(props.get("media.role", "")).casefold()
        kind = ""
        if media_type == "audio" and category == "capture":
            kind = "microphone"
        elif media_type == "video" and category == "capture" and role == "camera":
            kind = "camera"
        elif media_type == "video" and role in {"screen", "screencast", "screen-share", "screenshare"}:
            kind = "screen"
        if not kind:
            continue
        application = str(
            props.get("application.name")
            or props.get("pipewire.access.portal.app_id")
            or props.get("application.process.binary")
            or "알 수 없는 앱"
        )
        try:
            node_id = int(item.get("id", -1))
        except (TypeError, ValueError):
            node_id = -1
        sessions.append(PrivacySession(kind, application, node_id))
    return PrivacySnapshot(True, tuple(sessions))


class PrivacyProvider:
    def __init__(
        self,
        changed: Callable[[PrivacySnapshot], None],
        runner: Runner = run_command,
        interval_seconds: float = 2.0,
    ) -> None:
        self.changed = changed
        self.runner = runner
        self.interval_seconds = interval_seconds
        self.snapshot = PrivacySnapshot(False, error="not_started")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def collect(self) -> PrivacySnapshot:
        result = self.runner(("pw-dump", "--no-colors"))
        if result.returncode != 0:
            return PrivacySnapshot(False, error="backend_failed:pw-dump")
        try:
            return parse_pw_dump(json.loads(result.stdout))
        except (json.JSONDecodeError, PrivacyError) as exc:
            return PrivacySnapshot(False, error=str(exc))

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def worker() -> None:
            while not self._stop.is_set():
                self.snapshot = self.collect()
                self.changed(self.snapshot)
                self._stop.wait(self.interval_seconds)

        self._thread = threading.Thread(target=worker, name="luminophore-privacy", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=max(1.0, self.interval_seconds + 0.5))
        self._thread = None
