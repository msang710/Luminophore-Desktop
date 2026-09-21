from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import threading
import time
from uuid import uuid4
from .background_store import atomic_json
from .background_scene import SceneAsset
from .background_protocol import asset_spec
from .scene_profile_store import ProfileStore
from .scene_profile_compiler import validate_package
from .bootstrap import internal_python_environment


class SceneProfileService:
    def __init__(self, store=None, binary=None, python=None):
        self.store = store or ProfileStore()
        self.binary = (
            Path(binary)
            if binary
            else Path(__file__).with_name("luminophore-background-layer")
        )
        self.python = python or os.environ.get("LUMINOPHORE_PROFILE_PYTHON", sys.executable)
        self.lock = threading.RLock()
        self.generation = 0
        self.process = None
        self.result = None
        self.error = ""
        self.phase = "editing"

    def cancel(self):
        with self.lock:
            self.generation += 1
            if self.process and self.process.poll() is None:
                self.process.terminate()
            if self.result:
                for frame in self.result.get("frames", []):
                    Path(frame).unlink(missing_ok=True)
            self.result = None
            self.phase = "editing"
            self.error = ""

    def compile(self, draft, callback=lambda: None):
        self.cancel()
        self.store.save(draft)
        with self.lock:
            token = self.generation
            self.phase = "compiling"
        directory = self.store.directory(draft.id)
        snapshot = directory / f"request-{uuid4().hex}.json"
        atomic_json(snapshot, draft.to_dict())

        def work():
            process = None
            frames = []
            spec = directory / f"{snapshot.stem}-preview.json"
            try:
                with self.lock:
                    if token != self.generation:
                        return
                    process = subprocess.Popen(
                        [
                            self.python,
                            "-m",
                            "luminophore_shell.scene_profile_worker",
                            str(snapshot),
                            str(directory / "generated"),
                        ],
                        cwd=Path(__file__).resolve().parent.parent,
                        env={
                            **internal_python_environment(),
                            "OPENBLAS_NUM_THREADS": "1",
                            "OMP_NUM_THREADS": "1",
                        },
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                        start_new_session=True,
                    )
                    self.process = process
                stdout, _ = process.communicate(timeout=100)
                result = json.loads(stdout)
                if process.returncode or not result.get("ok"):
                    raise ValueError(result.get("error", "compile_failed"))
                package = Path(result["package"])
                validate_package(package)
                base_spec = asset_spec(
                    SceneAsset.capture(
                        package,
                        fit=draft.source.fit,
                        focal_x=draft.source.focal_x,
                        focal_y=draft.source.focal_y,
                    )
                )
                frames = []
                # A bounded native-rendered motion loop, never a Python approximation.
                preview_deadline = time.monotonic() + 30
                for frame in range(48 if draft.motion else 1):
                    if time.monotonic() >= preview_deadline:
                        raise ValueError("preview_timeout")
                    with self.lock:
                        if token != self.generation:
                            return
                    frame_spec = dict(base_spec)
                    frame_spec["preview_time"] = frame / 12
                    atomic_json(spec, frame_spec)
                    preview = directory / f"{snapshot.stem}-preview-{frame}.png"
                    with self.lock:
                        if token != self.generation:
                            return
                        process = subprocess.Popen(
                            [str(self.binary), "--preview", str(spec), str(preview)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        self.process = process
                    process.wait(
                        timeout=max(0.1, min(20, preview_deadline - time.monotonic()))
                    )
                    if process.returncode:
                        raise ValueError("native_preview_failed")
                    frames.append(str(preview))
                with self.lock:
                    if token != self.generation:
                        return
                    self.result = {
                        "package": str(package),
                        "preview": frames[0],
                        "frames": frames,
                        "revision": draft.revision,
                    }
                    self.phase = "preview_ready"
            except Exception as exc:
                with self.lock:
                    if process and process.poll() is None:
                        process.kill()
                        process.wait(timeout=2)
                    if token == self.generation:
                        self.phase = "error"
                        self.error = (
                            str(exc)
                            if isinstance(exc, ValueError)
                            else type(exc).__name__
                        )
            finally:
                snapshot.unlink(missing_ok=True)
                spec.unlink(missing_ok=True)
                for temporary in (directory / "generated").glob(f".{snapshot.stem}-*"):
                    if temporary.is_dir():
                        shutil.rmtree(temporary)
                with self.lock:
                    if token != self.generation or self.phase != "preview_ready":
                        for frame in frames:
                            Path(frame).unlink(missing_ok=True)
                    if token == self.generation:
                        self.process = None
                callback()

        threading.Thread(target=work, daemon=True, name="luminophore-profile-compile").start()

    def save(self, draft):
        with self.lock:
            if (
                self.phase != "preview_ready"
                or not self.result
                or self.result["revision"] != draft.revision
            ):
                raise ValueError("preview_required")
            validate_package(Path(self.result["package"]))
            atomic_json(
                self.store.directory(draft.id) / "saved.json",
                {"schema": 1, **self.result},
            )
            self.phase = "saved"
            return SceneAsset.capture(
                Path(self.result["package"]),
                fit=draft.source.fit,
                focal_x=draft.source.focal_x,
                focal_y=draft.source.focal_y,
            )
