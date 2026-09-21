// Standalone CPU microbenchmark. Does not connect to a compositor/session.
#include "LuminophoreSpatialModel.hpp"
#include "LuminophoreSpatialTransaction.hpp"
#include "LuminophoreSpatialProjection.hpp"
#include "LuminophoreSpatialCommitter.hpp"
#ifdef LUMINOPHORE_PREVIEW_CACHE
#include "LuminophoreSpatialPreview.hpp"
#endif
#include <algorithm>
#include <array>
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <vector>

using Clock = std::chrono::steady_clock;
static uint64_t elapsed(Clock::time_point start) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now() - start).count();
}
static uint64_t percentile(std::vector<uint64_t> values, double p) {
    if (values.empty())
        return 0;
    std::ranges::sort(values);
    return values[static_cast<size_t>((values.size() - 1) * p)];
}
int main() {
    for (const int columns : {9, 15})
        for (const int tiled : {1, 16, 75})
            for (const int floating : {0, 10, 100})
                for (const int outputCount : {1, 2, 4}) {
                    if (tiled > columns * 5)
                        continue;
#ifdef LUMINOPHORE_PREVIEW_CACHE
                    auto model = CLuminophoreSpatialModel::finiteFixture({.columns = columns, .rows = 5});
#else
                    CLuminophoreSpatialModel model({.columns = columns, .rows = 5});
#endif
                    for (int i = 0; i < tiled; ++i)
                        if (!model.addTiled(i + 1, SLuminophoreBoardPoint{i % columns, i / columns}))
                            throw std::runtime_error("invalid tiled fixture");
                    for (int i = 0; i < floating; ++i)
                        if (!model.attachFloating(1000 + i, {i % columns, i % 5}))
                            throw std::runtime_error("invalid floating fixture");
                    std::vector<SLuminophoreOutputView>     views;
                    std::vector<SLuminophorePhysicalOutput> outputs;
                    for (int i = 0; i < outputCount; ++i) {
                        views.push_back({.outputID = static_cast<uint64_t>(i + 10), .rect = {.origin = {i * 2, 0}, .columns = 2, .rows = 2}});
                        outputs.push_back({.id = static_cast<uint64_t>(i + 10), .box = {.x = i * 1920, .width = 1920, .height = 1080}});
                    }
                    if (!model.configureOutputViews(views, 1))
                        throw std::runtime_error("invalid output fixture");
                    for (const bool jitter : {true, false}) {
                        std::array<std::vector<uint64_t>, 6> samples;
                        uint64_t                             calls = 0, accepted = 0;
#ifdef LUMINOPHORE_PREVIEW_CACHE
                        CLuminophoreSpatialPreviewCache cache;
#endif
                        for (int i = 0; i < 1000; ++i) {
                            const auto total    = Clock::now();
                            auto       start    = Clock::now();
                            const auto snapshot = model.snapshot();
                            samples[0].push_back(elapsed(start));
                            const SLuminophoreBoardPoint point{jitter ? 1 : (i % columns), jitter ? 1 : ((i / columns) % 5)};
#ifdef LUMINOPHORE_PREVIEW_CACHE
                            const SLuminophoreSpatialPreviewKey key{.generation = 1, .revision = snapshot.revision, .topologyRevision = 1, .window = 1, .outputID = 10, .point = point};
                            if (cache.matches(key)) {
                                samples[5].push_back(elapsed(total));
                                continue;
                            }
#endif
                            ++calls;
                            start          = Clock::now();
                            auto candidate = model;
                            samples[1].push_back(elapsed(start));
                            start             = Clock::now();
                            const auto result = candidate.transact(
                                {.expectedRevision = snapshot.revision, .payload = SMoveWindowToCommand{.key = 1, .point = point, .outputID = 10, .expectedTopologyRevision = 1}});
                            samples[2].push_back(elapsed(start));
                            if (result.status == eLuminophoreSpatialTransactionStatus::APPLIED) {
                                start           = Clock::now();
                                const auto plan = CLuminophoreSpatialProjection::plan(result.snapshot, 1, outputs);
                                samples[3].push_back(elapsed(start));
                                if (!plan)
                                    throw std::runtime_error("projection rejected fixture");
                                start               = Clock::now();
                                const auto prepared = CLuminophoreSpatialCommitter::prepare(*plan);
                                samples[4].push_back(elapsed(start));
                                if (!prepared)
                                    throw std::runtime_error("prepare rejected fixture");
                                ++accepted;
                            }
#ifdef LUMINOPHORE_PREVIEW_CACHE
                            cache.remember(key);
#endif
                            samples[5].push_back(elapsed(total));
                        }
                        std::cout << "{\"columns\":" << columns << ",\"tiled\":" << tiled << ",\"floating\":" << floating << ",\"outputs\":" << outputCount << ",\"trace\":\""
                                  << (jitter ? "same-cell" : "traverse") << "\",\"inputs\":1000,\"solver_calls\":" << calls << ",\"applied\":" << accepted;
                        const std::array names = {"snapshot", "copy", "transact", "projection", "prepare", "total"};
                        for (size_t stage = 0; stage < names.size(); ++stage)
                            std::cout << ",\"" << names[stage] << "\":{\"samples\":" << samples[stage].size() << ",\"p50_ns\":" << percentile(samples[stage], .5)
                                      << ",\"p95_ns\":" << percentile(samples[stage], .95) << ",\"p99_ns\":" << percentile(samples[stage], .99) << '}';
                        std::cout << "}\n";
                    }
                }
}
