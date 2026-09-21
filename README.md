# Luminophore Desktop

**한국어** · [English](README.en.md)

Luminophore는 **Hyprland 계보의 컴포지터를 기반으로 발전한 독립형 Wayland 데스크톱 환경(DE)**입니다.
컴포지터만 꾸미는 rice가 아니라 Shell, 설정 앱, 그리터, 세션 런처, 포털과 전용 런타임을
하나의 데스크톱 제품으로 함께 개발하고 배포합니다.

시스템의 Hyprland 세션과 별도의 `Luminophore` 세션으로 설치되며, 독립된 설정 경로,
IPC, systemd unit, portal identity와 runtime generation을 사용합니다.

## 핵심 컨셉

### Spatial Desktop

Luminophore는 전통적인 타일링 workspace 대신 **모니터별 독립 Board와 View**를 사용합니다.
창은 유한한 2차원 보드의 셀에 놓이고, View는 그중 현재 화면에 보이는 영역을 뜻합니다.

- 모니터마다 독립된 Board
- 창 하나당 하나의 공간 위치
- View 이동과 편집을 통한 공간 탐색
- WIDE/DESKTOP 등 Luminophore 전용 창 모드
- 네이티브 drag, preview, collision 처리

세부 프로토콜은
[Spatial Protocol](compositor/luminophore/SPATIAL_PROTOCOL.md)을 참고하세요.

### 하나의 DE로 관리되는 구성 요소

Luminophore는 다음 구성 요소를 하나의 제품 경계 안에서 함께 관리합니다.

- Luminophore compositor
- Luminophore Shell과 위젯
- Settings
- Greeter와 session launcher
- xdg-desktop-portal backend
- wallpaper / visual effect runtime
- package / generation / rollback runtime

각 구성 요소를 따로 조합하는 것이 아니라, 서로 호환되는 한 세대로 묶어 배포하는 것을
기본 전제로 합니다.

## 기술 스택

| 영역 | 주요 기술 |
|---|---|
| Compositor | C++, Wayland, Hyprland fork lineage |
| Shell / Settings / Greeter | Python, GTK4, PyGObject, gtk4-layer-shell |
| Native visual / wallpaper helpers | C/C++, EGL, OpenGL ES 2.0, Wayland |
| Configuration | Lua, TOML |
| Desktop integration | systemd user services, UWSM, greetd, XDG portal |
| Build / release runtime | Python, Bubblewrap, content-addressed immutable generations |
| Packaging | Arch Linux / CachyOS `.pkg.tar.zst` |

커널과 GPU 드라이버는 Luminophore가 자체 제공하지 않고 호스트 Linux 배포판의 것을
사용합니다.

## 설치

현재 공개 릴리스는 **Arch Linux / CachyOS x86_64용 프리릴리스 패키지**입니다.

1. [GitHub Releases](https://github.com/msang710/Luminophore-Desktop/releases)에서
   최신 `luminophore-compositor-*.pkg.tar.zst`와 `SHA256SUMS`를 받습니다.
2. 다운로드 디렉터리에서 checksum을 확인합니다.

```sh
sha256sum -c SHA256SUMS
```

3. 패키지를 설치합니다.

```sh
sudo pacman -U ./luminophore-compositor-*.pkg.tar.zst
```

4. 로그아웃한 뒤 Wayland session 선택 화면에서 **Luminophore**를 선택합니다.

패키지는 시스템 Hyprland를 `provide`, `replace`, `conflict`하지 않습니다.
설치만으로 사용 중인 display manager를 변경하지도 않습니다.

> 현재 릴리스는 프리릴리스입니다. 일반 하드웨어 전체에 대한 호환성이나 무인 업그레이드를
> 보장하는 안정판으로 간주하지 마세요.

## 소스에서 받기

업스트림 컴포지터 의존성은 고정된 Git submodule로 관리합니다.

```sh
git clone --recurse-submodules https://github.com/msang710/Luminophore-Desktop.git
cd Luminophore-Desktop
```

전체 DE의 재현 가능한 빌드와 패키징 과정은
[runtime 문서](Luminophore-OS/runtime/README.md)를 참고하세요.

## 저장소 구조

- `compositor/`: 컴포지터, 제어 도구, 네이티브 설정, Luminophore 공간 모델과 패키징 자산
- `config/`: Shell, Settings, Greeter, 위젯, 배경화면/시각 효과와 데스크톱 통합
- `Luminophore-OS/runtime/`: 전체 DE 빌드, 패키징, immutable generation, session/rollback 도구
- `Luminophore-OS/update-guardian/`: 호스트 업데이트 관찰과 후속 검증 도구
- `releases/`: 공개 릴리스 메타데이터

`Luminophore-OS` 디렉터리 이름은 기존 빌드/테스트 경로 호환을 위해 유지합니다.
이 저장소에는 커스텀 커널이나 EFI 구현이 포함되어 있지 않습니다.

## 현재 상태

현재 기준 릴리스는 `v0.1.0-rc6.8`이며 독립 Wayland session으로 부팅되는 상태까지
확인했습니다.

현재 공개 기준에서 알려진 제한과 마이그레이션 주의사항은
[해당 릴리스 노트](https://github.com/msang710/Luminophore-Desktop/releases/tag/v0.1.0-rc6.8)에
기록합니다. README에는 특정 개발 머신에서 발생한 과거 장애 내역 대신 제품의 현재
구조와 사용 방법만 유지합니다.

## 검증

런타임 unit test:

```sh
cd Luminophore-OS/runtime
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```

소스 테스트와 빌드 성공은 실제 GPU, 입력, suspend/resume, monitor hotplug 등 모든
실기 세션 동작을 보증하지 않습니다. 물리 세션 검증은 별도의 acceptance 단계로 다룹니다.

## 라이선스

저장소 루트의 LICENSE는 GPL-3.0입니다. Shell의 MIT 라이선스와 컴포지터의 업스트림
라이선스를 포함한 기존 컴포넌트 및 서드파티 라이선스/고지는 각 원래 디렉터리에
유지됩니다.
