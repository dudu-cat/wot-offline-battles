import os
import shutil
import tempfile
import unittest
from unittest import mock

import core
import preferences_overlay


packed_xml = preferences_overlay.packed_xml


def _string(text):
    return packed_xml.PackedValue(
        packed_xml.TYPE_STRING, text.encode("utf-8"))


def _stock_config():
    nested = packed_xml.PackedElement(children=[
        (b"enabled", packed_xml.PackedValue(
            packed_xml.TYPE_BOOLEAN, True)),
        (b"workers", packed_xml.PackedValue(
            packed_xml.TYPE_INTEGER, 3)),
    ])
    return packed_xml.PackedElement(children=[
        (b"renderer", packed_xml.PackedValue(
            packed_xml.TYPE_ELEMENT, nested)),
        (b"preferences", _string("preferences.xml")),
        (b"debugKey", _string("unchanged")),
    ])


class PreferencesOverlayTest(unittest.TestCase):
    def setUp(self):
        self.game = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.game, True)
        self._write(core.GAME_EXECUTABLE, b"")
        self._write(
            "version.xml", b"<version> v.0.9.22.0.1 #1513 </version>")
        self.stock_root = _stock_config()
        self.stock_data = packed_xml.write_packed_xml(self.stock_root)
        self.stock_path = self._write(
            preferences_overlay.STOCK_ENGINE_CONFIG, self.stock_data)
        self.overlay_path = os.path.join(
            self.game,
            *preferences_overlay.OVERLAY_ENGINE_CONFIG.split("/"))

    def _write(self, relative, data):
        path = os.path.join(self.game, *relative.split("/"))
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(path, "wb") as stream:
            stream.write(data)
        return path

    @staticmethod
    def _child(root, name):
        values = [value for current, value in root.children
                  if current == name.encode("utf-8")]
        if len(values) != 1:
            raise AssertionError(name)
        return values[0]

    def test_complete_stock_clone_changes_only_preferences(self):
        action = preferences_overlay.ensure_preferences_overlay(self.game)

        self.assertIn("Created", action)
        with open(self.stock_path, "rb") as stream:
            self.assertEqual(self.stock_data, stream.read())
        with open(self.overlay_path, "rb") as stream:
            overlay = packed_xml.read_packed_xml(stream.read())

        preferences = self._child(overlay, "preferences")
        self.assertEqual(packed_xml.TYPE_ELEMENT, preferences.value_type)
        self.assertEqual(
            preferences_overlay.PROFILE_RELATIVE_PATH,
            self._child(preferences.value, "path").value.decode("utf-8"))
        self.assertEqual(
            preferences_overlay.PROFILE_PATH_BASE,
            self._child(preferences.value, "pathBase").value.decode("utf-8"))

        restored = preferences_overlay._build_overlay(
            overlay, profile_path=preferences_overlay.PROFILE_RELATIVE_PATH)
        index, unused = preferences_overlay._preference_child(restored)
        restored.children[index] = self.stock_root.children[
            preferences_overlay._preference_child(self.stock_root)[0]]
        self.assertEqual(
            packed_xml.write_packed_xml(self.stock_root),
            packed_xml.write_packed_xml(restored))

    def test_an_up_to_date_overlay_is_not_replaced(self):
        preferences_overlay.ensure_preferences_overlay(self.game)
        with mock.patch.object(preferences_overlay.os, "replace") as replace:
            action = preferences_overlay.ensure_preferences_overlay(self.game)

        replace.assert_not_called()
        self.assertIn("already up to date", action)

    def test_a_previous_owned_profile_path_is_updated_safely(self):
        old = preferences_overlay._build_overlay(
            self.stock_root,
            "WoTOfflineBattles/client_profiles/0.9.22.0.1/preferences.xml")
        self._write(
            preferences_overlay.OVERLAY_ENGINE_CONFIG,
            packed_xml.write_packed_xml(old))

        action = preferences_overlay.ensure_preferences_overlay(self.game)

        self.assertIn("Updated", action)
        with open(self.overlay_path, "rb") as stream:
            current = packed_xml.read_packed_xml(stream.read())
        section = self._child(current, "preferences").value
        self.assertEqual(
            preferences_overlay.PROFILE_RELATIVE_PATH,
            self._child(section, "path").value.decode("utf-8"))

    def test_an_unknown_third_party_overlay_is_left_byte_for_byte(self):
        third_party = preferences_overlay._build_overlay(
            self.stock_root, "OtherTool/preferences.xml")
        third_party_data = packed_xml.write_packed_xml(third_party)
        self._write(
            preferences_overlay.OVERLAY_ENGINE_CONFIG, third_party_data)

        with self.assertRaisesRegex(
                core.LauncherError, "another tool"):
            preferences_overlay.ensure_preferences_overlay(self.game)

        with open(self.overlay_path, "rb") as stream:
            self.assertEqual(third_party_data, stream.read())

    def test_other_third_party_engine_changes_are_a_conflict(self):
        changed = preferences_overlay._build_overlay(self.stock_root)
        self._child(changed, "debugKey").value = b"third-party"
        changed_data = packed_xml.write_packed_xml(changed)
        self._write(
            preferences_overlay.OVERLAY_ENGINE_CONFIG, changed_data)

        with self.assertRaisesRegex(
                core.LauncherError, "another tool"):
            preferences_overlay.ensure_preferences_overlay(self.game)

        with open(self.overlay_path, "rb") as stream:
            self.assertEqual(changed_data, stream.read())

    def test_missing_or_corrupt_stock_fails_before_creating_an_overlay(self):
        for data in (None, b"not packed xml"):
            if os.path.exists(self.stock_path):
                os.unlink(self.stock_path)
            if data is not None:
                self._write(preferences_overlay.STOCK_ENGINE_CONFIG, data)

            with self.assertRaises(core.LauncherError):
                preferences_overlay.ensure_preferences_overlay(self.game)

            self.assertFalse(os.path.lexists(self.overlay_path))

    def test_a_commit_failure_preserves_the_previous_owned_overlay(self):
        old = preferences_overlay._build_overlay(
            self.stock_root,
            "WoTOfflineBattles/client_profiles/0.9.22.1/preferences.xml")
        old_data = packed_xml.write_packed_xml(old)
        self._write(preferences_overlay.OVERLAY_ENGINE_CONFIG, old_data)

        with mock.patch.object(
                preferences_overlay.os, "replace",
                side_effect=OSError("synthetic failure")):
            with self.assertRaisesRegex(core.LauncherError, "committed"):
                preferences_overlay.ensure_preferences_overlay(self.game)

        with open(self.overlay_path, "rb") as stream:
            self.assertEqual(old_data, stream.read())
        leftovers = [name for name in os.listdir(os.path.dirname(
            self.overlay_path)) if name.startswith(".offline-preferences-")]
        self.assertEqual([], leftovers)

    def test_profile_path_is_bounded_to_local_app_data(self):
        local = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, local, True)
        self.assertEqual(
            os.path.realpath(os.path.join(
                local,
                *preferences_overlay.PROFILE_RELATIVE_PATH.split("/"))),
            preferences_overlay.profile_path({"LOCALAPPDATA": local}))
        self.assertIsNone(preferences_overlay.profile_path({}))
        self.assertIsNone(preferences_overlay.profile_path(
            {"LOCALAPPDATA": "relative"}))
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, True)
        try:
            os.symlink(outside, os.path.join(local, "WoTOfflineBattles"))
        except (AttributeError, NotImplementedError, OSError):
            return
        self.assertIsNone(preferences_overlay.profile_path(
            {"LOCALAPPDATA": local}))


if __name__ == "__main__":
    unittest.main()
