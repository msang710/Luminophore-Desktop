from collections import OrderedDict
import io
from pathlib import Path
from threading import Condition, Thread
from PIL import Image, ImageOps
from .scene_profile_compiler import validate_package


class BackgroundThumbnails:
    """Bounded latest-first decoding; obsolete navigation never blocks new cards."""

    def __init__(self):
        self.condition = Condition()
        self.cache = OrderedDict()
        self.pending = OrderedDict()
        self.active = None
        self.closed = False
        self.worker = Thread(target=self._run, daemon=True, name="scene-thumbnail")
        self.worker.start()

    def request(self, asset, callback):
        key = (asset.sha256, asset.fit, asset.focal_x, asset.focal_y)
        discarded = []
        with self.condition:
            if self.closed:
                return
            if key in self.cache:
                cached = self.cache[key]
            else:
                if self.active and self.active[0] == key:
                    callbacks = self.active[1]
                elif key in self.pending:
                    callbacks = self.pending[key][1]
                    self.pending.move_to_end(key)
                else:
                    if len(self.pending) >= 12:
                        _, (_, discarded) = self.pending.popitem(last=False)
                    callbacks = []
                    self.pending[key] = (asset, callbacks)
                callbacks.append(callback)
                del callbacks[:-6]
                self.condition.notify()
                cached = None
        for subscriber in discarded:
            subscriber(None)
        if cached is not None:
            callback(cached)

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending)
                if self.closed:
                    return
                key, (asset, callbacks) = self.pending.popitem(last=True)
                self.active = (key, callbacks)
            pixels = None
            try:
                asset.validate()
                path = Path(asset.path)
                if path.suffix.lower() == ".json":
                    data = validate_package(path)
                    path = Path(data["source"])
                with Image.open(path) as im:
                    if im.width * im.height > 32_000_000:
                        raise ValueError("thumbnail_budget")
                    im = ImageOps.exif_transpose(im).convert("RGB")
                    im.thumbnail((320, 200))
                    out = io.BytesIO()
                    im.save(out, format="PNG")
                    pixels = out.getvalue()
            except (OSError, ValueError, TypeError, KeyError):
                pass
            with self.condition:
                self.active = None
                if pixels:
                    self.cache[key] = pixels
                    while len(self.cache) > 24:
                        self.cache.popitem(last=False)
                subscribers = tuple(callbacks) if not self.closed else ()
            for callback in subscribers:
                callback(pixels)

    def close(self):
        with self.condition:
            self.closed = True
            self.pending.clear()
            self.condition.notify()
