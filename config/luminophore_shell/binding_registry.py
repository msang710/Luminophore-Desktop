from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Iterable, Mapping


class BindingPhase(StrEnum):
    PRESS = "press"
    RELEASE = "release"


_MODIFIER_ORDER = ("SUPER", "CTRL", "ALT", "SHIFT")
_MODIFIER_ALIASES = {
    "CONTROL": "CTRL",
    "CTRL": "CTRL",
    "SUPER": "SUPER",
    "META": "SUPER",
    "ALT": "ALT",
    "SHIFT": "SHIFT",
}


def normalize_chord(value: str) -> str:
    parts = [part.strip() for part in value.split("+") if part.strip()]
    if not parts:
        raise ValueError("binding chord must not be empty")
    modifiers: set[str] = set()
    key = ""
    for raw in parts:
        upper = raw.upper()
        modifier = _MODIFIER_ALIASES.get(upper)
        if modifier:
            modifiers.add(modifier)
            continue
        if key:
            raise ValueError("binding chord must contain exactly one key")
        key = raw if raw.startswith(("code:", "mouse:")) else upper
    if not key:
        raise ValueError("binding chord requires a key")
    ordered = [item for item in _MODIFIER_ORDER if item in modifiers]
    return " + ".join((*ordered, key))


@dataclass(frozen=True, slots=True)
class BindingFlags:
    locked: bool = False
    repeating: bool = False
    release: bool = False
    non_consuming: bool = False

    def __post_init__(self) -> None:
        if self.release and self.repeating:
            raise ValueError("release bindings cannot repeat")

    @property
    def phase(self) -> BindingPhase:
        return BindingPhase.RELEASE if self.release else BindingPhase.PRESS


@dataclass(frozen=True, slots=True)
class BindingAction:
    action_id: str
    label: str
    category: str
    chord: str | None
    flags: BindingFlags = BindingFlags()
    group_id: str = ""
    recovery: bool = False

    def __post_init__(self) -> None:
        if not self.action_id or not self.label or not self.category:
            raise ValueError("binding action metadata must not be empty")
        if self.chord is not None:
            object.__setattr__(self, "chord", normalize_chord(self.chord))


@dataclass(frozen=True, slots=True)
class BindingRegistry:
    actions: tuple[BindingAction, ...]

    def __post_init__(self) -> None:
        identifiers = [action.action_id for action in self.actions]
        if identifiers != sorted(identifiers) or len(set(identifiers)) != len(identifiers):
            raise ValueError("binding actions must be unique and sorted")
        self.validate()

    def validate(self) -> None:
        triggers: dict[tuple[str, BindingPhase], str] = {}
        for action in self.actions:
            if action.chord is None:
                continue
            trigger = (action.chord, action.flags.phase)
            previous = triggers.get(trigger)
            if previous:
                raise ValueError(f"duplicate binding chord: {action.chord} ({previous}, {action.action_id})")
            triggers[trigger] = action.action_id
        groups: dict[str, list[BindingAction]] = {}
        for action in self.actions:
            if action.group_id:
                groups.setdefault(action.group_id, []).append(action)
        for group_id, members in groups.items():
            bound = [member.chord is not None for member in members]
            if any(bound) and not all(bound):
                raise ValueError(f"atomic binding group is partially unbound: {group_id}")
            if not any(bound):
                continue
            if len(members) != 2 or {member.flags.phase for member in members} != {
                BindingPhase.PRESS,
                BindingPhase.RELEASE,
            }:
                raise ValueError(f"atomic binding group must contain one press and one release: {group_id}")
            if len({member.chord for member in members}) != 1:
                raise ValueError(f"atomic binding group must use one chord: {group_id}")
            if len({member.flags.locked for member in members}) != 1:
                raise ValueError(f"atomic binding group must use consistent locked flags: {group_id}")
        recovery = [action for action in self.actions if action.recovery and action.chord is not None]
        if not recovery:
            raise ValueError("at least one recovery binding must remain bound")

    @classmethod
    def build(cls, actions: Iterable[BindingAction]) -> "BindingRegistry":
        return cls(tuple(sorted(actions, key=lambda action: action.action_id)))

    def update(self, changes: Mapping[str, tuple[str | None, BindingFlags | None]]) -> "BindingRegistry":
        known = {action.action_id for action in self.actions}
        unknown = set(changes) - known
        if unknown:
            raise ValueError(f"unknown binding actions: {sorted(unknown)}")
        updated = []
        for action in self.actions:
            if action.action_id not in changes:
                updated.append(action)
                continue
            chord, flags = changes[action.action_id]
            updated.append(replace(action, chord=chord, flags=flags or action.flags))
        return BindingRegistry.build(updated)

    def payload(self) -> bytes:
        from .generated_bindings import serialize_binding_data

        return serialize_binding_data(self)


