import struct
from struct import pack, unpack
from typing import BinaryIO

def readuint8(data:bytes) -> int:
    if len(data) == 1:
        return int(struct.unpack('B', data)[0])
    else:
        raise ValueError(f"1 byte needed, {len(data)} given.")

def readuint16(data:bytes) -> int:
    if len(data) == 2:
        return int(struct.unpack('H', data)[0])
    else:
        raise ValueError(f"2 byte needed, {len(data)} given.")

def readuint32(data:bytes) -> int:
    if len(data) == 4:
        return struct.unpack('I', data)[0]
    else:
        raise ValueError(f"4 byte needed, {len(data)} given.")

def readint32(data:bytes) -> int:
    if len(data) == 4:
        return struct.unpack('I', data)[0]
    else:
        raise ValueError(f"4 byte needed, {len(data)} given.")

def readuint64(data:bytes) -> int:
    if len(data) == 8:
        return struct.unpack('Q', data)[0]
    else:
        raise ValueError(f"8 byte needed, {len(data)} given.")

def readfloat32(data:bytes):
    if len(data) == 4:
        return struct.unpack('f', data)[0]
    else:
        raise ValueError(f"4 byte needed, {len(data)} given.")

def writeuint8(input:int):
    return struct.pack('B', input)

def writeuint16(input:int):
    return struct.pack('H', input)

def writeuint32(input:int):
    return struct.pack('I', input)

def writeuint64(input:int):
    return struct.pack('Q', input)

def writefloat32(input:float):
    return struct.pack('f', input)

def writeint32(input:int):
    return struct.pack('i', input)

def readLEB128(file:BinaryIO) -> int:
    value, shift = 0, 0
    while True:
        byte = file.read(1)[0]
        value |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return value
        shift += 7

def writeLEB128(value: int) -> bytes:
    """
    Encodes an integer as LEB128 (unsigned).
    Returns a bytes object.
    """
    result = bytearray()


    while True:
        byte = value & 0x7F
        value >>= 7
        if value != 0:
            byte |= 0x80
        result.append(byte)
        if value == 0:
            break

    return bytes(result)