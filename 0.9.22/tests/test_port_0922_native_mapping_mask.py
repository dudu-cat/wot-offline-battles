from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = (
    ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import native_mapping_mask


class _Bridge(object):
    def __init__(self, apply_status=0, restore_status=0):
        self.apply_status = apply_status
        self.restore_status = restore_status
        self.events = []
        self.active = False

    def apply_standard_gameplay_mask(self):
        self.events.append('apply')
        if self.apply_status == 0:
            self.active = True
        return self.apply_status

    def restore_standard_gameplay_mask(self):
        self.events.append('restore')
        if not self.active and self.restore_status == 0:
            return 102
        if self.restore_status == 0:
            self.active = False
        return self.restore_status


class NativeMappingMaskTests(unittest.TestCase):
    def test_native_bridge_wraps_one_mapping_and_restores(self):
        bridge = _Bridge()
        observed = []

        def mapping(space_id, path=None):
            observed.append((space_id, path, bridge.active))
            return 37

        result = native_mapping_mask.call_with_standard_gameplay_mask(
            mapping, (1073741825,), {'path': 'spaces/02_malinovka'},
            bridge)

        self.assertEqual(37, result)
        self.assertEqual(
            [(1073741825, 'spaces/02_malinovka', True)], observed)
        self.assertEqual(['apply', 'restore'], bridge.events)
        self.assertFalse(bridge.active)

    def test_mapping_exception_still_restores_original_mask(self):
        bridge = _Bridge()

        def mapping():
            self.assertTrue(bridge.active)
            raise LookupError('mapping failed')

        with self.assertRaisesRegex(LookupError, 'mapping failed'):
            native_mapping_mask.call_with_standard_gameplay_mask(
                mapping, native_bridge=bridge)

        self.assertEqual(['apply', 'restore'], bridge.events)
        self.assertFalse(bridge.active)

    def test_apply_failure_fails_before_callback(self):
        bridge = _Bridge(apply_status=103)
        called = []

        with self.assertRaisesRegex(
                RuntimeError, 'signature changed.*status 103'):
            native_mapping_mask.call_with_standard_gameplay_mask(
                lambda: called.append(True), native_bridge=bridge)

        self.assertEqual([], called)
        self.assertEqual(['apply'], bridge.events)

    def test_failed_native_rollback_is_reported(self):
        bridge = _Bridge(apply_status=108, restore_status=106)

        with self.assertRaisesRegex(RuntimeError, 'rollback.*status 108'):
            native_mapping_mask.call_with_standard_gameplay_mask(
                lambda: None, native_bridge=bridge)

        self.assertEqual(['apply', 'restore'], bridge.events)

    def test_restore_failure_replaces_mapping_result_with_failure(self):
        bridge = _Bridge(restore_status=106)

        with self.assertRaisesRegex(
                RuntimeError, 'protection restore.*status 106'):
            native_mapping_mask.call_with_standard_gameplay_mask(
                lambda: 37, native_bridge=bridge)

        self.assertEqual(['apply', 'restore'], bridge.events)

    def test_missing_native_method_fails_closed(self):
        bridge = object()

        with self.assertRaisesRegex(RuntimeError, 'bridge is incomplete'):
            native_mapping_mask.call_with_standard_gameplay_mask(
                lambda: None, native_bridge=bridge)

    def test_default_path_loads_the_exact_sidecar_bridge(self):
        bridge = _Bridge()
        with mock.patch.object(
                native_mapping_mask, '_load_native_bridge',
                return_value=bridge) as loader:
            self.assertEqual(
                9,
                native_mapping_mask.call_with_standard_gameplay_mask(
                    lambda: 9))

        loader.assert_called_once_with()
        self.assertEqual(['apply', 'restore'], bridge.events)


if __name__ == '__main__':
    unittest.main()
