"""Non-executing, read-only decoder for the historical binding JSON format."""
import json
from .binding_registry import BindingFlags, BindingRegistry, default_registry
from .generated_bindings import BINDING_PAYLOAD_VERSION, binding_generation_id

class BindingServiceError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def decode_registry(payload: bytes, baseline_registry=None) -> BindingRegistry:
    baseline_registry = baseline_registry or default_registry()
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BindingServiceError("validation", "binding state JSON is invalid") from exc
    if not isinstance(document, dict) or set(document) != {"bindings", "generation_id", "version"}:
        raise BindingServiceError("validation", "binding state fields are invalid")
    if document["version"] != BINDING_PAYLOAD_VERSION or not isinstance(document["bindings"], list):
        raise BindingServiceError("validation", "binding state version is unsupported")
    baseline = {action.action_id: action for action in baseline_registry.actions}
    changes: dict[str, tuple[str | None, BindingFlags | None]] = {}
    seen: set[str] = set()
    for row in document["bindings"]:
        if not isinstance(row, dict) or set(row) != {"action_id", "chord", "flags", "group_id", "recovery"}:
            raise BindingServiceError("validation", "custom binding fields are forbidden")
        action_id = row["action_id"]
        if not isinstance(action_id, str) or action_id not in baseline or action_id in seen:
            raise BindingServiceError("validation", "custom or duplicate binding action is forbidden")
        seen.add(action_id)
        base = baseline[action_id]
        if row["group_id"] != base.group_id or row["recovery"] is not base.recovery:
            raise BindingServiceError("validation", "binding action metadata is immutable")
        flags = row["flags"]
        if not isinstance(flags, dict) or set(flags) != {"locked", "non_consuming", "release", "repeating"}:
            raise BindingServiceError("validation", "binding flags are invalid")
        if not all(type(value) is bool for value in flags.values()):
            raise BindingServiceError("validation", "binding flags must be boolean")
        chord = row["chord"]
        if chord is not None and not isinstance(chord, str):
            raise BindingServiceError("validation", "binding chord is invalid")
        changes[action_id] = (chord, BindingFlags(
            locked=flags["locked"], repeating=flags["repeating"],
            release=flags["release"], non_consuming=flags["non_consuming"],
        ))
    if seen != set(baseline):
        raise BindingServiceError("validation", "binding state must contain the canonical action set")
    try:
        registry = baseline_registry.update(changes)
    except ValueError as exc:
        raise BindingServiceError("validation", str(exc)) from exc
    if document["generation_id"] != binding_generation_id(registry):
        raise BindingServiceError("validation", "binding generation ID does not match canonical state")
    return registry

