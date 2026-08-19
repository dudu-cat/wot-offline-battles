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
                return_value=('server_random', run_server)), \
                mock.patch.object(
                    windows_server, '_ensure_windows_firewall_rule') as ensure:
            with mock.patch.object(sys, 'argv', ['server.exe', '--port', '1']):
                self.assertEqual(0, windows_server.main())

        ensure.assert_called_once_with(28782)
        run_server.assert_called_once_with(
            '0.0.0.0',
            28782,
            'server_random',
            30,
            'client',
        )

    def test_firewall_request_precedes_server_bind(self):
        events = []

        def ensure(port):
            events.append(('firewall', port))

        def run_server(host, port, map_name, max_players, authority_mode):
            events.append(('server', host, port, map_name, max_players,
                           authority_mode))

        with mock.patch.object(
                windows_server, '_load_server',
                return_value=('server_random', run_server)), \
                mock.patch.object(
                    windows_server, '_ensure_windows_firewall_rule',
                    side_effect=ensure):
            self.assertEqual(0, windows_server.main())

        self.assertEqual([
            ('firewall', 28782),
            ('server', '0.0.0.0', 28782, 'server_random', 30, 'client'),
        ], events)

    def test_source_process_never_checks_or_changes_firewall(self):
        with mock.patch.object(
                windows_server, '_is_frozen_windows_executable',
                return_value=False), \
                mock.patch.object(
                    windows_server, '_windows_firewall_rule_exists') as exists, \
                mock.patch.object(
                    windows_server, '_request_windows_firewall_rule') as request:
            self.assertFalse(
                windows_server._ensure_windows_firewall_rule(28782))

        exists.assert_not_called()
        request.assert_not_called()

    def test_existing_rule_does_not_request_uac_again(self):
        with mock.patch.object(
                windows_server, '_is_frozen_windows_executable',
                return_value=True), \
                mock.patch.object(
                    windows_server, '_windows_firewall_rule_exists',
                    return_value=True) as exists, \
                mock.patch.object(
                    windows_server, '_request_windows_firewall_rule') as request:
            self.assertTrue(
                windows_server._ensure_windows_firewall_rule(28782))

        exists.assert_called_once()
        request.assert_not_called()

    def test_missing_rule_requests_narrow_elevated_netsh_rule(self):
        calls = []

        def shell_execute(*args):
            calls.append(args)
            return 42

        path = r'C:\Games\WoT LAN\WoT-0.9.22-LAN-Server.exe'
        netsh_path = r'C:\Windows\System32\netsh.exe'
        rule_name = windows_server._windows_firewall_rule_name(path, 28782)

        self.assertTrue(windows_server._request_windows_firewall_rule(
            rule_name, path, 28782, shell_execute=shell_execute,
            netsh_path=netsh_path))
        self.assertEqual(1, len(calls))
        _, verb, executable, arguments, _, _ = calls[0]
        self.assertEqual('runas', verb)
        self.assertEqual(netsh_path, executable)
        self.assertIn('dir=in', arguments)
        self.assertIn('action=allow', arguments)
        self.assertIn('protocol=TCP', arguments)
        self.assertIn('localport=28782', arguments)
        self.assertIn('remoteip=any', arguments)
        self.assertIn('program=' + path, arguments)

    def test_rule_identity_is_stable_across_windows_path_case(self):
        first = windows_server._windows_firewall_rule_name(
            r'C:\Games\WoT\server.exe', 28782)
        second = windows_server._windows_firewall_rule_name(
            r'c:/games/wot/SERVER.EXE', 28782)
        self.assertEqual(first, second)
        self.assertFalse(set('*?[') & set(first))

    def test_rule_lookup_is_bounded_and_uses_literal_safe_identity(self):
        result = mock.Mock(returncode=0)
        runner = mock.Mock(return_value=result)
        powershell_path = (
            r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe')
        rule_name = windows_server._windows_firewall_rule_name(
            r'C:\Games\WoT\server.exe', 28782)

        self.assertTrue(windows_server._windows_firewall_rule_exists(
            rule_name, runner=runner, powershell_path=powershell_path))

        args, kwargs = runner.call_args
        self.assertEqual(powershell_path, args[0][0])
        script = args[0][-1]
        self.assertIn(rule_name, script)
        self.assertIn("$_.Direction -eq 'Inbound'", script)
        self.assertIn("$_.Enabled -eq 'True'", script)
        self.assertIn("$_.Action -eq 'Allow'", script)
        self.assertNotIn('-Direction Inbound', script)
        self.assertNotIn('-Enabled True', script)
        self.assertNotIn('-Action Allow', script)
        self.assertEqual(windows_server.FIREWALL_QUERY_TIMEOUT_SECONDS,
                         kwargs['timeout'])

    def test_uac_cancellation_is_nonfatal(self):
        self.assertFalse(windows_server._request_windows_firewall_rule(
            'test', r'C:\server.exe', 28782,
            shell_execute=lambda *args: 5,
            netsh_path=r'C:\Windows\System32\netsh.exe'))

    def test_netsh_path_comes_from_windows_system_directory(self):
        calls = []

        def get_system_directory(buffer, size):
            calls.append(size)
            buffer.value = r'C:\Windows\System32'
            return len(buffer.value)

        self.assertEqual(
            r'C:\Windows\System32\netsh.exe',
            windows_server._windows_system_netsh_path(
                get_system_directory=get_system_directory))
        self.assertEqual([32768], calls)

    def test_powershell_path_comes_from_windows_system_directory(self):
        def get_system_directory(buffer, size):
            buffer.value = r'C:\Windows\System32'
            return len(buffer.value)

        self.assertEqual(
            r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',
            windows_server._windows_system_path(
                r'WindowsPowerShell\v1.0\powershell.exe',
                get_system_directory=get_system_directory))

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
        self.assertIn('Get-NetFirewallRule', workflow)
        self.assertIn('Get-NetFirewallApplicationFilter', workflow)
        self.assertIn('Get-NetFirewallPortFilter', workflow)
        self.assertIn('Remove-NetFirewallRule', workflow)
        self.assertIn('$_.Direction -eq "Inbound"', workflow)
        self.assertNotIn('-Direction Inbound', workflow)
        self.assertNotIn('-Enabled True', workflow)
        self.assertNotIn('-Action Allow', workflow)
        self.assertIn(
            'Packaged server did not create its firewall rule', workflow)
        self.assertIn(
            'WoT 0.9.22 LAN Server TCP 28782 - $digest', workflow)

    def test_windows_readme_carries_source_and_runtime_license_notices(self):
        readme = (SERVER_ROOT / 'WINDOWS_SERVER_README.txt').read_text(
            encoding='utf-8')

        for required in (
                'GNU GPL',
                '/tree/v0.5.0',
                'any remote address/profile',
                'trusted-LAN server',
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
