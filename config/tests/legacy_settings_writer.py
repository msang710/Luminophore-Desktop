"""Historical single-file writer used only by archived coordinator regression tests."""
from pathlib import Path
from typing import Any
from luminophore_shell.config import ConfigConflictError,ConfigError,config_digest,load_config_text,render_config_patch,write_config_patch,_write_text_atomic
from luminophore_shell.settings_contract import SettingsMutationError,SettingsResultCategory

class ConfigSettingsWriter:
    """Atomic coordinator adapter for the existing validated TOML store."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def digest(self) -> str:
        return config_digest(self.path)

    def validate(self, changes: dict[str, Any]) -> None:
        text = self.path.read_text(encoding="utf-8")
        load_config_text(render_config_patch(text, changes, config_root=self.path.parent), self.path)

    def snapshot(self) -> tuple[str, str]:
        return self.path.read_text(encoding="utf-8"), self.digest()

    def write_atomic(self, changes: dict[str, Any], expected_digest: str) -> str:
        try:
            write_config_patch(self.path, changes, expected_digest)
        except ConfigConflictError as exc:
            raise SettingsMutationError(SettingsResultCategory.CONFLICT, str(exc)) from exc
        except ConfigError as exc:
            raise SettingsMutationError(SettingsResultCategory.VALIDATION, str(exc)) from exc
        return self.digest()

    def restore_atomic(self, snapshot: object) -> str:
        if not isinstance(snapshot, tuple) or len(snapshot) != 2 or not isinstance(snapshot[0], str):
            raise ConfigError("invalid settings snapshot")
        load_config_text(snapshot[0], self.path)
        _write_text_atomic(self.path, snapshot[0])
        return self.digest()
