# 네이티브 장면 배경화면

**한국어** · [English / original](BACKGROUND.en.md)

설정 앱의 **배경화면 → 배경화면 제작**에서 왼쪽·오른쪽 이미지를 하나의 장면으로 만든다. 파일을 고르는 것만으로 현재 배경이 바뀌지는 않는다.

1. Source에서 원본과 cover/contain, 중심점을 정한다.
2. Layers에서 전경 포함·배경 제외·연결 영역 채우기·지우기로 마스크를 지정한다. 배경 외 최대 7개 레이어를 추가할 수 있다.
3. Motion에서 레이어 깊이와 장면 움직임을 정한다.
4. Preview에서 실제 native 셰이더로 생성한 움직임을 확인하고 각 화면 프로필을 저장한다.
5. 양쪽 장면을 등록한다. `SUPER+ALT+W` 선택기에서 적용한다. 선택기에서 이름·순서·프로필을 다시 편집할 수 있다.

선택기의 방향키·휠·화살표·옆 카드는 장면을 탐색하고 Enter/적용/중앙 카드 클릭은 전환을 요청한다. Escape와 바깥 클릭은 닫는다. 1차 디자인은 세 개의 고정 슬롯, 중앙 pair 카드, 모니터 팔레트와 기존 Shell 발광·motion 경로를 사용한다. 간격·크기·속도는 실제 화면을 본 뒤 조정한다.

## 빌드와 검증

Native 의존성은 C11, wayland-client/wayland-egl, EGL/GLES2, libpng, libjpeg, json-c, wayland-scanner와 wayland-protocols다. 기존 GTK4/gtk4-layer-shell Shell 의존성도 필요하다.

OpenCV는 오프라인 프로필 제작 worker에만 필요하다. `requirements-background.txt`는 검증한 OpenCV 버전을 고정한다. 배경을 재생하는 native 프로세스는 Python/OpenCV를 로드하지 않는다. AI 모델이나 모델 다운로드, Cubism SDK는 사용하지 않는다.

```sh
python3 -m venv --system-site-packages /path/to/profile-runtime
/path/to/profile-runtime/bin/python -m pip install -r requirements-background.txt
LUMINOPHORE_PROFILE_PYTHON=/path/to/profile-runtime/bin/python scripts/verify-background-scene /path/to/new-candidate
```

검증 스크립트는 Shell 소스와 native 실행 파일을 같은 후보에 복사하고 전체 테스트와 offscreen EGL 픽셀 검증을 실행한다. 후보 `manifest.json`은 모든 파일 hash와 renderer build ID를 포함한다. 기존 실행 환경은 교체하지 않는다.

후보 설정 UI를 확인할 때는 해당 후보 `shell/luminophore-shell settings --page wallpaper`를 실행하고 `LUMINOPHORE_PROFILE_PYTHON`을 준비한 제작 환경에 지정한다. 실제 적용은 대응 Shell daemon과 native binary를 함께 활성화한 뒤에만 한다. 설치·서비스 재시작·세션 재시작은 이 검증 스크립트가 수행하지 않는다.

## 계약과 제한

- Scene/Profile/Package/IPC schema 1. package compiler identity는 `opencv-<version>/luminophore-profile-2`. 원본 hash, draft와 compiler identity로 생성물을 구분한다.
- PNG/JPEG 원본, 제작 입력 800만 픽셀, native 정적 입력 3,200만 픽셀. EXIF 방향은 프로필 제작에서 정규화한다. 직접 정적 scene의 JPEG는 원본 픽셀 방향을 사용한다.
- 최대 8개 RGBA 레이어, 프로필당 디코딩 texture 128 MiB. 두 출력의 current+next texture 최대 512 MiB, 합성 target 최대 256 MiB. GL 한도도 준비 단계에서 검사한다.
- 16×16 grid와 최대 0.05 움직임. 레이어 순서는 아래→위다. 기본 배경 depth는 0으로 고정한다.
- 마스크 레이어당 최대 10,000개 stroke. worker CPU 90초, 주소 공간 4 GiB, 컴파일 응답 제한 100초, 미리보기 생성 제한 30초. native 미리보기는 640×360, 최대 48 frame/12fps의 반복 견본이며 실제 desktop frame pacing의 측정값이 아니다.
- 프로필 생성물 저장량이 2 GiB를 초과하면 추가 컴파일을 거부한다. 저장된 원본/패키지를 자동 삭제하지 않는다. 임시 compile/preview는 해당 요청 소유 파일만 정리한다.
- thumbnail cache 최대 24개(320×200), worker 1개, pending asset 최대 12개. 드래그 자동 저장은 500ms debounce, 소스 preview decode는 별도 worker다.
- scene 목록 최대 1,000개, 작업 이력 64개, pending 최신 요청 1개. PREPARE/READY 후 공통 monotonic 시작 시간을 사용하고, 두 출력의 최종 presentation feedback 이후만 현재 scene과 palette를 갱신한다. 서로 다른 vblank 자체를 동기화하지는 않는다.
- 초기 역할 바인딩은 서로 다른 x 위치의 정확히 두 출력만 지원한다. 미지원 배치는 명시적으로 거부한다.

## 소유권과 복구

`BackgroundSceneController`가 유일한 적용 소유자다. Settings는 초안과 저장된 scene을 편집하고 IPC는 scene intent를 전달한다. scene 목록은 `$XDG_CONFIG_HOME/luminophore-shell/wallpaper-scenes.json`, 프로필과 last-good는 `$XDG_STATE_HOME/luminophore-shell` 아래에 저장한다.

준비 실패는 기존 native 화면을 유지한다. commit 이후 프로세스 소실·timeout·topology 변경은 last-good snapshot으로 재준비한다. 세 번 실패하면 native 단색 복구 화면과 degraded 상태를 사용하며 외부 provider를 자동 실행하지 않는다. 컴파일된 last-good는 원본이 없어져도 보관한 레이어로 복구할 수 있다. 새 적용은 변경/소실된 원본을 거부한다.

최초 명시적 적용의 두 출력 presentation 이후에만 기존 같은 사용자 소유의 hyprpaper/awww-daemon을 종료한다. native state가 존재하는 다음 시작에서는 autostart가 외부 provider를 시작하지 않는다. 최초 native 활성화 전에는 기존 autostart 동작을 유지한다. 외부 systemd provider 서비스를 따로 등록했다면 배포 시 그 재시작 정책도 제거해야 한다.

수동 개발 rollback은 Shell을 정지한 뒤 last-good state를 **삭제하지 않고 별도 보관**하고 기존 Shell/provider를 복구하는 별도 운영 절차다. 제품 runtime의 fallback과 다르다. 원본과 scene library는 그대로 유지한다. Greeter/login 경로는 변경하지 않는다.

## 실제 세션에서 추가 검증 필요

실제 Wayland의 dual presentation, hotplug/kill 복구, 모니터별 scale/crop/이음새, 검은 frame 유무, 키보드/바깥 클릭/input-empty, 60/144Hz frame pacing, 사용자 원본의 마스크·채움 품질과 디자인 수용은 실기 검증 대상이다. Offscreen EGL와 fixture 통과로 이 항목을 완료 처리하지 않는다.
