# 독립 모니터 보드

**한국어** · [English](SPATIAL_PROTOCOL.en.md)

LUMINOPHORE는 모니터 connector마다 세션 동안 안정적으로 유지되는 독립 보드 하나를
사용합니다. 좌표는 signed 64-bit integer이며, 기본 view는 viewport일 뿐 보드의
전체 용량을 의미하지 않습니다. snapshot에서 창은 기존 public window address와
해당 board의 조합으로 식별됩니다. connector identity와 return lease는 컴포지터
세션이 유지되는 동안 유효합니다.

## 조회와 편집 계약

`hyprctl -j luminophorespatialstate2`는 `protocolVersion: 2`를 반환합니다.
기존 `luminophorespatialstate` endpoint는 migration error와 함께 fail-closed로
종료됩니다. 일치하는 Shell과 컴포지터를 함께 배포해야 합니다. transport 자체는
여전히 JSON/string IPC이며, C++ command variant는 내부 type checking을 제공할
뿐 프로세스 간 typed transport를 제공하지 않습니다.

- `revision`은 atomic model commit generation이고, `topologyRevision`은 관찰된
  output topology입니다. 편집 전 `committedRevision`이 둘 모두와 일치해야 합니다.
- `outputViews[].board`는 connector output을 독립 board에 바인딩합니다.
  서로 다른 board의 view는 같은 좌표를 사용할 수 있습니다.
- `windows[].board`는 동일 좌표의 창을 구분합니다. 보이는 창은 하나의 output에서
  서로 겹치지 않는 여러 `fragments`를 가질 수 있습니다. `box`는 그 영역을
  통해 샘플링한 하나의 client bounding rectangle입니다.
- `focusRevision`은 focus ownership 변경을 구분합니다. `meshes[]`는 각 board의
  mesh revision과 `lastResetRevision`을 제공합니다. 0은 명시적인 reconcile
  reset이 없었다는 뜻입니다. mesh 변경도 model revision을 증가시킵니다.
- Lua request의 edit identity는 숫자 반올림을 피하기 위해 10진 문자열입니다.
  좌표 인자는 Lua integer이므로 정확한 signed 64-bit 값을 보존합니다.
- native grab layout은 grab generation과 acknowledged editor frame에 묶입니다.
  각 cell은 destination `output`을 지정할 수 있지만 frame 자체는 원래 editor
  surface에 유지됩니다. rebinding은 이전 cell geometry를 무효화합니다.
  오래된 generation은 drop을 commit할 수 없습니다. 점유된 위치로 drop하면
  swap만 수행합니다.

편집기는 전체 board를 할당하지 않고 제한된 viewport만 렌더링합니다. 화면 표시용
origin은 logical coordinate와 분리되어 있습니다. monitor tab은 board를 전환하고,
navigation control은 editor viewport를 이동합니다. 모니터별 palette는 선택된
board를 구분합니다. UI 크기로 좌표 범위를 추론하지 않습니다.

## 기본 view 설정

Lua option `misc.luminophore_default_view_columns`와
`misc.luminophore_default_view_rows`는 1–64를 허용하며 기본값은 2입니다.
Shell 설정은 `compositor.default_view_columns` /
`compositor.default_view_rows`입니다. 값을 바꾸면 이후 생성되는 board의 기본값과
자동 shrink의 하한이 갱신되지만, 이미 사용자가 편집한 view를 reset하지는 않습니다.
이 옵션들은 LUMINOPHORE 전용이며 upstream Hyprland option이 아닙니다.

## presentation 소유권

활성 runtime은 independent-board transaction을 적용한 뒤 mesh ownership,
projection, 하나의 native commit 순서로 처리합니다. 기본 model constructor는
항상 독립 board를 만듭니다. legacy finite-model construction은 baseline regression
test에서만 사용하는 명시적 `finiteFixture` factory가 필요하며, runtime caller는
이를 선택하거나 dual-write하지 않습니다. rendering과 hit testing은 animated
geometry를 포함한 `regionsFor`를 사용합니다. native popup surface는 자신의
geometry와 input region을 유지하고, client의 subsurface는 해당 clip을 상속합니다.
window capture는 capture-local coordinate를 사용합니다. 기존 window effect도
동일한 owned-region clip을 사용합니다.

WIDE는 full logical size가 동일하고 가장자리가 서로 맞닿은 output의 연결 집합을
사용합니다. workarea reservation이나 서로 다른 physical scale은 eligibility를
결정하지 않습니다. 다른 output은 일반 board를 유지합니다. 일반 fullscreen
suppression은 output 단위입니다. 통합 effect predicate는 native decoration/role
정보를 사용해 WIDE/fullscreen에서 window effect를 억제하며, Shell/OSD effect는
기존 경로의 소유권을 그대로 유지합니다.

resize는 native gesture를 위해 노출된 face를 캡처합니다. 해당 edge를 없애는
ownership 변경이 발생하면 gesture를 종료합니다. pure solver는 local slab을 따라
pressure를 전달하고, 원래 geometry가 허용하는 경우 100 logical pixel core를
유지합니다. client bounding-size constraint는 추가 제한으로 적용됩니다. concave
client constraint는 전체 bounding union을 기준으로 검사되며 이동을 보수적으로
clamp할 수 있습니다. 이것이 보이는 형태를 직사각형으로 강제하는 것은 아닙니다.

`benchmark-spatial-boards`는 75, 300, 1,000개 창에서 CPU의 candidate/transaction/
projection/prepare 비용을 측정합니다. client configure latency, GPU work, missed
frame, 실제 pointer-to-presentation latency는 측정하지 않습니다. 그런 항목은
실제 graphics session에서 검증해야 합니다.

