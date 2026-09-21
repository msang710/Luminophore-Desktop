from pathlib import Path
import subprocess
import tempfile
import unittest

from luminophore_runtime.greeter_trial import Trial
from luminophore_runtime.cli import parser


class GreeterTrialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.commands = []
        self.old = b'[default_session]\ncommand = "Hyprland"\nuser = "louise"\n'
        self.new = b'[default_session]\ncommand = "luminophore-greeter-session"\nuser = "greeter"\n'
        self.trial = Trial(self.root, self.control)
        self.trial.config.parent.mkdir(parents=True)
        self.trial.config.write_bytes(self.old)
        self.trial.candidate.parent.mkdir(parents=True)
        self.trial.candidate.write_bytes(self.new)

    def control(self, *args):
        self.commands.append(args)
        if args == ('enable', '--now', self.trial.timer):
            self.assertEqual(self.trial.config.read_bytes(), self.old)
            self.assertTrue(self.trial.pending.is_file())
            self.assertTrue((self.trial.state / 'guard.py').is_file())

    def test_guard_armed_before_switch_and_survives_caller_loss(self):
        self.trial.start()
        self.assertEqual(self.trial.config.read_bytes(), self.new)
        # A fresh process can restore using only persisted state.
        Trial(self.root, self.control).restore()
        self.assertEqual(self.trial.config.read_bytes(), self.old)
        self.assertFalse(self.trial.pending.exists())
        self.assertEqual(self.commands[-1], ('disable', '--now', self.trial.timer))

    def test_unavailable_watchdog_never_changes_login_config(self):
        def fail(*args):
            if args[0] == 'enable':
                raise subprocess.CalledProcessError(1, args)
        trial = Trial(self.root, fail)
        with self.assertRaises(subprocess.CalledProcessError):
            trial.start()
        self.assertEqual(trial.config.read_bytes(), self.old)

    def test_interrupted_restart_preserves_recovery_for_independent_guard(self):
        def interrupted(*args):
            self.control(*args)
            if args == ('restart', 'greetd.service'):
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            Trial(self.root, interrupted).start()
        self.assertTrue(self.trial.pending.is_file())
        self.assertEqual(self.trial.config.read_bytes(), self.new)
        self.trial.restore()
        self.assertEqual(self.trial.config.read_bytes(), self.old)

    def test_second_trial_cannot_overwrite_original_fallback(self):
        self.trial.start()
        with self.assertRaisesRegex(RuntimeError, 'pending'):
            self.trial.start()
        self.assertEqual(self.trial.pending.read_bytes(), self.old)

    def test_confirm_requires_explicit_call_and_prevents_late_restore(self):
        self.trial.start()
        self.assertTrue(self.trial.pending.exists())
        self.trial.confirm()
        self.trial.restore()
        self.assertEqual(self.trial.config.read_bytes(), self.new)

    def test_failed_restore_restart_retains_pending_for_retry(self):
        self.trial.start()
        def fail(*args):
            if args[0] == 'restart':
                raise subprocess.CalledProcessError(1, args)
        with self.assertRaises(subprocess.CalledProcessError):
            Trial(self.root, fail).restore()
        self.assertEqual(self.trial.config.read_bytes(), self.old)
        self.assertTrue(self.trial.pending.exists())

    def test_guard_is_enabled_at_boot_and_has_bounded_delay(self):
        self.trial.start()
        timer = (self.trial.units / self.trial.timer).read_text()
        service = (self.trial.units / self.trial.service).read_text()
        self.assertIn('WantedBy=timers.target', timer)
        self.assertIn('OnActiveSec=120', timer)
        self.assertIn('Restart=on-failure', service)
        self.assertIn('/var/lib/luminophore/greeter-trial/guard.py restore', service)

    def test_trial_commands_are_explicit_cli_actions(self):
        for action in ('start', 'restore', 'confirm'):
            name = 'greeter-trial-' + action
            self.assertEqual(parser().parse_args([name]).action, name)


if __name__ == '__main__':
    unittest.main()
