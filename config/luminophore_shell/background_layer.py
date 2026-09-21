from __future__ import annotations
import json
from pathlib import Path
import queue
import subprocess
import threading
import time


class BackgroundLayer:
    def __init__(self, binary=None):
        self.binary = (
            Path(binary)
            if binary
            else Path(__file__).with_name("luminophore-background-layer")
        )
        self.process = None
        self.events = queue.Queue(maxsize=128)
        self.build = ""

    def start(self):
        if self.process and self.process.poll() is None:
            return
        self.close()
        expected = subprocess.run(
            [str(self.binary), "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        ).stdout.strip()
        self.events = queue.Queue(maxsize=128)
        self.process = subprocess.Popen(
            [str(self.binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        process, events = self.process, self.events

        def read():
            try:
                for line in process.stdout:
                    if len(line) > 65536:
                        break
                    event = json.loads(line)
                    events.put_nowait(event)
            except (ValueError, queue.Full):
                pass
            finally:
                try:
                    events.put_nowait({"event": "EXIT"})
                except queue.Full:
                    pass

        threading.Thread(
            target=read, daemon=True, name="luminophore-background-events"
        ).start()
        hello = self.wait("HELLO", None, 5)
        if (
            hello.get("schema") != 1
            or hello.get("build") != expected
            or hello.get("pid") != process.pid
        ):
            self.close()
            raise RuntimeError("renderer_protocol")
        self.build = expected

    def send(self, command, generation, **fields):
        if not self.process or self.process.poll() is not None:
            raise RuntimeError("renderer_exited")
        payload = json.dumps(
            {"command": command, "generation": generation, **fields},
            allow_nan=False,
            separators=(",", ":"),
        )
        if len(payload.encode()) > 60000:
            raise ValueError("protocol_budget")
        self.process.stdin.write(payload + "\n")
        self.process.stdin.flush()

    def wait(self, kind, generation, timeout=10, cancelled=lambda: False):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancelled():
                raise RuntimeError("superseded")
            try:
                event = self.events.get(
                    timeout=min(0.1, max(0.001, deadline - time.monotonic()))
                )
            except queue.Empty:
                continue
            if event.get("event") == "EXIT":
                raise RuntimeError("renderer_exited")
            if generation is not None and event.get("generation") != generation:
                continue
            if event.get("event") == "FAILED":
                raise RuntimeError(event.get("detail", "renderer_failed"))
            if event.get("event") == kind:
                return event
        raise TimeoutError("renderer_timeout")

    def close(self):
        process, self.process = self.process, None
        if not process:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process.stdin:
            process.stdin.close()
        if process.stdout:
            process.stdout.close()
