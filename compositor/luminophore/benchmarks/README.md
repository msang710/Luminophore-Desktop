# 공간 프리뷰 측정

**한국어** · [English](README.en.md)

`luminophore/scripts/benchmark-spatial-preview`는 순수 model/projection/commit
준비 fixture를 C++23과 `-O2`로 컴파일한 뒤 JSON Lines를 출력합니다.
컴포지터 연결은 열지 않습니다. `--baseline PATH`는 변경 전
`src/luminophore` 복사본을 가리키며 동일한 input trace에 대해 기존의 cache 없는
계산을 실행합니다. 현재 실행은 실제 native grab cache class를 사용합니다.

fixture는 9×5 / 15×5, tiled window 1 / 16 / 75개(9×5에서는 75개 제외),
floating window 0 / 10 / 100개, output 1 / 2 / 4개를 조합한 45개의 유효 구성을
다룹니다. 각 구성마다 same-cell sample 1,000개와 changing-cell sample 1,000개를
실행합니다. 프로그램은 잘못된 fixture를 거부하고 model snapshot, copy,
transaction, projection, prepare, 전체 sample processing에 대해 호출 수와
p50/p95/p99 나노초를 보고합니다. 여기서 snapshot은 **순수 model snapshot**이며
Runtime의 host/window snapshot이 아닙니다. native host validation, IPC,
serialization, GTK rendering, driver, presentation 비용은 제외됩니다.
cache-hit percentile에는 의도적으로 저렴한 sample이 포함됩니다. 한 번 측정된
cache miss를 miss latency의 통계적 추정치로 사용하면 안 됩니다. 비교 시에는
조용한 머신을 사용하고 compiler/CPU/load를 함께 기록하세요.

독립적으로 승인된 compositor/Shell 세션에서는 실행 전에 두 프로세스 환경에
`LUMINOPHORE_SPATIAL_PREVIEW_METRICS=1`을 지정할 수 있습니다. 이 설정 자체는
설치나 재시작을 수행하지 않습니다. native grab 종료 시 process log에 aggregate
count, total/max nanoseconds, 제한된 log2 time bucket을 기록합니다. Shell의
cancel/completion은 Python logging을 통해 최근 2,048개 sample의 count와
p50/p95/p99를 기록합니다. 데이터는 timing/counter만 포함하며 window name이나
application content는 포함하지 않습니다. native snapshot metric에는 전체 Runtime
snapshot construction이 포함되고, 다른 read consumer도 그 counter에 기여할 수
있습니다. pointer별 log나 file I/O는 없습니다. 평소에는 이 변수를 설정하지 마세요.

실제 frame pacing을 측정하려면 output refresh rate와 동일한 window/output fixture를
기록한 뒤 pointer input, preview presentation, presented interval, missed frame,
main-thread CPU를 상호 연관해 분석하세요. same-cell jitter, A→B→A, 빠른 이동,
collision/bounds, pause→release, cancellation, unmap, hotplug을 포함해야 합니다.
60 Hz와 장치의 더 높은 refresh rate를 비교하세요. CPU microbenchmark와
build/unit-test 결과만으로 presentation latency를 인증하거나 장치별 time budget을
정할 수는 없습니다.
