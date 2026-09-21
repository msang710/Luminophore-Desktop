from pathlib import Path
from tempfile import TemporaryDirectory
import subprocess
import unittest


class MonitorConfirmationStateTests(unittest.TestCase):
    def test_confirmation_fences_publication_and_stale_answers(self):
        native=Path(__file__).resolve().parents[2]/'compositor/src/config/luminophore'
        with TemporaryDirectory() as directory:
            root=Path(directory);source=root/'probe.cpp';binary=root/'probe'
            source.write_text(r'''
#include "AsyncJointSettings.hpp"
#include <cassert>
using namespace Luminophore::Settings;
int main() {
 const std::string epoch(32,'a'),base(64,'b'),candidate(64,'c');
 for(bool keep:{false,true}) {
  CAsyncJointSettings joint(epoch);SJointRequest r{epoch,1,base,candidate};
  assert(joint.start(r)==eJointState::RUNNING);
  assert(!joint.decide(r,true));
  for(int i=0;i<8;++i) {
   auto cmd=joint.pending();assert(cmd);
   if(cmd->target==eSettingsTarget::NATIVE && cmd->operation==eSettingsOperation::PREPARE)assert(joint.requireConfirmation(r));
   assert(cmd->operation!=eSettingsOperation::PUBLISH);
   assert(joint.reply(*cmd,eSettingsReply::OK,cmd->operation==eSettingsOperation::CURRENT?base:""));
  }
  assert(joint.awaitingConfirmation());assert(!joint.pending());
  assert(joint.start(r)==eJointState::RUNNING);assert(joint.awaitingConfirmation());
  auto wrong=r;wrong.sequence=2;assert(!joint.decide(wrong,true));
  wrong=r;wrong.epoch=std::string(32,'d');assert(!joint.decide(wrong,true));
  wrong=r;wrong.candidate=std::string(64,'e');assert(!joint.decide(wrong,true));
  assert(joint.decide(r,keep));assert(!joint.decide(r,!keep));
  assert(joint.pending()->operation==(keep?eSettingsOperation::PUBLISH:eSettingsOperation::CURRENT));
  for(int i=0;i<12 && joint.pending();++i) {
   auto cmd=*joint.pending();assert(keep || cmd.operation!=eSettingsOperation::PUBLISH);
   assert(joint.reply(cmd,eSettingsReply::OK,cmd.operation==eSettingsOperation::CURRENT?(keep?candidate:base):""));
  }
  assert(joint.state()==(keep?eJointState::COMPLETE:eJointState::ABORTED));
  assert(!joint.decide(r,true));
 }
}
''')
            subprocess.run(['g++','-std=c++23','-I',str(native),str(source),str(native/'AsyncJointSettings.cpp'),'-o',str(binary)],check=True,capture_output=True,text=True)
            subprocess.run([str(binary)],check=True,capture_output=True,text=True)
