# Spatial preview measurement

[한국어](README.md) · **English**

`luminophore/scripts/benchmark-spatial-preview` compiles the pure model/projection/commit
preparation fixture with C++23 and `-O2`, then prints JSON Lines. It never opens a
compositor connection. `--baseline PATH` points to the pre-change `src/luminophore`
copy and runs the old uncached calculation on the same input trace. The current
run uses the actual native grab cache class.

Fixtures cover 9×5 / 15×5, 1 / 16 / 75 tiled windows (75 is omitted on 9×5),
0 / 10 / 100 floating windows, and 1 / 2 / 4 outputs: 45 valid configurations.
Each gets 1,000 same-cell samples and 1,000 changing-cell samples. The program
rejects invalid fixtures and reports calls plus p50/p95/p99 nanoseconds for model
snapshot, copy, transact, projection, prepare and total sample processing.
The snapshot here is the **pure model snapshot**, not Runtime's host/window
snapshot. Native host validation, IPC, serialization, GTK rendering, driver and
presentation costs are excluded. Cache-hit percentiles intentionally include
cheap samples; one measured miss is not a statistical estimate of miss latency.
Use a quiet machine and record compiler/CPU/load with comparisons.

For an independently authorized compositor/Shell session, set
`LUMINOPHORE_SPATIAL_PREVIEW_METRICS=1` in the process environments before launch.
This does not install/restart anything itself. Native grab end emits aggregate
counts, total/max nanoseconds and bounded log2 time buckets to the process log;
Shell cancellation/completion emits counts and p50/p95/p99 of its last 2,048
samples through Python logging. Data is timings/counters only, with no window
names or application content. The native snapshot metric includes full Runtime
snapshot construction; other read consumers can contribute to that counter.
There is no per-pointer log or file I/O. Leave the variable unset normally.

For actual frame pacing, record output refresh rate and identical window/output
fixtures, then correlate pointer input, preview presentation, presented intervals,
missed frames and main-thread CPU. Include same-cell jitter, A→B→A, rapid traversal,
collision/bounds, pause→release, cancellation, unmap and hotplug. Compare 60 Hz and
the device's higher refresh rate. CPU microbenchmarks and build/unit-test results
cannot certify presentation latency or establish a device-specific time budget.
