# Luminophore Desktop — 컴포지터

**한국어** · [English](README.en.md)

이 디렉터리는 고정된 LUMINOPHORE Hyprland 포크의 재현성 메타데이터를 관리합니다.
의도적으로 세션을 설치하거나 `/usr/bin/Hyprland`를 변경하지 않습니다.

## 소스 검증

```sh
./luminophore/scripts/verify-source
```

## 격리된 카나리 아티팩트 빌드

```sh
./luminophore/scripts/build-canary
```

카나리 빌드는 컴포지터가 소유하는 LUMINOPHORE 효과를 기본으로 활성화합니다.
`LUMINOPHORE_EFFECTS=0`을 지정하면 같은 소스에서 효과 통합을 컴파일 단계에서
제외한 A/B 비교용 아티팩트를 만들 수 있습니다. 어느 모드도 실행 중인 세션을
활성화하지 않습니다.

빌드 결과는 `luminophore/artifacts/<binary-sha256>/`에 생성됩니다. 실행 파일은
`bin/luminophore-hyprland-canary`이며, `--version`은 LUMINOPHORE 빌드 식별자
뒤에 포함된 업스트림 버전을 출력합니다. 이후 별도로 승인된 배포 단계에서
명시적으로 선택한 격리 세션에서만 실행하세요.

래퍼는 카나리 세션에 `LUMINOPHORE_COMPOSITOR=1`을 export합니다. 공간 데스크톱
단축키는 `SUPER+D`를 floating, `SUPER+F`를 fullscreen, `SUPER+G`를 WIDE,
`SUPER+X`를 DESKTOP, `SUPER+SPACE`를 상시 편집기에 사용합니다. 편집기는
네이티브 SUPER 드래그와도 공유됩니다. 기존의 상단 드롭, 가장자리 half-clear,
런처 드롭 최소화, 번호 기반 workspace 어댑터는 제거됐으며, 흔들기 isolate와
개별 restore는 독립 동작으로 남아 있습니다.

## 의미 기반 시각 설정

Settings의 외형 페이지는 `[visual]` schema version, `balanced` preset,
enabled, breathing, intensity(0–3)만 저장합니다. blur kernel, bloom 범위, alpha
curve, damage margin은 컴포지터가 소유합니다. 설정이 없으면 balanced 기본값을
사용하고, 형식이 잘못됐거나 오래된 bundle은 전체 단위로 복구합니다. 불러오기는
기존 TOML을 다시 쓰지 않습니다. 저장하면 완전한 semantic table을 만들고 관련
없는 텍스트는 보존합니다. 이 table이 존재하면 기존 glow 필드는 파생된 호환
값으로 취급되며 이를 덮어쓸 수 없습니다. 기존 컴포지터 blur 설정은 일반
비-LUMINOPHORE 렌더 패스에서 계속 사용할 수 있습니다.

`hl.dsp.luminophore.visual_state()`는 정규화된 설정, 복구 사유, revision과
renderer/receipt protocol capability를 반환합니다. `rendererSchema=1`은 효과가
컴파일됐다는 뜻일 뿐 GPU presentation을 증명하지 않습니다. Settings UI는
renderer와 receipt capability가 모두 있어야 실시간 적용을 허용합니다.

`hl.dsp.luminophore.visual_settings(settings, source, token, serial)`은 다섯
semantic field와 별도의 receipt metadata를 캡처합니다. source는 owner 수명 동안
유지되는 identity이고, serial은 정확한 10진수 uint64 문자열이며, token은 길이가
제한된 ASCII 식별자입니다. 실행 시 source와 기대 serial이 모두 일치해야 합니다.
bundle을 보존하는 작업을 포함해 성공한 모든 apply는 serial을 증가시키고,
revision은 bundle이 바뀔 때만 증가합니다. 인자 하나짜리 형식은 설정 호출자를
위한 whole-bundle 복구를 유지하지만 serial은 동일하게 증가시킵니다.

Shell은 한 번의 mutation 뒤 자신의 정확한 receipt를 검증합니다. acknowledgment를
잃으면 확인 가능한 pending request가 남고 두 Settings frontend의 새 쓰기를
차단합니다. 상태 확인은 이를 다시 실행하지 않습니다. 명시적 복구는 현재 관찰된
bundle을 조건부로 다시 써서 queued request를 fence할 수 있으며, 화면에 보이는
설정은 그대로 보존합니다. 어느 요청이 먼저 성공하든 serial을 증가시키고 다른
요청은 거부됩니다. 아직 시작되지 않은 Shell request ID는 지연된 IPC 작업이
시작되기 전에 폐기할 수도 있습니다. 재연결은 오래된 editor gesture를 취소합니다.
같은 visual owner는 read-only가 되고, 새 owner는 저장된 bundle을 받습니다.
실제 세션, 입력, GPU acceptance는 소스 테스트 및 빌드와 별도로 검증해야 합니다.

## 독립 공간 보드

모니터 보드 런타임과 Shell은 [공간 프로토콜 버전 2](SPATIAL_PROTOCOL.md)를
사용합니다. 일치하는 Shell이 반드시 필요하며, 기존 state endpoint는 더 이상
편집 가능한 snapshot을 제공하지 않습니다.
