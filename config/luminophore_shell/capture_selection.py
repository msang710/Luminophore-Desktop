"""Selection geometry shared by the capture overlay and its transaction."""
from dataclasses import dataclass
import math

@dataclass
class CaptureSelection:
    button: int = 0
    origin: tuple[float, float] | None = None
    rect: tuple[int, int, int, int] | None = None

    def begin(self, button, x, y):
        if self.origin is not None or button not in (1, 3):
            self.cancel()
            return False
        self.button, self.origin = button, (x, y)
        return True

    def update(self, x, y):
        if self.origin is None or not all(math.isfinite(v) for v in (*self.origin, x, y)):
            return None
        ox, oy = self.origin
        left, top = math.floor(min(ox, x)), math.floor(min(oy, y))
        self.rect = (left, top, math.ceil(max(ox, x)) - left, math.ceil(max(oy, y)) - top)
        return self.rect

    def finish(self, x, y):
        rect = self.update(x, y)
        result = {'mode': 'pip' if self.button == 3 else 'screenshot', 'rect': rect} if rect and min(rect[2:]) > 1 else None
        self.cancel()
        return result

    def cancel(self):
        self.button, self.origin, self.rect = 0, None, None
