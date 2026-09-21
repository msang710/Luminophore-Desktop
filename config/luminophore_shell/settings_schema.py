from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import ShellConfig


@dataclass(frozen=True)
class SettingsCategory:
    category_id: str
    title: str
    icon: str
    description: str


@dataclass(frozen=True)
class SettingSpec:
    path: str
    category: str
    label: str
    kind: str
    help: str = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float = 1.0
    digits: int = 0
    choices: tuple[tuple[str, str], ...] = ()
    screen_picker: bool = False
    visible: bool = True


CATEGORIES = (
    SettingsCategory("appearance", "외형", "applications-graphics-symbolic", "투명도·네온·블러·글꼴"),
    SettingsCategory("layout", "배치", "view-grid-symbolic", "여백·크기·드롭 영역"),
    SettingsCategory("launcher", "런처·태스크바", "system-run-symbolic", "검색·고정 앱·태스크바"),
    SettingsCategory("notifications", "알림", "notifications-symbolic", "기록·팝업·방해 금지"),
    SettingsCategory("weather", "날씨", "weather-clear-symbolic", "위치·시간대·캐시"),
    SettingsCategory("metrics", "성능·경고", "utilities-system-monitor-symbolic", "수집 주기·위험 임계값"),
    SettingsCategory("palette", "네온 색상", "applications-graphics-symbolic", "색상 source·고정색·추출"),
    SettingsCategory("compositor", "컴포지터", "preferences-desktop-display-symbolic", "간격·테두리·투명도·블러·VRR"),
    SettingsCategory("input", "입력", "input-keyboard-symbolic", "키 반복·포인터·스크롤"),
    SettingsCategory("motion", "모션", "media-playback-start-symbolic", "애니메이션 preset과 속도"),
)


