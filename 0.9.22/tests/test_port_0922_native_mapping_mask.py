from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = (
    ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client')
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922 import native_mapping_mask


class _Memory(object):
    def __init__(self, signature=None, module_base=0x00400000):
        self._module_base = module_base
        self.signature_address = (
            module_base + native_mapping_mask._SIGNATURE_RVA)
        self.mask_address = (
            module_base + native_mapping_mask._MASK_IMMEDIATE_RVA)
        if signature is None:
            signature = native_mapping_mask._ORIGINAL_SIGNATURE
        self.data = bytearray(signature)
        self.writes = []

    def module_base(self):
        return self._module_base

    def read(self, address, size):
        offset = address - self.signature_address
        return bytes(self.data[offset:offset + size])

    def write_byte(self, address, value):
        offset = address - self.signature_address
        self.data[offset] = value
        self.writes.append((address, value))


class _PostWriteFailureMemory(_Memory):
    def __init__(self):
        _Memory.__init__(self)
        self.failed = False

    def write_byte(self, address, value):
        _Memory.write_byte(self, address, value)
        if value == 0x01 and not self.failed:
            self.failed = True
            raise native_mapping_mask.NativeMappingMaskError(
                'simulated post-write failure')


class NativeMappingMaskTests(unittest.TestCase):
    def test_exact_1513_signature_wraps_one_mapping_and_restores(self):
        memory = _Memory()
        observed = []

        def mapping(space_id, path=None):
            observed.append((
                space_id, path, memory.read(memory.mask_address, 1)))
            return 37

        result = native_mapping_mask.call_with_standard_gameplay_mask(
            mapping, (1073741825,), {'path': 'spaces/02_malinovka'},
            memory)

        self.assertEqual(37, result)
        self.assertEqual(
            [(1073741825, 'spaces/02_malinovka', b'\x01')], observed)
        self.assertEqual(
            [(memory.mask_address, 0x01),
             (memory.mask_address, 0xff)], memory.writes)
        self.assertEqual(
            native_mapping_mask._ORIGINAL_SIGNATURE, bytes(memory.data))

    def test_mapping_exception_still_restores_original_opcode(self):
        memory = _Memory()

        def mapping():
            self.assertEqual(b'\x01', memory.read(memory.mask_address, 1))
            raise LookupError('mapping failed')

        with self.assertRaisesRegex(LookupError, 'mapping failed'):
            native_mapping_mask.call_with_standard_gameplay_mask(
                mapping, memory=memory)

        self.assertEqual(b'\xff', memory.read(memory.mask_address, 1))
        self.assertEqual(
            native_mapping_mask._ORIGINAL_SIGNATURE, bytes(memory.data))

    def test_wrong_executable_signature_fails_before_callback(self):
        signature = bytearray(native_mapping_mask._ORIGINAL_SIGNATURE)
        signature[0] ^= 0xff
        memory = _Memory(signature)
        called = []

        with self.assertRaisesRegex(
                RuntimeError, 'signature does not match'):
            native_mapping_mask.call_with_standard_gameplay_mask(
                lambda: called.append(True), memory=memory)

        self.assertEqual([], called)
        self.assertEqual([], memory.writes)

    def test_unexpected_module_base_fails_before_memory_access(self):
        memory = _Memory(module_base=0x00500000)

        with self.assertRaisesRegex(RuntimeError, 'unexpected.*module base'):
            native_mapping_mask.call_with_standard_gameplay_mask(
                lambda: None, memory=memory)

        self.assertEqual([], memory.writes)

    def test_post_write_failure_rolls_back_changed_opcode(self):
        memory = _PostWriteFailureMemory()

        with self.assertRaisesRegex(RuntimeError, 'post-write failure'):
            native_mapping_mask.call_with_standard_gameplay_mask(
                lambda: None, memory=memory)

        self.assertEqual(
            [(memory.mask_address, 0x01),
             (memory.mask_address, 0xff)], memory.writes)
        self.assertEqual(
            native_mapping_mask._ORIGINAL_SIGNATURE, bytes(memory.data))

    def test_concurrent_opcode_change_restores_ours_then_fails_closed(self):
        memory = _Memory()

        def mapping():
            memory.data[0] ^= 0xff

        with self.assertRaisesRegex(RuntimeError, 'signature changed'):
            native_mapping_mask.call_with_standard_gameplay_mask(
                mapping, memory=memory)

        # A neighbouring opcode changed, but the finally path still restores
        # the one immediate byte owned by this helper before reporting it.
        self.assertEqual(b'\xff', memory.read(memory.mask_address, 1))
        self.assertEqual(
            [(memory.mask_address, 0x01),
             (memory.mask_address, 0xff)], memory.writes)


if __name__ == '__main__':
    unittest.main()
