from pathlib import Path
import subprocess

from test_artifact import ArtifactFixture


class PackageTests(ArtifactFixture):
    def test_build_input_owns_only_independent_paths(self):
        from luminophore_runtime.packaging import prepare
        release = self.pack()
        output = self.root / 'package'
        prepare(release, output)
        root = output / 'payload'
        for name in ['luminophore-session', 'luminophore-compositor', 'luminophorectl']:
            self.assertTrue((root / 'usr/bin' / name).is_file())
        self.assertFalse((root / 'usr/bin/Hyprland').exists())
        self.assertFalse((root / 'usr/bin/hyprctl').exists())
        result = subprocess.run(['bash', '-c', 'source "$1"; declare -p provides conflicts replaces',
                                 'test', str(output / 'PKGBUILD')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('hyprland', result.stdout)
        self.assertTrue((root / 'usr/share/wayland-sessions/luminophore.desktop').is_file())
        settings = root / 'usr/share/applications/io.github.msang710.LuminophoreSettings.desktop'
        self.assertIn('Exec=/usr/bin/luminophore-shell settings', settings.read_text())
        for profile in ('eco', 'balanced', 'gaming'):
            self.assertTrue((root / f'usr/lib/tuned/profiles/luminophore-{profile}-capped/tuned.conf').is_file())
        self.assertTrue((root / 'usr/lib/luminophore/releases' / release.name / 'release.json').is_file())
        self.assertFalse((root / 'var').exists())
        self.assertFalse((root / 'usr/share/libalpm/hooks/05-luminophore-runtime.hook').exists())
        self.assertFalse((root / 'usr/lib/luminophore/luminophore-update-guard').exists())
        self.assertTrue((root / 'usr/share/libalpm/hooks/04-luminophore-remove.hook').is_file())
