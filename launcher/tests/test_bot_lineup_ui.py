import unittest
from unittest import mock

import bot_lineup_profiles
import bot_lineup_ui


class _Variable(object):
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Combo(object):
    def __init__(self, index=-1):
        self.index = index
        self.options = {}

    def config(self, **options):
        self.options.update(options)

    def current(self):
        return self.index


class BotLineupUITests(unittest.TestCase):
    def test_restore_resolves_the_complete_nation_vehicle_identity(self):
        store, profile_name = bot_lineup_profiles.create(
            bot_lineup_profiles.empty_store(), "Saved")
        store = bot_lineup_profiles.set_assignment(
            store, profile_name, 2, 0, "germany:G12_Ltraktor")
        editor = bot_lineup_ui.BotLineupEditorWindow.__new__(
            bot_lineup_ui.BotLineupEditorWindow)
        editor._game_root = "/game"
        editor._store = store
        editor._profile_name = profile_name
        editor.status = _Variable()
        editor._rows = {(2, 0): {
            "nation": _Variable(), "vehicle": _Variable(),
            "nation_box": _Combo(), "vehicle_box": _Combo(),
        }}
        choices = [
            {"nation": "ussr", "vehicle": "G12_Ltraktor",
             "member": "ussr.xml", "label": "Other",
             "tags": ("lightTank",)},
            {"nation": "germany", "vehicle": "G12_Ltraktor",
             "member": "germany.xml", "label": "Leichttraktor",
             "tags": ("lightTank",)},
        ]

        with mock.patch.object(
                bot_lineup_ui.vehicle_overlays, "list_vehicle_choices",
                return_value=choices):
            editor._load_choices()

        row = editor._rows[(2, 0)]
        self.assertEqual("germany", row["nation"].get())
        self.assertEqual("Leichttraktor", row["vehicle"].get())
        self.assertEqual(
            ("Leichttraktor",), row["vehicle_box"].options["values"])


if __name__ == "__main__":
    unittest.main()
