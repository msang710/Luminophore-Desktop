# Luminophore Desktop

Hyprland용 개인 rice와 GTK4 기반 Luminophore Shell입니다. 배경화면을 `matugen`으로 분석해 위젯 팔레트를 만들고, 별도 적용 버튼으로 GTK3/4, Qt5/6, KDE, Kitty, Alacritty, Btop, Ghostty, Hyprland와 Bibata 커서를 동기화합니다.

## 주요 기능

- Hyprpaper, Awww, Linux Wallpaper Engine 배경화면 소스 지원
- Matugen v4 semantic dark/light 팔레트
- 위젯 팔레트 적용과 시스템 테마 적용 버튼 분리
- 다중 모니터 팔레트 및 전환 애니메이션
- 앱·창·이모지 런처, 암호화 클립보드, 알림, 날씨, 시스템 모니터링
- 포커스 모니터별 Hyprpaper/Awww 배경 적용과 Matugen 팔레트 자동 갱신
- 시스템 테마 적용 전 미리보기, 백업, drift 감지, 롤백
- Matugen semantic palette로 Bibata 형상을 재색상하는 Hyprcursor/XCursor 동시 생성과 원자적 롤백

## 요구 사항

Arch Linux 패키지 이름을 기준으로 한 필수 의존성입니다.

```sh
sudo pacman -S python python-gobject python-opengl gtk4 gtk4-layer-shell python-pillow \
  python-requests python-dbus matugen hyprland greetd hyprpaper wl-clipboard uwsm
```

데스크톱 배경화면 공급자는 사용하는 항목을 설치합니다. 로그인 스택은 데스크톱 공급자와 무관하게 정적 monitor 배경을 표시하기 위해 `hyprpaper`를 필수로 사용합니다.

- `hyprpaper`
- `awww`
- `linux-wallpaperengine`

시스템 테마 동기화 대상은 선택 의존성입니다.

- GTK: `gsettings`, `adw-gtk3`
- Qt: `qt6ct`, `qt5ct`
- KDE: `plasma-apply-colorscheme`
- 앱: `kitty`, `alacritty`, `btop`
- 커서: `hyprcursor`, `librsvg`, `xorg-xcursorgen`

그 밖에 아이콘 생성에는 `librsvg`, 색상 선택에는 `hyprpicker`, 알림 소리에는 `libcanberra`, 파일 검색에는 `fd`가 사용됩니다. NVIDIA GPU 지표는 `nvidia-smi`가 있을 때만 표시됩니다.

독립 데스크톱 제어에는 `playerctl`, `ddcutil`, `networkmanager`, `bluez`, `tuned-cachy`, `geoclue`, `libsecret`, `polkit-gnome`이 사용됩니다. Ghostty는 이 설치의 `~/.config/ghostty/config.ghostty`에서 `theme = matugen`을 선택합니다.

전원 프로필은 상속 없는 `power-profiles/luminophore-*-capped`만 노출하며 firmware가 소유한 CPU boost와 최소 성능 비율을 변경하지 않습니다. 설치기는 `power-profiles-daemon`의 D-Bus 재활성화까지 막도록 unit을 mask하고, TuneD 시작 전에 Luminophore balanced preset을 기록해 upstream 기본 프로필도 적용되지 않게 합니다. 롤백은 설치 전 mask/enable/active 상태를 복원합니다. 실제 backend 전환 전에 다음 비파괴 검사를 실행합니다.

```sh
./scripts/check-power-backend
```

`tuned-cachy` 설치, system service 소유권 전환과 `/etc/tuned/profiles` 쓰기는 `install-power-backend --apply`를 관리자 권한으로 명시적으로 실행할 때만 발생합니다.

기존 12개 테마 대상의 이전 상태는 `luminophore_shell/templates/matugen/coverage.json`에 고정되어 있습니다. 각 항목은 Matugen 공통/전용 대상으로 연결되거나 지원되는 안전한 주입 API가 없는 경우 사유와 함께 `intentional-drop`으로 표시됩니다.

Google Calendar를 연결하려면 Google Cloud에서 Calendar API와 OAuth 동의 화면을 활성화하고 Desktop app OAuth client JSON을 내려받아 다음 위치에 권한 `0600`으로 둡니다.

```sh
install -Dm600 ~/Downloads/client_secret_*.json ~/.config/luminophore-shell/google-oauth-client.json
```

연결 버튼을 누르기 전에는 브라우저나 Google API가 호출되지 않습니다. refresh token과 일정 캐시는 일반 파일 대신 Secret Service에 저장됩니다.

## 설치

이 rice는 `~/.config/hypr/config` 배치를 기준으로 합니다.

```sh
git clone https://github.com/msang710/myrice.git ~/.config/hypr/config
cd ~/.config/hypr/config
./scripts/deploy-luminophore-shell
```

`hyprland.lua`에서 `config` 디렉터리의 Lua 구성을 불러오고 있는지 확인하세요. 글꼴, 터미널, 고정 앱, 날씨 위치와 모니터별 팔레트는 `luminophore_shell/config.toml`에서 환경에 맞게 수정할 수 있습니다.

서비스 상태와 설정은 다음 명령으로 확인합니다.

```sh
systemctl --user status luminophore-shell.service
./luminophore-shell validate-config
./luminophore-shell ctl status
```

제거할 때는 다음 스크립트로 사용자 systemd unit을 해제합니다. 설정과 상태 데이터는 자동 삭제하지 않습니다.

```sh
./scripts/rollback-luminophore-shell
```

## 팔레트 사용

