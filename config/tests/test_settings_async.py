from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def compile_async_probe(root):
    source = root / 'probe.cpp'
    source.write_text(r'''
#include "SettingsCommandProtocol.hpp"
#include "JointSettingsMember.hpp"
#include <cassert>
#include <iostream>
#include <stdexcept>
using namespace Luminophore::Settings;
int main(int argc, char** argv) {
    assert(argc==2 || argc==4);
    const int test=std::stoi(argv[1]);
    const std::string epoch(32,'a'),base=argc==4?argv[2]:std::string(64,'b'),next=argc==4?argv[3]:std::string(64,'c');
    CAsyncJointSettings joint(epoch);
    SJointRequest request{epoch,1,base,next};
    assert(joint.start(request)==eJointState::RUNNING);
    assert(joint.start(request)==eJointState::RUNNING);
    auto other=request;other.sequence=2;
    assert(joint.start(other)==eJointState::BUSY);
    Snapshot values=defaults();values["motion.speed"]=1.5;
    Snapshot runtime[2]{defaults(),defaults()};
    unsigned writes[2]{},publications=0,restores=0,abandoned=0;
    bool injected=false;
    auto make=[&](int n) {return std::make_shared<CSettingsParticipant>(epoch,base,defaults(),
        [&,n](const Snapshot& v) {runtime[n]=v;++writes[n];},[&,n] {return runtime[n];});};
    auto n=make(0),s=make(1);
    SSettingsMember members[2]{makeJointMember(n,[&](const std::string&) {if(test==11)throw std::runtime_error("candidate unavailable"); return values;}),makeJointMember(s,[&](const std::string&) {return values;})};
    if(test==10)assert(n->prepare({epoch,4,0,next,values})==eResult::OK);
    std::string current=test==12?std::string(64,'d'):base;
    std::optional<SSettingsCommand> old;
    unsigned steps=0;
    while(auto command=joint.pending()) {
        assert(++steps<80);
        if(old) assert(!joint.reply(*old,eSettingsReply::OK,base));
        if(test==1 && command->target==eSettingsTarget::SHELL && command->operation==eSettingsOperation::APPLY && !injected) {
            injected=true;
            assert(joint.timeout(*command));
            assert(joint.state()==eJointState::UNKNOWN);
            assert(joint.start(other)==eJointState::BUSY);
            assert(joint.recover(request)==eJointState::UNKNOWN);
            assert(joint.pending()==command && restores==0);
        }
        SSettingsResponse response{eSettingsReply::OK,""};
        if(command->target==eSettingsTarget::STORE) {
            if(test==20) {
                std::cout<<commandWire(*command)<<std::endl;
                do {
                    std::string line; if(!std::getline(std::cin,line))return 90;
                    const auto parsed=parseReceipt(*command,line);assert(parsed);response=*parsed;
                    assert(joint.reply(*command,response.result,response.current));
                } while(joint.pending()==command);
                old=command;
                continue;
            }
            switch(command->operation) {
                case eSettingsOperation::CURRENT: response.current=current;break;
                case eSettingsOperation::PUBLISH:
                    assert(runtime[0]==values && runtime[1]==values);
                    ++publications;
                    if(test==13) {response.result=eSettingsReply::FAILED;break;}
                    current=next;
                    if(test==2) response.result=eSettingsReply::FAILED; // Completed rename, response lost.
                    break;
                case eSettingsOperation::ABANDON:++abandoned;break;
                default:break;
            }
        } else {
            auto local=*command;local.target=eSettingsTarget::NATIVE;
            response=executeMember(local,members[command->target==eSettingsTarget::NATIVE?0:1]);
            if(command->operation==eSettingsOperation::RESTORE)++restores;
            if((test==3 || test==6) && command->target==eSettingsTarget::SHELL && command->operation==eSettingsOperation::APPLY)response.result=eSettingsReply::FAILED;
            if(test==6 && command->operation==eSettingsOperation::RESTORE && !injected) {
                injected=true; response.result=eSettingsReply::FAILED;
            }
            if(test==4 && command->operation==eSettingsOperation::CONFIRM && command->target==eSettingsTarget::SHELL && !injected) {
                injected=true;response.result=eSettingsReply::FAILED;
            }
        }
        if(test==5 && command->operation==eSettingsOperation::PREPARE && !injected) {
            injected=true;
            auto wrong=*command;wrong.ticket++;
            assert(!joint.reply(wrong,eSettingsReply::OK));
            const auto valid=commandWire(*command)+" ok -";
            assert(parseReceipt(*command,valid));
            assert(!parseReceipt(*command,valid+" trailing"));
            assert(!parseReceipt(wrong,valid));
            assert(!parseReceipt(*command,commandWire(*command)+" success -"));
        }
        if(test==7 && command->operation==eSettingsOperation::CURRENT && !injected) {
            injected=true;response.result=eSettingsReply::FAILED;
        }
        if(test==8 && command->operation==eSettingsOperation::CURRENT && publications && !injected) {
            injected=true;response.current=std::string(64,'d');
        }
        if(test==9 && command->operation==eSettingsOperation::CURRENT && !injected) {
            injected=true;
            assert(joint.reply(*command,eSettingsReply::OK,"garbled"));
            assert(joint.state()==eJointState::UNKNOWN && joint.pending()==command);
            assert(joint.recover(request)==eJointState::UNKNOWN);
        }
        assert(joint.reply(*command,response.result,response.current));
        old=command;
        if(test==6 && joint.state()==eJointState::UNKNOWN) {
            assert(!joint.pending() && restores==2 && abandoned==0);
            assert(joint.recover(request)==eJointState::RUNNING);
        }
        if((test==7 || test==8) && joint.state()==eJointState::UNKNOWN) {
            assert(!joint.pending() && restores==0 && abandoned==0);
            assert(joint.recover(request)==eJointState::RUNNING);
        }
        if(test==4 && joint.state()==eJointState::UNKNOWN) {
            assert(!joint.pending());assert(restores==0 && publications==1);
            assert(joint.recover(request)==eJointState::RUNNING);
        }
    }
    if(test==11 || test==13) {
        assert(joint.state()==eJointState::ABORTED && abandoned==1);
        assert(runtime[0]==defaults() && runtime[1]==defaults());
        if(test==11)assert(writes[0]==0 && writes[1]==0 && publications==0);
        else assert(writes[0]==2 && writes[1]==2 && publications==1);
    } else if(test==12) {
        assert(joint.state()==eJointState::STALE && writes[0]==0 && writes[1]==0 && publications==0);
    } else if(test==10) {
        assert(joint.state()==eJointState::UNKNOWN && abandoned==0 && publications==0);
        assert(n->state().phase==ePhase::PREPARED && n->state().sequence==4);
    } else if(test==7) {
        assert(joint.state()==eJointState::ABORTED && writes[0]==0 && writes[1]==0 && abandoned==0);
    } else if(test==3 || test==6) {
        assert(joint.state()==eJointState::ABORTED && restores==(test==6?4U:2U) && abandoned==1);
        assert(runtime[0]==defaults() && runtime[1]==defaults() && publications==0);
    } else {
        assert(joint.state()==eJointState::COMPLETE);
        assert(runtime[0]==values && runtime[1]==values && writes[0]==1 && writes[1]==1);
        assert(n->state().confirmedGeneration==next && s->state().confirmedGeneration==next);
        assert(restores==0);
    }
    auto bad=request; bad.candidate=base; assert(joint.start(bad)==eJointState::INVALID);
    bad=request;bad.epoch=std::string(32,'f');assert(joint.start(bad)==eJointState::STALE);
    bad=request;bad.sequence=0;assert(joint.start(bad)==eJointState::STALE);
    if(test==20)std::cout<<"complete"<<std::endl;
}
''')
    native = ROOT/'compositor/src/config/luminophore'
    binary = root/'async-probe'
    sources = ('GeneratedSettings.cpp', 'SettingsParticipant.cpp', 'JointSettingsMember.cpp',
               'AsyncJointSettings.cpp', 'SettingsCommandProtocol.cpp')
    subprocess.run(['g++','-std=c++23','-Wall','-Wextra','-Werror','-I',str(native),str(source),
                    *[str(native/name) for name in sources],'-o',str(binary)],check=True,capture_output=True)
    return binary


class AsyncSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.binary = compile_async_probe(Path(cls.temp.name))

    def run_case(self, case):
        subprocess.run([str(self.binary),str(case)],check=True,capture_output=True)

    def test_native_participants_complete_in_order(self): self.run_case(0)
    def test_timeout_waits_for_same_receipt_without_rollback(self): self.run_case(1)
    def test_publish_error_queries_pointer_without_reapplying(self): self.run_case(2)
    def test_terminal_partial_failure_restores_both(self): self.run_case(3)
    def test_confirmation_recovery_never_rolls_back_publication(self): self.run_case(4)
    def test_strict_wire_identity_and_duplicate_receipt(self): self.run_case(5)

    def test_restore_failure_attempts_other_member_and_retains_journal(self): self.run_case(6)
    def test_initial_query_failure_does_not_apply_candidate(self): self.run_case(7)
    def test_unrelated_checkpoint_never_authorizes_restore(self): self.run_case(8)
    def test_malformed_query_keeps_request_pending(self): self.run_case(9)

    def test_unrelated_prepared_member_cannot_acknowledge_restore(self): self.run_case(10)

    def test_candidate_load_failure_does_not_touch_runtime(self): self.run_case(11)
    def test_stale_checkpoint_does_not_prepare(self): self.run_case(12)
    def test_publication_failure_before_rename_restores(self): self.run_case(13)
