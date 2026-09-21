"""Real five-file store with a deterministic participant for UI adapter tests."""
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
from luminophore_shell.settings_bundle import settings_store
from luminophore_shell.settings_store import SettingsPaths, FILES
from luminophore_shell.settings_generation import edit_candidate


class Domains:
    def __init__(self, case, runtime=None):
        temp = TemporaryDirectory(); case.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.store = settings_store(SettingsPaths(root/'config', root/'state', root/'cache'))
        docs = {name: 'schema_version=1\n' for name in FILES}
        self.runtime = runtime
        if runtime:
            from luminophore_shell.hyprland_settings import SEMANTIC_OPTION_MAP
            from luminophore_shell.toml_edit import patch
            colors = {}
            for option, token in SEMANTIC_OPTION_MAP.items():
                rgba = runtime.values[option][5:-1]
                colors[token] = rgba[6:]+rgba[:6]
            docs['settings.toml'] = patch(docs['settings.toml'], 'native.palette', colors)
        generation = self.store.prepare(docs, '')
        self.store.publish(generation.id, '')
        self.fail = None

    def snapshot(self):
        current = self.store.current()
        return {name: tomllib.loads(text) for name, text in current.documents.items()}, current.id

    def commit(self, changes, digest):
        if self.fail: raise self.fail
        current = self.store.current()
        candidate = edit_candidate(self.store, current, changes)
        self.store.prepare(candidate.documents, digest)
        if self.runtime and 'native' in changes:
            from luminophore_shell.hyprland_settings import SEMANTIC_OPTION_MAP
            colors = changes['native']['palette']
            try:
                self.runtime.apply_options({option: 'rgba('+colors[token][2:]+colors[token][:2]+')' for option, token in SEMANTIC_OPTION_MAP.items()})
            except Exception:
                self.store.abandon(candidate.id, expected=digest)
                raise
        self.store.publish(candidate.id, digest)
        return {'ok': True, 'digest': candidate.id, 'category': 'ok'}
