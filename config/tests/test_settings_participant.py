from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SettingsParticipantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        source = root / 'probe.cpp'
        source.write_text(r'''
#include "SettingsParticipant.hpp"
#include <cassert>
#include <stdexcept>
using namespace Luminophore::Settings;
int main(int argc, char** argv) {
    assert(argc == 2);
    const int test = std::stoi(argv[1]);
    const std::string epoch(32, 'a'), initial(64, 'b'), candidate(64, 'c');
    auto runtime = defaults();
    int writes = 0;
    bool failWrite = false, failRead = false, ignoreWrite = false;
    std::function<void()> reenter;
    CSettingsParticipant p(epoch, initial, runtime, [&](const Snapshot& values) {
        ++writes;
        if (reenter) reenter();
        if (!ignoreWrite) runtime = values;
        if (failWrite) throw std::runtime_error("after write");
    }, [&] {
        if (failRead) throw std::runtime_error("readback");
        return runtime;
    });
    auto values = defaults(); values["motion.speed"] = 1.5;
    SRequest req{epoch, 1, 0, candidate, values};
    if (test == 0) {
        assert(p.prepare(req) == eResult::OK && writes == 0);
        assert(p.prepare(req) == eResult::OK);
        assert(p.confirm(epoch, 1) == eResult::BUSY);
        assert(p.apply(epoch, 1) == eResult::OK && writes == 1);
        assert(p.apply(epoch, 1) == eResult::OK && writes == 1);
        assert(p.state().confirmedGeneration == initial);
        assert(p.confirm(epoch, 1) == eResult::OK);
        assert(p.confirm(epoch, 1) == eResult::OK && p.state().revision == 1);
        assert(p.state().confirmedGeneration == candidate);
        assert(p.restore(epoch, 1) == eResult::BUSY);
    } else if (test == 1) {
        auto bad = req; bad.epoch = std::string(32, 'f');
        assert(p.prepare(bad) == eResult::STALE);
        bad = req; bad.values.erase("motion.speed");
        assert(p.prepare(bad) == eResult::INVALID);
        bad = req; bad.values["motion.speed"] = -1.0;
        assert(p.prepare(bad) == eResult::INVALID);
        bad = req; bad.generation = initial;
        assert(p.prepare(bad) == eResult::INVALID);
        assert(p.prepare(req) == eResult::OK);
        bad = req; bad.values["motion.speed"] = 2.0;
        assert(p.prepare(bad) == eResult::INVALID);
        assert(p.apply(std::string(32, 'f'), 1) == eResult::STALE);
        assert(p.restore(epoch, 2) == eResult::STALE);
        assert(writes == 0);
    } else if (test == 2) {
        assert(p.prepare(req) == eResult::OK);
        failWrite = true;
        assert(p.apply(epoch, 1) == eResult::UNKNOWN && writes == 1);
        assert(p.apply(epoch, 1) == eResult::UNKNOWN && writes == 1);
        auto next = req; next.sequence = 2;
        assert(p.prepare(next) == eResult::BUSY);
        assert(p.state().confirmedGeneration == initial);
        assert(p.restore(epoch, 1) == eResult::UNKNOWN && writes == 2);
        failWrite = false;
        assert(p.restore(epoch, 1) == eResult::OK && runtime == defaults());
        assert(p.restore(epoch, 1) == eResult::OK && writes == 3);
        assert(p.apply(epoch, 1) == eResult::BUSY);
        assert(p.prepare(next) == eResult::STALE);
        next.baseRevision = 1;
        assert(p.prepare(next) == eResult::OK);
        assert(p.apply(epoch, 1) == eResult::STALE);
    } else if (test == 3) {
        assert(p.prepare(req) == eResult::OK);
        ignoreWrite = true;
        assert(p.apply(epoch, 1) == eResult::UNKNOWN);
        assert(p.confirm(epoch, 1) == eResult::UNKNOWN);
        ignoreWrite = false; failRead = true;
        assert(p.restore(epoch, 1) == eResult::UNKNOWN);
        failRead = false;
        assert(p.restore(epoch, 1) == eResult::OK);
    } else if (test == 4) {
        runtime["motion.speed"] = 2.0;
        assert(p.prepare(req) == eResult::UNKNOWN && writes == 0);
        assert(p.restore(epoch, 1) == eResult::OK);
        req.sequence = 2; req.baseRevision = 1;
        assert(p.prepare(req) == eResult::OK);
        assert(p.apply(epoch, 2) == eResult::OK);
        runtime["motion.speed"] = 2.0;
        assert(p.confirm(epoch, 2) == eResult::UNKNOWN);
        assert(p.state().confirmedGeneration == initial);
    } else if (test == 5) {
        assert(p.prepare(req) == eResult::OK);
        reenter = [&] {
            assert(p.prepare(req) == eResult::BUSY);
            assert(p.apply(epoch, 1) == eResult::BUSY);
            assert(p.restore(epoch, 1) == eResult::BUSY);
            assert(p.confirm(epoch, 1) == eResult::BUSY);
        };
        assert(p.apply(epoch, 1) == eResult::OK);
        assert(p.restore(epoch, 1) == eResult::OK);
    } else return 10;
}
''')
        native = ROOT / 'compositor/src/config/luminophore'
        cls.binary = root/'probe'
        subprocess.run(['g++','-std=c++23','-Wall','-Wextra','-Werror','-I',str(native),str(source),
                        str(native/'GeneratedSettings.cpp'),str(native/'SettingsParticipant.cpp'),
                        '-o',str(cls.binary)],check=True,capture_output=True)

    def test_normal_confirm_and_duplicate_calls(self): self.run_case(0)
    def test_invalid_stale_and_reused_identity(self): self.run_case(1)
    def test_partial_failure_blocks_retry_until_recovery(self): self.run_case(2)
    def test_readback_and_restore_failures(self): self.run_case(3)
    def test_external_mutation_before_prepare_and_confirm(self): self.run_case(4)
    def test_callback_reentry_cannot_start_competing_mutation(self): self.run_case(5)

    def run_case(self, case):
        subprocess.run([str(self.binary), str(case)], check=True, capture_output=True)
