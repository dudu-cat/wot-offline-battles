import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BATTLE = ROOT / "scripts/client/gui/mods/offhangar/offline_battle.py"


class MusicLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = BATTLE.read_text()

    def test_synthetic_arena_uses_original_music_lifecycle(self):
        self.assertIn("_mc.onEnterArena()", self.source)
        self.assertIn("_music_controller.onLeaveArena()", self.source)
        self.assertIn("_offh_arena_lifecycle", self.source)

    def test_arena_sound_resolver_includes_combat_ambience(self):
        self.assertIn("eventId == _MC.AMBIENT_EVENT_COMBAT", self.source)
        self.assertIn("ambientSound", self.source)

    def test_countdown_does_not_explicitly_silence_loading_music(self):
        self.assertNotIn(
            "g_musicController.play(_MC.MUSIC_EVENT_NONE)", self.source
        )

    def test_music_startup_preserves_user_volume_preferences(self):
        self.assertIn("_SG.g_instance.applyPreferences()", self.source)
        self.assertNotIn("setVolume('music', 1.0)", self.source)
        self.assertNotIn("setVolume('ambient', 1.0)", self.source)


if __name__ == "__main__":
    unittest.main()
