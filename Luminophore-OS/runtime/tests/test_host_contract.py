from pathlib import Path
import json
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from luminophore_runtime.common import ContractError, digest
from luminophore_runtime import host_contract


class HostContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "usr/lib").mkdir(parents=True)
        self.library = self.root / "usr/lib/libshared.so.1"
        self.library.write_bytes(b"baseline")
        self.info = {
            "needed": [], "soname": "libshared.so.1", "interpreter": None,
            "search": [], "versions": ["ABI_1", "ABI_2"],
        }
        self.contract = host_contract.create(
            {"usr/lib/libshared.so.1": digest(self.library)},
            {"bin/private": {"needed": ["libshared.so.1"], "soname": None,
                             "interpreter": "/usr/lib/ld.so", "search": [],
                             "versions": ["ABI_1"]}},
            self.root,
            inspect_fn=lambda _path: dict(self.info),
        )

    def evaluate(self, **kwargs):
        return host_contract.evaluate(
            self.contract, self.root, inspect_fn=lambda _path: dict(self.info), **kwargs
        )

    def test_content_only_patch_is_compatible(self):
        self.library.write_bytes(b"patched")

        result = self.evaluate()

        self.assertEqual(result["status"], "compatible")
        self.assertIn("content-changed-compatible", result["reasons"])

    def test_missing_soname_or_required_symbol_is_incompatible(self):
        for key, value in (("soname", "other.so.1"), ("versions", ["ABI_2"])):
            with self.subTest(key=key):
                previous = self.info[key]
                self.info[key] = value
                self.assertEqual(self.evaluate()["status"], "incompatible")
                self.info[key] = previous

    def test_protocol_and_capability_removal_is_incompatible(self):
        contract = host_contract.with_requirements(
            self.contract,
            protocols={"wayland": 3},
            capabilities={"dmabuf": True},
        )

        result = host_contract.evaluate(
            contract, self.root, protocols={"wayland": 2},
            capabilities={"dmabuf": False}, inspect_fn=lambda _path: dict(self.info),
        )

        self.assertEqual(result["status"], "incompatible")
        self.assertTrue(any("wayland" in reason for reason in result["reasons"]))
        self.assertTrue(any("dmabuf" in reason for reason in result["reasons"]))

    def test_explicit_change_policy_requests_rebuild(self):
        self.library.write_bytes(b"patched")
        contract = host_contract.with_change_policy(
            self.contract, {"usr/lib/libshared.so.1": "rebuild-required"}
        )

        result = host_contract.evaluate(
            contract, self.root, inspect_fn=lambda _path: dict(self.info)
        )

        self.assertEqual(result["status"], "rebuild-required")

    def test_mixed_gpu_vendor_stack_is_incompatible(self):
        contract = host_contract.with_graphics_stack(
            self.contract,
            {"loader": "neutral", "egl-vendor-a": "mesa", "egl-vendor-b": "nvidia"},
        )

        result = host_contract.evaluate(
            contract, self.root, inspect_fn=lambda _path: dict(self.info)
        )

        self.assertEqual(result["status"], "incompatible")
        self.assertTrue(any("graphics" in reason for reason in result["reasons"]))

    def test_manifest_tampering_fails_closed(self):
        self.contract["providers"]["usr/lib/libshared.so.1"]["required_versions"] = []
        with self.assertRaisesRegex(ContractError, "digest"):
            self.evaluate()

    def test_checked_in_cachyos_boundary_keeps_hypr_and_abseil_private(self):
        profile = json.loads(
            (Path(__file__).resolve().parents[1] / "profiles/cachyos-providers-2026-09-20.json")
            .read_text(encoding="utf-8")
        )

        for soname in (
            "libaquamarine.so.14", "libhyprcursor.so.0", "libhyprlang.so.2",
            "libhyprutils.so.13", "libabsl_base.so.2608.0.0",
        ):
            self.assertIn(soname, profile["required_private"])
        for soname in ("ld-linux-x86-64.so.2", "libc.so.6", "libEGL.so.1", "libgbm.so.1"):
            self.assertIn(soname, profile["system"])


if __name__ == "__main__":
    unittest.main()
