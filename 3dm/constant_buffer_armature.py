import os
from pathlib import Path

import bpy
from bpy_extras.io_utils import axis_conversion
from mathutils import Matrix, Vector

from .constant_buffer_pose import ConstantBufferPose, parse_constant_buffer_pose_file


def pose_to_blender_matrices(pose: ConstantBufferPose) -> list[Matrix]:
    matrices = []
    for rows in pose.as_3x4_rows():
        matrix = Matrix(rows)
        matrix.resize_4x4()
        matrices.append(matrix)
    return matrices


def create_pose_armature(
    context,
    matrices: list[Matrix],
    name: str,
    target_obj=None,
    target_objs=None,
    limit_bones_to_vertex_groups: bool = True,
    axis_forward: str = "-Z",
    axis_up: str = "Y",
    pose_cb_step: int = 1,
    hide_when_parented: bool = True,
):
    targets = _normalize_targets(target_obj, target_objs)
    if limit_bones_to_vertex_groups and targets:
        max_vertex_groups = max(len(obj.vertex_groups) for obj in targets)
        matrices = matrices[:max_vertex_groups]

    arm_data = bpy.data.armatures.new(name)
    arm = bpy.data.objects.new(name, object_data=arm_data)

    conversion_matrix = axis_conversion(
        from_forward=axis_forward, from_up=axis_up
    ).to_4x4()

    context.scene.collection.objects.link(arm)

    arm.select_set(True)
    context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    for i in range(len(matrices)):
        bone = arm_data.edit_bones.new(str(i * pose_cb_step))
        bone.tail = Vector((0.0, 0.10, 0.0))
    bpy.ops.object.mode_set(mode="OBJECT")

    for i, matrix in enumerate(matrices):
        bone = arm.pose.bones[str(i * pose_cb_step)]
        matrix = matrix.copy()
        matrix.resize_4x4()
        bone.matrix_basis = (conversion_matrix @ matrix) @ conversion_matrix.inverted()

    for target in targets:
        mod = target.modifiers.new(arm.name, "ARMATURE")
        mod.object = arm
        target.parent = arm

    if targets and hide_when_parented:
        arm.hide_set(True)

    return arm


def import_constant_buffer_pose_armature(
    context,
    filepath: str | Path,
    start_row: int,
    end_row: int,
    target_obj=None,
    target_objs=None,
    limit_bones_to_vertex_groups: bool = True,
    axis_forward: str = "-Z",
    axis_up: str = "Y",
    pose_cb_step: int = 1,
):
    pose = parse_constant_buffer_pose_file(filepath, start_row, end_row)
    matrices = pose_to_blender_matrices(pose)
    return create_pose_armature(
        context,
        matrices,
        os.path.basename(filepath),
        target_obj=target_obj,
        target_objs=target_objs,
        limit_bones_to_vertex_groups=limit_bones_to_vertex_groups,
        axis_forward=axis_forward,
        axis_up=axis_up,
        pose_cb_step=pose_cb_step,
    )


def _normalize_targets(target_obj=None, target_objs=None):
    targets = []
    seen = set()

    if target_obj is not None:
        targets.append(target_obj)
        seen.add(target_obj.name)

    if target_objs:
        for obj in target_objs:
            if obj is None or obj.name in seen:
                continue
            targets.append(obj)
            seen.add(obj.name)

    return targets
