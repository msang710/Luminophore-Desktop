import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PortalBuildTests(unittest.TestCase):
    def test_portal_build_has_locked_independent_source_recipe(self):
        self.assertTrue((ROOT / 'scripts/build-portal.py').is_file())
        self.assertTrue((ROOT / 'portal/upstream.json').is_file())
        self.assertTrue((ROOT / 'portal/namespace.patch').is_file())

    @unittest.skipUnless(os.environ.get('LUMINOPHORE_TEST_PORTAL_SOURCE'), 'pinned upstream source required')
    def test_prepared_portal_owns_distinct_bus_and_control_path(self):
        script = ROOT / 'scripts/build-portal.py'
        self.assertTrue(script.is_file(), 'portal builder required')
        spec = importlib.util.spec_from_file_location('portal_builder', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            target = module.prepare(Path(os.environ['LUMINOPHORE_TEST_PORTAL_SOURCE']), Path(directory) / 'source')
            manager = (target / 'src/core/PortalManager.cpp').read_text()
            self.assertIn('org.freedesktop.impl.portal.desktop.luminophore', manager)
            self.assertNotIn('"org.freedesktop.impl.portal.desktop.hyprland"', manager)
            picker = (target / 'src/shared/ScreencopyShared.cpp').read_text()
            self.assertIn('LUMINOPHORE_INSTANCE_SIGNATURE', picker)
            self.assertNotIn('getenv("HYPRLAND_INSTANCE_SIGNATURE")', picker)
            self.assertIn('/proc/self/exe', picker)