SETTINGS = (
    SettingSpec("compositor.color_management", "compositor", "색상 관리 (다음 세션부터 적용)", "boolean"),
    SettingSpec("compositor.lock_background", "compositor", "잠금화면 뒤에 데스크톱 표시", "boolean"),
    SettingSpec("compositor.lock_blur", "compositor", "잠금화면 데스크톱 배경 블러", "boolean"),
    SettingSpec("compositor.cursor_start_output", "compositor", "로그인 시 포인터를 둘 출력", "text"),
    SettingSpec("compositor.zoom_factor", "compositor", "기본 화면 확대 배율", "decimal", minimum=1, maximum=10, step=0.05, digits=2),
    SettingSpec("compositor.locale", "compositor", "컴포지터 안내 언어", "text"),
    SettingSpec("compositor.font_family", "compositor", "컴포지터 안내 글꼴", "text"),
    SettingSpec("compositor.wake_on_key", "compositor", "키 입력으로 꺼진 화면 켜기", "boolean"),
    SettingSpec("compositor.wake_on_pointer", "compositor", "포인터 이동으로 꺼진 화면 켜기", "boolean"),
    SettingSpec("compositor.display_idle_minutes", "compositor", "화면 자동 끄기 대기 (분, 0은 끄기)", "integer", minimum=0, maximum=1440),
    SettingSpec("compositor.primary_selection", "compositor", "Wayland 선택한 글 가운데 클릭 붙여넣기", "boolean"),
    SettingSpec("compositor.auto_hdr", "compositor", "전체화면 HDR 자동 전환", "choice", minimum=0, maximum=2, choices=((0, '0'), (1, '1'), (2, '2'))),
    SettingSpec("compositor.sdr_transfer", "compositor", "SDR 색상 곡선", "choice", choices=(('default', 'default'), ('auto', 'auto'), ('srgb', 'srgb'), ('gamma22', 'gamma22'), ('gamma22force', 'gamma22force'))),
    SettingSpec("compositor.icc_vcgt", "compositor", "ICC 보정 곡선 사용", "boolean"),
    SettingSpec("compositor.xwayland_native_pixels", "compositor", "XWayland 앱을 물리 픽셀 크기로 표시", "boolean"),
    SettingSpec("compositor.background_color", "compositor", "바탕색 (AARRGGBB)", "text"),
    SettingSpec("compositor.shadow_enabled", "compositor", "창 그림자", "boolean"),
    SettingSpec("compositor.shadow_range", "compositor", "그림자 범위", "integer", minimum=0, maximum=100, visible=False),
    SettingSpec("compositor.shadow_power", "compositor", "그림자 감쇠", "integer", minimum=1, maximum=4, visible=False),
    SettingSpec("compositor.shadow_sharp", "compositor", "그림자 선명도", "boolean", visible=False),
    SettingSpec("compositor.shadow_color", "compositor", "그림자 색상", "text"),
    SettingSpec("compositor.shadow_inactive_color", "compositor", "비활성 그림자 색상 (inherit은 활성 색상)", "text"),
    SettingSpec("compositor.shadow_scale", "compositor", "그림자 배율", "decimal", minimum=0, maximum=1, step=0.05, digits=2, visible=False),
    SettingSpec("compositor.shadow_offset_x", "compositor", "그림자 가로 이동", "decimal", minimum=-250, maximum=250, step=0.05, digits=2, visible=False),
    SettingSpec("compositor.shadow_offset_y", "compositor", "그림자 세로 이동", "decimal", minimum=-250, maximum=250, step=0.05, digits=2, visible=False),
    SettingSpec("compositor.float_gap_top", "compositor", "플로팅 창 위 여백", "integer", minimum=0, maximum=100),
    SettingSpec("compositor.float_gap_right", "compositor", "플로팅 창 오른쪽 여백", "integer", minimum=0, maximum=100),
    SettingSpec("compositor.float_gap_bottom", "compositor", "플로팅 창 아래 여백", "integer", minimum=0, maximum=100),
    SettingSpec("compositor.float_gap_left", "compositor", "플로팅 창 왼쪽 여백", "integer", minimum=0, maximum=100),
    SettingSpec("compositor.xwayland_nearest_neighbor", "compositor", "XWayland 픽셀 경계 선명하게", "boolean", "끄면 X11 앱에 부드러운 보간을 사용합니다. 창별 최근접 보간 규칙은 별도로 적용됩니다."),
    SettingSpec("compositor.allow_tearing", "compositor", "화면 찢어짐 허용", "boolean", "입력 지연을 줄일 수 있지만 화면이 갈라져 보일 수 있습니다. 지원 출력에서 앱·창 규칙의 조건도 충족해야 적용됩니다."),
    SettingSpec("compositor.pointer_focus_output", "compositor", "포인터가 이동한 모니터에 포커스", "boolean", "공간 드래그 중에는 기존 포커스를 유지합니다."),
    SettingSpec("compositor.fullscreen_focus_policy", "compositor", "전체화면 뒤의 타일 창 선택", "choice", "앱 전체화면·최대화의 포커스 처리이며 WIDE 설정과는 별개입니다.", choices=((0, "기존 전체화면 유지"), (1, "선택한 창에 전체화면 승계"), (2, "전체화면 해제"))),
    SettingSpec("compositor.fullscreen_after_close", "compositor", "전체화면 창 종료 후 상태 승계", "boolean", "닫힌 창 대신 선택되는 창에 기존 전체화면·최대화 상태를 적용합니다."),
    SettingSpec("compositor.zoom_rigid", "compositor", "확대 카메라 고정", "boolean", "분리 카메라에서는 가장자리 추적을 멈추고, 일반 카메라에서는 확대 기준점을 화면 중앙에 둡니다."),
    SettingSpec("compositor.zoom_detached_camera", "compositor", "확대 카메라 분리", "boolean", "포인터가 확대 화면 가장자리에 도달하면 카메라를 이동합니다. 카메라 고정 시에는 이동하지 않습니다."),
    SettingSpec("compositor.zoom_disable_aa", "compositor", "확대 픽셀 보간 끄기", "boolean", "확대 화면을 부드럽게 보간하지 않고 픽셀 경계를 선명하게 표시합니다."),
    SettingSpec("compositor.fullscreen_opacity", "compositor", "전체화면 창 불투명도", "decimal", "앱 전체화면 상태에 적용", minimum=0, maximum=1, step=0.05, digits=2),
    SettingSpec("compositor.dim_inactive", "compositor", "비활성 창 어둡게 표시", "boolean", ""),
    SettingSpec("compositor.dim_modal", "compositor", "모달 창 뒤의 부모 창 어둡게 표시", "boolean", ""),
    SettingSpec("compositor.dim_strength", "compositor", "비활성 창 어둡게 하는 정도", "decimal", "0이면 변화 없음", minimum=0, maximum=1, step=0.05, digits=2),
    SettingSpec("compositor.dim_around", "compositor", "주변 화면 어둡게 하는 정도", "decimal", "주변 어둡게 표시 규칙이 있는 창·패널에 적용", minimum=0, maximum=1, step=0.05, digits=2),
    SettingSpec("compositor.blur_popups", "compositor", "팝업 배경 블러", "boolean", "창의 팝업 메뉴 배경에 적용"),
    SettingSpec("compositor.blur_input_methods", "compositor", "입력기 배경 블러", "boolean", "입력기 후보 창 배경에 적용"),
    SettingSpec("compositor.render_unfocused_fps", "compositor", "백그라운드 창 렌더링 제한", "integer", "백그라운드 렌더링이 지정된 창의 초당 최대 프레임", minimum=1, maximum=120),
    SettingSpec("input.drag_threshold", "input", "창 드래그 시작 거리", "integer", "픽셀 단위. 0이면 즉시 시작", minimum=0, maximum=2147483647),
    SettingSpec("input.scroll_event_delay", "input", "스크롤 단축키 간격", "integer", "밀리초 단위", minimum=0, maximum=2000),
    SettingSpec("input.cursor_inactive_timeout", "input", "포인터 자동 숨김 대기", "decimal", "초 단위. 0이면 숨기지 않음", minimum=0, maximum=20, step=0.25, digits=2),
    SettingSpec("input.cursor_no_warps", "input", "포인터 자동 이동 막기", "boolean", "창 포커스 전환 등에 따른 자동 이동을 제한"),
    SettingSpec("input.cursor_persistent_warps", "input", "창별 포인터 위치 기억", "boolean", "창을 다시 선택하면 마지막 상대 위치로 이동"),
    SettingSpec("input.cursor_hide_on_key_press", "input", "키 입력 중 포인터 숨기기", "boolean", "마우스를 움직이면 다시 표시"),
    SettingSpec("input.cursor_hide_on_touch", "input", "터치 입력 중 포인터 숨기기", "boolean", ""),
    SettingSpec("input.cursor_hide_on_tablet", "input", "태블릿 입력 중 포인터 숨기기", "boolean", ""),
    SettingSpec("input.cursor_warp_back_after_non_mouse_input", "input", "다른 입력 후 마우스 위치 복원", "boolean", "터치·태블릿 입력 이전의 포인터 위치로 복귀"),
    SettingSpec("input.resize_on_border", "input", "창 테두리를 잡아 크기 조절", "boolean", ""),
    SettingSpec("input.extend_border_grab_area", "input", "테두리 바깥 잡기 여백", "integer", "픽셀 단위. 테두리 크기 조절을 켰을 때 적용", minimum=0, maximum=100),
    SettingSpec("input.resize_on_border_inner_area", "input", "테두리 안쪽 잡기 여백", "integer", "픽셀 단위", minimum=0, maximum=100),
    SettingSpec("input.hover_icon_on_border", "input", "테두리에서 크기 조절 포인터 표시", "boolean", ""),
    SettingSpec("input.resize_corner", "input", "floating 크기 조절 기준 모서리", "choice", choices=((0, "자동"), (1, "왼쪽 위"), (2, "오른쪽 위"), (3, "오른쪽 아래"), (4, "왼쪽 아래"))),
    SettingSpec("input.close_gesture_timeout", "input", "닫기 제스처 대기 한도", "integer", "밀리초 단위", minimum=10, maximum=2000),
    SettingSpec("touchdevice.transform", "input", "터치스크린 · 회전·반전", "integer", minimum=0, maximum=6),
    SettingSpec("touchdevice.output", "input", "터치스크린 · 연결할 모니터", "text"),
    SettingSpec("touchdevice.enabled", "input", "터치스크린 · 사용", "boolean"),
    SettingSpec("virtualkeyboard.share_states", "input", "가상 키보드 · 키 상태 공유", "integer", minimum=0, maximum=2),
    SettingSpec("virtualkeyboard.release_pressed_on_close", "input", "가상 키보드 · 종료 시 눌린 키 해제", "boolean"),
    SettingSpec("tablet.transform", "input", "태블릿 · 회전·반전", "integer", minimum=0, maximum=6),
    SettingSpec("tablet.output", "input", "태블릿 · 연결할 모니터", "text"),
    SettingSpec("tablet.absolute_region_position", "input", "태블릿 · 영역의 절대 위치 사용", "boolean"),
    SettingSpec("tablet.relative_input", "input", "태블릿 · 상대 좌표 입력", "boolean"),
    SettingSpec("tablet.left_handed", "input", "태블릿 · 왼손 모드", "boolean"),
    SettingSpec("tablet.region_position_x", "input", "태블릿 · 표시 영역 위치 X", "decimal", minimum=-20000, maximum=20000, step=0.05, digits=2),
    SettingSpec("tablet.region_position_y", "input", "태블릿 · 표시 영역 위치 Y", "decimal", minimum=-20000, maximum=20000, step=0.05, digits=2),
    SettingSpec("tablet.region_size_x", "input", "태블릿 · 표시 영역 크기 X", "decimal", minimum=-100, maximum=4000, step=0.05, digits=2),
    SettingSpec("tablet.region_size_y", "input", "태블릿 · 표시 영역 크기 Y", "decimal", minimum=-100, maximum=4000, step=0.05, digits=2),
    SettingSpec("tablet.active_area_size_x", "input", "태블릿 · 입력 영역 크기 (mm) X", "decimal", minimum=0, maximum=500, step=0.05, digits=2),
    SettingSpec("tablet.active_area_size_y", "input", "태블릿 · 입력 영역 크기 (mm) Y", "decimal", minimum=0, maximum=500, step=0.05, digits=2),
    SettingSpec("tablet.active_area_position_x", "input", "태블릿 · 입력 영역 위치 (mm) X", "decimal", minimum=0, maximum=500, step=0.05, digits=2),
    SettingSpec("tablet.active_area_position_y", "input", "태블릿 · 입력 영역 위치 (mm) Y", "decimal", minimum=0, maximum=500, step=0.05, digits=2),
    SettingSpec("tablettool.eraser_button_mode", "input", "펜 도구 · 지우개 버튼 모드", "integer", minimum=0, maximum=6),
    SettingSpec("tablettool.eraser_button_override", "input", "펜 도구 · 지우개 버튼 코드", "integer", minimum=0, maximum=2147483647),
    SettingSpec("tablettool.pressure_range_min", "input", "펜 도구 · 최소 압력", "decimal", minimum=-1, maximum=1, step=0.05, digits=2),
    SettingSpec("tablettool.pressure_range_max", "input", "펜 도구 · 최대 압력", "decimal", minimum=-1, maximum=1, step=0.05, digits=2),

    SettingSpec("touchpad.disable_while_typing", "input", "입력 중 터치패드 끄기", "boolean"),
    SettingSpec("touchpad.natural_scroll", "input", "터치패드 자연스러운 스크롤", "boolean"),
    SettingSpec("touchpad.scroll_factor", "input", "터치패드 스크롤 속도", "decimal", minimum=0, maximum=2, step=0.05, digits=2),
    SettingSpec("touchpad.middle_button_emulation", "input", "양쪽 버튼으로 가운데 클릭", "boolean"),
    SettingSpec("touchpad.tap_button_map", "input", "탭 버튼 순서", "choice", choices=(("", "기본"), ("lrm", "왼쪽·오른쪽·가운데"), ("lmr", "왼쪽·가운데·오른쪽"))),
    SettingSpec("touchpad.clickfinger_behavior", "input", "손가락 수로 버튼 선택", "boolean"),
    SettingSpec("touchpad.tap_to_click", "input", "탭하여 클릭", "boolean"),
    SettingSpec("touchpad.drag_lock", "input", "드래그 잠금", "integer", minimum=0, maximum=2),
    SettingSpec("touchpad.tap_and_drag", "input", "탭하여 드래그", "boolean"),
    SettingSpec("touchpad.flip_x", "input", "가로 이동 반전", "boolean"),
    SettingSpec("touchpad.flip_y", "input", "세로 이동 반전", "boolean"),
    SettingSpec("touchpad.drag_3fg", "input", "여러 손가락 드래그", "integer", minimum=0, maximum=2),

    SettingSpec("input.kb_file", "input", "사용자 키맵 파일", "text", "가져온 내용을 보관합니다. 원본 수정은 다시 가져오면 반영됩니다. 빈 값은 기본 배열"),
    SettingSpec("input.kb_layout", "input", "키보드 배열", "text"),
    SettingSpec("input.kb_model", "input", "키보드 모델", "text"),
    SettingSpec("input.kb_variant", "input", "키보드 배열 변형", "text"),
    SettingSpec("input.kb_options", "input", "키보드 옵션", "text"),
    SettingSpec("input.kb_rules", "input", "키보드 규칙", "text"),

    SettingSpec("input.scroll_method", "input", "스크롤 방식", "choice", choices=(("", "장치 기본값"), ("2fg", "두 손가락"), ("edge", "가장자리"), ("on_button_down", "버튼을 눌러 스크롤"), ("no_scroll", "스크롤 끄기"))),
    SettingSpec("input.scroll_button", "input", "스크롤 버튼", "integer", "0이면 장치 기본값", minimum=0, maximum=300),
    SettingSpec("input.scroll_button_lock", "input", "스크롤 버튼 잠금", "boolean"),
    SettingSpec("input.rotation", "input", "포인터 회전", "integer", "시계 방향 각도", minimum=0, maximum=359),
    SettingSpec("input.numlock_by_default", "input", "키보드 초기 Num Lock", "boolean", "키맵 초기화 시 Num Lock을 켬"),
    SettingSpec("input.resolve_binds_by_sym", "input", "배열의 문자로 단축키 해석", "boolean", "키보드 배열에 맞는 문자 기준으로 단축키를 해석"),
    SettingSpec("input.follow_mouse", "input", "포인터 포커스 추종", "choice", choices=((0, "추종 안 함"), (1, "추종"), (2, "키보드 포커스 유지"), (3, "클릭에도 포커스 유지"))),
    SettingSpec("input.follow_mouse_threshold", "input", "포커스 이동 임계값", "decimal", "포커스 변경에 필요한 포인터 이동 거리", step=0.25, digits=2),
    SettingSpec("input.mouse_refocus", "input", "포인터 위치로 재포커스", "boolean"),
    SettingSpec("input.follow_mouse_shrink", "input", "포커스 판정 여백", "integer", "비활성 창의 포커스 판정 영역을 줄이는 픽셀 수", minimum=0, maximum=300),
    SettingSpec("input.off_window_axis_events", "input", "창 바깥 스크롤", "choice", choices=((0, "무시"), (1, "전달"), (2, "경계로 제한"), (3, "포인터 이동"))),
    SettingSpec("input.emulate_discrete_scroll", "input", "단계형 스크롤 변환", "choice", choices=((0, "사용 안 함"), (1, "비표준 이벤트"), (2, "모든 이벤트"))),
    SettingSpec("input.focus_on_close", "input", "창을 닫은 뒤 포커스", "choice", choices=((0, "배치의 다음 창"), (1, "포인터 아래 창"), (2, "최근 사용한 창"))),
    SettingSpec("input.float_switch_override_focus", "input", "창 종류 간 포커스 전환", "choice", "포커스 추종을 끈 경우의 포인터 전환 동작", choices=((0, "사용 안 함"), (1, "타일·floating 사이"), (2, "floating 창 사이도 포함"))),
    SettingSpec("input.accel_profile", "input", "포인터 가속 프로필", "text", "빈 값: 장치 기본값. adaptive / flat / custom 간격 점1 점2 …"),
    SettingSpec("input.scroll_points", "input", "사용자 스크롤 곡선", "text", "custom 프로필에서 간격 점1 점2 …; 빈 값은 기본 곡선"),
    SettingSpec("input.repeat_rate", "input", "키 반복 속도", "integer", "초당 반복 횟수 · 0이면 반복하지 않음", minimum=0, maximum=200),
    SettingSpec("input.repeat_delay", "input", "키 반복 대기", "integer", "밀리초", minimum=0, maximum=2000),
    SettingSpec("input.sensitivity", "input", "마우스 감도", "decimal", minimum=-1, maximum=1, step=0.05, digits=2),
    SettingSpec("input.scroll_factor", "input", "스크롤 속도", "decimal", minimum=0, maximum=2, step=0.05, digits=2),
    SettingSpec("input.natural_scroll", "input", "자연스러운 스크롤", "boolean"),
    SettingSpec("input.left_handed", "input", "왼손잡이 버튼 배치", "boolean"),
    SettingSpec("input.force_no_accel", "input", "포인터 가속 사용 안 함", "boolean"),
    SettingSpec("visual.enabled", "appearance", "시각 효과", "boolean", "배경 블러와 네온 효과"),
    SettingSpec("visual.breathing", "appearance", "호흡 애니메이션", "boolean", "끄면 일정한 밝기로 표시"),
    SettingSpec("visual.intensity", "appearance", "네온 밝기", "decimal", minimum=0, maximum=3, step=0.05, digits=2),
    SettingSpec("visual.preset", "appearance", "효과 프리셋", "choice", choices=(("balanced", "균형"),)),
    SettingSpec("visual.schema_version", "appearance", "설정 버전", "integer", visible=False),
    SettingSpec("theme.backdrop_opacity", "appearance", "배경 불투명도", "decimal", minimum=0.2, maximum=0.95, step=0.01, digits=2),
    SettingSpec("layout.radius", "appearance", "모서리 반경", "integer", "px", minimum=0),
    SettingSpec("theme.outline_width", "appearance", "외곽선 굵기", "integer", "px · 1~12 · 두꺼울수록 내용 공간 감소", minimum=1, maximum=12),
    SettingSpec("theme.palette_transition_ms", "appearance", "색상 전환 시간", "integer", "ms", minimum=0),
    SettingSpec("theme.body_font", "appearance", "본문 글꼴", "font_family", "설치된 시스템 글꼴"),
    SettingSpec("theme.numeric_font", "appearance", "숫자 글꼴", "font_family", "설치된 시스템 글꼴"),
    SettingSpec("theme.ui_scale", "appearance", "UI 배율", "decimal", "0.8~1.5", minimum=0.8, maximum=1.5, step=0.05, digits=2),
    SettingSpec("theme.high_contrast", "appearance", "고대비", "boolean", "텍스트와 패널 경계를 강화"),
    SettingSpec(
        "theme.app_icon_theme", "appearance", "앱 아이콘", "choice",
        "Arcticons와 승인한 사용자 선화는 이 위젯에서만 사용",
        choices=(("luminophore-shell-arcticons", "Arcticons + 사용자 선화"), ("system", "시스템 원본")),
    ),
    SettingSpec("theme.app_icon_aliases", "appearance", "앱 아이콘 별칭", "icon_aliases", "설치 앱과 Arcticons 이름 연결"),

    SettingSpec("layout.edge_margin", "layout", "화면 가장자리 여백", "integer", "px", minimum=0),
    SettingSpec("layout.panel_height", "layout", "접힌 패널 높이", "integer", "px", minimum=1),
    SettingSpec("layout.expansion_ms", "layout", "펼침 애니메이션", "integer", "ms", minimum=0),
    SettingSpec("layout.weather_width", "layout", "날씨 너비", "integer", "px", minimum=1),
    SettingSpec("layout.weather_height", "layout", "날씨 높이", "integer", "px", minimum=1),
    SettingSpec("layout.system_width", "layout", "성능 너비", "integer", "px", minimum=1),
    SettingSpec("layout.system_height", "layout", "성능 높이", "integer", "px", minimum=1),

    SettingSpec("launcher.fixed_apps", "launcher", "고정 앱", "app_list"),
    SettingSpec("taskbar.pinned", "launcher", "태스크바 고정 앱", "taskbar_apps"),
    SettingSpec("launcher.app_limit", "launcher", "검색 앱 개수", "integer", minimum=1),
    SettingSpec("launcher.window_limit", "launcher", "검색 창 개수", "integer", minimum=1),
    SettingSpec("launcher.clipboard_limit", "launcher", "클립보드 기록 개수", "integer", minimum=1),
    SettingSpec("launcher.clipboard_retention_hours", "launcher", "클립보드 보존 시간", "decimal", "시간 · Secret Service 암호화 저장", minimum=0.5, step=0.5, digits=1),
    SettingSpec("taskbar.visible_limit", "launcher", "태스크바 표시 개수", "integer", minimum=1),
    SettingSpec("launcher.file_debounce_ms", "launcher", "파일 검색 지연", "integer", "ms", minimum=0),
    SettingSpec("launcher.file_root", "launcher", "파일 검색 시작 경로", "string", "고급"),
    SettingSpec("launcher.web_url", "launcher", "웹 검색 URL", "string", "{query} 필요 · 고급"),
    SettingSpec("launcher.shell", "launcher", "명령 셸", "string", "고급"),
    SettingSpec("launcher.terminal", "launcher", "터미널 실행 파일", "string", "고급"),
    SettingSpec("launcher.preferred_actions", "launcher", "선호 앱 동작", "app_actions", "고급"),

    SettingSpec("notifications.history_limit", "notifications", "기록 보관 개수", "integer", minimum=0),
    SettingSpec("notifications.retention_hours", "notifications", "기록 보존 시간", "decimal", "시간 · 세션 메모리", minimum=0.5, step=0.5, digits=1),
    SettingSpec("notifications.toast_limit", "notifications", "동시 팝업 개수", "integer", minimum=1),
    SettingSpec("notifications.default_timeout_ms", "notifications", "기본 팝업 시간", "integer", "ms", minimum=0),
    SettingSpec("notifications.dot_limit", "notifications", "앱 점 표시 개수", "integer", minimum=0),
    SettingSpec("notifications.dnd_allowlist", "notifications", "방해 금지 허용 앱 이름", "string_list", "한 줄에 하나"),

    SettingSpec("location.automatic", "weather", "현재 위치 자동 사용", "boolean", "GeoClue 실패 시 아래 수동 위치 사용"),
    SettingSpec("location.latitude", "weather", "수동 위도", "decimal", minimum=-90, maximum=90, step=0.0001, digits=4),
    SettingSpec("location.longitude", "weather", "수동 경도", "decimal", minimum=-180, maximum=180, step=0.0001, digits=4),
    SettingSpec("location.timezone", "weather", "IANA 시간대", "string", "예: Asia/Seoul"),
    SettingSpec("weather.cache_hours", "weather", "캐시 유효 시간", "integer", "시간", minimum=0),
    SettingSpec("weather.timeout_seconds", "weather", "요청 제한 시간", "decimal", "초", minimum=0.1, step=0.1, digits=1),

    SettingSpec("metrics.sample_seconds", "metrics", "측정 주기", "decimal", "초", minimum=1, step=0.5, digits=1),
    SettingSpec("metrics.graph_seconds", "metrics", "그래프 구간", "integer", "초", minimum=1),
    SettingSpec("metrics.cpu.warning", "metrics", "CPU 경고", "decimal", "°C", minimum=0, step=1, digits=1),
    SettingSpec("metrics.cpu.danger", "metrics", "CPU 위험", "decimal", "°C", minimum=0, step=1, digits=1),
    SettingSpec("metrics.gpu.warning", "metrics", "GPU 경고", "decimal", "°C", minimum=0, step=1, digits=1),
    SettingSpec("metrics.gpu.danger", "metrics", "GPU 위험", "decimal", "°C", minimum=0, step=1, digits=1),
    SettingSpec("metrics.coolant.warning", "metrics", "냉각수 경고", "decimal", "°C", minimum=0, step=1, digits=1),
    SettingSpec("metrics.coolant.danger", "metrics", "냉각수 위험", "decimal", "°C", minimum=0, step=1, digits=1),
    SettingSpec("metrics.nvme_0700.warning", "metrics", "NVMe 07:00 경고", "decimal", "°C", minimum=0, step=1, digits=1),
    SettingSpec("metrics.nvme_0700.danger", "metrics", "NVMe 07:00 위험", "decimal", "°C", minimum=0, step=1, digits=1),
    SettingSpec("metrics.nvme_0100.warning", "metrics", "NVMe 01:00 경고", "decimal", "°C", minimum=0, step=1, digits=1),
    SettingSpec("metrics.nvme_0100.danger", "metrics", "NVMe 01:00 위험", "decimal", "°C", minimum=0, step=1, digits=1),
    SettingSpec("metrics.pump_warning_rpm", "metrics", "펌프 경고", "integer", "RPM 미만", minimum=0),
    SettingSpec("metrics.pump_danger_rpm", "metrics", "펌프 위험", "integer", "RPM 미만", minimum=0),
    SettingSpec("metrics.fan_warning_rpm", "metrics", "팬 위험", "integer", "RPM 미만", minimum=0),
    SettingSpec("metrics.fan_coolant_gate", "metrics", "팬 경고 냉각수 조건", "decimal", "°C", minimum=0, step=0.5, digits=1),

    SettingSpec(
        "theme.palette_source", "palette", "색상 source", "choice",
        choices=(
            ("hyprpaper", "Hyprpaper 추출값"),
            ("awww", "Awww 추출값"),
            ("native", "Luminophore 장면 추출값"),
            ("fixed", "고정색"),
        ),
    ),
    SettingSpec(
        "theme.fallback_primary", "palette", "기본 primary", "color",
        "추출값이 없거나 실패할 때 · 외곽선·발광·주 강조",
        visible=False,
    ),
    SettingSpec(
        "theme.fallback_secondary", "palette", "기본 secondary", "color",
        "추출값이 없거나 실패할 때 · hover·선택·보조 강조",
        visible=False,
    ),
    SettingSpec(
        "theme.fixed_primary", "palette", "고정 primary", "color",
        "monitor별 값이 없을 때 적용 · 외곽선·발광·주 강조",
        visible=False,
    ),
    SettingSpec(
        "theme.fixed_secondary", "palette", "고정 secondary", "color",
        "monitor별 값이 없을 때 적용 · hover·선택·보조 강조",
        visible=False,
    ),
    SettingSpec(
        "theme.fixed_monitor_palettes", "palette", "모니터별 고정색", "monitor_palette_map",
        "연결된 monitor와 저장된 connector마다 primary·secondary를 별도로 지정",
    ),

    SettingSpec("compositor.default_view_columns", "compositor", "기본 뷰 가로 칸", "integer", "새 보드와 자동 축소 하한에 적용 · 현재 편집한 뷰 유지", minimum=1, maximum=64),
    SettingSpec("compositor.default_view_rows", "compositor", "기본 뷰 세로 칸", "integer", "보드의 최대 크기가 아닙니다", minimum=1, maximum=64),
    SettingSpec("compositor.gaps_in", "compositor", "창 사이 간격", "integer", "px", minimum=0, maximum=100),
    SettingSpec("compositor.gaps_out", "compositor", "화면 가장자리 간격", "integer", "px", minimum=0, maximum=100),
    SettingSpec("compositor.border_size", "compositor", "창 테두리 굵기", "integer", "px", minimum=0, maximum=20),
    SettingSpec("compositor.rounding", "compositor", "창 모서리 반경", "integer", "px", minimum=0, maximum=100),
    SettingSpec("compositor.active_opacity", "compositor", "활성 창 불투명도", "decimal", minimum=0.1, maximum=1.0, step=0.05, digits=2),
    SettingSpec("compositor.inactive_opacity", "compositor", "비활성 창 불투명도", "decimal", "활성 창 이하", minimum=0.1, maximum=1.0, step=0.05, digits=2),
    SettingSpec("compositor.dim_special", "compositor", "특수 workspace 어둡게", "decimal", minimum=0.0, maximum=1.0, step=0.05, digits=2),
    SettingSpec("compositor.vrr", "compositor", "VRR", "choice", choices=((0, "끔"), (1, "전체 화면"), (2, "항상"), (3, "자동"))),
    SettingSpec('compositor.swallow_enabled', 'compositor', '터미널 창 삼키기', 'boolean'),
    SettingSpec('compositor.swallow_regex', 'compositor', '창 삼키기 대상', 'text'),
    SettingSpec('compositor.swallow_exception_regex', 'compositor', '창 삼키기 제외 제목', 'text'),
    SettingSpec('compositor.screen_shader', 'compositor', '화면 셰이더 경로', 'text'),
    SettingSpec('compositor.motion_blur_enabled', 'compositor', '창 모션 블러', 'boolean'),
    SettingSpec('compositor.motion_blur_samples', 'compositor', '모션 블러 샘플', 'integer', minimum=1, maximum=64),
    SettingSpec('compositor.snap_enabled', 'compositor', '플로팅 창 스냅', 'boolean'),
    SettingSpec('compositor.snap_window_gap', 'compositor', '창 스냅 거리', 'integer', minimum=0, maximum=100),
    SettingSpec('compositor.snap_monitor_gap', 'compositor', '화면 가장자리 스냅 거리', 'integer', minimum=0, maximum=100),
    SettingSpec('compositor.snap_border_overlap', 'compositor', '스냅 테두리 겹침', 'boolean'),
    SettingSpec('compositor.snap_respect_gaps', 'compositor', '스냅 시 창 간격 유지', 'boolean'),
    SettingSpec("motion.enabled", "motion", "애니메이션", "boolean"),
    SettingSpec("motion.preset", "motion", "모션 preset", "choice", choices=(("fast", "빠르게"), ("balanced", "균형"), ("smooth", "부드럽게"), ("custom", "사용자 지정"))),
    SettingSpec("motion.speed", "motion", "모션 속도 배율", "decimal", "0.1~10.0", minimum=0.1, maximum=10.0, step=0.1, digits=1),
    SettingSpec(
        "appearance.wallpaper_provider", "palette", "배경화면 provider", "choice",
        "색상 source와 독립적으로 배경화면 표시 backend를 선택",
        choices=(("hyprpaper", "Hyprpaper"), ("awww", "Awww")),
    ),
)