## native drag target과 badge asset

`luminophorespatialgrab` schema 2는 `output`을 고정된 source identity로 유지하면서
`targetOutput`(physical monitor gap에서는 0)과 monotonic `targetEpoch`를
추가합니다. physical monitor 경계를 넘어도 grab generation과 animation은
유지됩니다. `spatial_drag_layout`에는 `target_epoch`가 포함되며, 오래된 epoch가
새 layout을 무효화할 수 없습니다. 일치하는 presented frame geometry가 있을 때만
drop할 수 있습니다. release 시 최종 physical target을 다시 검사하고 grab을
소비한 뒤 한 번만 commit합니다.

Shell은 icon을 한 번 resolve하고 GTK window나 snapshot 없이 decode한 뒤, 64×64
row-major alpha mask를 정확히 8192자의 lowercase hex로
`hl.dsp.luminophore.spatial_badge`에 전달합니다. identity field `generation`과
`target_epoch`는 정확한 10진 문자열입니다. `red/green/blue`는 0..1 범위의
finite theme primary component입니다. 빈 mask는 tint만 갱신하며, 형식이 잘못됐거나
오래된 request는 거부됩니다. 파일 경로, decoder, filesystem access는 컴포지터로
들어가지 않습니다.

native shader는 silhouette에 tint를 입히고 halo를 출력합니다. built-in generic
mask는 즉시 사용할 수 있습니다. icon readiness는 release를 막지 않으며 editor
frame readiness만 영향을 줍니다. native motion은 교차하는 output 사이의 위치를
제어하고 기존 shrink/crossfade curve를 유지하며 target 변경 시 tint를 transition
합니다. end/cancel은 texture를 지웁니다. 늦게 도착한 registration은 다른 grab에
붙을 수 없습니다. 일치하는 compositor/Shell 조합을 함께 설치해야 합니다.

## native 보조 창

board 참여 여부는 `checkBorders()`가 채운 X11 role 결과를 재사용합니다.
notification, tooltip, popup/menu, combo, splash, override-redirect surface는 이미
parent-attached 상태가 아닌 한 external overlay로 처리됩니다. 최초 native floating
경로도 같은 결과를 사용하며 기존 `wantsFocus()` 결정을 유지합니다. Steam
class/title blacklist나 중복 atom table을 새로 만들지 않습니다. 사용자의
`decorate=false`와 일반 floating 상태만으로 창을 auxiliary로 표시하지 않습니다.
auxiliary window는 board coordinate를 할당하지 않고 board growth를 유발하지도
않습니다. 기존 observation removal과 native rendering/input 경로가 최종 기준입니다.

## editor에서 시작하는 native window drag

`hl.dsp.luminophore.spatial_drag_begin`은 정확한 10진 문자열 필드
`window_id`, `expected_revision`, `topology_revision`, `output`,
`press_time`을 캡처합니다. 마지막 값은 원래 primary-button event timestamp
(uint32)이며 IPC timestamp가 아닙니다. native input은 동일한 press가 여전히
눌린 상태인지, 그리고 지정 output의 presented editor surface에서 시작됐는지를
검사합니다. 지연된 request는 이후 click을 가져갈 수 없습니다. dispatcher는
acceptance를 bool로 응답하며 이후 motion과 release는 GTK나 Shell IPC가 아니라
native input이 소유합니다. `spatial_drag_cancel({press_time="..."})`은 해당
editor-origin press만 취소합니다.

grab event에는 optional `editorOrigin`(기본 false)이 포함됩니다. editor-origin
grab은 persistent editor viewport를 보존하고 reveal animation을 다시 실행하지
않습니다. 종료되면 이전 persistent mode로 돌아갑니다. Shell은 기존
frame/target-epoch contract를 통해 geometry와 badge asset을 resolve합니다.
window 이동은 더 이상 Shell preview/commit worker를 사용하지 않으며 view handle은
그 worker를 계속 사용합니다.

tiled drag와 board-owned floating drag는 동일한 native lifetime과 pointer 기반
monitor selection을 공유합니다. floating motion은 release 전까지 시각적 상태로만
존재합니다. screen drop은 grab offset과 logical pixel size를 보존하고, grid drop은
floating host를 다른 위치로 옮기되 tiled window로 바꾸지 않습니다. 앱이 제어하는
auxiliary surface는 기존 native compatibility 경로를 유지합니다. 이 소스 변경에는
자동 restart나 deployment가 포함되지 않습니다.

### 안정화된 drag 종료

schema 2의 `end`/`cancel` event는 추가로 `settledRevision`,
`settledTopologyRevision`, `settledCommitted`를 보고합니다. 이 값들은 move
result, native input ownership cleanup, pointer monitor focus synchronization이
끝난 뒤의 runtime snapshot을 설명합니다. `revision`은 계속 원래 grab을
식별하고, `result`는 move transaction을 설명합니다. client는 둘 중 어느 것도
settled revision으로 덮어쓰면 안 됩니다. 세 필드는 구형 producer 호환을 위해
한 묶음으로 optional이며 nonterminal event에서는 유효하지 않습니다.

persistent editor는 종료 시 바로 refresh합니다. refresh 도중 새로 눌린 press는
변경되지 않은 유효 input target을 기다릴 수 있지만, release, cancellation,
다른 target으로의 remap 또는 connection reset 이후에는 재실행할 수 없습니다.
native press identity와 정확한 source revision 검사는 계속 필수입니다.
