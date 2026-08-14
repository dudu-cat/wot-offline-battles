#!/usr/bin/env python3
"""Audit the pinned WoT 0.8.2 executable and native filter bridge artifact."""

import argparse
import hashlib
import struct
from pathlib import Path


EXPECTED_EXE_SHA256 = (
    "8b3fe162117d2bc40aef2209a0cadbafe5ef4e9479410c12cd6ac6efde6deabd"
)
EXPECTED_TIMESTAMP = 0x50B8ECCF
EXPECTED_IMAGE_BASE = 0x00400000
EXPECTED_IMAGE_SIZE = 0x0140F000

RVA_PY_INIT_MODULE4 = 0x00019800
RVA_PY_ARG_PARSE_TUPLE = 0x0001D580
RVA_PY_ERR_SET_STRING = 0x0000D620
RVA_WG_FILTER2_TYPE = 0x00D77FC8
RVA_WG_VEHICLE_PHYSICS2_TYPE = 0x00D77690
RVA_WG_FILTER2_TYPE_NAME = 0x00B672E8
RVA_WG_FILTER2_VTABLE = 0x00B67698
RVA_WG_FILTER2_INPUT = 0x0050A350
RVA_WG_FILTER2_OUTPUT = 0x0050DDE0
RVA_WG_FILTER2_SET_VEHICLE_PHYSICS = 0x0050B4D0
RVA_WG_PHYSICS_FINALIZE_ROOT = 0x004EAF20
RVA_WG_PHYSICS_MATRIX_GETTER = 0x004E3AB0
RVA_MATRIX_YAW = 0x00130B40
RVA_MATRIX_PITCH = 0x00130E00
RVA_MATRIX_ROLL = 0x00149170
RVA_ENTITY_DIRECTION_REGISTRATION = 0x0017834F
RVA_ENTITY_YAW_GETTER = 0x004FB530
RVA_ENTITY_PITCH_GETTER = 0x00228660
RVA_ENTITY_ROLL_GETTER = 0x00451880

SIGNATURES = {
    RVA_PY_INIT_MODULE4: bytes.fromhex("81ec18020000a1"),
    RVA_PY_ARG_PARSE_TUPLE: bytes.fromhex("518b4c24088b5424"),
    RVA_PY_ERR_SET_STRING: bytes.fromhex("8b4424085650e8e5"),
    RVA_WG_FILTER2_INPUT: bytes.fromhex("83ec145355568bf1"),
    RVA_WG_FILTER2_OUTPUT: bytes.fromhex("6aff680ec6ec0064"),
    RVA_WG_FILTER2_SET_VEHICLE_PHYSICS: bytes.fromhex(
        "6aff68e0c5ec0064"
    ),
    RVA_WG_PHYSICS_FINALIZE_ROOT: bytes.fromhex(
        "83ec185355568bd9578dab98030000"
    ),
    RVA_WG_PHYSICS_MATRIX_GETTER: bytes.fromhex(
        "81c11807000051e814b8caff"
    ),
    RVA_MATRIX_YAW: bytes.fromhex("83ec0c8b41208b51"),
    RVA_MATRIX_PITCH: bytes.fromhex("83ec108b41208b51"),
    RVA_MATRIX_ROLL: bytes.fromhex("83ec1c8b018b5104"),
    RVA_ENTITY_YAW_GETTER: bytes.fromhex("d9412851d91c24"),
    RVA_ENTITY_PITCH_GETTER: bytes.fromhex("d9412c51d91c24"),
    RVA_ENTITY_ROLL_GETTER: bytes.fromhex("d9413051d91c24"),
}

BRIDGE_REQUIRED_STRINGS = (
    b"seed_filter\0",
    b"output_filter\0",
    b"filter_has_physics\0",
    b"publish_physics_root\0",
    b"native bridge Filter::output mismatch\0",
    b"native bridge Filter::output timestamp is not finite\0",
    b"native bridge Filter::output timestamp is not newer\0",
    b"native bridge Filter::output timestamp did not advance\0",
    b"native bridge filter physics owner mismatch\0",
    b"native bridge requires an exact WGVehiclePhysics2 object\0",
    b"native bridge physics root matrix is unreadable\0",
    b"native bridge physics root output did not advance\0",
)

