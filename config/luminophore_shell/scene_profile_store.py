import json
from pathlib import Path
from .background_scene import identifier
from .background_store import atomic_json, state_root
from .scene_profile import SceneProfileDraft


class ProfileStore:
    def __init__(self, root=None):
        self.root = Path(root) if root else state_root() / "scene-profiles"

    def directory(self, profile_id):
        return self.root / identifier(profile_id)

    def save(self, draft):
        atomic_json(self.directory(draft.id) / "draft.json", draft.to_dict())

    def load(self, profile_id):
        return SceneProfileDraft.parse(
            json.loads((self.directory(profile_id) / "draft.json").read_text())
        )
