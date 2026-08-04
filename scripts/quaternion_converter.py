from bpy_extras.io_utils import axis_conversion
from mathutils import Matrix, Quaternion


INPUT = "-0.0259,0.0259,-0.7066,0.7066"


GAME_TO_BLENDER = axis_conversion(
    from_forward="Z",
    from_up="Y",
    to_forward="-Y",
    to_up="Z",
).to_4x4()


def parse_xyzw(value: str) -> tuple[float, float, float, float]:
    cleaned = value.strip().strip('"').strip("'")
    parts = [part.strip().strip('"').strip("'") for part in cleaned.split(",")]
    if len(parts) != 4:
        raise ValueError(f"Expected 4 comma-separated values, got {len(parts)}: {value!r}")
    return tuple(float(part) for part in parts)


def convert_game_xyzw_to_blender(value: str) -> Quaternion:
    x, y, z, w = parse_xyzw(value)

    source_rotation = Quaternion((w, x, y, z))
    source_rotation.normalize()

    converted_matrix = GAME_TO_BLENDER @ source_rotation.to_matrix().to_4x4()
    _location, converted_rotation, _scale = converted_matrix.decompose()
    converted_rotation.normalize()
    return converted_rotation


def format_wxyz(rotation: Quaternion) -> str:
    return (
        f"{rotation.w:.8f},"
        f"{rotation.x:.8f},"
        f"{rotation.y:.8f},"
        f"{rotation.z:.8f}"
    )


def format_xyzw(rotation: Quaternion) -> str:
    return (
        f"{rotation.x:.8f},"
        f"{rotation.y:.8f},"
        f"{rotation.z:.8f},"
        f"{rotation.w:.8f}"
    )


rotation = convert_game_xyzw_to_blender(INPUT)
print("Input game XYZW:", INPUT)
print("Output Blender WXYZ:", format_wxyz(rotation))
print("Output Blender XYZW:", format_xyzw(rotation))
