from bpy_extras.io_utils import axis_conversion
from mathutils import Matrix


GAME_TO_BLENDER = axis_conversion(
    from_forward="Z",
    from_up="Y",
    to_forward="-Y",
    to_up="Z",
).to_4x4()
BLENDER_TO_GAME = GAME_TO_BLENDER.inverted()

NEOX_TO_BLENDER_BONE_AXES = Matrix(
    (
        (0.0, 1.0, 0.0, 0.0),
        (-1.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
)

NEOX_LOCAL_TO_BLENDER_BONE = Matrix(
    (
        (0.0, -1.0, 0.0, 0.0),
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
)
BLENDER_BONE_TO_NEOX_LOCAL = NEOX_LOCAL_TO_BLENDER_BONE.inverted()