# MinGW compiles the three exact Matrix angle calls into these ordered image-
# base-relative immediates inside publish_physics_root. Requiring the sequence
# prevents a future yaw/roll source swap from passing the executable ABI audit.
BRIDGE_ROOT_ANGLE_ORDER = bytes.fromhex(
    "8d86400b1300"  # Matrix::yaw
    "d95dcc"
    "ffd0"
    "8d86000e1300"  # Matrix::pitch
    "8b4da0"
    "81c670911400"  # Matrix::roll
    "d95ddc"
    "ffd0"
    "8b4da0"
    "d95de0"
    "ffd6"
    "d9c0"
    "d95de4"
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class PEFile:
    def __init__(self, path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        require(self.data[:2] == b"MZ", "%s is not an MZ image" % self.path)
        self.pe_offset = struct.unpack_from("<I", self.data, 0x3C)[0]
        require(
            self.data[self.pe_offset:self.pe_offset + 4] == b"PE\0\0",
            "%s has no PE signature" % self.path,
        )
        coff = self.pe_offset + 4
        self.machine, self.section_count, self.timestamp = struct.unpack_from(
            "<HHI", self.data, coff
        )
        self.optional_size = struct.unpack_from("<H", self.data, coff + 16)[0]
        self.optional_offset = coff + 20
        self.optional_magic = struct.unpack_from(
            "<H", self.data, self.optional_offset
        )[0]
        require(self.optional_magic == 0x10B, "%s is not PE32" % self.path)
        self.image_base = self.u32_at_file(self.optional_offset + 28)
        self.image_size = self.u32_at_file(self.optional_offset + 56)
        self.dll_characteristics = struct.unpack_from(
            "<H", self.data, self.optional_offset + 70
        )[0]
        directory_count = self.u32_at_file(self.optional_offset + 92)
        self.directories = []
        for index in range(min(directory_count, 16)):
            self.directories.append(struct.unpack_from(
                "<II", self.data, self.optional_offset + 96 + index * 8
            ))
        section_offset = self.optional_offset + self.optional_size
        self.sections = []
        for index in range(self.section_count):
            offset = section_offset + index * 40
            name = self.data[offset:offset + 8].split(b"\0", 1)[0].decode(
                "ascii", "replace"
            )
            virtual_size, virtual_address, raw_size, raw_offset = (
                struct.unpack_from("<IIII", self.data, offset + 8)
            )
            self.sections.append((
                name, virtual_address, virtual_size, raw_offset, raw_size
            ))

    def u16_at_file(self, offset):
        return struct.unpack_from("<H", self.data, offset)[0]

    def u32_at_file(self, offset):
        return struct.unpack_from("<I", self.data, offset)[0]

    def file_offset(self, rva, size=1):
        for name, virtual_address, virtual_size, raw_offset, raw_size in (
                self.sections):
            span = max(virtual_size, raw_size)
            if virtual_address <= rva and rva + size <= virtual_address + span:
                delta = rva - virtual_address
                require(
                    delta + size <= raw_size,
                    "RVA 0x%x is virtual-only in %s" % (rva, name),
                )
                return raw_offset + delta
        raise RuntimeError("RVA 0x%x is outside mapped sections" % rva)

    def bytes_at_rva(self, rva, size):
        offset = self.file_offset(rva, size)
        return self.data[offset:offset + size]

    def u32_at_rva(self, rva):
        return struct.unpack("<I", self.bytes_at_rva(rva, 4))[0]

    def c_string_at_rva(self, rva):
        offset = self.file_offset(rva)
        end = self.data.find(b"\0", offset)
        require(end >= 0, "unterminated string at RVA 0x%x" % rva)
        return self.data[offset:end]

    def export_names(self):
        if not self.directories or self.directories[0] == (0, 0):
            return []
        export_rva = self.directories[0][0]
        raw = self.bytes_at_rva(export_rva, 40)
        name_count = struct.unpack_from("<I", raw, 24)[0]
        names_rva = struct.unpack_from("<I", raw, 32)[0]
        return [
            self.c_string_at_rva(self.u32_at_rva(names_rva + index * 4))
            for index in range(name_count)
        ]

    def import_dlls(self):
        if len(self.directories) < 2 or self.directories[1] == (0, 0):
            return []
        descriptor_rva = self.directories[1][0]
        names = []
        for index in range(256):
            raw = self.bytes_at_rva(descriptor_rva + index * 20, 20)
            fields = struct.unpack("<IIIII", raw)
            if fields == (0, 0, 0, 0, 0):
                return names
            names.append(self.c_string_at_rva(fields[3]))
        raise RuntimeError("import descriptor table is not terminated")


def audit_executable(path):
    pe = PEFile(path)
    digest = hashlib.sha256(pe.data).hexdigest()
    require(digest == EXPECTED_EXE_SHA256, "WorldOfTanks.exe SHA-256 mismatch")
    require(pe.machine == 0x14C, "WorldOfTanks.exe is not x86")
    require(pe.timestamp == EXPECTED_TIMESTAMP, "PE timestamp mismatch")
    require(pe.image_base == EXPECTED_IMAGE_BASE, "image base mismatch")
    require(pe.image_size == EXPECTED_IMAGE_SIZE, "image size mismatch")
    require(
        not (pe.dll_characteristics & 0x40),
        "unexpected ASLR flag invalidates absolute runtime validation",
    )
    for rva, signature in SIGNATURES.items():
        require(
            pe.bytes_at_rva(rva, len(signature)) == signature,
            "function signature mismatch at RVA 0x%x" % rva,
        )
    require(
        pe.u32_at_rva(RVA_WG_FILTER2_TYPE + 12) ==
        EXPECTED_IMAGE_BASE + RVA_WG_FILTER2_TYPE_NAME,
        "WGVehicleFilter2 Python type name pointer mismatch",
    )
    require(
        pe.c_string_at_rva(RVA_WG_FILTER2_TYPE_NAME) == b"WGVehicleFilter2",
        "WGVehicleFilter2 Python type name mismatch",
    )
    require(
        pe.u32_at_rva(RVA_WG_FILTER2_VTABLE + 4) ==
        EXPECTED_IMAGE_BASE + RVA_WG_FILTER2_INPUT,
        "WGVehicleFilter2 Filter::input vtable slot mismatch",
    )
    require(
        pe.u32_at_rva(RVA_WG_FILTER2_VTABLE + 8) ==
        EXPECTED_IMAGE_BASE + RVA_WG_FILTER2_OUTPUT,
        "WGVehicleFilter2 Filter::output vtable slot mismatch",
    )
    setter = pe.bytes_at_rva(RVA_WG_FILTER2_SET_VEHICLE_PHYSICS, 0x90)
    require(
        struct.pack("<I", EXPECTED_IMAGE_BASE +
                    RVA_WG_VEHICLE_PHYSICS2_TYPE) in setter,
        "WGVehicleFilter2 setter physics type gate mismatch",
    )
    require(
        bytes.fromhex("8b86f0040000c6442458013bc7") in setter and
        bytes.fromhex("89bef0040000") in setter,
        "WGVehicleFilter2 physics owner offset mismatch",
    )
    finalizer = pe.bytes_at_rva(RVA_WG_PHYSICS_FINALIZE_ROOT, 0x100)
    require(
        bytes.fromhex("8dab18070000") in finalizer and
        bytes.fromhex("8db398030000") in finalizer,
        "WGVehiclePhysics2 root matrix copy contract mismatch",
    )
    registration = pe.bytes_at_rva(RVA_ENTITY_DIRECTION_REGISTRATION, 0xE0)
    require(
        bytes.fromhex("68c0c0f200") in registration and
        bytes.fromhex("bf30b58f00") in registration and
        bytes.fromhex("68c4c0f200") in registration and
        bytes.fromhex("bf60866200") in registration and
        bytes.fromhex("68ccc0f200") in registration and
        bytes.fromhex("bf80188500") in registration,
        "Entity yaw/pitch/roll registration contract mismatch",
    )
    print("EXE OK sha256=%s timestamp=0x%08x image=0x%x" % (
        digest, pe.timestamp, pe.image_size
    ))


def audit_bridge(path):
    pe = PEFile(path)
    require(pe.machine == 0x14C, "native bridge is not x86")
    require(pe.timestamp == 0, "native bridge is not deterministic")
    exports = pe.export_names()
    require(
        exports == [b"initoffhangar_native_seed"],
        "unexpected native bridge exports: %r" % (exports,),
    )
    imports = [name.lower() for name in pe.import_dlls()]
    require(
        not any(name.startswith(b"python") for name in imports),
        "native bridge unexpectedly imports a Python DLL",
    )
    require(b"kernel32.dll" in imports, "native bridge lacks KERNEL32")
    for marker in BRIDGE_REQUIRED_STRINGS:
        require(
            marker in pe.data,
            "native bridge lacks required output marker: %r" % marker[:-1],
        )
    require(
        BRIDGE_ROOT_ANGLE_ORDER in pe.data,
        "native bridge physics root yaw/pitch/roll call order mismatch",
    )
    digest = hashlib.sha256(pe.data).hexdigest()
    print("PYD OK sha256=%s imports=%s exports=%s" % (
        digest,
        ",".join(name.decode("ascii") for name in imports),
        ",".join(name.decode("ascii") for name in exports),
    ))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--pyd", required=True, type=Path)
    args = parser.parse_args()
    audit_executable(args.exe)
    audit_bridge(args.pyd)


if __name__ == "__main__":
    main()
