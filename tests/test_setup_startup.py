import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('setup_startup', Path(__file__).parents[1] / 'tools/setup-startup.py')
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


class StartupInstallTests(unittest.TestCase):
    def test_fresh_install_stages_before_root_install_and_never_reboots(self):
        with patch.object(startup, 'preflight'), patch.object(startup, 'STATE') as state, patch.object(startup, 'run') as run:
            state.exists.return_value = False
            startup.install()
            calls = [tuple(map(str, c.args)) for c in run.call_args_list]
            self.assertEqual(calls[0], ('sudo', '-v'))
            self.assertEqual(calls[1][2:4], ('stage', '--output'))
            self.assertEqual(calls[2][3:5], ('install', '--staged'))
            self.assertEqual(calls[1][-1], calls[2][-1])
            self.assertEqual(calls[3][-2:], ('display-handoff', '--experimental'))
            self.assertEqual(calls[4][-1], 'x-background')
            self.assertEqual(calls[6][-2:], ('--background', 'hdf'))
            self.assertFalse(any('reboot' in c for c in calls))

    def test_existing_installation_is_not_staged_or_backed_up_again(self):
        with patch.object(startup, 'preflight'), patch.object(startup, 'STATE') as state, patch.object(startup, 'run') as run:
            state.exists.return_value = True
            startup.install()
            self.assertFalse(any('stage' in c.args or 'install' in c.args for c in run.call_args_list))
            self.assertTrue(any('display-handoff' in c.args for c in run.call_args_list))

    def test_failed_preflight_prevents_all_installation_commands(self):
        with patch.object(startup, 'preflight', side_effect=RuntimeError('unsupported')), patch.object(startup, 'run') as run:
            with self.assertRaises(RuntimeError):
                startup.install()
            run.assert_not_called()

    def test_failed_system_step_prevents_remaining_steps(self):
        def fail_handoff(*args):
            if 'display-handoff' in args:
                raise startup.subprocess.CalledProcessError(1, args)
        with patch.object(startup, 'preflight'), patch.object(startup, 'STATE') as state, patch.object(startup, 'run', side_effect=fail_handoff) as run:
            state.exists.return_value = True
            with self.assertRaises(startup.subprocess.CalledProcessError):
                startup.install()
            self.assertEqual(len(run.call_args_list), 2)

    def test_unsupported_system_is_rejected_before_commands(self):
        with patch.object(startup.os, 'geteuid', return_value=1000), patch.object(startup, 'RELEASE') as release, patch.object(startup, 'BIOS') as bios, patch.object(startup, 'run') as run:
            release.read_text.return_value = '# R36 (release), REVISION: 4.4,'
            bios.read_text.return_value = '36.4.4'
            with self.assertRaises(RuntimeError):
                startup.preflight()
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
