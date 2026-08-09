from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'ports' / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import destructibles_compat
from gui.mods.offline_lan_0922 import destructibles_sensor


class DestructiblesCompatibilityTests(unittest.TestCase):

    def tearDown(self):
        destructibles_sensor.set_event_sink(None)

    def test_restores_only_names_moved_by_1513(self):
        area = types.ModuleType('AreaDestructibles')
        cache = types.ModuleType('DestructiblesCache')
        cache.chunkIDFromPosition = object()
        cache.encodeFallenTree = object()
        cache.encodeFallenColumn = object()
        cache.encodeDestructibleModule = object()
        cache.DESTR_TYPE_TREE = 1
        cache.DESTR_TYPE_FALLING_ATOM = 2
        cache.DESTR_TYPE_FRAGILE = 3
        cache.DESTR_TYPE_STRUCTURE = 4

        destructibles_compat._INSTALLED = False
        with mock.patch.dict(
                sys.modules,
                {'AreaDestructibles': area, 'DestructiblesCache': cache}):
            self.assertTrue(destructibles_compat.install())

        self.assertIs(cache.encodeFallenTree, area.encodeFallenTree)
        self.assertIs(cache.chunkIDFromPosition, area.chunkIDFromPosition)
        self.assertIs(cache.encodeFallenColumn, area.encodeFallenColumn)
        self.assertIs(
            cache.encodeDestructibleModule,
            area.encodeDestructibleModule)
        self.assertEqual(1, area._DAMAGE_TYPE_TREE)
        self.assertEqual(2, area._DAMAGE_TYPE_COLUMN)
        self.assertEqual(3, area._DAMAGE_TYPE_FRAGILE)
        self.assertEqual(4, area._DAMAGE_TYPE_MODULE)
        self.assertEqual(1, area.DESTR_TYPE_TREE)
        self.assertEqual(2, area.DESTR_TYPE_FALLING_ATOM)
        self.assertEqual(3, area.DESTR_TYPE_FRAGILE)
        self.assertEqual(4, area.DESTR_TYPE_STRUCTURE)

    def test_does_not_replace_an_existing_client_name(self):
        original = object()
        area = types.ModuleType('AreaDestructibles')
        area.encodeFallenTree = original
        cache = types.ModuleType('DestructiblesCache')
        cache.chunkIDFromPosition = object()
        cache.encodeFallenTree = object()
        cache.encodeFallenColumn = object()
        cache.encodeDestructibleModule = object()
        cache.DESTR_TYPE_TREE = 1
        cache.DESTR_TYPE_FALLING_ATOM = 2
        cache.DESTR_TYPE_FRAGILE = 3
        cache.DESTR_TYPE_STRUCTURE = 4

        destructibles_compat._INSTALLED = False
        with mock.patch.dict(
                sys.modules,
                {'AreaDestructibles': area, 'DestructiblesCache': cache}):
            destructibles_compat.install()

        self.assertIs(original, area.encodeFallenTree)

    def test_sensor_publishes_normalized_client_report(self):
        events = []
        destructibles_sensor.set_event_sink(
            lambda event: events.append(event) or True)

        self.assertTrue(destructibles_sensor._publish_destroyed(
            'tree', 12, 3, (1, 2, 4), 0.75, 8.0,
            isShotDamage=True))

        self.assertEqual({
            'destructible_kind': 'tree', 'chunk_id': 12,
            'item_index': 3, 'x': 1.0, 'y': 2.0, 'z': 4.0,
            'fall_yaw': 0.75, 'speed': 8.0, 'is_shot': True,
        }, events[0])

    def test_sensor_does_not_silently_accept_failed_lan_report(self):
        destructibles_sensor.set_event_sink(lambda unused_event: False)

        with self.assertRaisesRegex(RuntimeError, 'not admitted'):
            destructibles_sensor._publish_destroyed(
                'fragile', 12, 3, (1, 2, 4))


if __name__ == '__main__':
    unittest.main()
