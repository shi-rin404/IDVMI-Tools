from typing import Any, BinaryIO, Callable, Literal
import os
import xml.etree.ElementTree as ET

from . import byte_handler as bh
from .sub_parse_handler import attributeFunctions as af


BINARY_MAGIC = b"\xC1\x59\x41\x0D"
BINARY_HEADER_SIZE = 12

# The engine accepts type IDs 0x01 through 0x08. Type 0x07 is provisionally
# interpreted as float64; the parser routine only proves that its payload is
# eight bytes long.
DATA_TYPE_READERS: dict[int, Callable[[BinaryIO], Any]] = {
    0x01: af.stringAttribute,
    0x02: af.unsignedInteger32Attribute,
    0x03: af.booleanAttribute,
    0x04: af.float32Attribute,
    0x05: af.signedInteger32Attribute,
    0x06: af.matrixAttribute,
    0x07: af.float64Attribute,
    0x08: af.unsignedInteger64Attribute,
}


def _read_exact(file: BinaryIO, size: int, context: str) -> bytes:
    data = file.read(size)
    if len(data) != size:
        raise EOFError(
            f"Unexpected EOF while reading {context}: "
            f"expected {size} bytes, received {len(data)}"
        )
    return data


def typeFile(file_path: os.PathLike) -> Literal["Binary", "XML"]:
    with open(file_path, "rb") as file:
        header = file.read(512)

        if header.startswith(BINARY_MAGIC):
            return "Binary"

    xml_header = header.lstrip(b"\xef\xbb\xbf\r\n\t ")
    if xml_header.startswith((b"<Neo", b"<?xml")):
        try:
            ET.parse(file_path)
        except ET.ParseError as exc:
            raise ValueError(f"Invalid XML file: {file_path}") from exc
        return "XML"

    raise ValueError("File format error. Check your file <:")


def readUnknownLenInt(value: list[bytes]) -> int:
    bytes_value = b"".join(value)
    read_functions = {
        1: bh.readuint8,
        2: bh.readuint16,
        4: bh.readuint32,
        8: bh.readuint64,
    }

    data_size = len(bytes_value)
    if data_size not in read_functions:
        raise ValueError("Unsupported parameter amount format")

    return read_functions[data_size](bytes_value)


# def getParameterAmount(file: BinaryIO) -> int:
#     parameter_amount = []
#
#     while parameter_amount == [] or not parameter_amount[-1].isalpha():
#         parameter_amount.append(file.read(1))
#
#     parameter_amount.pop(-1)
#     file.seek(-1, 1)
#
#     if parameter_amount[-1] == b"\x01":
#         parameter_amount.pop(-1)
#
#     return readUnknownLenInt(parameter_amount)


def getParameters(parameter_amount: int, file: BinaryIO) -> list[str]:
    parameter_list = []

    for parameter_index in range(parameter_amount):
        collected_data = bytearray()

        while True:
            byte = file.read(1)
            if not byte:
                raise EOFError(
                    "Unexpected EOF while reading parameter "
                    f"{parameter_index} of {parameter_amount}"
                )
            if byte == b"\x00":
                break
            collected_data.extend(byte)

        parameter_list.append(collected_data.decode(encoding="utf-8"))

    return parameter_list


def getElementTags(
    element_list: list[str],
    element_amount: int,
    file: BinaryIO,
) -> list[tuple[str, int]]:
    element_tags = []

    for element_number in range(element_amount):
        element_id = bh.readLEB128(file)
        child_count = bh.readLEB128(file)

        if element_id >= len(element_list):
            raise ValueError(
                f"Element name index {element_id} is out of range "
                f"at element {element_number}"
            )

        element_tags.append((element_list[element_id], child_count))

    return element_tags


def readTypedValue(file: BinaryIO) -> tuple[int, Any]:
    type_offset = file.tell()
    data_type = _read_exact(file, 1, "BXML value type")[0]
    reader = DATA_TYPE_READERS.get(data_type)

    if reader is None:
        raise ValueError(
            f"Unsupported custom binary attribute data type: "
            f"0x{data_type:02X} at file offset 0x{type_offset:X}"
        )

    try:
        return data_type, reader(file)
    except Exception as exc:
        raise ValueError(
            f"Failed to read BXML data type 0x{data_type:02X} "
            f"at file offset 0x{type_offset:X}"
        ) from exc


def getAttributes(
    element_list_len: int,
    attribute_list: list[str],
    file: BinaryIO,
) -> tuple[list[dict[str, Any]], list[Any]]:
    collected_attributes = []
    collected_element_values = []

    for element_number in range(element_list_len):
        attribute_amount = bh.readLEB128(file)
        element_attributes: dict[str, Any] = {}

        for attribute_number in range(attribute_amount):
            attribute_id = bh.readLEB128(file)

            if attribute_id >= len(attribute_list):
                raise ValueError(
                    f"Attribute name index {attribute_id} is out of range "
                    f"at element {element_number}, attribute {attribute_number}"
                )

            _data_type, value = readTypedValue(file)
            element_attributes[attribute_list[attribute_id]] = value

        # Every element has its own typed value after its attributes. The old
        # implementation assumed this was always the empty string marker 01 00,
        # which desynchronized the stream for non-string element values.
        _element_data_type, element_value = readTypedValue(file)

        collected_attributes.append(element_attributes)
        collected_element_values.append(element_value)

    return collected_attributes, collected_element_values


def parseCustomBinFormat(
    filepath: os.PathLike,
    *,
    include_element_values: bool = False,
) -> tuple:
    with open(filepath, "rb") as file:
        if _read_exact(file, 4, "BXML magic") != BINARY_MAGIC:
            raise ValueError("Invalid file format")

        # The engine loader treats this as the total container size. It is read
        # here for structural completeness, but not enforced because some files
        # may be embedded in a larger stream.
        _file_size = bh.readuint64(_read_exact(file, 8, "BXML file size"))

        element_def_amount = bh.readLEB128(file)
        element_list = getParameters(element_def_amount, file)

        attribute_def_amount = bh.readLEB128(file)
        attribute_list = getParameters(attribute_def_amount, file)

        # Stored relative to the payload start, which begins immediately after
        # the 12-byte outer header.
        attributes_offset = bh.readuint64(
            _read_exact(file, 8, "BXML attribute stream offset")
        )
        attribute_stream_offset = BINARY_HEADER_SIZE + attributes_offset

        tag_amount = bh.readLEB128(file)
        element_tags = getElementTags(element_list, tag_amount, file)

        file.seek(0, os.SEEK_END)
        actual_file_size = file.tell()
        if attribute_stream_offset > actual_file_size:
            raise ValueError(
                f"Attribute stream offset 0x{attribute_stream_offset:X} "
                f"is outside the file (size 0x{actual_file_size:X})"
            )

        file.seek(attribute_stream_offset)
        attribute_map, element_values = getAttributes(
            tag_amount,
            attribute_list,
            file,
        )

    if include_element_values:
        return element_tags, attribute_map, element_values

    return element_tags, attribute_map
