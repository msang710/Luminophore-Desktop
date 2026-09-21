from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SettingsMemberReadbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        source = root / 'probe.cpp'
        source.write_text(r'''
#include "JointSettingsMember.hpp"
#include <cassert>
#include <stdexcept>
using namespace Luminophore::Settings;
int main(int argc, char**) {
    assert(argc == 2);
    const std::string epoch(32,'a'), base(64,'b'), next(64,'c');
    auto original = defaults(), candidate = defaults(); candidate["motion.speed"] = 1.5;
    Snapshot runtime[1]{original}; int writes[1]{};
    auto native = std::make_shared<CSettingsParticipant>(epoch, base, original,
        [&](const Snapshot& v) {runtime[0]=v; ++writes[0];}, [&] {return runtime[0];});
        assert(native->prepare({epoch,1,0,next,candidate})==eResult::OK);
        assert(native->verify(epoch,1)==eResult::BUSY);
        assert(native->apply(epoch,1)==eResult::OK);
        runtime[0]=original;
        assert(native->verify(epoch,1)==eResult::UNKNOWN);
        runtime[0]=candidate;
        assert(native->verify(epoch,1)==eResult::OK && writes[0]==1);
        assert(native->state().confirmedGeneration==base);
        runtime[0]=original;
        assert(native->confirm(epoch,1)==eResult::UNKNOWN);
        runtime[0]=candidate;
        assert(native->verify(epoch,1)==eResult::OK);
        assert(native->confirm(epoch,1)==eResult::OK);
        assert(native->verify(epoch,1)==eResult::OK);
        assert(native->verify(epoch,2)==eResult::STALE);
}
''')
        native = ROOT / 'compositor/src/config/luminophore'
        cls.binary = root/'probe'
        subprocess.run(['g++', '-std=c++23', '-Wall', '-Wextra', '-Werror', '-I', str(native), str(source),
                        *[str(native/name) for name in ('GeneratedSettings.cpp', 'SettingsParticipant.cpp',
                                                       'JointSettingsMember.cpp')],
                        '-o', str(cls.binary)], check=True, capture_output=True)

    def run_case(self, case):
        subprocess.run([str(self.binary), str(case)], check=True, capture_output=True)

    def test_native_readback_retries_without_writes(self): self.run_case(10)
