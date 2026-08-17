from __future__ import annotations

from dataclasses import dataclass
import json
import struct
from typing import Any


BPSE_HEADER_SIZE = 0x10
BPSE_REFERENCE_MARKER = "__bpse_reference__"
BPSE_STRING_TABLE_ROOT_KEY = "0"
BPSE_OBJECT_ROOT_KEY = "1"

JsonValue = None | bool | int | float | str | list[Any] | dict[str, Any]


@dataclass(frozen=True)
class BPSEReference:
    value: int


@dataclass(frozen=True)
class BPSEDocument:
    magic: bytes
    unk0: int
    unk1: int
    unk2: int
    root: Any


class BPSEError(ValueError):
    pass


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def read(self, size: int, context: str) -> bytes:
        if size < 0:
            raise BPSEError(f"Negative read size for {context}: {size}")
        end = self.offset + size
        if end > len(self.data):
            raise EOFError(f"Unexpected end of BPSE data while reading {context}")
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def u8(self, context: str) -> int:
        return self.read(1, context)[0]

    def uint(self, width: int, context: str) -> int:
        return int.from_bytes(self.read(width, context), "little")

    def f32(self, context: str) -> float:
        return struct.unpack("<f", self.read(4, context))[0]

    def f64(self, context: str) -> float:
        return struct.unpack("<d", self.read(8, context))[0]


def from_bytes(
    data: bytes,
    *,
    key_names: list[str] | tuple[str, ...] | dict[int, str] | None = None,
    require_root_dict: bool = True,
    resolve_string_table: bool = True,
) -> BPSEDocument:
    if len(data) < BPSE_HEADER_SIZE:
        raise EOFError("BPSE data is shorter than the 16-byte header")

    reader = _Reader(data)
    magic = reader.read(8, "magic")
    unk0 = reader.uint(2, "unk0")
    unk1 = reader.uint(2, "unk1")
    unk2 = reader.uint(4, "unk2")
    parse_key_names = None if resolve_string_table else key_names
    root = _read_value(reader, has_key=False, key_names=parse_key_names)

    if require_root_dict and not isinstance(root, dict):
        raise BPSEError("BPSE payload root is not a dictionary")
    if reader.offset != len(data):
        raise BPSEError(
            f"BPSE data has {len(data) - reader.offset} trailing byte(s)"
        )
    if resolve_string_table:
        root = resolve_string_table_keys(root)
    return BPSEDocument(magic=magic, unk0=unk0, unk1=unk1, unk2=unk2, root=root)


def loads(
    data: bytes,
    *,
    key_names: list[str] | tuple[str, ...] | dict[int, str] | None = None,
    include_header: bool = True,
    indent: int | None = 4,
    resolve_string_table: bool = True,
) -> str:
    document = from_bytes(
        data,
        key_names=key_names,
        resolve_string_table=resolve_string_table,
    )
    if include_header:
        payload: JsonValue = {
            "magic": document.magic.hex(),
            "unk0": document.unk0,
            "unk1": document.unk1,
            "unk2": document.unk2,
            "root": _to_json_value(document.root),
        }
    else:
        payload = _to_json_value(document.root)
    return json.dumps(payload, ensure_ascii=False, indent=indent)


def resolve_string_table_keys(root: Any) -> Any:
    if not isinstance(root, dict):
        return root

    table = root.get(BPSE_STRING_TABLE_ROOT_KEY)
    object_root = root.get(BPSE_OBJECT_ROOT_KEY)
    if not _is_string_table(table) or object_root is None:
        return root
    return _resolve_value_keys(object_root, table)


def _read_value(
    reader: _Reader,
    *,
    has_key: bool,
    key_names: list[str] | tuple[str, ...] | dict[int, str] | None,
) -> Any:
    if has_key:
        _read_key_index(reader)

    type_and_data = reader.u8("value tag")
    return _read_value_body(reader, type_and_data, key_names=key_names)


def _read_entry(
    reader: _Reader,
    *,
    key_names: list[str] | tuple[str, ...] | dict[int, str] | None,
) -> tuple[str, Any]:
    key_index = _read_key_index(reader)
    key = _key_name(key_index, key_names)
    type_and_data = reader.u8("dictionary value tag")
    return key, _read_value_body(reader, type_and_data, key_names=key_names)


