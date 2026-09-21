"""One compile per disposable worker process; parent may cancel at any point."""

import json
import resource
import sys
from pathlib import Path
from .scene_profile import SceneProfileDraft
from .scene_profile_compiler import compile_profile


def main():
    resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (90, 95))
    try:
        draft = SceneProfileDraft.parse(json.loads(Path(sys.argv[1]).read_text()))
        path = compile_profile(
            draft, Path(sys.argv[2]), temporary_prefix=f".{Path(sys.argv[1]).stem}-"
        )
        print(
            json.dumps({"ok": True, "package": str(path), "revision": draft.revision})
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "compiler_unavailable"
                    if isinstance(exc, ModuleNotFoundError)
                    else str(exc)
                    if isinstance(exc, ValueError)
                    else type(exc).__name__,
                }
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
