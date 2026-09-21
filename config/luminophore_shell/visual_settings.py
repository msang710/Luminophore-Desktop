from __future__ import annotations

from dataclasses import dataclass
import math
import re


@dataclass(frozen=True)
class VisualSettings:
    schema_version: int = 1
    preset: str = "balanced"
    enabled: bool = True
    breathing: bool = True
    intensity: float = 0.42

    def wire(self, receipt: tuple[str, str, int] | None = None) -> str:
        if type(self.schema_version) is not int or not 0 <= self.schema_version < 2**32:
            raise ValueError("invalid visual schema version")
        if not isinstance(self.preset, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", self.preset):
            raise ValueError("invalid visual preset identifier")
        if type(self.enabled) is not bool or type(self.breathing) is not bool:
            raise ValueError("visual switches must be boolean")
        if type(self.intensity) not in (int, float) or not math.isfinite(self.intensity):
            raise ValueError("visual intensity must be finite")
        words = [str(self.schema_version), self.preset, str(self.enabled).lower(), str(self.breathing).lower(), str(self.intensity)]
        if receipt is not None:
            source, token, serial = receipt
            if (not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{32}", source)
                    or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", token)
                    or type(serial) is not int or not 0 <= serial < 2**64):
                raise ValueError("invalid visual receipt identity")
            words.extend((source, token, str(serial)))
        return ' '.join(words)


@dataclass(frozen=True)
class VisualSettingsState:
    revision: int
    configured: bool
    recovery: str
    settings: VisualSettings
    renderer_schema: int = 0
    source: str = ""
    serial: int = 0
    token: str = ""


def parse_visual_settings(data: object) -> VisualSettingsState:
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1:
        raise ValueError("invalid visual response schema")
    revision = data.get("revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9]{1,20}", revision) or int(revision) >= 2**64:
        raise ValueError("invalid visual revision")
    configured = data.get("configured")
    if type(configured) is not bool or configured != (int(revision) > 0):
        raise ValueError("inconsistent visual activation state")
    recovery = data.get("recovery")
    if not isinstance(recovery, str) or recovery not in {"none", "malformed", "schema", "preset", "intensity"}:
        raise ValueError("invalid visual recovery")
    settings = data.get("settings")
    if not isinstance(settings, dict) or type(settings.get("schemaVersion")) is not int or settings["schemaVersion"] != 1:
        raise ValueError("invalid normalized visual settings")
    try:
        result = VisualSettings(settings["schemaVersion"], settings["preset"], settings["enabled"], settings["breathing"], settings["intensity"])
        result.wire()  # Validate types without evaluating configuration code.
    except (KeyError, TypeError, OverflowError) as error:
        raise ValueError("invalid normalized visual fields") from error
    if result.preset != "balanced" or not 0 <= result.intensity <= 3:
        raise ValueError("visual response is not normalized")
    if recovery != "none" and result != VisualSettings():
        raise ValueError("visual recovery did not restore the complete default")
    renderer_schema = data.get("rendererSchema", 0)
    if type(renderer_schema) is not int or renderer_schema not in (0, 1):
        raise ValueError("unsupported visual renderer schema")
    source, serial, token = "", 0, ""
    if "receiptSchema" in data:
        if type(data["receiptSchema"]) is not int or data["receiptSchema"] != 1:
            raise ValueError("unsupported visual receipt schema")
        source, raw_serial, token = data.get("source"), data.get("serial"), data.get("token")
        if (not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{32}", source)
                or not isinstance(raw_serial, str) or not re.fullmatch(r"[0-9]{1,20}", raw_serial)
                or int(raw_serial) >= 2**64 or not isinstance(token, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{0,128}", token)):
            raise ValueError("invalid visual receipt")
        serial = int(raw_serial)
        if configured != (serial > 0) or serial < int(revision) or (serial == 0 and token):
            raise ValueError("inconsistent visual receipt")
    return VisualSettingsState(int(revision), configured, recovery, result, renderer_schema, source, serial, token)


def resolve_visual_config(raw: object) -> tuple[VisualSettings, str]:
    """Normalize a persisted semantic bundle without mutating its source file."""
    default = VisualSettings()
    if raw is None:
        return default, "none"
    if not isinstance(raw, dict) or set(raw) - set(VisualSettings.__dataclass_fields__):
        return default, "malformed"
    candidate = VisualSettings(**raw)
    if type(candidate.schema_version) is not int:
        return default, "malformed"
    if candidate.schema_version != 1:
        return default, "schema"
    if not isinstance(candidate.preset, str):
        return default, "malformed"
    if candidate.preset != "balanced":
        return default, "preset"
    if type(candidate.enabled) is not bool or type(candidate.breathing) is not bool:
        return default, "malformed"
    if type(candidate.intensity) not in (int, float):
        return default, "malformed"
    try:
        finite = math.isfinite(candidate.intensity)
    except OverflowError:
        finite = False
    if not finite:
        return default, "intensity"
    return VisualSettings(candidate.schema_version, candidate.preset, candidate.enabled,
                          candidate.breathing, min(3.0, max(0.0, candidate.intensity))), "none"
