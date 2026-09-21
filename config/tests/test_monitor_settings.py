from dataclasses import asdict
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import tomllib
import unittest
from luminophore_shell.monitor_settings import decode_monitors
from luminophore_shell.settings_store import StoreError


class MonitorSettingsParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(); cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name); cls.binary = root/'probe'
        native = Path(__file__).resolve().parents[2]/'compositor/src/config/luminophore'
        source=root/'probe.cpp'
        source.write_text(r'''
#include "MonitorSettings.hpp"
#include <iostream>
#include <iomanip>
#include <iterator>
int main() {
 try {
  const std::string text{std::istreambuf_iterator<char>(std::cin), {}};
  const auto rules=Luminophore::Settings::decodeMonitorSettings(text);
  std::cout << std::setprecision(17) << "{"; bool first=true;
  for(const auto& [name,r]:rules) {
   if(!first)std::cout<<",";first=false;
   std::cout<<"\""<<name<<"\":{\"width\":"<<r.width<<",\"height\":"<<r.height<<",\"refresh\":"<<r.refresh<<",\"scale\":"<<r.scale<<",\"transform\":"<<r.transform<<",\"disabled\":"<<(r.disabled?"true":"false")<<",\"position\":";
   if(r.position)std::cout<<"["<<r.position->first<<","<<r.position->second<<"]";else std::cout<<"null";
   std::cout<<",\"vrr\":"; if(r.vrr)std::cout<<*r.vrr;else std::cout<<"null";
   std::cout<<"}";
  } std::cout<<"}";
 }catch(const std::exception& e){std::cerr<<e.what();return 1;}
}
''')
        subprocess.run(['g++','-std=c++23','-I',str(native),str(source),str(native/'MonitorSettings.cpp'),'-ltomlplusplus','-o',str(cls.binary)],check=True,capture_output=True,text=True)

    def check(self, body, valid):
        document='schema_version = 1\n'+body
        native=subprocess.run([str(self.binary)],input=document,text=True,capture_output=True,timeout=3)
        parsed=tomllib.loads(document);parsed.pop('schema_version')
        if valid:
            expected=json.loads(json.dumps({k:asdict(v) for k,v in decode_monitors(parsed).items()}))
            self.assertEqual(native.returncode,0,native.stderr)
            self.assertEqual(json.loads(native.stdout),expected)
        else:
            with self.assertRaises(StoreError):decode_monitors(parsed)
            self.assertNotEqual(native.returncode,0,document)

    def test_valid_parity_and_disconnected_outputs(self):
        for body in ['', '[positions]\nDP-1=[-1920,0]\n', '[outputs.DP-1]\nmode="1920x1080@59.94"\nscale=1.25\ntransform=3\nposition=[-500,100]\ndisabled=false\n', '[outputs.MISSING]\nmode="preferred"\nscale=2\ndisabled=true\n', '[outputs.DP-1]\n[outputs.DP-2]\ntransform=7\n']:
            with self.subTest(body=body): self.check(body, True)

    def test_invalid_parity(self):
        for field in ['mode=true','mode="0x720@60"','mode="32769x720@60"','mode="1920x1080@1001"','mode="1920x1080"','scale=true','scale=nan','scale=inf','scale=0.0','scale=9','transform=true','transform=1.0','transform=8','disabled=1','position=[true,0]','position=[0.0,0]','position=[100001,0]','position=[0]','surprise=1']:
            with self.subTest(field=field):self.check('[outputs.DP-1]\n'+field,False)
        for body in ['outputs=[]','[positions]\n"bad:name"=[0,0]','[positions]\nDP-1=[0,0]\n[outputs.DP-1]\nposition=[0,0]', '[outputs]\nDP-1=1', '\n'.join(f'[outputs.DP-{i}]' for i in range(33))]:
            with self.subTest(body=body):self.check(body,False)

    def test_output_vrr_policy_parity(self):
        for value in range(4):
            self.check('[outputs.DP-1]\nvrr='+str(value), True)
        for value in ('true', '1.0', '-1', '4', '"2"'):
            self.check('[outputs.DP-1]\nvrr='+value, False)
