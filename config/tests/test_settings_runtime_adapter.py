from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class RuntimeSettingsAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        source = root/'probe.cpp'
        source.write_text(r'''
#include "RuntimeSettingsAdapter.hpp"
#include "MotionSettings.hpp"
#include <cassert>
#include <stdexcept>
using namespace Luminophore::Settings;
static Value native(const std::string& key, Value v) {
    if (const auto* d = std::get_if<double>(&v); d && (key == "compositor.dim_special" || key == "compositor.fullscreen_opacity" || key == "compositor.dim_strength" || key == "compositor.dim_around" || key == "compositor.active_opacity" || key == "compositor.inactive_opacity" || key == "input.sensitivity" || key == "input.cursor_inactive_timeout" || key == "input.follow_mouse_threshold" || key == "input.scroll_factor" || key == "touchpad.scroll_factor" || key.starts_with("tablet.region_") || key.starts_with("tablet.active_area_") || key.starts_with("tablettool.pressure_range_"))) return double(float(*d));
    return v;
}
int main(int argc, char** argv) {
    assert(argc == 2); const int test = std::stoi(argv[1]);
    const auto initial = defaults(); auto actual = initial;
    for (auto& [key,v] : actual) v = native(key,v);
    int writes = 0, refresh = 0; bool fail = false;
    std::map<std::string,SRuntimeSlot> slots;
    for (const auto& key : CRuntimeSettingsAdapter::supported())
        slots[key] = {[&,key]{return actual.at(key);}, [&,key](const Value& v){++writes;actual[key]=native(key,v);}};
    CRuntimeSettingsAdapter adapter(initial, slots, [&]{++refresh;if(fail)throw std::runtime_error("refresh");});
    auto candidate = initial;candidate["compositor.fullscreen_opacity"] = 0.73;candidate["compositor.dim_strength"] = 0.31;candidate["compositor.dim_around"] = 0.27;candidate["input.cursor_inactive_timeout"] = 1.3;candidate["input.sensitivity"] = 0.42;candidate["input.scroll_factor"] = 1.3;candidate["compositor.dim_special"] = 0.42;candidate["compositor.border_size"]=int64_t(9);
    candidate["input.follow_mouse_threshold"] = 0.3; candidate["tablet.region_position_x"] = 0.3; candidate["tablet.active_area_position_y"] = 0.7; candidate["tablettool.pressure_range_min"] = 0.1;
    if (test == 0) {
        adapter.apply(candidate); assert(refresh == 1 && writes == int(slots.size()));
        assert(adapter.read() == candidate);
        assert(std::get<double>(actual.at("compositor.dim_special")) != 0.42);
        assert(std::get<double>(actual.at("tablet.region_position_x")) != 0.3);
        actual["tablet.region_position_x"] = double(float(0.31));
        assert(adapter.read() != candidate);
        adapter.apply(candidate);
        actual["compositor.dim_special"] = double(float(0.43));
        assert(adapter.read() != candidate);
        adapter.apply(initial);assert(adapter.read() == initial && refresh == 3);
    } else if (test == 1) {
        candidate["unknown"] = 2.0;
        try {adapter.apply(candidate);return 1;} catch (const std::invalid_argument&) {}
        assert(writes == 0 && refresh == 0 && adapter.read() == initial);
        candidate = initial;candidate["compositor.border_size"] = int64_t(100);
        try {adapter.apply(candidate);return 2;} catch (const std::invalid_argument&) {}
        assert(writes == 0);
    } else if (test == 2) {
        actual["compositor.vrr"] = std::string("wrong");
        try {adapter.apply(candidate);return 3;} catch (const std::runtime_error&) {}
        assert(writes == 0);
        try {CRuntimeSettingsAdapter bad(initial,slots,[]{});return 4;} catch(const std::invalid_argument&) {}
    } else if (test == 3) {
        CSettingsParticipant p(std::string(32,'a'),std::string(64,'b'),initial,
            [&](const Snapshot& v){adapter.apply(v);},[&]{return adapter.read();});
        SRequest req{std::string(32,'a'),1,0,std::string(64,'c'),candidate};
        assert(p.prepare(req) == eResult::OK);fail=true;
        assert(p.apply(req.epoch,1)==eResult::UNKNOWN);
        assert(p.state().confirmedGeneration == std::string(64,'b'));
        assert(p.apply(req.epoch,1)==eResult::UNKNOWN && writes == int(slots.size()));
        fail=false;
        assert(p.restore(req.epoch,1)==eResult::OK);
        assert(adapter.read()==initial);
    } else if (test == 5 || test == 6 || test == 7) {
        bool drift=false, available=true, repair=true;
        auto semanticSlots=slots;
        semanticSlots["motion.speed"] = {
            [&]() -> Value {if(drift)throw std::runtime_error("animation settings drift");return actual.at("motion.speed");},
            [&](const Value& v) {actual["motion.speed"]=v; ++writes;},
            [&] {if(!available)throw std::runtime_error("missing animation destination");}
        };
        CRuntimeSettingsAdapter semantic(initial,semanticSlots,[&] {if(repair)drift=false;});
        CSettingsParticipant p(std::string(32,'a'),std::string(64,'b'),initial,
            [&](const Snapshot& v){semantic.apply(v);},[&]{return semantic.read();});
        SRequest req{std::string(32,'a'),1,0,std::string(64,'c'),candidate};
        assert(p.prepare(req)==eResult::OK);
        assert(p.apply(req.epoch,1)==eResult::OK);
        drift=true;
        assert(p.verify(req.epoch,1)==eResult::UNKNOWN);
        const int before=writes;
        available=test!=6;repair=test!=7;
        if(test==5) {
            assert(p.restore(req.epoch,1)==eResult::OK && semantic.read()==initial);
            assert(writes>before);
        } else {
            assert(p.restore(req.epoch,1)==eResult::UNKNOWN);
            if(test==6)assert(writes==before);
        }
    } else if (test == 8) {
        slots["input.kb_snapshot"] = {[]() -> Value { return std::string{}; },
            [](const Value&) {}, {}, [](const Value& value) {
                if (!std::get<std::string>(value).empty()) throw std::runtime_error("unsupported snapshot");
            }};
        CRuntimeSettingsAdapter legacy(initial,slots,[&]{++refresh;});
        candidate["input.kb_file"]=std::string("/tmp/example.xkb");
        candidate["input.kb_snapshot"]=std::string("/tmp/example.xkb\nkeymap");
        try { legacy.apply(candidate); return 9; } catch (const std::runtime_error&) {}
        assert(writes==0 && refresh==0 && legacy.read()==initial);
        legacy.apply(initial);assert(refresh==1);
    } else if (test == 4) {
        assert(CRuntimeSettingsAdapter::supported().size() == defaults().size());
        candidate["motion.speed"] = 1.3; candidate["motion.preset"] = std::string("smooth");
        candidate["visual.intensity"] = 0.42; candidate["compositor.gaps_in"] = int64_t(7);
        candidate["compositor.default_view_columns"] = int64_t(1);
        adapter.apply(candidate); assert(adapter.read() == candidate);
        const auto nodes = motionNodes(candidate);
        assert(nodes.size() == 4 && nodes[0].speed == float(5*1.3) && nodes[1].curve == "spring:easy");
        assert(nodes[2].style == "slide top" && nodes[3].style == "slide bottom");
        candidate["motion.enabled"] = false;
        for (const auto& n : motionNodes(candidate)) assert(!n.enabled && n.speed == 1 && n.curve == "default" && n.style.empty());
        for (const auto& preset : {"fast", "balanced", "custom"}) {
            candidate["motion.enabled"] = true; candidate["motion.preset"] = std::string(preset);
            const auto n = motionNodes(candidate);
            assert(n[0].curve == "quick" && n[0].speed == float((std::string(preset)=="fast" ? 2 : 3)*1.3));
        }
    }
}
''')
        native = ROOT/'compositor/src/config/luminophore'
        cls.binary = root/'probe'
        subprocess.run(['g++','-std=c++23','-Wall','-Wextra','-Werror','-I',str(native),str(source),
                        *[str(native/name) for name in ('GeneratedSettings.cpp','SettingsParticipant.cpp','RuntimeSettingsAdapter.cpp','MotionSettings.cpp')],
                        '-o',str(cls.binary)],capture_output=True,check=True)

    def run_case(self, case): subprocess.run([str(self.binary),str(case)],check=True,capture_output=True)
    def test_precision_readback_and_restore(self): self.run_case(0)
    def test_unsupported_and_invalid_values_do_not_write(self): self.run_case(1)
    def test_all_destinations_checked_before_write(self): self.run_case(2)
    def test_refresh_failure_integrates_with_participant_recovery(self): self.run_case(3)

    def test_all_scalar_slots_and_motion_presets(self): self.run_case(4)

    def test_semantic_drift_does_not_prevent_restore_write(self): self.run_case(5)
    def test_missing_semantic_destination_keeps_restore_unknown(self): self.run_case(6)
    def test_unrepaired_semantic_readback_keeps_restore_unknown(self): self.run_case(7)

    def test_unsupported_slot_candidate_rejected_before_any_write(self): self.run_case(8)
