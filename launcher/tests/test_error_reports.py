"""Session-boundary and privacy tests for one-click error reports."""

import datetime
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

import core
import error_reports


class ErrorReportTest(unittest.TestCase):
    SESSION_1 = "20260823T120000Z-111111111111"
    SESSION_2 = "20260823T130000Z-222222222222"

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.game = os.path.join(self.root, "game")
        os.makedirs(self.game)
        self.settings = os.path.join(self.root, "state", "launcher.json")
        self.settings_patch = mock.patch.object(
            core, "settings_path", return_value=self.settings)
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.server_log_patch = mock.patch.object(
            core, "server_log_path", return_value=os.path.join(
                self.root, "state", "server.log"))
        self.server_log_patch.start()
        self.addCleanup(self.server_log_patch.stop)

    def _game_log(self, role):
        return os.path.join(
            self.game, error_reports._GAME_LOG_FILENAMES[role])

    @staticmethod
    def _write(path, payload, mode="wb"):
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, mode) as stream:
            stream.write(payload)

    @staticmethod
    def _archive(report):
        with zipfile.ZipFile(report["path"], "r") as archive:
            return dict((name, archive.read(name))
                        for name in archive.namelist())

    def test_single_player_report_contains_only_this_session_log_slices(self):
        visible = self._game_log(error_reports.ROLE_VISIBLE_CLIENT)
        worker = self._game_log(error_reports.ROLE_HIDDEN_WORKER)
        starter = self._game_log(error_reports.ROLE_HIDDEN_WORKER_STARTER)
        self._write(visible, b"old visible\n")
        self._write(worker, b"old worker\n")
        self._write(starter, b"old starter line that is much longer\n" * 8)
        self._write(os.path.join(self.game, "preferences.xml"), b"private")
        self._write(os.path.join(
            self.game, "mods", "configs", "offline_lan_0922",
            "vehicle_profiles.json"), b"private")

        session = error_reports.begin_session(
            self.game, needs_worker=True, local_server=True,
            session_id=self.SESSION_1, started_at="start")
        server = error_reports.attach_server(session, dedicated=True)
        error_reports.expect_worker_starter_reset(session)
        self._write(visible, b"new visible\n", "ab")
        self._write(worker, b"new worker\n", "ab")
        replacement = starter + ".new"
        self._write(replacement, b"new starter\n")
        os.replace(replacement, starter)
        self._write(server, b"new server\n")
        self.assertTrue(error_reports.finalize_session(
            session, ended_at="end"))

        self._write(visible, b"future visible\n", "ab")
        self._write(worker, b"future worker\n", "ab")
        self._write(server, b"future server\n", "ab")
        report = error_reports.create_report(
            now=datetime.datetime(2026, 8, 23, 12, 30, 0))
        payloads = self._archive(report)

        self.assertEqual({
            "server.log": b"new server\n",
            "visible-client.log": b"new visible\n",
            "hidden-worker.log": b"new worker\n",
            "hidden-worker-starter.log": b"new starter\n",
        }, payloads)
        self.assertEqual((), report["missing"])
        self.assertEqual((), report["notRun"])
        self.assertNotIn("preferences.xml", payloads)
        self.assertNotIn("vehicle_profiles.json", payloads)
        self.assertEqual(
            os.path.join(self.root, "state", "reports"),
            os.path.dirname(report["path"]))

    def test_new_empty_session_never_falls_back_to_the_previous_logs(self):
        visible = self._game_log(error_reports.ROLE_VISIBLE_CLIENT)
        self._write(visible, b"first session\n")
        first = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="first")
        self._write(visible, b"first new bytes\n", "ab")
        error_reports.finalize_session(first, ended_at="first-end")
        self.assertTrue(error_reports.create_report()["included"])

        second = error_reports.begin_session(
            self.game, session_id=self.SESSION_2, started_at="second")
        error_reports.finalize_session(second, ended_at="second-end")

        with self.assertRaisesRegex(
                core.LauncherError, "No earlier session was included"):
            error_reports.create_report()

    def test_partial_single_player_report_names_missing_current_logs(self):
        session = error_reports.begin_session(
            self.game, needs_worker=True, local_server=True,
            session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        error_reports.finalize_session(session, ended_at="end")

        report = error_reports.create_report()

        self.assertEqual(("visible-client.log",), report["included"])
        self.assertEqual(
            ("server.log", "hidden-worker.log"), report["missing"])
        self.assertEqual((), report["notRun"])

    def test_network_join_reports_roles_that_this_session_did_not_run(self):
        session = error_reports.begin_session(
            self.game, needs_worker=False, local_server=False,
            session_id=self.SESSION_1, started_at="start")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        error_reports.finalize_session(session, ended_at="end")

        report = error_reports.create_report()

        self.assertEqual((), report["missing"])
        self.assertEqual(
            ("server.log", "hidden-worker.log"), report["notRun"])

    def test_reused_server_is_cut_at_both_session_boundaries(self):
        server = core.server_log_path()
        self._write(server, b"before session\n")
        session = error_reports.begin_session(
            self.game, local_server=True, session_id=self.SESSION_1,
            started_at="start")
        error_reports.attach_server(session, dedicated=False)
        self._write(server, b"during session\n", "ab")
        self._write(
            self._game_log(error_reports.ROLE_VISIBLE_CLIENT), b"visible\n")
        error_reports.finalize_session(session, ended_at="end")
        self._write(server, b"after session\n", "ab")

        payloads = self._archive(error_reports.create_report())

        self.assertEqual(b"during session\n", payloads["server.log"])

    def test_unexpected_log_replacement_is_not_mistaken_for_this_session(self):
        visible = self._game_log(error_reports.ROLE_VISIBLE_CLIENT)
        self._write(visible, b"old visible\n")
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        replacement = visible + ".replacement"
        self._write(replacement, b"unrelated replacement\n")
        os.replace(replacement, visible)
        error_reports.finalize_session(session, ended_at="end")

        with self.assertRaisesRegex(
                core.LauncherError, "No earlier session was included"):
            error_reports.create_report()

    def test_a_log_symlink_created_during_the_session_is_never_collected(self):
        private = os.path.join(self.root, "private.txt")
        self._write(private, b"private data")
        session = error_reports.begin_session(
            self.game, session_id=self.SESSION_1, started_at="start")
        try:
            os.symlink(
                private,
                self._game_log(error_reports.ROLE_VISIBLE_CLIENT))
        except (AttributeError, NotImplementedError, OSError):
            self.skipTest("symlinks are unavailable")

        with self.assertRaisesRegex(
                core.LauncherError, "No earlier session was included"):
            error_reports.create_report()
        self.assertIsNone(session["endedAt"])

    def test_a_redirected_session_server_directory_is_refused(self):
        session = error_reports.begin_session(
            self.game, local_server=True, session_id=self.SESSION_1,
            started_at="start")
        session_root = error_reports.session_logs_directory()
        os.makedirs(session_root)
        redirected = os.path.join(self.root, "redirected")
        os.makedirs(redirected)
        try:
            os.symlink(redirected, os.path.join(session_root, self.SESSION_1))
        except (AttributeError, NotImplementedError, OSError):
            self.skipTest("symlinks are unavailable")

        with self.assertRaisesRegex(
                core.LauncherError, "not a regular directory"):
            error_reports.attach_server(session, dedicated=True)

    def test_missing_session_has_a_clear_refusal(self):
        with self.assertRaisesRegex(
                core.LauncherError, "No launcher game session"):
            error_reports.create_report()

    def test_explorer_command_selects_the_exact_zip(self):
        report = os.path.join(self.root, "report with spaces.zip")
        self._write(report, b"zip")
        calls = []

        error_reports.select_in_explorer(
            report, runner=lambda *args, **kwargs: calls.append(
                (args, kwargs)))

        self.assertEqual(
            ["explorer.exe", "/select,", os.path.normpath(report)],
            calls[0][0][0])


if __name__ == "__main__":
    unittest.main()
