from importlib.util import module_from_spec, spec_from_file_location
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
def load(name, filename):
    spec = spec_from_file_location(name, ROOT / filename); module = module_from_spec(spec)
    spec.loader.exec_module(module); return module
installer = load("install_release", "install-release.py")
follow = load("desktop_follow", "desktop-follow.py")


class InstallReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("queue", "work", "packages", "db", "generations"):
            (self.root / name).mkdir()
        desc = self.root / "db/glibc-1"; desc.mkdir()
        (desc/"desc").write_text("%NAME%\nglibc\n\n%VERSION%\n1\n")
        self.transaction = "a"*64; self.generation = "d"*64
        (self.root/"generations"/self.generation).mkdir()
        self.package = self.root/"packages/luminophore.pkg.tar.zst"; self.package.write_bytes(b"package")
        host = follow.identity(follow.package_set(self.root/"db"))
        self.receipt = {"schema":"luminophore-follow-build/v1","transaction":self.transaction,
            "host_digest":host,"generation":self.generation,"version":"0.2.0",
            "package":self.package.name,"package_sha256":hashlib.sha256(b"package").hexdigest(),
            "components":["compositor","control","shell","greeter","portal"],"provenance":{},"sbom_digest":"e"*64}
        self.receipt_path = self.root/"work"/(self.transaction+".build.json")
        self.receipt_path.write_text(json.dumps(self.receipt))
        self.record_path = self.root/"queue"/("transaction-"+self.transaction+".json")
        follow.atomic_json(self.record_path,{"schema":follow.RECORD_SCHEMA,"transaction":self.transaction,
            "state":"staged","observed_at":"x","updated_at":"x","package_set_digest":host,
            "changes":[{"name":"glibc","version":"1"}],"release_digest":"c"*64,
            "detail":{"receipt":str(self.receipt_path),"package":str(self.package),"generation":self.generation,"host_digest":host},
            "last_error":None,"history":[{"state":"observed","at":"x"},{"state":"staged","at":"x"}]})
        self.calls=[]

    def install_call(self, **kwargs):
        def runner(argv, **options): self.calls.append((argv,options)); return subprocess.CompletedProcess(argv,0,"","")
        return installer.install(self.transaction,queue=self.root/"queue",receipts=self.root/"work",
            packages=self.root/"packages",dbpath=self.root/"db",generations=self.root/"generations",
            runner=runner,expected_uid=os.getuid(),**kwargs)

    def test_verified_package_uses_fixed_pacman_argv_and_marks_installed(self):
        self.assertEqual(self.install_call(), self.generation)
        self.assertEqual(self.calls[0][0][:4],["/usr/bin/pacman","-U","--noconfirm","--"])
        self.assertEqual(json.loads(self.record_path.read_text())["state"],"installed")

    def test_tampered_package_and_changed_host_are_rejected_before_pacman(self):
        self.package.write_bytes(b"tampered")
        with self.assertRaisesRegex(installer.InstallError,"hash"):
            self.install_call()
        self.assertEqual(self.calls,[])

        self.package.write_bytes(b"package")
        extra=self.root/"db/extra-1"; extra.mkdir(); (extra/"desc").write_text("%NAME%\nextra\n\n%VERSION%\n1\n")
        with self.assertRaisesRegex(installer.InstallError,"host"):
            self.install_call()
        self.assertEqual(self.calls,[])

    def test_receipt_path_escape_and_package_symlink_are_rejected(self):
        outside=self.root/"outside.json"; outside.write_text(json.dumps(self.receipt))
        record=json.loads(self.record_path.read_text()); record["detail"]["receipt"]=str(outside)
        follow.atomic_json(self.record_path,record)
        with self.assertRaisesRegex(installer.InstallError,"outside"):
            self.install_call()

    def test_downgrade_policy_rejects_before_pacman(self):
        desc=self.root/"db/luminophore-compositor-1"; desc.mkdir()
        (desc/"desc").write_text("%NAME%\nluminophore-compositor\n\n%VERSION%\n1.0.0\n")
        host=follow.identity(follow.package_set(self.root/"db")); self.receipt["host_digest"]=host
        self.receipt_path.write_text(json.dumps(self.receipt))
        record=json.loads(self.record_path.read_text()); record["package_set_digest"]=host
        follow.atomic_json(self.record_path,record)
        with self.assertRaisesRegex(installer.InstallError,"downgrade"):
            self.install_call(compare=lambda candidate,current:-1)
        self.assertEqual(self.calls,[])


if __name__ == "__main__": unittest.main()
