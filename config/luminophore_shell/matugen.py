from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
from typing import Mapping, Sequence

from .appearance_compiler import AppearanceCompilerError
from .appearance_types import (
    AppearanceCompileRequest,
    AppearanceMode,
    AppearanceSourceKind,
    CompiledAppearance,
)
from .theme import Palette


_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
REQUIRED_TOKENS = (
    "source_color", "primary", "on_primary", "secondary", "on_secondary",
    "surface", "surface_container", "on_surface", "on_surface_variant",
    "error", "on_error", "outline",
)


class MatugenError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class MatugenModeTokens:
    mode: str
    colors: dict[str, str]


@dataclass(frozen=True)
class MatugenScheme:
    generation_id: str
    source_color: str
    modes: dict[str, MatugenModeTokens]
    render_data: dict[str, object]


@dataclass(frozen=True)
class GeneratedPalette:
    palette: Palette
    scheme: MatugenScheme


def _hex_rgb(value: str) -> tuple[float, float, float]:
    if not _HEX.fullmatch(value):
        raise MatugenError("backend_incompatible", "matugen 색상 형식이 호환되지 않습니다")
    return tuple(int(value[index:index + 2], 16) / 255 for index in (1, 3, 5))  # type: ignore[return-value]


def _luminance(value: str) -> float:
    def channel(raw: float) -> float:
        return raw / 12.92 if raw <= 0.04045 else ((raw + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(item) for item in _hex_rgb(value))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(foreground: str, background: str) -> float:
    first, second = _luminance(foreground), _luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def parse_matugen_json(value: str | Mapping[str, object]) -> MatugenScheme:
    try:
        raw = json.loads(value) if isinstance(value, str) else dict(value)
    except json.JSONDecodeError as exc:
        raise MatugenError("backend_incompatible", "matugen JSON을 읽지 못했습니다") from exc
    colors = raw.get("colors")
    if not isinstance(colors, dict):
        raise MatugenError("backend_incompatible", "matugen colors 구조가 호환되지 않습니다")
    modes: dict[str, MatugenModeTokens] = {}
    for mode in ("dark", "light"):
        normalized: dict[str, str] = {}
        for token in REQUIRED_TOKENS:
            row = colors.get(token)
            mode_row = row.get(mode) if isinstance(row, dict) else None
            color = mode_row.get("color") if isinstance(mode_row, dict) else None
            if not isinstance(color, str) or not _HEX.fullmatch(color):
                raise MatugenError("backend_incompatible", f"matugen {mode}.{token} token이 없습니다")
            normalized[token] = color.upper()
        for foreground, background in (
            ("on_surface", "surface"),
            ("on_surface", "surface_container"),
            ("on_primary", "primary"),
        ):
            if contrast_ratio(normalized[foreground], normalized[background]) < 4.5:
                raise MatugenError("validation", f"matugen {mode} {foreground}/{background} 대비가 부족합니다")
        modes[mode] = MatugenModeTokens(mode, normalized)
    sanitized = dict(raw)
    sanitized.pop("image", None)
    canonical = json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    generation_id = hashlib.sha256(canonical.encode()).hexdigest()
    return MatugenScheme(
        generation_id,
        modes["dark"].colors["source_color"],
        modes,
        sanitized,
    )


def generated_palette(scheme: MatugenScheme) -> GeneratedPalette:
    dark = scheme.modes["dark"].colors
    return GeneratedPalette(
        Palette(
            dark["primary"],
            dark["secondary"],
            dark["surface"],
            dark["on_surface"],
            dark["on_surface_variant"],
        ),
        scheme,
    )


class MatugenPaletteBackend:
    def __init__(self, executable: Path = Path("/usr/bin/matugen"), timeout_seconds: float = 10.0) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def shutdown(self) -> None:
        with self._lock:
            process = self._process
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def _run(self, source: Sequence[str]) -> GeneratedPalette:
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
            raise MatugenError("backend_missing", "matugen이 설치되어 있지 않습니다")
        argv = [
            str(self.executable), "--type", "scheme-tonal-spot", "--mode", "dark",
            "--source-color-index", "0", "--include-image-in-json", "false",
            "--dry-run", "--quiet", "--json", "hex", *source,
        ]
        try:
            temporary = tempfile.TemporaryDirectory(prefix="luminophore-matugen-palette-")
            root = Path(temporary.name)
            isolated = {
                "XDG_CONFIG_HOME": root / "config",
                "XDG_STATE_HOME": root / "state",
                "XDG_CACHE_HOME": root / "cache",
                "XDG_DATA_HOME": root / "data",
            }
            for path in isolated.values():
                path.mkdir(mode=0o700)
            environment = os.environ.copy()
            environment.update({name: str(path) for name, path in isolated.items()})
            environment["NO_COLOR"] = "1"
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                start_new_session=True,
            )
        except OSError as exc:
            raise MatugenError("backend_missing", "matugen을 실행할 수 없습니다") from exc
        with self._lock:
            self._process = process
        try:
            stdout, _stderr = process.communicate(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.communicate(timeout=1.0)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
            raise MatugenError("compile_timeout", "matugen 색상 생성 시간이 초과됐습니다") from exc
        finally:
            temporary.cleanup()
            with self._lock:
                if self._process is process:
                    self._process = None
        if process.returncode:
            raise MatugenError("generation_failed", "matugen이 색상을 생성하지 못했습니다")
        return generated_palette(parse_matugen_json(stdout))

    def generate_image(self, path: Path) -> GeneratedPalette:
        path = path.expanduser()
        if not path.is_absolute() or not path.is_file():
            raise MatugenError("invalid_source", "배경 이미지 파일을 읽을 수 없습니다")
        return self._run(("image", str(path)))

    def generate_color(self, color: str) -> GeneratedPalette:
        if not _HEX.fullmatch(color):
            raise MatugenError("invalid_source", "배경 색상 형식이 올바르지 않습니다")
        return self._run(("color", "hex", color))


class MatugenAppearanceCompiler:
    TEMPLATE_NAMES = {
        "gtk3": "gtk3.css",
        "gtk4": "gtk4.css",
        "qt": "qtct.conf",
        "kde": "kcolorscheme.colors",
        "kitty": "kitty.conf",
        "alacritty": "alacritty.toml",
        "btop": "btop.theme",
        "ghostty": "ghostty.conf",
    }
    SUPPORTED_OUTPUTS = frozenset(TEMPLATE_NAMES)

    def __init__(
        self,
        executable: Path = Path("/usr/bin/matugen"),
        template_root: Path | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.executable = executable
        self.template_root = template_root or Path(__file__).with_name("templates") / "matugen"
        self.timeout_seconds = timeout_seconds
        self.palette_backend = MatugenPaletteBackend(executable, timeout_seconds)
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def shutdown(self) -> None:
        self.palette_backend.shutdown()
        with self._lock:
            process = self._process
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    @staticmethod
    def _toml_string(path: Path) -> str:
        return json.dumps(str(path))

    def _render(self, scheme: MatugenScheme, mode: AppearanceMode, names: Sequence[str]) -> dict[str, bytes]:
        templates = {name: self.template_root / self.TEMPLATE_NAMES[name] for name in names}
        if any(not path.is_file() for path in templates.values()):
            raise AppearanceCompilerError("compiler_incompatible", "luminophore-shell matugen template이 완전하지 않습니다")
        with tempfile.TemporaryDirectory(prefix="luminophore-matugen-compile-") as directory:
            root = Path(directory)
            render_path = root / "scheme.json"
            config_path = root / "config.toml"
            render_path.write_text(json.dumps(scheme.render_data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.chmod(render_path, 0o600)
            output_paths = {name: root / f"{name}.out" for name in templates}
            lines = ["[config]", ""]
            for name, template in templates.items():
                lines.extend((f"[templates.{name}]", f"input_path = {self._toml_string(template)}", f"output_path = {self._toml_string(output_paths[name])}", ""))
            config_path.write_text("\n".join(lines), encoding="utf-8")
            os.chmod(config_path, 0o600)
            isolated = {key: root / value for key, value in {
                "XDG_CONFIG_HOME": "xdg-config", "XDG_STATE_HOME": "xdg-state",
                "XDG_CACHE_HOME": "xdg-cache", "XDG_DATA_HOME": "xdg-data",
            }.items()}
            for path in isolated.values():
                path.mkdir(mode=0o700)
            env = os.environ.copy()
            env.update({key: str(path) for key, path in isolated.items()})
            env["NO_COLOR"] = "1"
            argv = (str(self.executable), "--config", str(config_path), "--mode", mode.value, "--quiet", "json", str(render_path))
            try:
                process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
            except OSError as exc:
                raise AppearanceCompilerError("compiler_missing", "matugen을 실행할 수 없습니다") from exc
            with self._lock:
                self._process = process
            try:
                _stdout, stderr = process.communicate(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.communicate(timeout=1.0)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.communicate()
                raise AppearanceCompilerError("compile_timeout", "matugen theme 생성 시간이 초과됐습니다") from exc
            finally:
                with self._lock:
                    if self._process is process:
                        self._process = None
            if process.returncode:
                detail = " ".join(stderr.strip().split())[:240]
                raise AppearanceCompilerError("compiler_incompatible", f"matugen theme 생성 실패: {detail}" if detail else "matugen theme 생성에 실패했습니다")
            outputs: dict[str, bytes] = {}
            for name, path in output_paths.items():
                try:
                    payload = path.read_bytes()
                except OSError as exc:
                    raise AppearanceCompilerError("compiler_incompatible", f"matugen {name} 출력이 없습니다") from exc
                if not payload or b"{{" in payload or b"{%" in payload:
                    raise AppearanceCompilerError("compiler_incompatible", f"matugen {name} template 출력이 완전하지 않습니다")
                outputs[name] = payload
            return outputs

    def compile(self, request: AppearanceCompileRequest) -> CompiledAppearance:
        unknown = set(request.required_outputs) - self.SUPPORTED_OUTPUTS
        if unknown:
            raise AppearanceCompilerError("compiler_incompatible", f"지원하지 않는 appearance 출력입니다: {', '.join(sorted(unknown))}")
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
            raise AppearanceCompilerError("compiler_missing", "matugen이 설치되어 있지 않습니다")
        try:
            if request.source.kind is AppearanceSourceKind.IMAGE:
                generated = self.palette_backend.generate_image(Path(request.source.value))
            elif request.source.kind is AppearanceSourceKind.COLOR:
                generated = self.palette_backend.generate_color(request.source.value)
            else:
                raise AppearanceCompilerError("invalid_source", "지원하지 않는 appearance source입니다")
            return self.compile_scheme(generated.scheme, request.mode, request.required_outputs)
        except AppearanceCompilerError:
            raise
        except MatugenError as exc:
            categories = {
                "backend_missing": "compiler_missing", "backend_incompatible": "compiler_incompatible",
                "invalid_source": "invalid_source", "generation_failed": "compiler_incompatible",
                "compile_timeout": "compile_timeout", "validation": "validation",
            }
            raise AppearanceCompilerError(categories.get(exc.category, "compiler_incompatible"), str(exc)) from exc

    def compile_scheme(
        self, scheme: MatugenScheme, mode: AppearanceMode, required_outputs: Sequence[str],
    ) -> CompiledAppearance:
        unknown = set(required_outputs) - self.SUPPORTED_OUTPUTS
        if unknown:
            raise AppearanceCompilerError("compiler_incompatible", f"지원하지 않는 appearance 출력입니다: {', '.join(sorted(unknown))}")
        tokens = dict(scheme.modes[mode.value].colors)
        palette = {
            "primary": tokens["primary"], "secondary": tokens["secondary"],
            "background": tokens["surface"], "foreground": tokens["on_surface"],
            "muted": tokens["on_surface_variant"],
        }
        outputs = self._render(scheme, mode, required_outputs) if required_outputs else {}
        return CompiledAppearance.build(tokens, palette, outputs)
