from __future__ import annotations
import fcntl
import json
import os
from pathlib import Path
import tempfile
from .background_scene import WallpaperScene, canonical, identifier


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".scene-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def config_root():
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        / "luminophore-shell"
    )


def state_root():
    return (
        Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        / "luminophore-shell"
    )


class BackgroundStore:
    def __init__(self, root=None, state=None):
        self.root = Path(root) if root else config_root()
        self.state = Path(state) if state else state_root()

    def scenes(self):
        path = self.root / "wallpaper-scenes.json"
        if not path.exists():
            return ()
        data = json.loads(path.read_text())
        if set(data) != {"schema", "scenes"} or data["schema"] != 1:
            raise ValueError("manifest_schema")
        rows = tuple(WallpaperScene.parse(row) for row in data["scenes"])
        if len(rows) > 1000 or len({s.id for s in rows}) != len(rows):
            raise ValueError("duplicate_scene")
        return tuple(sorted(rows, key=lambda s: (s.order, s.id)))

    def save(self, scene):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".wallpaper-scenes.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            rows = {row.id: row for row in self.scenes()}
            rows[scene.id] = scene
            atomic_json(
                self.root / "wallpaper-scenes.json",
                {
                    "schema": 1,
                    "scenes": [
                        row.to_dict()
                        for row in sorted(rows.values(), key=lambda s: (s.order, s.id))
                    ],
                },
            )

    def committed(self):
        path = self.state / "background-state.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        if data.get("schema") != 1:
            raise ValueError("state_schema")
        identifier(data["scene"])
        if (
            type(data.get("generation")) is not int
            or not 1 <= data["generation"] < 2**53
        ):
            raise ValueError("state_generation")
        if (
            "snapshot" in data
            and WallpaperScene.parse(data["snapshot"]).id != data["scene"]
        ):
            raise ValueError("state_snapshot")
        return data

    def commit(self, scene, generation, bindings):
        atomic_json(
            self.state / "background-state.json",
            {
                "schema": 1,
                "scene": scene.id,
                "generation": generation,
                "bindings": bindings,
                "snapshot": scene.to_dict(),
            },
        )
