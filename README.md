# Luminophore Desktop

**한국어** · [English](README.en.md)

Luminophore는 하나의 Linux 데스크톱 제품입니다. 컴포지터, 제어 클라이언트, 위젯
셸, 그리터, 세션 런처, 포털, 설정, 전용 런타임을 하나로 빌드하고 배포합니다.
Hyprland와 나란히 동작하는 자체 Wayland 데스크톱 세션을 갖습니다. 커스텀 커널,
EFI 렌더러와 부트로더 실험은 이 제품의 범위에 포함되지 않으며, 커널과 그래픽
드라이버는 호스트 OS가 제공합니다.

## 소스 구성

- `compositor/`: 컴포지터, 제어 도구, 네이티브 TOML 설정과 패키징 자산.
- `config/`: Shell, 그리터, 위젯 자산, 데스크톱 통합과 사용자 서비스.
- `Luminophore-OS/runtime/`: 전체 데스크톱 빌드, 패키징, 세대 관리와 세션 도구.
- `Luminophore-OS/update-guardian/`: 데스크톱 전용 호스트 업데이트 관찰과 후속 처리.

기존 빌드/테스트 경로를 유지하기 위해 `Luminophore-OS` 디렉터리 이름은 그대로
사용합니다. 이 저장소에는 커스텀 커널이나 EFI 구현이 포함되어 있지 않습니다.
업스트림 컴포지터 의존성은 고정된 Git 서브모듈로 관리합니다.

```sh
git clone --recurse-submodules https://github.com/msang710/Luminophore-Desktop.git
```

## 현재 기준선

최초 소스 반입은 2026-09-22의 독립 세션 안정화 상태를 기록합니다. 현재 배포된
패키지는 `luminophore-compositor 0.1.0rc6-8`입니다. 패키지 이름은 과거 명칭을
유지하고 있지만 전체 데스크톱 런타임을 포함합니다. 정확한 바이너리 digest는
[릴리스 메타데이터](releases/0.1.0rc6-8.json)를 확인하세요. 빌드/런타임 도구는
[runtime/README.md](Luminophore-OS/runtime/README.md)에 정리되어 있습니다.
패키징 입력은 `compositor/luminophore/packaging/arch/`에 있습니다.

관리자의 CachyOS 머신에서 부팅과 겹쳐 보이던 위젯 발광이 사라지는 것을
확인했습니다. 겹침의 원인은 시각 설정 값의 차이가 아니라 기존 NEON Shell과
Luminophore Shell이 동시에 실행된 것이었습니다. 기존 NEON 설치에서 이 데스크톱을
사용하려면 예전 Shell 서비스와 그 하위 bloom 프로세스를 먼저 중지해야 합니다.
현재 패키지는 이 레거시 정리를 자동으로 수행하지 않습니다. 하나의 Wayland
세션에서 두 Shell을 동시에 실행하지 마세요.

부팅 그리터는 Luminophore UI를 사용하며, 세션 잠금 해제는 현재 gtklock을
사용합니다. 두 화면의 외형은 아직 통일되지 않았습니다. 이 안정화 기준선이
일반적인 하드웨어 호환성이나 무인 업그레이드까지 보장하는 것은 아닙니다.
호스트 업데이트는 관찰하지만 차단하지 않습니다. 패키지를 설치하는 것만으로
디스플레이 매니저가 자동 전환되지는 않습니다.

## 검증

```sh
cd Luminophore-OS/runtime
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```

릴리스 패키지는 이 모노레포로 소스를 반입하기 전에 빌드되었고 그대로 공개되어
있습니다. 이번 반입이 새로운 재현 가능 빌드를 주장하는 것은 아닙니다. 로컬 백업,
세션 로그, 패키지 payload 트리와 커스텀 커널 소스는 제외되어 있습니다.

## 라이선스

저장소 루트의 LICENSE는 GPL-3.0입니다. Shell의 MIT 라이선스와 컴포지터의 업스트림
라이선스를 포함한 기존 컴포넌트 및 서드파티 라이선스/고지는 원래 디렉터리에
그대로 남아 있습니다. 적용 범위는 각 파일을 확인하세요.
