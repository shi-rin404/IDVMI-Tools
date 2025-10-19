from typing import BinaryIO
from .. import byte_handler as bh

# \x01
def stringAttribute(file:BinaryIO):
    collected_data = bytearray()

    while collected_data == b"" or collected_data[-1] != 0:
        collected_data += file.read(1)

    return collected_data[:-1].decode(encoding="utf-8")

# \x02
def unsignedInteger32Attribute(file:BinaryIO):
    return str(bh.readuint32(file.read(4)))

# \x05
def signedInteger32Attribute(file:BinaryIO):
    return str(bh.readint32(file.read(4)))

# \x06
def matrixAttribute(file:BinaryIO):
    matrix_size = bh.readuint32(file.read(4))
    matrix = []

    for _ in range(matrix_size):
        matrix.append(f"{bh.readfloat32(file.read(4)):.4f}")

    return ",".join(matrix)

# \x08
def unsignedInteger64Attribute(file:BinaryIO):
    return str(bh.readuint64(file.read(8)))