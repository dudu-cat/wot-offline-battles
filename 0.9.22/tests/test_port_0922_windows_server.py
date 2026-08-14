from pathlib import Path
import sys
import unittest
from unittest import mock


PORT_ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = PORT_ROOT / 'server'
sys.path.insert(0, str(SERVER_ROOT))

import windows_server  # noqa: E402


class WindowsServerLauncherTests(unittest.TestCase):
    def test_double_click_entry_uses_fixed_zero_configuration_contract(self):
        run_server = mock.Mock()
        with mock.patch.object(
                windows_server, '_load_server',
                return_value=('server_random', run_server)):
            with mock.patch.object(sys, 'argv', ['server.exe', '--port', '1']):
                self.assertEqual(0, windows_server.main())

        run_server.assert_called_once_with(
            '0.0.0.0',
            28782,
            'server_random',
            30,
        )

    def test_startup_error_returns_failure_without_hiding_the_traceback(self):
        run_server = mock.Mock(side_effect=OSError('busy'))
        with mock.patch.object(
                windows_server, '_load_server',
                return_value=('server_random', run_server)):
            with mock.patch.object(windows_server, '_pause_after_error') as pause:
                with mock.patch.object(windows_server.traceback,
                                       'print_exc') as print_exc:
                    self.assertEqual(1, windows_server.main())

        print_exc.assert_called_once_with()
        pause.assert_called_once_with()

    def test_packaged_import_error_is_visible_and_keeps_console_open(self):
        with mock.patch.object(
                windows_server, '_load_server',
                side_effect=ImportError('missing bundled module')):
            with mock.patch.object(windows_server, '_pause_after_error') as pause:
                with mock.patch.object(windows_server.traceback,
                                       'print_exc') as print_exc:
                    self.assertEqual(1, windows_server.main())

        print_exc.assert_called_once_with()
        pause.assert_called_once_with()

    def test_windows_build_dependency_is_pinned(self):
        requirements = (
            SERVER_ROOT / 'requirements-windows-build.txt'
        ).read_text(encoding='utf-8').splitlines()
        self.assertEqual(['pyinstaller==6.21.0'], requirements)

    def test_build_recreates_and_verifies_the_exact_delivery_directory(self):
        source = (SERVER_ROOT / 'build_windows_server.ps1').read_text(
            encoding='utf-8')
        remove = 'Remove-Item -LiteralPath $DistRoot -Recurse -Force'
        create = 'New-Item -ItemType Directory -Force -Path $DistRoot'
        package = 'python -m PyInstaller'

        self.assertIn(remove, source)
        self.assertLess(source.index(remove), source.index(create))
        self.assertLess(source.index(create), source.index(package))
        self.assertIn(
            '$ExpectedFiles = @("README.txt", '
            '"WoT-0.9.22-LAN-Server.exe")', source)
        self.assertIn('Get-ChildItem -LiteralPath $DistRoot -Force', source)
        self.assertNotIn('Remove-Item -LiteralPath $PortRoot', source)

    def test_workflow_here_strings_are_at_the_powershell_block_baseline(self):
        workflow = (PORT_ROOT.parent / '.github' / 'workflows' /
                    'tests.yml').read_text(encoding='utf-8')

        self.assertIn('python-version: "3.11.9"', workflow)
        self.assertIn(
            "\n          $ProtocolProbe = @'\n          import json\n",
            workflow)
        self.assertIn("\n          '@\n\n          $process", workflow)
        self.assertNotIn("\n              @'\n", workflow)

    def test_windows_readme_carries_source_and_runtime_license_notices(self):
        readme = (SERVER_ROOT / 'WINDOWS_SERVER_README.txt').read_text(
            encoding='utf-8')

        for required in (
                'GNU GPL',
                '/tree/v0.4.0',
                'CPython 3.11.9',
                'docs.python.org/3.11/license.html',
                'PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2',
                'Copyright (c) 2001, 2002, 2003, 2004, 2005',
                '2023 Python Software Foundation;',
                'All Rights Reserved',
                'BEOPEN.COM LICENSE AGREEMENT FOR PYTHON 2.0',
                'CNRI LICENSE AGREEMENT FOR PYTHON 1.6.1',
                'CWI LICENSE AGREEMENT FOR PYTHON 0.9.0 THROUGH 1.2',
                'PyInstaller 6.21.0',
                'v6.21.0/COPYING.txt'):
            self.assertIn(required, readme)


if __name__ == '__main__':
    unittest.main()
