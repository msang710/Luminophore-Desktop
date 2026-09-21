#include "LuminophoreSpatialTransaction.hpp"
#include "LuminophoreSpatialProjection.hpp"
#include "LuminophoreSpatialCommitter.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <iostream>

static auto send(CLuminophoreSpatialModel& model, LuminophoreSpatialPayload payload) {
    return model.transact({model.revision(), std::move(payload)});
}
int main() {
    for (const uint64_t count : {75, 300, 1000}) {
        const int         columns = static_cast<int>(std::ceil(std::sqrt(count))), rows = static_cast<int>((count + columns - 1) / columns);
        CLuminophoreSpatialModel model;
        send(model, SSpatialTopologyCommand{{{10, "benchmark", 0, 0, 3840, 2160}}, 1, columns, rows});
        for (uint64_t key = 1; key <= count; ++key) {
            if (send(model, SObserveWindowCommand{.key = key, .mode = eLuminophoreWindowPlacementMode::TILED, .outputID = 10}).status != eLuminophoreSpatialTransactionStatus::APPLIED)
                return 2;
            send(model, SMoveWindowToCommand{key, {int64_t((key - 1) % columns), int64_t((key - 1) / columns)}, 10, 1});
        }
        send(model, SResizeOutputViewCommand{10, {{0, 0}, columns, rows}, 1});
        std::vector<double> samples;
        size_t              fragments = 0;
        for (int i = 0; i < 200; ++i) {
            const auto start     = std::chrono::steady_clock::now();
            auto       candidate = model;
            const auto result    = send(candidate, SMoveWindowToCommand{count, {i % columns, (i / columns) % rows}, 10, 1});
            const auto plan      = CLuminophoreSpatialProjection::plan(result.snapshot, 1, {{10, "benchmark", {0, 0, 3840, 2160}}});
            if (!plan || !CLuminophoreSpatialCommitter::prepare(*plan))
                return 3;
            samples.push_back(std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count());
            fragments = 0;
            for (const auto& w : plan->windows)
                fragments += w.fragments.size();
        }
        std::ranges::sort(samples);
        std::cout << "windows=" << count << " view=" << columns << "x" << rows << " fragments=" << fragments << " samples=" << samples.size() << " p50_ms=" << samples[100]
                  << " p95_ms=" << samples[190] << " p99_ms=" << samples[198] << " max_ms=" << samples.back() << std::endl;
    }
}
