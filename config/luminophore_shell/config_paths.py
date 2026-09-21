"""Paths shared by portable sessions and source-tree development."""
from pathlib import Path


def lua_config_path(domain: str, config_path: Path) -> Path:
    if domain not in {"bindings", "theme", "monitor_layout", "placements", "bundles", "owned"}:
        raise ValueError("unknown generated Lua domain")
    config_path = Path(config_path)
    root = config_path.parent / "config" if config_path.name == "shell.toml" else config_path.parent.parent
    return root / f"luminophore_{domain}.lua"
