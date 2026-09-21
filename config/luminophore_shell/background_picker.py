class ScenePickerModel:
    def __init__(self, scenes=(), current=None):
        self.scenes = tuple(scenes)
        self.index = next((i for i, s in enumerate(self.scenes) if s.id == current), 0)

    def navigate(self, delta):
        if self.scenes:
            self.index = (self.index + delta) % len(self.scenes)
        return self.selected

    @property
    def selected(self):
        return self.scenes[self.index] if self.scenes else None