def _action(
    action_id: str,
    label: str,
    category: str,
    chord: str,
    *,
    flags: BindingFlags = BindingFlags(),
    group_id: str = "",
    recovery: bool = False,
) -> BindingAction:
    return BindingAction(action_id, label, category, chord, flags, group_id, recovery)


def default_registry() -> BindingRegistry:
    locked = BindingFlags(locked=True)
    locked_repeat = BindingFlags(locked=True, repeating=True)
    actions = [
        _action("window.kill_active", "활성 프로세스 종료", "window", "SUPER + ESCAPE"),
        _action("window.close", "창 닫기", "window", "SUPER + Q"),
        _action("window.float", "플로팅 전환", "window", "SUPER + D"),
        _action("window.fullscreen", "전체 화면", "window", "SUPER + F"),
        _action("view.wide.toggle", "와이드 뷰 전환", "window", "SUPER + G"),
        _action("window.focus.left", "왼쪽 창 선택", "window", "SUPER + ALT + LEFT"),
        _action("window.focus.right", "오른쪽 창 선택", "window", "SUPER + ALT + RIGHT"),
        _action("window.focus.up", "위쪽 창 선택", "window", "SUPER + ALT + UP"),
        _action("window.focus.down", "아래쪽 창 선택", "window", "SUPER + ALT + DOWN"),
        _action("view.move.left", "뷰를 왼쪽으로 이동", "window", "SUPER + LEFT"),
        _action("view.move.right", "뷰를 오른쪽으로 이동", "window", "SUPER + RIGHT"),
        _action("view.move.up", "뷰를 위로 이동", "window", "SUPER + UP"),
        _action("view.move.down", "뷰를 아래로 이동", "window", "SUPER + DOWN"),
        _action("view.adjust.left", "뷰 왼쪽 경계 조절", "window", "SUPER + CTRL + LEFT"),
        _action("view.adjust.right", "뷰 오른쪽 경계 조절", "window", "SUPER + CTRL + RIGHT"),
        _action("view.adjust.up", "뷰 위쪽 경계 조절", "window", "SUPER + CTRL + UP"),
        _action("view.adjust.down", "뷰 아래쪽 경계 조절", "window", "SUPER + CTRL + DOWN"),
        _action("spatial.undo", "공간 되돌리기", "window", "SUPER + Z"),
        _action("spatial.redo", "공간 다시 실행", "window", "SUPER + SHIFT + Z"),
        _action("window.cycle", "다음 창", "window", "ALT + TAB"),
        _action("board.move.left", "창을 왼쪽 보드 좌표로 이동", "window", "SUPER + SHIFT + LEFT"),
        _action("board.move.right", "창을 오른쪽 보드 좌표로 이동", "window", "SUPER + SHIFT + RIGHT"),
        _action("board.move.up", "창을 위쪽 보드 좌표로 이동", "window", "SUPER + SHIFT + UP"),
        _action("board.move.down", "창을 아래쪽 보드 좌표로 이동", "window", "SUPER + SHIFT + DOWN"),
        _action("window.drag.begin", "창 드래그", "pointer", "SUPER + mouse:272"),
        _action("window.resize", "창 크기 조정", "pointer", "SUPER + mouse:273"),
        _action("shell.outside_click", "Shell 바깥 클릭", "pointer", "mouse:272", flags=BindingFlags(release=True, non_consuming=True)),
        _action("cursor.zoom_out", "화면 축소", "accessibility", "SUPER + MINUS", flags=BindingFlags(repeating=True)),
        _action("cursor.zoom_in", "화면 확대", "accessibility", "SUPER + PLUS", flags=BindingFlags(repeating=True)),
        _action("cursor.zoom_out_keypad", "키패드 화면 축소", "accessibility", "SUPER + code:82", flags=BindingFlags(repeating=True)),
        _action("cursor.zoom_in_keypad", "키패드 화면 확대", "accessibility", "SUPER + code:86", flags=BindingFlags(repeating=True)),
        _action("shell.launcher", "런처", "shell", "SUPER + TAB"),
        _action("shell.settings", "설정", "shell", None, recovery=True),
        _action("shell.spatial_editor", "공간 편집기", "shell", "SUPER + SPACE"),
        _action("view.desktop.toggle", "바탕화면 노출 전환", "window", "SUPER + X"),
        _action("shell.overview", "전체 위젯 보기", "shell", "SUPER + SUPER_L", flags=BindingFlags(release=True)),
        _action("shell.emoji", "이모지", "shell", "SUPER + PERIOD"),
        _action("shell.appearance", "Appearance", "shell", "SUPER + SHIFT + W"),
        _action("shell.wallpaper", "Wallpaper", "shell", "SUPER + ALT + W"),
        _action("shell.clipboard", "클립보드", "shell", "SUPER + V"),
        _action("shell.notifications", "알림", "shell", "SUPER + A"),
        _action("app.terminal", "터미널", "application", "SUPER + RETURN", recovery=True),
        _action("app.files", "파일 관리자", "application", "SUPER + E"),
        _action("app.editor", "편집기", "application", "SUPER + T"),
        _action("app.calculator", "계산기", "application", "SUPER + C"),
        _action("app.calculator_hardware", "계산기 키", "application", "XF86Calculator"),
        _action("app.browser", "브라우저", "application", "SUPER + W"),
        _action("app.mission_center", "시스템 모니터", "application", "CTRL + SHIFT + ESCAPE"),
        _action("hardware.volume_up", "볼륨 높이기", "hardware", "XF86AudioRaiseVolume", flags=locked_repeat),
        _action("hardware.volume_down", "볼륨 낮추기", "hardware", "XF86AudioLowerVolume", flags=locked_repeat),
        _action("hardware.volume_mute", "음소거", "hardware", "XF86AudioMute", flags=locked),
        _action("hardware.microphone_mute", "마이크 음소거", "hardware", "XF86AudioMicMute", flags=locked),
        _action("hardware.media_toggle", "미디어 재생/일시정지", "hardware", "XF86AudioPlay", flags=locked),
        _action("hardware.media_pause", "미디어 일시정지", "hardware", "XF86AudioPause", flags=locked),
        _action("hardware.media_next", "다음 미디어", "hardware", "XF86AudioNext", flags=locked),
        _action("hardware.media_previous", "이전 미디어", "hardware", "XF86AudioPrev", flags=locked),
        _action("hardware.brightness_up.preview", "밝기 높이기", "hardware", "XF86MonBrightnessUp", flags=locked_repeat, group_id="brightness_up"),
        _action("hardware.brightness_up.commit", "밝기 높이기 확정", "hardware", "XF86MonBrightnessUp", flags=BindingFlags(locked=True, release=True), group_id="brightness_up"),
        _action("hardware.brightness_down.preview", "밝기 낮추기", "hardware", "XF86MonBrightnessDown", flags=locked_repeat, group_id="brightness_down"),
        _action("hardware.brightness_down.commit", "밝기 낮추기 확정", "hardware", "XF86MonBrightnessDown", flags=BindingFlags(locked=True, release=True), group_id="brightness_down"),
        _action("capture.region", "영역 캡처", "utility", "PRINT"),
        _action("capture.region_save", "영역 캡처 저장", "utility", "SUPER + PRINT"),
        _action("utility.color_picker", "색상 선택", "utility", "SUPER + P"),
    ]
    return BindingRegistry.build(actions)
