from __future__ import annotations

from typing import Protocol

from .appearance_types import AppearanceCompileRequest, CompiledAppearance


class AppearanceCompilerError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class AppearanceCompiler(Protocol):
    def compile(self, request: AppearanceCompileRequest) -> CompiledAppearance: ...

    def shutdown(self) -> None: ...
