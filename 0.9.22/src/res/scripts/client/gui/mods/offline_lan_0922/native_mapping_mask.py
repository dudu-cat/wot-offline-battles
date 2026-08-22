from __future__ import print_function


# Exact WorldOfTanks.exe #1513 addresses.  The executable has relocations
# stripped and is linked at 0x00400000, but use the process module handle for
# the final address and fail closed if Windows did not load that exact image.
_EXPECTED_IMAGE_BASE = 0x00400000
_SIGNATURE_RVA = 0x00254FB9
_MASK_IMMEDIATE_RVA = 0x00254FC2
_MASK_SIGNATURE_INDEX = _MASK_IMMEDIATE_RVA - _SIGNATURE_RVA
_ORIGINAL_SIGNATURE = (
    b'\xc6\x45\xfc\x06\x85\xf6\x74\x44\x6a\xff\x57\x8d\x45\xb0'
    b'\x8b\xce\x50\xff\xb5\x24\xff\xff\xff\xff\xb5\x20\xff\xff'
    b'\xff\xe8\xc5\xda\x46\x00')
_PATCHED_SIGNATURE = (
    _ORIGINAL_SIGNATURE[:_MASK_SIGNATURE_INDEX] + b'\x01' +
    _ORIGINAL_SIGNATURE[_MASK_SIGNATURE_INDEX + 1:])
_PAGE_EXECUTE_READWRITE = 0x40


class NativeMappingMaskError(RuntimeError):
    pass


class _WindowsProcessMemory(object):
    """Small Win32 process-memory boundary, injectable for unit tests."""

    def __init__(self, ctypes_module=None, kernel32=None):
        if ctypes_module is None:
            import ctypes as ctypes_module
        self._ctypes = ctypes_module
        if kernel32 is None:
            try:
                kernel32 = ctypes_module.windll.kernel32
            except AttributeError:
                raise NativeMappingMaskError(
                    '#1513 native mapping mask requires Windows')
        self._kernel32 = kernel32
        self._configure_functions()

    def _configure_functions(self):
        ctypes_module = self._ctypes
        self._kernel32.GetModuleHandleW.argtypes = [ctypes_module.c_wchar_p]
        self._kernel32.GetModuleHandleW.restype = ctypes_module.c_void_p
        self._kernel32.GetCurrentProcess.argtypes = []
        self._kernel32.GetCurrentProcess.restype = ctypes_module.c_void_p
        self._kernel32.VirtualProtect.argtypes = [
            ctypes_module.c_void_p, ctypes_module.c_size_t,
            ctypes_module.c_ulong, ctypes_module.POINTER(
                ctypes_module.c_ulong)]
        self._kernel32.VirtualProtect.restype = ctypes_module.c_int
        self._kernel32.FlushInstructionCache.argtypes = [
            ctypes_module.c_void_p, ctypes_module.c_void_p,
            ctypes_module.c_size_t]
        self._kernel32.FlushInstructionCache.restype = ctypes_module.c_int

    def _last_error(self):
        get_last_error = getattr(self._kernel32, 'GetLastError', None)
        if callable(get_last_error):
            return int(get_last_error())
        return 0

    def module_base(self):
        value = self._kernel32.GetModuleHandleW(None)
        value = getattr(value, 'value', value)
        if not value:
            raise NativeMappingMaskError(
                'GetModuleHandleW failed: winerror=%d' % self._last_error())
        return int(value)

    def read(self, address, size):
        return self._ctypes.string_at(address, size)

    def write_byte(self, address, value):
        ctypes_module = self._ctypes
        address_pointer = ctypes_module.c_void_p(address)
        old_protection = ctypes_module.c_ulong()
        if not self._kernel32.VirtualProtect(
                address_pointer, ctypes_module.c_size_t(1),
                _PAGE_EXECUTE_READWRITE,
                ctypes_module.byref(old_protection)):
            raise NativeMappingMaskError(
                'VirtualProtect enable failed: winerror=%d' %
                self._last_error())
        pending_error = None
        try:
            source = ctypes_module.c_ubyte(value)
            ctypes_module.memmove(
                address_pointer, ctypes_module.byref(source), 1)
            if self.read(address, 1) != bytes(bytearray((value,))):
                pending_error = NativeMappingMaskError(
                    '#1513 native mapping mask byte write was not applied')
            elif not self._kernel32.FlushInstructionCache(
                    self._kernel32.GetCurrentProcess(), address_pointer,
                    ctypes_module.c_size_t(1)):
                pending_error = NativeMappingMaskError(
                    'FlushInstructionCache failed: winerror=%d' %
                    self._last_error())
        finally:
            unused_protection = ctypes_module.c_ulong()
            if not self._kernel32.VirtualProtect(
                    address_pointer, ctypes_module.c_size_t(1),
                    old_protection.value,
                    ctypes_module.byref(unused_protection)):
                pending_error = NativeMappingMaskError(
                    'VirtualProtect restore failed: winerror=%d' %
                    self._last_error())
        if pending_error is not None:
            raise pending_error


