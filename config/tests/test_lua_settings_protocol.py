import subprocess
import tempfile
import unittest
from pathlib import Path
from luminophore_shell.config import load_config_text
from luminophore_shell.production_settings import document

class LuaSettingsProtocolTests(unittest.TestCase):
    def test_native_protocol_fences_replays_and_verifies_consumers(self):
        root=Path(__file__).resolve().parents[2]/'compositor/src/config/luminophore'
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'test.cpp'; binary=Path(tmp)/'test'
            source.write_text(r'''
#include "LuaSettingsRuntime.hpp"
#include <iostream>
using namespace Luminophore::Settings;
int main() {
 Snapshot actual=defaults();
 CLuaSettingsTransactions service([&](const SGeneration& before,const SGeneration& after) {
   if(actual != before.values) throw std::runtime_error("baseline");
   return SLuaSettingsBackend{
      [&,v=after.values]{actual=v;},
      [&,v=after.values]{if(actual!=v)throw std::runtime_error("readback");},
      [&,v=before.values]{actual=v;}
   };
 });
 std::string line; while(std::getline(std::cin,line)) {
   if(line=="drift") {actual["compositor.border_size"]=int64_t(11);continue;}
   std::cout << service.request(line) << std::endl;
 }
}
''')
            result=subprocess.run(['g++','-std=c++23','-I',str(root),str(source),str(root/'LuaSettingsRuntime.cpp'),str(root/'GeneratedSettings.cpp'),str(root/'SettingsGeneration.cpp'),str(root/'MonitorSettings.cpp'),str(root/'DesktopSettings.cpp'),'-lcrypto','-ltomlplusplus','-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            before=load_config_text('',Path('/tmp/shell.toml'))
            after=load_config_text('[compositor]\nborder_size=7\n',before.path)
            token='1 '+'1'*32+' '+'a'*64+' '+'b'*64+' '
            commands=[token+'prepare '+document(before,after).encode().hex(),token+'apply',token+'verify','drift',token+'verify',token+'restore',token+'apply']
            invalid=document(before,after)+'\n[after_devices.test]\nunknown_effect = true\n'
            commands.append('1 '+'2'*32+' '+'a'*64+' '+'b'*64+' prepare '+invalid.encode().hex())
            import json
            result=subprocess.run([str(binary)],input='\n'.join(commands)+'\n',capture_output=True,text=True)
            replies=[json.loads(line)['status'] for line in result.stdout.splitlines()]
            self.assertEqual(replies,['ok','ok','ok','failed','ok','failed','failed'],result.stderr)