1. 설정에서 Hyprpaper, Awww 또는 Linux Wallpaper Engine을 팔레트 소스로 선택합니다.
2. 위젯 팔레트 적용 버튼으로 Luminophore Shell의 색상을 먼저 확인합니다.
3. 시스템 테마 적용 버튼으로 지원 앱에 같은 Matugen generation을 적용합니다.
4. 문제가 있으면 시스템 테마 롤백을 실행합니다.

Matugen 상태 파일은 v2 형식만 지원합니다. 이전 v1 상태가 있으면 새 팔레트를 추출해야 합니다.

## 로그인 테마 스테이징

현재 Matugen scheme과 활성 Hyprpaper/Awww 배경에서 first-party Luminophore GTK4 로그인 테마를 사용자 state에 생성합니다. 화면에는 비밀번호 입력창과 재부팅·종료·firmware-setup 아이콘만 표시되며 계정과 세션 선택기는 없습니다.

```sh
./scripts/stage-session-stack
```

명령이 출력한 stage는 원본 배경 경로와 로그인 계정을 포함하지 않고 monitor별 PNG copy, portable Greeter Python package, supervisor, 전용 Hyprland/Hyprpaper config와 checksum manifest만 포함합니다. 다음 검사는 stage만 검증하며 `/etc/greetd`를 변경하지 않습니다.

```sh
./scripts/install-session-stack --staged <출력된-stage-path> --login-user <local-user> --root / --check
```

`--apply`는 `/etc/luminophore-shell`, `/usr/lib/luminophore-shell`과 Luminophore-owned `/etc/greetd/greetd.conf`를 하나의 rollback manifest로 설치합니다. 실제 적용, GUI preview, greetd 전환과 재부팅은 로그인 복구 경로를 준비한 뒤 각각 별도 rollout으로 실행해야 합니다.

Plymouth 마지막 프레임 유지도 Greeter 설치와 독립된 transaction입니다. 현재 vendor unit 계약만 확인하려면 다음 명령을 사용합니다.

```sh
./scripts/install-login-handoff --root / --check
```

`--retain-splash` drop-in은 독립 rollback을 가진 transaction으로 적용·read-back까지 완료했습니다. Lua blur/portal 보정이 포함된 실제 cold-boot 시각·journal 검증은 별도 reboot gate로 남아 있습니다.

### Boot health first-frame signal

The daemon arms the launcher layer surface after calling `present()`. It writes
`$XDG_RUNTIME_DIR/luminophore-shell/first-frame-ready.json` only after GDK reports a
completed frame timing with a non-zero presentation time, which is the public
GTK/GDK indication that the frame became visible. The private atomic signal is
bound to the current boot ID, kernel release, Shell process ID, and monotonic
timestamp. Update Guardian compares the same process identity across its
stability window; a Shell restart therefore cannot reuse an earlier signal as
healthy evidence.

## 개발 및 검증

```sh
cd ~/.config/hypr/config
python -m unittest discover -s tests
python -m compileall -q luminophore_shell tests
./luminophore-shell validate-config
```

## 라이선스

[MIT](LICENSE)

### App initial placement

In Luminophore Settings → compositor, select a running app (or enter its exact
Wayland app ID / XWayland class) and choose a view-relative direction. Save and
apply affects newly mapped regular Board windows only. Choosing the default
removes the app override. Floating windows, dialogs and PiP retain their existing
placement behavior.

Causal placement takes precedence, putting a window to the right of its source.
App rules apply only to confirmed direct launches through the Shell or the
packaged app launch shortcuts (`luminophore-shell launch -- <argv>`).
Unknown origins use the existing fallback even when an app rule exists.
A one-shot compositor token, expiring after 30 seconds, is passed using the UWSM
unit Environment property and consumed by the first mapped window. Missing or
unreadable process metadata falls back; it is not inferred from timing or PID.
Custom shortcuts can use the same launch command. Ordinary exec/autostart does
not claim a direct user launch.
The reference is the cursor monitor's current view when the new window appears.
The boundary's middle cell determines the perpendicular alignment; an occupied
cell pushes its chain in the requested direction. WIDE keeps its existing view
preservation policy. No coordinates or permanent reservations are configured.

The generated `luminophore_placements.lua` is a single atomic settings file.
Configuration errors leave it saved with a pending-application message; the
settings application does not retry an ambiguous reload or restart the session.

For hand-written native window rules, the static effect is
`initial_placement = "right"` (`left`, `up`, `down`, `default` are also accepted).
It never repositions an already registered window. This is a Luminophore
extension and needs separate upstream wiki documentation if proposed upstream.

## 창 그룹 호환 경로 제거

Luminophore의 공간 모델은 각 창을 개별 조작 대상으로 관리합니다. Hyprland의 창 그룹과 탭 막대는 지원하지 않습니다.

- `group:*`, `general:col.nogroup_border*`, `binds:ignore_group_lock`, `binds:movefocus_cycles_groupfirst` 설정은 제거되었습니다. 예전 설정을 옮길 때 해당 항목을 삭제하세요.
- `hl.dsp.group`, 그룹 합치기·분리·잠금 명령, `group` 창 규칙은 거부됩니다. 그룹 명령을 일반 창 이동으로 자동 치환하지 않습니다.
- 창 목록에서 `grouped` 속성과 Lua 그룹 객체는 제거되었습니다.
- 앱 묶음 실행, 묶음 Undo, 단축키 누름/해제 묶음은 별도 기능이며 유지됩니다. 앱 내부의 브라우저·편집기 탭에도 영향이 없습니다.
