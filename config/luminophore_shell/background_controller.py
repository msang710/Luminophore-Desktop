from __future__ import annotations
from collections import OrderedDict
import threading
from uuid import uuid4
from .background_scene import WallpaperScene, bind_outputs, identifier
from .background_protocol import asset_spec, topology_key
from .background_store import BackgroundStore
from .background_layer import BackgroundLayer


class BackgroundSceneController:
    """Single writer for native generations; UI only submits scene intent."""

    def __init__(
        self,
        monitors,
        store=None,
        layer=None,
        palette=lambda scene, bindings: None,
        retire=lambda: None,
    ):
        self.monitors = monitors
        self.store = store or BackgroundStore()
        self.layer = layer or BackgroundLayer()
        self.palette, self.retire = palette, retire
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.jobs = OrderedDict()
        self.pending = None
        self.generation = 0
        self.phase = "idle"
        self.error = ""
        self.active = None
        self.desired = None
        self.topology = None
        self.degraded_topology = None
        self.worker = threading.Thread(
            target=self._run, daemon=True, name="luminophore-background-controller"
        )
        self.worker.start()

    def status(self):
        with self.lock:
            return {
                "ok": True,
                "phase": self.phase,
                "error": self.error,
                "active": self.active,
                "generation": self.generation,
                "desired": self.desired,
                "jobs": {key: dict(value) for key, value in self.jobs.items()},
            }

    def request(self, scene_id, request_id=None):
        identifier(scene_id)
        request_id = identifier(request_id or uuid4().hex)
        with self.lock:
            if request_id in self.jobs:
                if self.jobs[request_id]["scene"] != scene_id:
                    raise ValueError("request_conflict")
                return self.status()
            if self.pending and self.pending[0] in self.jobs:
                self.jobs[self.pending[0]]["phase"] = "superseded"
            self.jobs[request_id] = {"scene": scene_id, "phase": "queued"}
            self.pending = (request_id, scene_id)
            self.desired = scene_id
            while len(self.jobs) > 64:
                self.jobs.popitem(last=False)
            self.wake.set()
        return self.status()

    def _set(self, phase, error="", job=None):
        with self.lock:
            self.phase, self.error = phase, error
            if job in self.jobs:
                self.jobs[job].update(phase=phase, error=error)

    def _apply(self, job, scene_id):
        scene = None
        if job is None:
            state = self.store.committed()
            if state and "snapshot" in state:
                scene = WallpaperScene.parse(state["snapshot"])
        if scene is None:
            scenes = {s.id: s for s in self.store.scenes()}
            if scene_id not in scenes:
                raise ValueError("scene_missing")
            scene = scenes[scene_id]
        monitors = self.monitors()
        bindings = bind_outputs(monitors)
        topology = topology_key(monitors)
        specs = [
            {
                "connector": bindings[role],
                **asset_spec(getattr(scene, role), recovery=job is None),
            }
            for role in ("left", "right")
        ]
        self.layer.start()
        self.generation += 1
        generation = self.generation
        self._set("preparing", job=job)
        self.layer.send("PREPARE", generation, outputs=specs)
        try:
            self.layer.wait(
                "READY",
                generation,
                cancelled=lambda: self.stopping.is_set() or self.pending is not None,
            )
            if topology_key(self.monitors()) != topology:
                raise RuntimeError("topology_changed")
        except Exception:
            try:
                self.layer.send("ABORT", generation)
            except (RuntimeError, OSError):
                pass
            raise
        self._set("transitioning", job=job)
        self.layer.send("COMMIT", generation, duration_ms=scene.duration_ms)
        # Native COMMITTED is emitted only after both final presentation feedbacks.
        self.layer.wait(
            "COMMITTED",
            generation,
            timeout=scene.duration_ms / 1000 + 10,
            cancelled=self.stopping.is_set,
        )
        if topology_key(self.monitors()) != topology:
            raise RuntimeError("topology_changed")
        self.store.commit(scene, generation, bindings)
        self.active, self.topology = scene.id, topology
        self.degraded_topology = None
        self._set("committed", job=job)
        try:
            self.retire()
        except Exception:
            self._set("committed", "external_provider_retirement_failed", job)
        try:
            self.palette(scene, bindings)
        except Exception:
            self._set("committed", "palette_failed", job)

    def _run(self):
        failures = 0
        while not self.stopping.is_set():
            self.wake.wait(2)
            self.wake.clear()
            with self.lock:
                request, self.pending = self.pending, None
            try:
                if (
                    request is None
                    and self.degraded_topology is not None
                    and topology_key(self.monitors()) != self.degraded_topology
                ):
                    failures = 0
                if request is None and self.active:
                    if failures < 3 and (
                        self.layer.process is None
                        or self.layer.process.poll() is not None
                        or topology_key(self.monitors()) != self.topology
                    ):
                        request = (None, self.active)
                        self.layer.close()
                if request is None:
                    continue
                if request[1] is None:
                    self._fallback("state_invalid")
                    failures = 3
                    continue
                self._apply(*request)
                failures = 0
            except Exception as exc:
                category = (
                    str(exc)
                    if isinstance(exc, (ValueError, RuntimeError, TimeoutError))
                    else type(exc).__name__
                )
                uncertain = self.phase == "transitioning" or category in {
                    "renderer_timeout",
                    "renderer_exited",
                    "renderer_protocol",
                    "topology_changed",
                }
                self._set(
                    "superseded" if category == "superseded" else "error",
                    category,
                    request[0] if request else None,
                )
                if category != "superseded" and (
                    uncertain or request and request[0] is None
                ):
                    self.layer.close()
                    failures += 1
                    if self.active and failures < 3:
                        with self.lock:
                            if self.pending is None:
                                self.pending = (None, self.active)
                        self.wake.wait(min(failures, 3))
                        self.wake.set()
                    elif self.active and not self.stopping.is_set():
                        self._fallback(category)
            finally:
                if self.pending:
                    self.wake.set()
        self.layer.close()

    def _fallback(self, cause):
        """Opaque native recovery surface; never commit it as the user's scene."""
        try:
            from PIL import Image

            path = self.store.state / "background-recovery.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (1, 1), (12, 14, 18, 255)).save(path)
            monitors = self.monitors()
            self.degraded_topology = topology_key(monitors)
            self.layer.start()
            self.generation += 1
            outputs = [
                {"connector": m.name, "layers": [{"path": str(path), "depth": 0}]}
                for m in monitors
            ]
            self.layer.send("FALLBACK", self.generation, outputs=outputs)
            self.layer.wait("READY", self.generation, cancelled=self.stopping.is_set)
            self.layer.send("COMMIT", self.generation, duration_ms=0)
            self.layer.wait(
                "COMMITTED", self.generation, cancelled=self.stopping.is_set
            )
            self._set("degraded", cause)
        except Exception:
            self.layer.close()
            self._set("degraded", "native_recovery_unavailable")

    def restore(self):
        try:
            state = self.store.committed()
        except (ValueError, OSError, TypeError, KeyError):
            with self.lock:
                self.pending = (None, None)
                self.wake.set()
            return
        if state:
            with self.lock:
                self.active = state["scene"]
                self.pending = (None, self.active)
                self.wake.set()

    def close(self):
        self.stopping.set()
        self.wake.set()
        self.worker.join(timeout=12)