def _read_value_body(
    reader: _Reader,
    type_and_data: int,
    *,
    key_names: list[str] | tuple[str, ...] | dict[int, str] | None,
) -> Any:
    storage_class = type_and_data & 0xC0
    extended_type = type_and_data & 0x0F
    width = 1 << ((type_and_data >> 4) & 0x03)

    if storage_class == 0x40:
        length = type_and_data & 0x3F
        return reader.read(length, "compact string").decode("utf-8")

    if storage_class == 0x80:
        return _decode_zigzag(type_and_data & 0x3F)

    if storage_class == 0xC0:
        entry_count = type_and_data & 0x3F
        return _read_dict(reader, entry_count, key_names=key_names)

    if type_and_data == 0x10:
        return None
    if type_and_data == 0x20:
        return False
    if type_and_data == 0x30:
        return True

    if extended_type == 0x01:
        return _decode_zigzag(reader.uint(width, "zigzag integer"))
    if extended_type == 0x02:
        return reader.uint(width, "unsigned integer")
    if extended_type == 0x03:
        if width == 4:
            return reader.f32("float32")
        if width == 8:
            return reader.f64("float64")
        raise BPSEError(f"Invalid BPSE floating-point width: {width}")
    if extended_type == 0x04:
        length = reader.uint(width, "string length")
        return reader.read(length, "string").decode("utf-8")
    if extended_type == 0x08:
        element_count = reader.uint(width, "list element count")
        return [
            _read_value(reader, has_key=False, key_names=key_names)
            for _ in range(element_count)
        ]
    if extended_type == 0x09:
        entry_count = reader.uint(width, "dictionary entry count")
        return _read_dict(reader, entry_count, key_names=key_names)
    if extended_type == 0x0A:
        return BPSEReference(reader.uint(width, "reference value"))

    raise BPSEError(f"Unknown BPSE value tag: 0x{type_and_data:02X}")


def _read_dict(
    reader: _Reader,
    entry_count: int,
    *,
    key_names: list[str] | tuple[str, ...] | dict[int, str] | None,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for _ in range(entry_count):
        key, value = _read_entry(reader, key_names=key_names)
        values[key] = value
    return values


def _read_key_index(reader: _Reader) -> int:
    prefix = reader.u8("key index prefix")
    if (prefix & 0x80) == 0:
        return prefix
    if (prefix & 0x40) == 0:
        return ((prefix & 0x3F) << 8) | reader.u8("key index byte 1")
    if (prefix & 0x20) == 0:
        byte_1 = reader.u8("key index byte 1")
        byte_2 = reader.u8("key index byte 2")
        return ((prefix & 0x1F) << 16) | (byte_1 << 8) | byte_2
    if (prefix & 0x10) == 0:
        byte_1 = reader.u8("key index byte 1")
        byte_2 = reader.u8("key index byte 2")
        byte_3 = reader.u8("key index byte 3")
        return ((prefix & 0x0F) << 24) | (byte_1 << 16) | (byte_2 << 8) | byte_3
    return int.from_bytes(reader.read(4, "extended key index"), "big")


def _decode_zigzag(value: int) -> int:
    return value >> 1 if (value & 1) == 0 else -((value >> 1) + 1)


def _key_name(
    key_index: int,
    key_names: list[str] | tuple[str, ...] | dict[int, str] | None,
) -> str:
    if isinstance(key_names, dict):
        return str(key_names.get(key_index, key_index))
    if key_names is not None and 0 <= key_index < len(key_names):
        return str(key_names[key_index])
    return str(key_index)


def _is_string_table(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _resolve_value_keys(value: Any, string_table: list[str]) -> Any:
    if isinstance(value, list):
        return [_resolve_value_keys(item, string_table) for item in value]
    if isinstance(value, dict):
        resolved: dict[str, Any] = {}
        for key, item in value.items():
            resolved[_resolve_table_key(key, string_table)] = _resolve_value_keys(
                item,
                string_table,
            )
        return resolved
    return value


def _resolve_table_key(key: Any, string_table: list[str]) -> str:
    text = str(key)
    if text.isdecimal():
        index = int(text)
        if 0 <= index < len(string_table):
            return string_table[index]
    return text


def _to_json_value(value: Any) -> JsonValue:
    if isinstance(value, BPSEReference):
        return {BPSE_REFERENCE_MARKER: value.value}
    if isinstance(value, list):
        return [_to_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    return value
