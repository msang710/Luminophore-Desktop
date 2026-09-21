#include "../../src/luminophore/LuminophoreSpatialCommitter.hpp"

#include <gtest/gtest.h>
#include <set>

TEST(LuminophoreSpatialCommitter, WideFragmentsRemainOutputAddressable) {
    const SLuminophoreProjectionPlan plan{
        .modelRevision    = 7,
        .topologyRevision = 3,
        .windows          = {{
            .key   = 11,
            .point = {.x = 2, .y = 1},
            .fragments =
                {
                    {.key = 11, .point = {.x = 2, .y = 1}, .outputID = 1, .box = {.x = 0, .y = 0, .width = 1920, .height = 1080}},
                    {.key = 11, .point = {.x = 2, .y = 1}, .outputID = 2, .box = {.x = 1920, .y = 0, .width = 1920, .height = 1080}},
                },
            .visible = true,
        }},
    };

    const auto commit = CLuminophoreSpatialCommitter::prepare(plan);
    ASSERT_TRUE(commit.has_value());
    ASSERT_EQ(commit->entries.size(), 1);
    EXPECT_EQ(commit->entries.front().clientBox, (SLuminophorePhysicalBox{.x = 0, .y = 0, .width = 3840, .height = 1080}));
    EXPECT_EQ(commit->entries.front().primaryOutputID, 1);
    ASSERT_EQ(commit->entries.front().fragments.size(), 2);
    EXPECT_EQ(commit->entries.front().fragments[0].outputID, 1);
    EXPECT_EQ(commit->entries.front().fragments[1].outputID, 2);
}

TEST(LuminophoreSpatialCommitter, InvalidPlanNeverProducesACommit) {
    const SLuminophoreProjectionPlan plan{
        .modelRevision    = 1,
        .topologyRevision = 1,
        .windows          = {{
            .key     = 12,
            .point   = {},
            .visible = true,
        }},
    };

    EXPECT_FALSE(CLuminophoreSpatialCommitter::prepare(plan).has_value());
}

TEST(LuminophoreSpatialCommitter, DisjointSameOutputRegionsAreAllowedButOverlapIsRejected) {
    const auto planWith = [](uint64_t secondOutput, int secondX) {
        return SLuminophoreProjectionPlan{
            .modelRevision    = 1,
            .topologyRevision = 1,
            .windows          = {{
                .key   = 12,
                .point = {},
                .fragments =
                    {
                        {.key = 12, .point = {}, .outputID = 1, .box = {.x = 0, .y = 0, .width = 100, .height = 100}},
                        {.key = 12, .point = {}, .outputID = secondOutput, .box = {.x = secondX, .y = 0, .width = 100, .height = 100}},
                    },
                .visible = true,
            }},
        };
    };

    EXPECT_TRUE(CLuminophoreSpatialCommitter::prepare(planWith(1, 100)).has_value());
    EXPECT_FALSE(CLuminophoreSpatialCommitter::prepare(planWith(1, 50)).has_value());
    EXPECT_FALSE(CLuminophoreSpatialCommitter::prepare(planWith(2, 50)).has_value());
}

TEST(LuminophoreSpatialCommitter, MissingTargetFailsBeforeAnyWrite) {
    const SLuminophoreSpatialCommit commit{
        .modelRevision    = 2,
        .topologyRevision = 1,
        .entries =
            {
                {.key = 1, .visible = true, .clientBox = {.width = 100, .height = 100}},
                {.key = 2, .visible = false},
            },
    };
    CLuminophoreSpatialCommitter committer;
    size_t                writes = 0;

    EXPECT_FALSE(committer.apply(commit, [](LuminophoreWindowKey key) { return key == 1; }, [](uint64_t) { return true; }, [&](const auto&) { ++writes; }));
    EXPECT_EQ(writes, 0);
    EXPECT_FALSE(committer.presented().has_value());
}

TEST(LuminophoreSpatialCommitter, SuccessfulCommitPublishesOnlyAfterAllWrites) {
    const SLuminophoreSpatialCommit commit{
        .modelRevision    = 4,
        .topologyRevision = 2,
        .entries =
            {
                {.key = 1, .visible = true, .clientBox = {.width = 100, .height = 100}},
                {.key = 2, .visible = false},
            },
    };
    CLuminophoreSpatialCommitter      committer;
    std::vector<LuminophoreWindowKey> writes;

    ASSERT_TRUE(committer.apply(
        commit, [](LuminophoreWindowKey) { return true; }, [](uint64_t) { return true; },
        [&](const auto& entry) {
            EXPECT_FALSE(committer.presented().has_value());
            EXPECT_TRUE(CLuminophoreSpatialCommitter::isApplying());
            ASSERT_NE(committer.effective(), nullptr);
            EXPECT_EQ(*committer.effective(), commit);
            writes.emplace_back(entry.key);
        }));
    EXPECT_FALSE(CLuminophoreSpatialCommitter::isApplying());
    EXPECT_EQ(writes, (std::vector<LuminophoreWindowKey>{1, 2}));
    ASSERT_TRUE(committer.presented().has_value());
    ASSERT_NE(committer.effective(), nullptr);
    EXPECT_EQ(*committer.presented(), commit);
    EXPECT_EQ(*committer.effective(), commit);
}