class _StandardGameplayMaskPatch(object):
    """Temporarily replace #1513's hard-coded all-gameplay mapping mask."""

    def __init__(self, memory):
        self._memory = memory
        self._signature_address = None
        self._mask_address = None
        self._applied = False

    def apply(self):
        module_base = self._memory.module_base()
        if module_base != _EXPECTED_IMAGE_BASE:
            raise NativeMappingMaskError(
                'unexpected #1513 module base: 0x%x' % module_base)
        signature_address = module_base + _SIGNATURE_RVA
        mask_address = module_base + _MASK_IMMEDIATE_RVA
        actual = self._memory.read(
            signature_address, len(_ORIGINAL_SIGNATURE))
        if actual != _ORIGINAL_SIGNATURE:
            raise NativeMappingMaskError(
                '#1513 addSpaceGeometryMapping signature does not match')
        self._signature_address = signature_address
        self._mask_address = mask_address
        try:
            self._memory.write_byte(mask_address, 0x01)
            self._applied = True
            if self._memory.read(
                    signature_address,
                    len(_PATCHED_SIGNATURE)) != _PATCHED_SIGNATURE:
                raise NativeMappingMaskError(
                    '#1513 standard gameplay mapping mask was not applied')
        except Exception:
            # Win32 may report a cache/protection failure after memmove has
            # already changed the byte.  Inspect the owned byte rather than
            # relying only on write_byte's return path before rolling back.
            if self._memory.read(mask_address, 1) == b'\x01':
                self._memory.write_byte(mask_address, 0xff)
            self._applied = False
            raise

    def restore(self):
        if not self._applied:
            return False
        try:
            actual = self._memory.read(
                self._signature_address, len(_PATCHED_SIGNATURE))
            if (len(actual) != len(_PATCHED_SIGNATURE) or
                    actual[_MASK_SIGNATURE_INDEX:_MASK_SIGNATURE_INDEX + 1]
                    != b'\x01'):
                raise NativeMappingMaskError(
                    '#1513 patched mapping-mask byte changed unexpectedly')
            signature_changed = actual != _PATCHED_SIGNATURE
            self._memory.write_byte(self._mask_address, 0xff)
            restored = self._memory.read(
                    self._signature_address,
                    len(_ORIGINAL_SIGNATURE))
            if restored != _ORIGINAL_SIGNATURE:
                if signature_changed:
                    raise NativeMappingMaskError(
                        '#1513 addSpaceGeometryMapping signature changed '
                        'during mapping')
                raise NativeMappingMaskError(
                    '#1513 addSpaceGeometryMapping mask was not restored')
        finally:
            self._applied = False
        return True


def call_with_standard_gameplay_mask(callback, args=(), kwargs=None,
                                     memory=None):
    """Call one native mapping with CTF bit 0 instead of its all-bits mask."""
    if not callable(callback):
        raise TypeError('native mapping callback must be callable')
    if kwargs is None:
        kwargs = {}
    if memory is None:
        memory = _WindowsProcessMemory()
    patch = _StandardGameplayMaskPatch(memory)
    patch.apply()
    try:
        return callback(*args, **kwargs)
    finally:
        patch.restore()
