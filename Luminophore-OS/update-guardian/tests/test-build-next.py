from importlib.util import module_from_spec, spec_from_file_location
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location("build_next", ROOT / "build-next.py")
build_next = module_from_spec(SPEC)
SPEC.loader.exec_module(build_next)
FSPEC = spec_from_file_location("desktop_follow", ROOT / "desktop-follow.py")
follow = module_from_spec(FSPEC)
FSPEC.loader.exec_module(follow)


class BuildNextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("queue", "work", "packages"):
            (self.root / name).mkdir()
        self.transaction = "a" * 64
        self.record = self.root / "queue" / f"transaction-{self.transaction}.json"
        follow.atomic_json(self.record, {"schema": follow.RECORD_SCHEMA, "transaction": self.transaction,
            "state": "observed", "observed_at": "x", "updated_at": "x",
            "package_set_digest": "b" * 64, "changes": [{"name":"glibc","version":"1"}],
            "release_digest": "c" * 64, "detail": {}, "last_error": None,
            "history": [{"state":"observed","at":"x"}]})

    def worker(self, name, body):
        path = self.root / name
        path.write_text("#!/bin/sh\nset -eu\n" + body); path.chmod(0o700)
        return path

    def args(self, evaluator, builder):
        return type("Args", (), {"queue":self.root/"queue", "work":self.root/"work",
            "packages":self.root/"packages", "evaluator":evaluator, "builder":builder, "timeout":5})

    def state(self): return json.loads(self.record.read_text())["state"]

    def test_compatible_update_does_not_build(self):
        evaluator = self.worker("eval", "printf '%s' '{\"status\":\"compatible\"}' > \"$2\"\n")
        builder = self.worker("builder", "touch '" + str(self.root/"built") + "'\n")
        self.assertEqual(build_next.run(self.args(evaluator,builder)), 0)
        self.assertEqual(self.state(), "compatible"); self.assertFalse((self.root/"built").exists())

    def test_rebuild_requires_whole_desktop_and_matching_host(self):
        package = self.root / "packages/luminophore.pkg.tar.zst"; package.write_bytes(b"package")
        sha = hashlib.sha256(b"package").hexdigest()
        evaluator = self.worker("eval", "printf '%s' '{\"status\":\"rebuild-required\"}' > \"$2\"\n")
        receipt = {"schema":"luminophore-follow-build/v1","transaction":self.transaction,
            "host_digest":"b"*64,"generation":"d"*64,"version":"0.2.0","package":package.name,
            "package_sha256":sha,"components":["compositor","control","shell","greeter","portal"],
            "provenance":{},"sbom_digest":"e"*64}
        builder = self.worker("builder", "cat > \"$2\" <<'EOF'\n"+json.dumps(receipt)+"\nEOF\n")
        self.assertEqual(build_next.run(self.args(evaluator,builder)), 0)
        self.assertEqual(self.state(), "staged")

    def test_wrong_host_or_partial_desktop_fails_without_installing(self):
        package = self.root / "packages/bad.pkg"; package.write_bytes(b"bad")
        evaluator = self.worker("eval", "printf '%s' '{\"status\":\"rebuild-required\"}' > \"$2\"\n")
        receipt = {"schema":"luminophore-follow-build/v1","transaction":self.transaction,
            "host_digest":"0"*64,"generation":"d"*64,"version":"0.2.0","package":package.name,
            "package_sha256":hashlib.sha256(b"bad").hexdigest(),"components":["compositor"],
            "provenance":{},"sbom_digest":"e"*64}
        builder = self.worker("builder", "cat > \"$2\" <<'EOF'\n"+json.dumps(receipt)+"\nEOF\n")
        build_next.run(self.args(evaluator,builder))
        self.assertEqual(self.state(), "failed")


if __name__ == "__main__": unittest.main()