TEST(LuminophoreSpatialCommitter, DuplicateIsIdempotentAndStaleCommitIsRejected) {
    const SLuminophoreSpatialCommit current{
        .modelRevision    = 5,
        .topologyRevision = 3,
        .entries          = {{.key = 1, .visible = false}},
    };
    const SLuminophoreSpatialCommit stale{
        .modelRevision    = 4,
        .topologyRevision = 3,
        .entries          = {{.key = 1, .visible = false}},
    };
    CLuminophoreSpatialCommitter committer;
    size_t                writes = 0;
    const auto            exists = [](LuminophoreWindowKey) { return true; };
    const auto            writer = [&](const auto&) { ++writes; };

    const auto            outputExists = [](uint64_t) { return true; };
    ASSERT_TRUE(committer.apply(current, exists, outputExists, writer));
    ASSERT_TRUE(committer.apply(current, exists, outputExists, writer));
    EXPECT_FALSE(committer.apply(stale, exists, outputExists, writer));
    EXPECT_EQ(writes, 1);
    EXPECT_EQ(*committer.presented(), current);
}

TEST(LuminophoreSpatialCommitter, MissingOutputFailsBeforeAnyWrite) {
    const SLuminophoreSpatialCommit commit{
        .modelRevision    = 2,
        .topologyRevision = 1,
        .entries          = {{
            .key             = 1,
            .visible         = true,
            .primaryOutputID = 7,
            .clientBox       = {.width = 100, .height = 100},
            .fragments       = {{.key = 1, .outputID = 7, .box = {.width = 100, .height = 100}}},
        }},
    };
    CLuminophoreSpatialCommitter committer;
    size_t                writes = 0;

    EXPECT_FALSE(committer.apply(commit, [](LuminophoreWindowKey) { return true; }, [](uint64_t) { return false; }, [&](const auto&) { ++writes; }));
    EXPECT_EQ(writes, 0);
    EXPECT_FALSE(committer.presented().has_value());
}

TEST(LuminophoreSpatialCommitter, FailedBatchDoesNotPublishCandidateRevision) {
    const SLuminophoreSpatialCommit current{
        .modelRevision    = 3,
        .topologyRevision = 2,
        .entries          = {{.key = 1, .visible = false}},
    };
    const SLuminophoreSpatialCommit candidate{
        .modelRevision    = 4,
        .topologyRevision = 2,
        .entries          = {{.key = 1, .visible = false}},
    };
    CLuminophoreSpatialCommitter committer;
    const auto            exists = [](auto) { return true; };

    ASSERT_TRUE(committer.applyBatch(current, exists, exists, [](const auto&) { return true; }));
    EXPECT_FALSE(committer.applyBatch(candidate, exists, exists, [](const auto&) { return false; }));
    ASSERT_TRUE(committer.presented());
    EXPECT_EQ(*committer.presented(), current);
    EXPECT_FALSE(CLuminophoreSpatialCommitter::isApplying());
}

TEST(LuminophoreSpatialCommitter, FloatingClientBoxIsNotCroppedToVisibleFragments) {
    SLuminophoreProjectionPlan plan{.modelRevision = 1, .topologyRevision = 1};
    plan.windows.push_back(SLuminophoreProjectedWindow{.key       = 1,
                                                .fragments = {{.key = 1, .outputID = 1, .box = {.x = 0, .y = 0, .width = 80, .height = 90}}},
                                                .visible   = true,
                                                .floating  = true,
                                                .clientBox = SLuminophorePhysicalBox{.x = -20, .y = -10, .width = 100, .height = 100}});
    const auto commit = CLuminophoreSpatialCommitter::prepare(plan);
    ASSERT_TRUE(commit);
    EXPECT_EQ(commit->entries.front().clientBox, (SLuminophorePhysicalBox{.x = -20, .y = -10, .width = 100, .height = 100}));
}

TEST(LuminophoreSpatialCommitter, DesktopModeIsEffectiveDuringBatchAndRollsBackOnFailure) {
    CLuminophoreSpatialCommitter committer;
    const auto            targetExists = [](LuminophoreWindowKey) { return true; };
    const auto            outputExists = [](uint64_t) { return true; };
    SLuminophoreSpatialCommit    commit;
    commit.modelRevision = 1;
    ASSERT_TRUE(committer.applyBatch(commit, targetExists, outputExists, [](const auto&) { return true; }));
    commit.modelRevision    = 2;
    commit.presentationMode = eLuminophorePresentationMode::DESKTOP;
    EXPECT_FALSE(committer.applyBatch(commit, targetExists, outputExists, [&](const auto&) {
        EXPECT_EQ(committer.effective()->presentationMode, eLuminophorePresentationMode::DESKTOP);
        return false;
    }));
    EXPECT_EQ(committer.effective()->presentationMode, eLuminophorePresentationMode::NORMAL);
    EXPECT_EQ(committer.effective()->modelRevision, 1U);
    ASSERT_TRUE(committer.applyBatch(commit, targetExists, outputExists, [](const auto&) { return true; }));
    EXPECT_EQ(committer.effective()->presentationMode, eLuminophorePresentationMode::DESKTOP);
}
