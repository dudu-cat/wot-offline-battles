import unittest
from unittest import mock

import lan_battle_server as server


class WindowsFirewallTest(unittest.TestCase):
    def test_source_process_never_checks_or_changes_firewall(self):
        with mock.patch.object(
                server, "_is_frozen_windows_executable", return_value=False), \
                mock.patch.object(
                    server, "_windows_firewall_rule_exists") as exists, \
                mock.patch.object(
                    server, "_request_windows_firewall_rule") as request:
            self.assertFalse(server._ensure_windows_firewall_rule(28782))

        exists.assert_not_called()
        request.assert_not_called()

    def test_existing_rule_does_not_request_uac_again(self):
        with mock.patch.object(
                server, "_is_frozen_windows_executable", return_value=True), \
                mock.patch.object(
                    server, "_windows_firewall_rule_exists",
                    return_value=True) as exists, \
                mock.patch.object(
                    server, "_request_windows_firewall_rule") as request:
            self.assertTrue(server._ensure_windows_firewall_rule(28782))

        exists.assert_called_once()
        request.assert_not_called()

    def test_missing_rule_requests_narrow_elevated_netsh_rule(self):
        calls = []

        def shell_execute(*args):
            calls.append(args)
            return 42

        path = r"C:\Games\WoT LAN\WoT-0.8.2-LAN-Server.exe"
        rule_name = server._windows_firewall_rule_name(path, 28782)

        self.assertTrue(server._request_windows_firewall_rule(
            rule_name, path, 28782, shell_execute=shell_execute))
        self.assertEqual(1, len(calls))
        _, verb, executable, arguments, _, _ = calls[0]
        self.assertEqual("runas", verb)
        self.assertEqual("netsh.exe", executable)
        self.assertIn("dir=in", arguments)
        self.assertIn("action=allow", arguments)
        self.assertIn("protocol=TCP", arguments)
        self.assertIn("localport=28782", arguments)
        self.assertIn("remoteip=any", arguments)
        self.assertIn("program=" + path, arguments)

    def test_rule_identity_is_stable_across_windows_path_case(self):
        first = server._windows_firewall_rule_name(
            r"C:\Games\WoT\server.exe", 28782)
        second = server._windows_firewall_rule_name(
            r"c:/games/wot/SERVER.EXE", 28782)
        self.assertEqual(first, second)

    def test_broader_scope_uses_a_new_rule_identity(self):
        path = r"C:\Games\WoT\server.exe"
        local_subnet = server._windows_firewall_rule_name(
            path, 28782, "localsubnet")
        any_remote = server._windows_firewall_rule_name(
            path, 28782, "any")
        self.assertNotEqual(local_subnet, any_remote)

    def test_uac_cancellation_is_nonfatal(self):
        self.assertFalse(server._request_windows_firewall_rule(
            "test", r"C:\server.exe", 28782,
            shell_execute=lambda *args: 5))


if __name__ == "__main__":
    unittest.main()
