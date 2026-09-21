import importlib.util
from pathlib import Path
import tempfile
import unittest


class ComponentStagingTests(unittest.TestCase):
    def test_offline_cmake_uses_locked_glaze_source(self):
        import subprocess
        script = Path(__file__).resolve().parents[1] / 'scripts/build-components.py'
        spec = importlib.util.spec_from_file_location('component_builder', script)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, glaze = root / 'compositor', root / 'glaze'
            source.mkdir(); glaze.mkdir()
            (source / 'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.30)\nproject(offline NONE)\ninclude(FetchContent)\nFetchContent_Declare(glaze GIT_REPOSITORY https://invalid.example/glaze)\nFetchContent_MakeAvailable(glaze)\nif(NOT TARGET pinned_glaze)\nmessage(FATAL_ERROR "missing vendored source")\nendif()\n')
            (glaze / 'CMakeLists.txt').write_text('add_library(pinned_glaze INTERFACE)\n')
            result = subprocess.run(builder.configure_command(source, root / 'build', root),
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_compositor_generators_write_to_work_copy(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/build-components.py'
        spec = importlib.util.spec_from_file_location('component_builder', script)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            (source / 'src').mkdir(parents=True)
            (source / 'src/version.h').write_text('original')
            target = builder.prepare_compositor(source, root / 'work')
            (target / 'src/version.h').write_text('generated')
            self.assertEqual((source / 'src/version.h').read_text(), 'original')

    def test_shell_copy_excludes_development_runtime_and_preserves_assets(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/build-components.py'
        spec = importlib.util.spec_from_file_location('component_builder', script)
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / 'source', root / 'staged'
            for name in ['luminophore_shell/__main__.py', 'luminophore_shell/assets/image.png',
                         'luminophore_shell/profile-runtime/bin/python',
                         'luminophore_shell/__pycache__/main.pyc', 'tests/test_source.py',
                         'native/helper.c', 'scripts/build-glow-layer', 'LICENSE', 'luminophore-shell']:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture')
            builder.prepare_shell(source, target)
            self.assertTrue((target / 'luminophore_shell/assets/image.png').exists())
            self.assertTrue((target / 'native/helper.c').exists())
            self.assertFalse((target / 'luminophore_shell/profile-runtime').exists())
            self.assertFalse((target / 'luminophore_shell/__pycache__').exists())
            self.assertFalse((target / 'tests').exists())

    def test_component_builder_refuses_non_build_working_directory(self):
        import subprocess
        import sys
        script = Path(__file__).resolve().parents[1] / 'scripts/build-components.py'
        result = subprocess.run([sys.executable, '-B', str(script)], cwd='/tmp', capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('locked build', result.stderr)
