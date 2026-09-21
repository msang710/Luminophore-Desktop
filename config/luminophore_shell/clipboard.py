from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import subprocess
import threading
import time
from typing import Callable, Protocol, Sequence


@dataclass(frozen=True)
class ClipboardItem:
    mime_type: str
    data: bytes
    captured_at: float

    @property
    def is_image(self) -> bool:
        return self.mime_type.startswith("image/")

    @property
    def text(self) -> str:
        return "" if self.is_image else self.data.decode("utf-8", errors="replace")

    @property
    def search_text(self) -> str:
        return self.text if self.text else self.mime_type


class ClipboardSecretStore(Protocol):
    def lookup(self) -> str: ...
    def store(self, value: str) -> None: ...
    def clear(self) -> None: ...


class SecretToolClipboardStore:
    _ATTRS = ("service", "luminophore-shell", "kind", "clipboard-history", "account", "session-user")

    @staticmethod
    def _run(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=10, **kwargs)

    def lookup(self) -> str:
        result = self._run(("secret-tool", "lookup", *self._ATTRS))
        return result.stdout.rstrip("\n") if result.returncode == 0 else ""

    def store(self, value: str) -> None:
        result = self._run(
            ("secret-tool", "store", "--label=Luminophore Shell clipboard history", *self._ATTRS),
            input=value,
        )
        if result.returncode != 0:
            raise RuntimeError("clipboard_secret_store_failed")

    def clear(self) -> None:
        result = self._run(("secret-tool", "clear", *self._ATTRS))
        if result.returncode not in {0, 1}:
            raise RuntimeError("clipboard_secret_clear_failed")


class ClipboardHistory:
    def __init__(
        self,
        limit: int,
        changed: Callable[[], None] | None = None,
        *,
        retention_hours: float = 24,
        secrets: ClipboardSecretStore | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.limit = limit
        self.retention_hours = retention_hours
        self.changed = changed or (lambda: None)
        self.secrets = secrets or SecretToolClipboardStore()
        self.now = now
        self.items: list[ClipboardItem] = []
        self.storage_error = ""
        self._last: tuple[str, bytes] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="luminophore-clipboard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._restore()
        while not self._stop.is_set():
            item = self._read_selection()
            if item and (item.mime_type, item.data) != self._last:
                self._last = (item.mime_type, item.data)
                self._insert(item)
            elif self._prune():
                self._persist()
                self.changed()
            self._stop.wait(0.7)

    def _read_selection(self) -> ClipboardItem | None:
        try:
            offered = subprocess.run(
                ["wl-paste", "--list-types"], check=False, capture_output=True, timeout=0.4,
            )
            if offered.returncode != 0:
                return None
            types = offered.stdout.decode("utf-8", errors="replace").splitlines()
            mime_type = next((value for value in types if value == "image/png"), "")
            mime_type = mime_type or next((value for value in types if value.startswith("image/")), "")
            mime_type = mime_type or next((value for value in types if value == "text/plain;charset=utf-8"), "")
            mime_type = mime_type or next((value for value in types if value.startswith("text/plain")), "")
            if not mime_type:
                return None
            result = subprocess.run(
                ["wl-paste", "--no-newline", "--type", mime_type],
                check=False, capture_output=True, timeout=0.8,
            )
            if result.returncode != 0 or not result.stdout:
                return None
            return ClipboardItem(mime_type, result.stdout, self.now())
        except (OSError, subprocess.TimeoutExpired):
            return None

    def _insert(self, item: ClipboardItem) -> None:
        with self._lock:
            self.items = [current for current in self.items if (current.mime_type, current.data) != (item.mime_type, item.data)]
            self.items.insert(0, item)
            self._prune_locked()
        self._persist()
        self.changed()

    def _prune_locked(self) -> bool:
        before = len(self.items)
        cutoff = self.now() - self.retention_hours * 3600
        self.items = [item for item in self.items if item.captured_at >= cutoff][: self.limit]
        return len(self.items) != before

    def _prune(self) -> bool:
        with self._lock:
            return self._prune_locked()

    def _serialize(self) -> str:
        with self._lock:
            payload = [
                {"mime_type": item.mime_type, "data": base64.b64encode(item.data).decode("ascii"), "captured_at": item.captured_at}
                for item in self.items
            ]
        return json.dumps({"version": 1, "items": payload}, separators=(",", ":"))

    def _persist(self) -> None:
        try:
            self.secrets.store(self._serialize())
            self.storage_error = ""
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            self.storage_error = "secret_service_unavailable"

    def _restore(self) -> None:
        try:
            raw = self.secrets.lookup()
            value = json.loads(raw) if raw else {"version": 1, "items": []}
            if value.get("version") != 1 or not isinstance(value.get("items"), list):
                raise ValueError("invalid clipboard history")
            restored = [
                ClipboardItem(str(item["mime_type"]), base64.b64decode(item["data"], validate=True), float(item["captured_at"]))
                for item in value["items"]
            ]
            with self._lock:
                self.items = restored
                self._prune_locked()
            self.storage_error = ""
            self.changed()
        except (OSError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, TypeError, ValueError):
            self.storage_error = "secret_service_unavailable"

    def copy_item(self, item: ClipboardItem) -> None:
        subprocess.run(["wl-copy", "--type", item.mime_type], input=item.data, check=False, timeout=0.8)

    def copy(self, value: str) -> None:
        self.copy_item(ClipboardItem("text/plain;charset=utf-8", value.encode("utf-8"), self.now()))

    def clear(self) -> None:
        with self._lock:
            self.items.clear()
            self._last = None
        try:
            self.secrets.clear()
            self.storage_error = ""
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            self.storage_error = "secret_service_unavailable"
        subprocess.run(["wl-copy", "--clear"], check=False, timeout=0.5)
        self.changed()
