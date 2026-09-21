#include "JointSettingsMember.hpp"
#include <stdexcept>
#include <utility>
using namespace Luminophore::Settings;

static void requireResult(eResult result) {
    if (result != eResult::OK)
        throw std::runtime_error("settings participant did not acknowledge phase");
}

SSettingsMember Luminophore::Settings::makeJointMember(std::shared_ptr<CSettingsParticipant> participant, std::function<Snapshot(const std::string&)> load) {
    if (!participant || !load)
        throw std::invalid_argument("missing settings participant or generation loader");
    auto touched = std::make_shared<bool>(false);
    return {
        .prepare =
            [participant, load = std::move(load), touched](const SJointRequest& request) {
                *touched         = false;
                const auto state = participant->state();
                if (state.epoch != request.epoch || state.confirmedGeneration != request.base)
                    throw std::runtime_error("settings participant base mismatch");
                const auto values = load(request.candidate);
                const auto result = participant->prepare({request.epoch, request.sequence, state.revision, request.candidate, values});
                const auto after  = participant->state();
                *touched          = after.sequence == request.sequence && after.candidateGeneration == request.candidate;
                requireResult(result);
            },
        .apply  = [participant](const SJointRequest& request) { requireResult(participant->apply(request.epoch, request.sequence)); },
        .verify = [participant](const SJointRequest& request) { requireResult(participant->verify(request.epoch, request.sequence)); },
        .restore =
            [participant, touched](const SJointRequest& request) {
                if (*touched)
                    requireResult(participant->restore(request.epoch, request.sequence));
                else {
                    const auto state = participant->state();
                    if (state.epoch != request.epoch || state.confirmedGeneration != request.base ||
                        (state.phase != ePhase::IDLE && state.phase != ePhase::RESTORED && state.phase != ePhase::CONFIRMED))
                        throw std::runtime_error("cannot acknowledge restoration of a different or busy participant base");
                }
            },
        .confirm = [participant](const SJointRequest& request) { requireResult(participant->confirm(request.epoch, request.sequence)); },
    };
}