RETIRED_VISUAL_PATHS = {
    "theme.outline_glow_intensity", "theme.animate_glow", "theme.outline_glow_radius",
    "compositor.blur_enabled", "compositor.blur_size", "compositor.blur_passes",
}


EXCLUDED_PATHS = RETIRED_VISUAL_PATHS | {
    "input.kb_snapshot",  # Imported immutable payload, not a user-facing editor.
    "layout.max_height_ratio",
    # Operational rollout switch. Keep it out of the user-facing appearance
    # controls until the GPU renderer passes the visual acceptance gate.
    "theme.glow_renderer",
    "theme.audio_spectrum_enabled",
    "taskbar.hover_delay_ms",
    "weather.forecast_days",
    "location.geoclue_timeout_seconds",
    "location.city_search_timeout_seconds",
}


CUSTOM_SETTING_PATHS = {
    "system_theme.mode",
}


def setting_value(config: ShellConfig, path: str) -> Any:
    value: Any = config
    for part in path.split("."):
        value = getattr(value, part)
    if path == "launcher.preferred_actions":
        return dict(value)
    if path == "theme.fixed_monitor_palettes":
        return {
            connector: [primary, secondary]
            for connector, primary, secondary in value
        }
    if path == "theme.app_icon_aliases":
        return dict(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def settings_values(config: ShellConfig) -> dict[str, Any]:
    return {spec.path: setting_value(config, spec.path) for spec in SETTINGS}


def specs_for_category(category: str) -> tuple[SettingSpec, ...]:
    return tuple(spec for spec in SETTINGS if spec.category == category and spec.visible)


# UI styles expand to owned values; no second preset state can drift from TOML.
SHADOW_STYLES = {
    "기본": dict(shadow_range=4, shadow_power=3, shadow_sharp=False, shadow_scale=1.0, shadow_offset_x=0.0, shadow_offset_y=0.0),
    "부드럽게": dict(shadow_range=24, shadow_power=2, shadow_sharp=False, shadow_scale=1.0, shadow_offset_x=0.0, shadow_offset_y=4.0),
    "선명하게": dict(shadow_range=8, shadow_power=3, shadow_sharp=True, shadow_scale=1.0, shadow_offset_x=0.0, shadow_offset_y=2.0),
}


def shadow_style(values):
    for name, fields in SHADOW_STYLES.items():
        if all(values.get("compositor." + key) == value for key, value in fields.items()):
            return name
    return "사용자 지정"
