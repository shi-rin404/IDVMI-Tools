"""BXML attribute readers to merge into attributeFunctions.py.

Type 0x07 is provisionally treated as float64. The recovered engine parser
confirms only that the payload occupies eight bytes; switch the dispatch to
``signedInteger64Attribute`` if later getter/serializer analysis proves that it
is an integer.
"""

import struct
from typing import BinaryIO
from .. import byte_handler as bh

def _read_exact(file: BinaryIO, size: int, context: str) -> bytes:
    data = file.read(size)
    if len(data) != size:
        raise EOFError(
            f"Unexpected EOF while reading {context}: "
            f"expected {size} bytes, received {len(data)}"
        )
    return data


def _format_float32(value: float) -> str:
    # Nine significant decimal digits are sufficient to round-trip binary32.
    return format(value, ".9g")


def _format_float64(value: float) -> str:
    # Seventeen significant decimal digits are sufficient to round-trip binary64.
    return format(value, ".17g")


# 0x01
def stringAttribute(file: BinaryIO) -> str:
    collected_data = bytearray()

    while True:
        byte = file.read(1)
        if not byte:
            raise EOFError("Unexpected EOF while reading BXML string")
        if byte == b"\x00":
            break
        collected_data.extend(byte)

    try:
        return collected_data.decode(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Could not decode BXML string: {collected_data!r}") from exc


# 0x02
def unsignedInteger32Attribute(file: BinaryIO) -> str:
    data = _read_exact(file, 4, "BXML uint32")
    return str(struct.unpack("<I", data)[0])


# 0x03
def booleanAttribute(file: BinaryIO) -> str:
    value = _read_exact(file, 1, "BXML boolean")[0]

    if value == 0:
        return "false"
    if value == 1:
        return "true"

    raise ValueError(f"Invalid BXML boolean value: 0x{value:02X}")


# 0x04
def float32Attribute(file: BinaryIO) -> str:
    data = _read_exact(file, 4, "BXML float32")
    return _format_float32(struct.unpack("<f", data)[0])


# 0x05
def signedInteger32Attribute(file: BinaryIO) -> str:
    data = _read_exact(file, 4, "BXML int32")
    return str(struct.unpack("<i", data)[0])


# 0x06
def floatArrayAttribute(file: BinaryIO) -> str:
    count_data = _read_exact(file, 4, "BXML float-array count")
    value_count = struct.unpack("<I", count_data)[0]
    values = []

    for value_index in range(value_count):
        data = _read_exact(
            file,
            4,
            f"BXML float-array value {value_index} of {value_count}",
        )
        values.append(_format_float32(struct.unpack("<f", data)[0]))

    return ",".join(values)


# Backward-compatible name used by the existing parser.
def matrixAttribute(file: BinaryIO) -> str:
    return floatArrayAttribute(file)


# 0x07 -- provisional semantic mapping
def float64Attribute(file: BinaryIO) -> str:
    data = _read_exact(file, 8, "BXML float64")
    return _format_float64(struct.unpack("<d", data)[0])


# Alternative reader for 0x07 if later analysis proves it is signed int64.
def signedInteger64Attribute(file: BinaryIO) -> str:
    data = _read_exact(file, 8, "BXML int64")
    return str(struct.unpack("<q", data)[0])


# 0x08
def unsignedInteger64Attribute(file: BinaryIO) -> str:
    data = _read_exact(file, 8, "BXML uint64")
    return str(struct.unpack("<Q", data)[0])
