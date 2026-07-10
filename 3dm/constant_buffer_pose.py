import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_CB_VALUE_RE = re.compile(
    r"^(?:buf|cb)\S*(?:\s+|[:=]\s*)(?:=|\:)?\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
)


@dataclass(frozen=True)
class ConstantBufferPose:
    rows: list[list[float]]

    def __post_init__(self):
        if len(self.rows) % 3:
            raise ValueError(
                "Bone matrix rows must be a multiple of 3, got %i rows"
                % len(self.rows)
            )

    @property
    def bone_count(self) -> int:
        return len(self.rows) // 3

    def as_3x4_rows(self) -> list[list[list[float]]]:
        return [self.rows[i : i + 3] for i in range(0, len(self.rows), 3)]

    def as_4x4_rows(self) -> list[list[list[float]]]:
        return [matrix + [[0.0, 0.0, 0.0, 1.0]] for matrix in self.as_3x4_rows()]


def parse_cb_float4_rows(
    lines: Iterable[str], start_row: int = 0, end_row: int | None = None
) -> list[list[float]]:
    """Parse 3DMigoto text constant-buffer dumps into float4 register rows.

    start_row and end_row use constant-buffer register units. end_row is
    inclusive. Use end_row=None to read to EOF.
    """
    rows = []
    row = []
    row_idx = 0

    for line in lines:
        match = _CB_VALUE_RE.match(line.strip())
        if not match:
            continue

        row.append(float(match.group(1)))
        if len(row) != 4:
            continue

        if row_idx >= start_row:
            rows.append(row)
        row = []
        row_idx += 1

        if end_row is not None and row_idx > end_row:
            break

    if row:
        raise ValueError("Incomplete float4 row in constant-buffer dump")
    if end_row is not None and row_idx <= end_row:
        raise ValueError(
            "end_row %i is outside constant-buffer dump with %i rows"
            % (end_row, row_idx)
        )

    return rows


def parse_cb_float4_rows_buf(
    data: bytes, start_row: int, end_row: int
) -> list[list[float]]:
    """Parse binary 3DMigoto .buf constant-buffer dumps into float4 rows."""
    float_size = 4
    row_float_count = 4
    row_byte_size = row_float_count * float_size

    if len(data) % row_byte_size:
        raise ValueError(
            "Constant-buffer .buf size must be a multiple of 16 bytes, got %i bytes"
            % len(data)
        )

    row_count = len(data) // row_byte_size
    if start_row >= row_count and row_count:
        raise ValueError(
            "start_row %i is outside constant-buffer .buf with %i rows"
            % (start_row, row_count)
        )
    if end_row >= row_count:
        raise ValueError(
            "end_row %i is outside constant-buffer .buf with %i rows"
            % (end_row, row_count)
        )

    rows = []
    for row_idx in range(row_count):
        if row_idx < start_row:
            continue
        if row_idx > end_row:
            break

        offset = row_idx * row_byte_size
        rows.append(list(struct.unpack_from("<4f", data, offset)))

    return rows


def parse_constant_buffer_pose(
    lines: Iterable[str], start_row: int = 0, end_row: int | None = None
) -> ConstantBufferPose:
    return ConstantBufferPose(parse_cb_float4_rows(lines, start_row, end_row))


def parse_constant_buffer_pose_buf(
    data: bytes, start_row: int, end_row: int
) -> ConstantBufferPose:
    return ConstantBufferPose(parse_cb_float4_rows_buf(data, start_row, end_row))


def parse_constant_buffer_pose_file(
    filepath: str | Path, start_row: int = 0, end_row: int | None = None
) -> ConstantBufferPose:
    if start_row < 0:
        raise ValueError("start_row must be >= 0")
    if end_row is None:
        raise ValueError("end_row must be specified")
    if end_row < start_row:
        raise ValueError("end_row must be >= start_row")
    if (end_row - start_row + 1) % 3:
        raise ValueError(
            "Bone matrix row range length must be a multiple of 3, got rows %i-%i"
            % (start_row, end_row)
        )

    path = Path(filepath)
    if path.suffix.lower() == ".buf":
        return parse_constant_buffer_pose_buf(path.read_bytes(), start_row, end_row)
    with path.open("r") as f:
        return parse_constant_buffer_pose(f, start_row, end_row)
