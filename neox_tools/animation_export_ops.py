"""
NeoX RAWANIMA / .cpdanimation exporter for Blender 3.6.

Supported format:
    data_PSR_dynamic.hexpat
    pack_prs_flags == 0x02

The exporter reads only the active armature's active Action F-curves.
It intentionally does not bake NLA blending, constraints, or drivers.

Expected armature properties:
    obj["NeoX:BoneOrder"]
    obj["Neox:BoneMatrix"]

Optional imported-animation properties:
    obj["NeoX:CPDAnimation:checksum"]            # 32-character hex string
    obj["NeoX:CPDAnimation:SkeletonPath"]
    obj["NeoX:CPDAnimation:fps"]
    obj["NeoX:CPDAnimation:loop"]
    obj["NeoX:CPDAnimation:has_position_keys"]
    obj["NeoX:CPDAnimation:has_rotation_keys"]
    obj["NeoX:CPDAnimation:has_scale_keys"]
    obj["NeoX:CPDAnimation:pack_prs_flags"]
    obj["NeoX:CPDAnimation:accumulation_flags"]  # list of five integers

The has_* values are written as the known format constants 1, 1, 0.
FPS comes from the operator/function argument, which defaults to scene FPS.
Loop comes from the operator argument; invoke() initializes it from the
armature property when available.
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras.io_utils import axis_conversion
from mathutils import Euler, Matrix, Quaternion, Vector


# -----------------------------------------------------------------------------
# Format constants
# -----------------------------------------------------------------------------

SIGNATURE = b"RAWANIMA"
VERSION = 0
DATA_CONSTANT = 0x04
SUPPORTED_PACK_PRS_FLAGS = 0x02
SECTION_ALIGNMENT = 16

HEADER_HAS_POSITION_KEYS = 1
HEADER_HAS_ROTATION_KEYS = 1
HEADER_HAS_SCALE_KEYS = 0
DEFAULT_ACCUMULATION_FLAGS = (0, 0, 1, 2, 3)

PROPERTY_PREFIX = "NeoX:CPDAnimation:"
PROPERTY_CHECKSUM = PROPERTY_PREFIX + "checksum"
PROPERTY_SKELETON_PATH = PROPERTY_PREFIX + "SkeletonPath"
PROPERTY_LOOP = PROPERTY_PREFIX + "loop"
PROPERTY_PACK_PRS_FLAGS = PROPERTY_PREFIX + "pack_prs_flags"
PROPERTY_ACCUMULATION_FLAGS = PROPERTY_PREFIX + "accumulation_flags"

SKELETON_PRESET_PATHS = {
    "woman": "chr/player/dm65_survivor_w/dm65_survivor_w.skeleton",
    "male": "chr/player/dm65_survivor_m/h55_survivor_m_zbs/h55_survivor_m_zbs.skeleton",
}

SOURCE_FORWARD = "Z"
SOURCE_UP = "Y"
TARGET_FORWARD = "-Y"
TARGET_UP = "Z"

POSITION_TOLERANCE = 1.0e-4
SCALE_TOLERANCE = 1.0e-4
ROTATION_TOLERANCE_DEGREES = 0.05
STATIC_MATRIX_EPSILON = 1.0e-6

X, Y, Z, W = 0, 1, 2, 3

# selector -> low, middle, high 10-bit payload destinations
PAYLOAD_COMPONENTS = {
    0: (W, Z, Y),  # X dropped
    1: (X, W, Z),  # Y dropped
    2: (Y, X, W),  # Z dropped
    3: (Z, Y, X),  # W dropped
}


# -----------------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class VectorKey:
    frame: int
    value: Vector


@dataclass(frozen=True)
class QuaternionKey:
    frame: int
    value: Quaternion


@dataclass
class ExportBoneTrack:
    name: str
    position_keys: list[VectorKey]
    scale_keys: list[VectorKey]
    rotation_keys: list[QuaternionKey]


@dataclass
class SourceRestBone:
    name: str
    source_global: Matrix
    source_local: Matrix
    source_location: Vector
    source_rotation: Quaternion
    source_scale: Vector
    correction: Matrix


@dataclass
class BoneActionCurves:
    location: tuple[bpy.types.FCurve | None, ...]
    scale: tuple[bpy.types.FCurve | None, ...]
    rotation_kind: str | None
    rotation: tuple[bpy.types.FCurve | None, ...]

    @property
    def has_location(self) -> bool:
        return any(curve is not None for curve in self.location)

    @property
    def has_scale(self) -> bool:
        return any(curve is not None for curve in self.scale)

    @property
    def has_rotation(self) -> bool:
        return self.rotation_kind is not None and any(
            curve is not None for curve in self.rotation
        )


@dataclass(frozen=True)
class BoneChannelDefaults:
    location: tuple[float, float, float]
    scale: tuple[float, float, float]
    rotation_kind: str
    rotation_values: tuple[float, ...]
    euler_order: str


@dataclass
class ExportResult:
    filepath: str
    action_name: str
    bone_count: int
    position_key_count: int
    scale_key_count: int
    rotation_key_count: int
    fps: int
    duration: float
    frame_end: int
    warnings: list[str]


class CPDExportError(RuntimeError):
    pass


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------


def align_up(value: int, alignment: int = SECTION_ALIGNMENT) -> int:
    if alignment <= 0 or alignment & (alignment - 1):
        raise ValueError("Alignment must be a positive power of two.")
    return (value + alignment - 1) & ~(alignment - 1)


def pad_payload(payload: bytes, alignment: int = SECTION_ALIGNMENT) -> bytes:
    target_size = align_up(len(payload), alignment)
    return payload + (b"\x00" * (target_size - len(payload)))


def pack_section(tag: bytes, raw_payload: bytes) -> bytes:
    if len(tag) != 4:
        raise ValueError("Section tag must contain exactly four bytes.")
    payload = pad_payload(raw_payload)
    return tag + struct.pack("<I", len(payload)) + payload


def make_trs_matrix(
    location: Vector,
    rotation: Quaternion,
    scale: Vector,
) -> Matrix:
    rotation = rotation.normalized()
    return (
        Matrix.Translation(location)
        @ rotation.to_matrix().to_4x4()
        @ Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0))
    )


def matrices_close(a: Matrix, b: Matrix, tolerance: float) -> bool:
    return max(
        abs(float(a[row][column]) - float(b[row][column]))
        for row in range(4)
        for column in range(4)
    ) <= tolerance


def effective_scene_fps(scene: bpy.types.Scene) -> float:
    fps_base = float(scene.render.fps_base)
    if fps_base <= 0.0:
        raise CPDExportError("Scene fps_base must be greater than zero.")
    return float(scene.render.fps) / fps_base


def warn_once(warnings: list[str], message: str) -> None:
    if message not in warnings:
        warnings.append(message)
        print(f"[CPD Export] Warning: {message}")


# -----------------------------------------------------------------------------
# Source-rest conversion
# -----------------------------------------------------------------------------


def id_property_matrix_to_source_column(value: object) -> Matrix:
    """Convert flattened source row-vector matrix to column-vector Matrix."""
    flat = [float(component) for component in value]  # type: ignore[arg-type]
    if len(flat) != 16:
        raise CPDExportError(
            f"Expected 16 values in Neox:BoneMatrix, got {len(flat)}."
        )

    source_row = Matrix(
        (
            flat[0:4],
            flat[4:8],
            flat[8:12],
            flat[12:16],
        )
    )
    return source_row.transposed()


def get_active_armature_with_action() -> tuple[bpy.types.Object, bpy.types.Action]:
    obj = bpy.context.view_layer.objects.active
    if obj is None or obj.type != "ARMATURE":
        raise CPDExportError("The active object must be the armature to export.")

    if "NeoX:BoneOrder" not in obj:
        raise CPDExportError('Active armature has no "NeoX:BoneOrder" property.')
    if "Neox:BoneMatrix" not in obj:
        raise CPDExportError('Active armature has no "Neox:BoneMatrix" property.')

    animation_data = obj.animation_data
    if animation_data is None or animation_data.action is None:
        raise CPDExportError("The active armature has no active Action.")

    return obj, animation_data.action


def build_source_rest_bones(
    armature_obj: bpy.types.Object,
    bone_order: Sequence[str],
) -> dict[str, SourceRestBone]:
    source_matrices_property = armature_obj["Neox:BoneMatrix"]
    source_order_property = [str(name) for name in armature_obj["NeoX:BoneOrder"]]

    if list(bone_order) != source_order_property:
        raise CPDExportError(
            "The supplied bone order does not match NeoX:BoneOrder."
        )

    if len(source_order_property) != len(source_matrices_property):
        raise CPDExportError(
            "NeoX:BoneOrder and Neox:BoneMatrix have different lengths: "
            f"{len(source_order_property)} vs {len(source_matrices_property)}."
        )

    missing_blender_bones = [
        name for name in source_order_property if name not in armature_obj.data.bones
    ]
    if missing_blender_bones:
        raise CPDExportError(
            "NeoX:BoneOrder contains bone(s) absent from the armature: "
            + ", ".join(missing_blender_bones)
        )

    source_global_by_name: dict[str, Matrix] = {}
    for index, name in enumerate(source_order_property):
        source_global_by_name[name] = id_property_matrix_to_source_column(
            source_matrices_property[index]
        )

    axis_matrix = axis_conversion(
        from_forward=SOURCE_FORWARD,
        from_up=SOURCE_UP,
        to_forward=TARGET_FORWARD,
        to_up=TARGET_UP,
    ).to_4x4()

    result: dict[str, SourceRestBone] = {}

    for name in source_order_property:
        blender_bone = armature_obj.data.bones[name]
        source_global = source_global_by_name[name]

        if blender_bone.parent is None:
            source_local = source_global.copy()
        else:
            parent_name = blender_bone.parent.name
            if parent_name not in source_global_by_name:
                raise CPDExportError(
                    f"Bone '{name}' has parent '{parent_name}', but the parent is "
                    "missing from NeoX:BoneOrder."
                )
            source_local = (
                source_global_by_name[parent_name].inverted_safe()
                @ source_global
            )

        source_location, source_rotation, source_scale = source_local.decompose()
        source_rotation.normalize()

        # This is the same per-bone correction used by the working importer:
        # BlenderRestGlobal = (Axis @ SourceRestGlobal) @ correction
        converted_source_rest = axis_matrix @ source_global
        blender_global_rest = blender_bone.matrix_local.copy()
        correction = converted_source_rest.inverted_safe() @ blender_global_rest

        result[name] = SourceRestBone(
            name=name,
            source_global=source_global,
            source_local=source_local,
            source_location=source_location,
            source_rotation=source_rotation,
            source_scale=source_scale,
            correction=correction,
        )

    return result


# -----------------------------------------------------------------------------
# Action/F-curve extraction
# -----------------------------------------------------------------------------


def find_curves(
    action: bpy.types.Action,
    data_path: str,
    component_count: int,
) -> tuple[bpy.types.FCurve | None, ...]:
    return tuple(
        action.fcurves.find(data_path, index=index)
        for index in range(component_count)
    )


def detect_bone_curves(
    action: bpy.types.Action,
    pose_bone: bpy.types.PoseBone,
) -> BoneActionCurves:
    location_path = pose_bone.path_from_id("location")
    scale_path = pose_bone.path_from_id("scale")

    location = find_curves(action, location_path, 3)
    scale = find_curves(action, scale_path, 3)

    candidates = (
        (
            "QUATERNION",
            find_curves(
                action,
                pose_bone.path_from_id("rotation_quaternion"),
                4,
            ),
        ),
        (
            "EULER",
            find_curves(
                action,
                pose_bone.path_from_id("rotation_euler"),
                3,
            ),
        ),
        (
            "AXIS_ANGLE",
            find_curves(
                action,
                pose_bone.path_from_id("rotation_axis_angle"),
                4,
            ),
        ),
    )

    active_candidates = [
        (kind, curves)
        for kind, curves in candidates
        if any(curve is not None for curve in curves)
    ]

    if len(active_candidates) > 1:
        kinds = ", ".join(kind for kind, _ in active_candidates)
        raise CPDExportError(
            f"Bone '{pose_bone.name}' has multiple rotation representations in "
            f"the active Action: {kinds}."
        )

    if active_candidates:
        rotation_kind, rotation = active_candidates[0]
    else:
        rotation_kind, rotation = None, tuple()

    return BoneActionCurves(
        location=location,
        scale=scale,
        rotation_kind=rotation_kind,
        rotation=rotation,
    )


def capture_bone_defaults(
    pose_bone: bpy.types.PoseBone,
    curves: BoneActionCurves,
) -> BoneChannelDefaults:
    if curves.rotation_kind == "QUATERNION":
        rotation_kind = "QUATERNION"
        rotation_values = tuple(float(value) for value in pose_bone.rotation_quaternion)
    elif curves.rotation_kind == "EULER":
        rotation_kind = "EULER"
        rotation_values = tuple(float(value) for value in pose_bone.rotation_euler)
    elif curves.rotation_kind == "AXIS_ANGLE":
        rotation_kind = "AXIS_ANGLE"
        rotation_values = tuple(float(value) for value in pose_bone.rotation_axis_angle)
    elif pose_bone.rotation_mode == "QUATERNION":
        rotation_kind = "QUATERNION"
        rotation_values = tuple(float(value) for value in pose_bone.rotation_quaternion)
    elif pose_bone.rotation_mode == "AXIS_ANGLE":
        rotation_kind = "AXIS_ANGLE"
        rotation_values = tuple(float(value) for value in pose_bone.rotation_axis_angle)
    else:
        rotation_kind = "EULER"
        rotation_values = tuple(float(value) for value in pose_bone.rotation_euler)

    return BoneChannelDefaults(
        location=tuple(float(value) for value in pose_bone.location),
        scale=tuple(float(value) for value in pose_bone.scale),
        rotation_kind=rotation_kind,
        rotation_values=rotation_values,
        euler_order=(
            pose_bone.rotation_mode
            if pose_bone.rotation_mode not in {"QUATERNION", "AXIS_ANGLE"}
            else "XYZ"
        ),
    )


def evaluate_components(
    curves: Sequence[bpy.types.FCurve | None],
    defaults: Sequence[float],
    frame: float,
) -> tuple[float, ...]:
    if len(curves) != len(defaults):
        raise ValueError("Curve/default component count mismatch.")

    return tuple(
        float(curve.evaluate(frame)) if curve is not None else float(default)
        for curve, default in zip(curves, defaults)
    )


def evaluate_blender_matrix_basis(
    curves: BoneActionCurves,
    defaults: BoneChannelDefaults,
    frame: float,
) -> Matrix:
    location_values = evaluate_components(
        curves.location,
        defaults.location,
        frame,
    )
    scale_values = evaluate_components(
        curves.scale,
        defaults.scale,
        frame,
    )

    location = Vector(location_values)
    scale = Vector(scale_values)

    if curves.rotation_kind == "QUATERNION":
        values = evaluate_components(
            curves.rotation,
            defaults.rotation_values,
            frame,
        )
        rotation = Quaternion(values)
    elif curves.rotation_kind == "EULER":
        values = evaluate_components(
            curves.rotation,
            defaults.rotation_values,
            frame,
        )
        rotation = Euler(values, defaults.euler_order).to_quaternion()
    elif curves.rotation_kind == "AXIS_ANGLE":
        values = evaluate_components(
            curves.rotation,
            defaults.rotation_values,
            frame,
        )
        angle, axis_x, axis_y, axis_z = values
        axis = Vector((axis_x, axis_y, axis_z))
        if axis.length_squared <= 1.0e-16:
            rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
        else:
            axis.normalize()
            rotation = Quaternion(axis, angle)
    elif defaults.rotation_kind == "QUATERNION":
        rotation = Quaternion(defaults.rotation_values)
    elif defaults.rotation_kind == "EULER":
        rotation = Euler(
            defaults.rotation_values,
            defaults.euler_order,
        ).to_quaternion()
    elif defaults.rotation_kind == "AXIS_ANGLE":
        angle, axis_x, axis_y, axis_z = defaults.rotation_values
        axis = Vector((axis_x, axis_y, axis_z))
        if axis.length_squared <= 1.0e-16:
            rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
        else:
            axis.normalize()
            rotation = Quaternion(axis, angle)
    else:
        raise CPDExportError(
            f"Unsupported rotation kind {defaults.rotation_kind!r}."
        )

    if rotation.dot(rotation) <= 1.0e-16:
        rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
    else:
        rotation.normalize()

    return make_trs_matrix(location, rotation, scale)


def fcurve_key_times(curves: Iterable[bpy.types.FCurve | None]) -> list[float]:
    times: set[float] = set()
    for curve in curves:
        if curve is None:
            continue
        times.update(float(point.co.x) for point in curve.keyframe_points)
        times.update(float(point.co.x) for point in curve.sampled_points)
    return sorted(times)


def output_frame_from_blender_frame(
    blender_frame: float,
    action_start: float,
    scene_fps: float,
    export_fps: int,
) -> tuple[int, float]:
    exact_output_frame = (
        (blender_frame - action_start) * float(export_fps) / scene_fps
    )
    return int(math.floor(exact_output_frame + 0.5)), exact_output_frame


def blender_frame_from_output_frame(
    output_frame: int,
    action_start: float,
    scene_fps: float,
    export_fps: int,
) -> float:
    return action_start + (float(output_frame) * scene_fps / float(export_fps))


def convert_key_times_to_output_frames(
    key_times: Sequence[float],
    action_start: float,
    scene_fps: float,
    export_fps: int,
    warnings: list[str],
    label: str,
) -> list[int]:
    result: set[int] = set()
    source_count_by_output: dict[int, int] = {}

    for key_time in key_times:
        output_frame, exact_output = output_frame_from_blender_frame(
            key_time,
            action_start,
            scene_fps,
            export_fps,
        )

        if output_frame < 0:
            warn_once(
                warnings,
                f"{label}: a key before action.frame_range start was clamped to frame 0.",
            )
            output_frame = 0

        if output_frame > 0xFFFF:
            raise CPDExportError(
                f"{label}: frame {output_frame} exceeds the u16 limit 65535."
            )

        if abs(exact_output - output_frame) > 1.0e-5:
            warn_once(
                warnings,
                f"{label}: fractional/rescaled key times were rounded to integer "
                "CPD frames.",
            )

        source_count_by_output[output_frame] = (
            source_count_by_output.get(output_frame, 0) + 1
        )
        result.add(output_frame)

    if any(count > 1 for count in source_count_by_output.values()):
        warn_once(
            warnings,
            f"{label}: multiple Blender keys collapsed onto the same exported frame.",
        )

    return sorted(result)


def inspect_action_features(
    action: bpy.types.Action,
    warnings: list[str],
) -> None:
    if any(len(curve.modifiers) > 0 for curve in action.fcurves):
        warn_once(
            warnings,
            "F-curve modifiers are not representable as sparse CPD keys; only "
            "their values at exported key times are sampled.",
        )

    nonlinear = False
    for curve in action.fcurves:
        for point in curve.keyframe_points:
            if point.interpolation not in {"LINEAR", "CONSTANT"}:
                nonlinear = True
                break
        if nonlinear:
            break

    if nonlinear:
        warn_once(
            warnings,
            "The Action contains non-linear Blender interpolation. CPD playback "
            "uses linear vector interpolation and quaternion interpolation, so "
            "between-key motion may differ.",
        )


# -----------------------------------------------------------------------------
# Blender basis -> source absolute parent-local TRS
# -----------------------------------------------------------------------------


def source_trs_from_blender_basis(
    blender_basis: Matrix,
    rest: SourceRestBone,
) -> tuple[Vector, Quaternion, Vector]:
    correction = rest.correction
    source_delta = correction @ blender_basis @ correction.inverted_safe()
    source_animated_local = rest.source_local @ source_delta

    location, rotation, scale = source_animated_local.decompose()
    rotation.normalize()
    return location, rotation, scale


def make_quaternion_keys_continuous(
    keys: Sequence[QuaternionKey],
) -> list[QuaternionKey]:
    result: list[QuaternionKey] = []
    previous: Quaternion | None = None

    for key in sorted(keys, key=lambda item: item.frame):
        current = key.value.copy()
        current.normalize()

        if previous is not None and previous.dot(current) < 0.0:
            current.negate()

        result.append(QuaternionKey(key.frame, current))
        previous = current

    return result


# -----------------------------------------------------------------------------
# Key canonicalization/reduction
# -----------------------------------------------------------------------------


def vectors_close(a: Vector, b: Vector, tolerance: float) -> bool:
    return (a - b).length <= tolerance


def quaternion_angle_degrees(a: Quaternion, b: Quaternion) -> float:
    a_normalized = a.normalized()
    b_normalized = b.normalized()
    dot = max(-1.0, min(1.0, abs(a_normalized.dot(b_normalized))))
    return math.degrees(2.0 * math.acos(dot))


def canonicalize_vector_track(
    keys: Sequence[VectorKey],
    rest_value: Vector,
    tolerance: float,
) -> list[VectorKey]:
    if not keys:
        return []

    ordered = sorted(keys, key=lambda item: item.frame)
    first = ordered[0].value

    if all(vectors_close(key.value, first, tolerance) for key in ordered):
        if vectors_close(first, rest_value, tolerance):
            return []
        return [VectorKey(0, first.copy())]

    return [VectorKey(key.frame, key.value.copy()) for key in ordered]


def canonicalize_quaternion_track(
    keys: Sequence[QuaternionKey],
    rest_value: Quaternion,
    tolerance_degrees: float,
) -> list[QuaternionKey]:
    if not keys:
        return []

    ordered = make_quaternion_keys_continuous(keys)
    first = ordered[0].value

    if all(
        quaternion_angle_degrees(key.value, first) <= tolerance_degrees
        for key in ordered
    ):
        if quaternion_angle_degrees(first, rest_value) <= tolerance_degrees:
            return []
        return [QuaternionKey(0, first.copy())]

    return ordered


def reduce_vector_keys(
    keys: Sequence[VectorKey],
    tolerance: float,
) -> list[VectorKey]:
    result = [VectorKey(key.frame, key.value.copy()) for key in keys]

    changed = True
    while changed and len(result) > 2:
        changed = False
        reduced = [result[0]]

        for index in range(1, len(result) - 1):
            left = reduced[-1]
            middle = result[index]
            right = result[index + 1]

            frame_span = right.frame - left.frame
            if frame_span <= 0:
                reduced.append(middle)
                continue

            factor = (middle.frame - left.frame) / frame_span
            predicted = left.value.lerp(right.value, factor)

            if vectors_close(middle.value, predicted, tolerance):
                changed = True
            else:
                reduced.append(middle)

        reduced.append(result[-1])
        result = reduced

    return result


def reduce_quaternion_keys(
    keys: Sequence[QuaternionKey],
    tolerance_degrees: float,
) -> list[QuaternionKey]:
    result = make_quaternion_keys_continuous(keys)

    changed = True
    while changed and len(result) > 2:
        changed = False
        reduced = [result[0]]

        for index in range(1, len(result) - 1):
            left = reduced[-1]
            middle = result[index]
            right = result[index + 1]

            frame_span = right.frame - left.frame
            if frame_span <= 0:
                reduced.append(middle)
                continue

            factor = (middle.frame - left.frame) / frame_span
            predicted = left.value.slerp(right.value, factor)
            predicted.normalize()

            if (
                quaternion_angle_degrees(middle.value, predicted)
                <= tolerance_degrees
            ):
                changed = True
            else:
                reduced.append(middle)

        reduced.append(result[-1])
        result = reduced

    return make_quaternion_keys_continuous(result)


# -----------------------------------------------------------------------------
# Track extraction
# -----------------------------------------------------------------------------


def extract_bone_track(
    armature_obj: bpy.types.Object,
    action: bpy.types.Action,
    bone_name: str,
    rest: SourceRestBone,
    action_start: float,
    scene_fps: float,
    export_fps: int,
    reduce_keys: bool,
    position_tolerance: float,
    scale_tolerance: float,
    rotation_tolerance_degrees: float,
    warnings: list[str],
) -> ExportBoneTrack:
    pose_bone = armature_obj.pose.bones[bone_name]
    curves = detect_bone_curves(action, pose_bone)
    defaults = capture_bone_defaults(pose_bone, curves)

    position_frames = convert_key_times_to_output_frames(
        fcurve_key_times(curves.location),
        action_start,
        scene_fps,
        export_fps,
        warnings,
        f"{bone_name} position",
    )
    scale_frames = convert_key_times_to_output_frames(
        fcurve_key_times(curves.scale),
        action_start,
        scene_fps,
        export_fps,
        warnings,
        f"{bone_name} scale",
    )
    rotation_frames = convert_key_times_to_output_frames(
        fcurve_key_times(curves.rotation),
        action_start,
        scene_fps,
        export_fps,
        warnings,
        f"{bone_name} rotation",
    )

    # A property without F-curves can still have a static non-rest channel value.
    # Frame 0 is sampled so such a value can become one constant key.
    sample_frames = set(position_frames) | set(scale_frames) | set(rotation_frames)
    sample_frames.add(0)

    sampled: dict[int, tuple[Vector, Quaternion, Vector]] = {}
    for output_frame in sorted(sample_frames):
        blender_frame = blender_frame_from_output_frame(
            output_frame,
            action_start,
            scene_fps,
            export_fps,
        )
        blender_basis = evaluate_blender_matrix_basis(
            curves,
            defaults,
            blender_frame,
        )
        sampled[output_frame] = source_trs_from_blender_basis(
            blender_basis,
            rest,
        )

    if not curves.has_location:
        static_location = sampled[0][0]
        if not vectors_close(
            static_location,
            rest.source_location,
            position_tolerance,
        ):
            position_frames = [0]

    if not curves.has_scale:
        static_scale = sampled[0][2]
        if not vectors_close(
            static_scale,
            rest.source_scale,
            scale_tolerance,
        ):
            scale_frames = [0]

    if not curves.has_rotation:
        static_rotation = sampled[0][1]
        if (
            quaternion_angle_degrees(
                static_rotation,
                rest.source_rotation,
            )
            > rotation_tolerance_degrees
        ):
            rotation_frames = [0]

    position_keys = [
        VectorKey(frame, sampled[frame][0].copy())
        for frame in position_frames
    ]
    scale_keys = [
        VectorKey(frame, sampled[frame][2].copy())
        for frame in scale_frames
    ]
    rotation_keys = [
        QuaternionKey(frame, sampled[frame][1].copy())
        for frame in rotation_frames
    ]

    position_keys = canonicalize_vector_track(
        position_keys,
        rest.source_location,
        position_tolerance,
    )
    scale_keys = canonicalize_vector_track(
        scale_keys,
        rest.source_scale,
        scale_tolerance,
    )
    rotation_keys = canonicalize_quaternion_track(
        rotation_keys,
        rest.source_rotation,
        rotation_tolerance_degrees,
    )

    if reduce_keys:
        position_keys = reduce_vector_keys(
            position_keys,
            position_tolerance,
        )
        scale_keys = reduce_vector_keys(
            scale_keys,
            scale_tolerance,
        )
        rotation_keys = reduce_quaternion_keys(
            rotation_keys,
            rotation_tolerance_degrees,
        )

        # Reduction can turn a multi-key track into a constant one.
        position_keys = canonicalize_vector_track(
            position_keys,
            rest.source_location,
            position_tolerance,
        )
        scale_keys = canonicalize_vector_track(
            scale_keys,
            rest.source_scale,
            scale_tolerance,
        )
        rotation_keys = canonicalize_quaternion_track(
            rotation_keys,
            rest.source_rotation,
            rotation_tolerance_degrees,
        )

    for keys, label in (
        (position_keys, "position"),
        (scale_keys, "scale"),
        (rotation_keys, "rotation"),
    ):
        if len(keys) > 0xFFFF:
            raise CPDExportError(
                f"Bone '{bone_name}' has more than 65535 {label} keys."
            )

    return ExportBoneTrack(
        name=bone_name,
        position_keys=position_keys,
        scale_keys=scale_keys,
        rotation_keys=rotation_keys,
    )


# -----------------------------------------------------------------------------
# Binary encoding
# -----------------------------------------------------------------------------


def pack_f16(value: float, label: str) -> bytes:
    if not math.isfinite(value):
        raise CPDExportError(f"{label} contains non-finite value {value!r}.")
    try:
        return struct.pack("<e", float(value))
    except (OverflowError, struct.error) as exc:
        raise CPDExportError(
            f"{label} value {value!r} cannot be represented as float16."
        ) from exc


def encode_quaternion_component(value: float) -> int:
    limit = 1.0 / math.sqrt(2.0)
    clamped = max(-limit, min(limit, float(value)))
    normalized = ((clamped * math.sqrt(2.0)) + 1.0) * 0.5
    code = int(math.floor((normalized * 1023.0) + 0.5))
    return max(0, min(1023, code))


def pack_quaternion(rotation: Quaternion) -> int:
    quaternion = rotation.copy()
    if quaternion.dot(quaternion) <= 1.0e-16:
        quaternion = Quaternion((1.0, 0.0, 0.0, 0.0))
    else:
        quaternion.normalize()

    xyzw = [
        float(quaternion.x),
        float(quaternion.y),
        float(quaternion.z),
        float(quaternion.w),
    ]

    # max() returns the first component on an exact tie: X, then Y, then Z, then W.
    selector = max(range(4), key=lambda index: abs(xyzw[index]))

    # The omitted component is reconstructed with a positive square root.
    if xyzw[selector] < 0.0:
        xyzw = [-value for value in xyzw]

    destinations = PAYLOAD_COMPONENTS[selector]
    codes = [encode_quaternion_component(xyzw[index]) for index in destinations]

    return (
        (selector << 30)
        | (codes[0] << 0)
        | (codes[1] << 10)
        | (codes[2] << 20)
    )


def build_head_payload(
    fps: int,
    duration: float,
    loop: bool,
    accumulation_flags: Sequence[int],
) -> bytes:
    if len(accumulation_flags) != 5:
        raise CPDExportError("accumulation_flags must contain exactly five bytes.")

    return struct.pack(
        "<ffBBBBB5B",
        float(fps),
        float(duration),
        int(bool(loop)),
        HEADER_HAS_POSITION_KEYS,
        HEADER_HAS_ROTATION_KEYS,
        HEADER_HAS_SCALE_KEYS,
        SUPPORTED_PACK_PRS_FLAGS,
        *(int(value) for value in accumulation_flags),
    )


def build_data_payload(tracks: Sequence[ExportBoneTrack]) -> bytes:
    payload = bytearray((DATA_CONSTANT,))

    for track in tracks:
        payload += struct.pack("<H", len(track.position_keys))
        for key in track.position_keys:
            payload += struct.pack("<H", key.frame)
            payload += pack_f16(key.value.x, f"{track.name} position.x")
            payload += pack_f16(key.value.y, f"{track.name} position.y")
            payload += pack_f16(key.value.z, f"{track.name} position.z")

        payload += struct.pack("<H", len(track.scale_keys))
        for key in track.scale_keys:
            payload += struct.pack("<H", key.frame)
            payload += pack_f16(key.value.x, f"{track.name} scale.x")
            payload += pack_f16(key.value.y, f"{track.name} scale.y")
            payload += pack_f16(key.value.z, f"{track.name} scale.z")

        payload += struct.pack("<H", len(track.rotation_keys))
        for key in track.rotation_keys:
            payload += struct.pack("<HI", key.frame, pack_quaternion(key.value))

    return bytes(payload)


def encode_utf8_string(value: str, label: str) -> bytes:
    if not value:
        raise CPDExportError(f"{label} must not be empty.")
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CPDExportError(f"{label} is not valid UTF-8 text.") from exc


def build_name_payload(
    action_name: str,
    bone_order: Sequence[str],
) -> bytes:
    strings = [action_name, *bone_order]
    payload = bytearray(struct.pack("<I", len(strings)))

    for index, text in enumerate(strings):
        encoded = encode_utf8_string(text, f"NAME string {index}")
        payload += struct.pack("<I", len(encoded))
        payload += encoded

    return bytes(payload)


def read_accumulation_flags(armature_obj: bpy.types.Object) -> tuple[int, ...]:
    if PROPERTY_ACCUMULATION_FLAGS not in armature_obj:
        return DEFAULT_ACCUMULATION_FLAGS

    values = tuple(
        int(value)
        for value in armature_obj[PROPERTY_ACCUMULATION_FLAGS]
    )
    if len(values) != 5 or any(value < 0 or value > 255 for value in values):
        raise CPDExportError(
            f"{PROPERTY_ACCUMULATION_FLAGS} must contain five integers in 0..255."
        )
    return values


def read_preserved_checksum(armature_obj: bpy.types.Object) -> bytes | None:
    if PROPERTY_CHECKSUM not in armature_obj:
        return None

    text = str(armature_obj[PROPERTY_CHECKSUM]).strip()
    try:
        checksum = bytes.fromhex(text)
    except ValueError as exc:
        raise CPDExportError(
            f"{PROPERTY_CHECKSUM} is not a valid hexadecimal string."
        ) from exc

    if len(checksum) != 16:
        raise CPDExportError(
            f"{PROPERTY_CHECKSUM} must decode to exactly 16 bytes, got "
            f"{len(checksum)}."
        )
    return checksum


def build_file_bytes(
    skeleton_path: str,
    head_section: bytes,
    data_section: bytes,
    name_section: bytes,
    preserved_checksum: bytes | None,
) -> bytes:
    skeleton_path_bytes = encode_utf8_string(skeleton_path, "Skeleton path")

    fixed_preamble_size = 8 + 8 + 4 + 4 + 16 + 4
    header_offset = fixed_preamble_size + len(skeleton_path_bytes)

    zero_checksum = b"\x00" * 16
    preamble = (
        SIGNATURE
        + struct.pack("<Q", 0)  # patched below
        + struct.pack("<I", VERSION)
        + struct.pack("<I", header_offset)
        + zero_checksum
        + struct.pack("<I", len(skeleton_path_bytes))
        + skeleton_path_bytes
    )

    file_bytes = bytearray(preamble + head_section + data_section + name_section)
    struct.pack_into("<Q", file_bytes, 8, len(file_bytes))

    checksum = preserved_checksum
    if checksum is None:
        # Deterministic fallback for newly authored animations. The checksum
        # field is zero while hashing, avoiding a circular dependency.
        checksum = hashlib.md5(file_bytes).digest()

    file_bytes[24:40] = checksum
    return bytes(file_bytes)


# -----------------------------------------------------------------------------
# Main export function
# -----------------------------------------------------------------------------


def export_cpdanimation(
    filepath: str,
    armature_obj: bpy.types.Object,
    skeleton_path: str,
    *,
    loop: bool | None = None,
    fps: int | None = None,
    reduce_keys: bool = True,
    position_tolerance: float = POSITION_TOLERANCE,
    scale_tolerance: float = SCALE_TOLERANCE,
    rotation_tolerance_degrees: float = ROTATION_TOLERANCE_DEGREES,
) -> ExportResult:
    if armature_obj.type != "ARMATURE":
        raise CPDExportError("armature_obj must be an Armature object.")

    if not filepath:
        raise CPDExportError("Export filepath is empty.")
    if not skeleton_path:
        raise CPDExportError("Skeleton path is empty.")

    animation_data = armature_obj.animation_data
    if animation_data is None or animation_data.action is None:
        raise CPDExportError("The armature has no active Action.")
    action = animation_data.action

    if "NeoX:BoneOrder" not in armature_obj:
        raise CPDExportError('Armature has no "NeoX:BoneOrder" property.')
    if "Neox:BoneMatrix" not in armature_obj:
        raise CPDExportError('Armature has no "Neox:BoneMatrix" property.')

    bone_order = [str(name) for name in armature_obj["NeoX:BoneOrder"]]
    if not bone_order:
        raise CPDExportError("NeoX:BoneOrder is empty.")
    if len(set(bone_order)) != len(bone_order):
        raise CPDExportError("NeoX:BoneOrder contains duplicate bone names.")

    if PROPERTY_PACK_PRS_FLAGS in armature_obj:
        stored_pack_flags = int(armature_obj[PROPERTY_PACK_PRS_FLAGS])
        if stored_pack_flags != SUPPORTED_PACK_PRS_FLAGS:
            raise CPDExportError(
                f"Armature metadata requests pack_prs_flags "
                f"0x{stored_pack_flags:02X}, but this exporter only supports "
                f"0x{SUPPORTED_PACK_PRS_FLAGS:02X}."
            )

    scene = bpy.context.scene
    scene_fps = effective_scene_fps(scene)
    export_fps = int(fps) if fps is not None else int(round(scene_fps))
    if export_fps <= 0:
        raise CPDExportError("Export FPS must be greater than zero.")

    export_loop = (
        bool(loop)
        if loop is not None
        else bool(armature_obj.get(PROPERTY_LOOP, False))
    )

    warnings: list[str] = []
    inspect_action_features(action, warnings)

    action_start = float(action.frame_range[0])
    action_end = float(action.frame_range[1])
    if action_end < action_start:
        action_start, action_end = action_end, action_start

    output_end_frame, exact_output_end = output_frame_from_blender_frame(
        action_end,
        action_start,
        scene_fps,
        export_fps,
    )
    output_end_frame = max(0, output_end_frame)

    if output_end_frame > 0xFFFF:
        raise CPDExportError(
            f"Exported duration reaches frame {output_end_frame}, exceeding "
            "the u16 limit 65535."
        )

    if abs(exact_output_end - output_end_frame) > 1.0e-5:
        warn_once(
            warnings,
            "The Action end time was rounded to an integer CPD frame.",
        )

    if abs(scene_fps - float(export_fps)) > 1.0e-6:
        warn_once(
            warnings,
            f"Action timing was rescaled from scene FPS {scene_fps:g} to export "
            f"FPS {export_fps}.",
        )

    source_rest = build_source_rest_bones(armature_obj, bone_order)

    tracks: list[ExportBoneTrack] = []
    for bone_name in bone_order:
        tracks.append(
            extract_bone_track(
                armature_obj=armature_obj,
                action=action,
                bone_name=bone_name,
                rest=source_rest[bone_name],
                action_start=action_start,
                scene_fps=scene_fps,
                export_fps=export_fps,
                reduce_keys=reduce_keys,
                position_tolerance=position_tolerance,
                scale_tolerance=scale_tolerance,
                rotation_tolerance_degrees=rotation_tolerance_degrees,
                warnings=warnings,
            )
        )

    max_key_frame = max(
        (
            key.frame
            for track in tracks
            for key in (
                *track.position_keys,
                *track.scale_keys,
                *track.rotation_keys,
            )
        ),
        default=0,
    )
    output_end_frame = max(output_end_frame, max_key_frame)
    duration = float(output_end_frame) / float(export_fps)

    accumulation_flags = read_accumulation_flags(armature_obj)
    preserved_checksum = read_preserved_checksum(armature_obj)

    head_section = pack_section(
        b"HEAD",
        build_head_payload(
            fps=export_fps,
            duration=duration,
            loop=export_loop,
            accumulation_flags=accumulation_flags,
        ),
    )
    data_section = pack_section(b"DATA", build_data_payload(tracks))
    name_section = pack_section(
        b"NAME",
        build_name_payload(action.name, bone_order),
    )

    file_bytes = build_file_bytes(
        skeleton_path=skeleton_path,
        head_section=head_section,
        data_section=data_section,
        name_section=name_section,
        preserved_checksum=preserved_checksum,
    )

    output_directory = os.path.dirname(os.path.abspath(filepath))
    if output_directory and not os.path.isdir(output_directory):
        os.makedirs(output_directory, exist_ok=True)

    with open(filepath, "wb") as stream:
        stream.write(file_bytes)

    position_key_count = sum(len(track.position_keys) for track in tracks)
    scale_key_count = sum(len(track.scale_keys) for track in tracks)
    rotation_key_count = sum(len(track.rotation_keys) for track in tracks)

    print("[CPD Export] Export complete")
    print(f"[CPD Export] File: {filepath}")
    print(f"[CPD Export] Action: {action.name}")
    print(f"[CPD Export] Skeleton path: {skeleton_path}")
    print(f"[CPD Export] Bones: {len(tracks)}")
    print(f"[CPD Export] FPS: {export_fps}")
    print(f"[CPD Export] Duration: {duration:g} s")
    print(f"[CPD Export] Frame range: 0-{output_end_frame}")
    print(
        "[CPD Export] Keys: "
        f"position={position_key_count}, "
        f"scale={scale_key_count}, "
        f"rotation={rotation_key_count}"
    )

    return ExportResult(
        filepath=filepath,
        action_name=action.name,
        bone_count=len(tracks),
        position_key_count=position_key_count,
        scale_key_count=scale_key_count,
        rotation_key_count=rotation_key_count,
        fps=export_fps,
        duration=duration,
        frame_end=output_end_frame,
        warnings=warnings,
    )


# -----------------------------------------------------------------------------
# Blender operator
# -----------------------------------------------------------------------------


def find_export_res_directory(output_filepath: str) -> str:
    output_directory = os.path.abspath(os.path.dirname(output_filepath))
    marker = os.path.normcase(os.path.join("Documents", "res"))
    normalized_output = os.path.normcase(os.path.normpath(output_directory))

    parts = []
    current = os.path.normpath(output_directory)
    while True:
        parent, name = os.path.split(current)
        if name:
            parts.append(name)
            current = parent
            continue
        if parent:
            parts.append(parent)
        break

    parts.reverse()
    for index in range(len(parts) - 1):
        pair = os.path.normcase(os.path.join(parts[index], parts[index + 1]))
        if pair != marker:
            continue

        res_dir = os.path.join(*parts[: index + 2])
        normalized_res_dir = os.path.normcase(os.path.normpath(res_dir))
        if (
            normalized_output == normalized_res_dir
            or normalized_output.startswith(normalized_res_dir + os.sep)
        ):
            return res_dir

    raise CPDExportError(
        "Export folder must be inside the game's Documents/res folder."
    )


def normalize_relative_path(path: str) -> str:
    return os.path.normpath(path).replace("\\", "/")


def relative_path_from_output(target_absolute_path: str, output_filepath: str) -> str:
    output_directory = os.path.dirname(os.path.abspath(output_filepath))
    relative_path = os.path.relpath(
        target_absolute_path,
        start=output_directory,
    )
    return normalize_relative_path(relative_path)


def resolve_custom_skeleton_path(
    custom_path: str,
    output_filepath: str,
    game_res_dir: str,
) -> str:
    custom_path = custom_path.strip()
    if not custom_path:
        raise CPDExportError("Custom skeleton path is empty.")

    if os.path.isabs(custom_path):
        return relative_path_from_output(custom_path, output_filepath)

    target_absolute_path = os.path.abspath(os.path.join(game_res_dir, custom_path))
    if os.path.commonpath([game_res_dir, target_absolute_path]) != game_res_dir:
        raise CPDExportError(
            "Custom relative skeleton path resolves outside the game's "
            f"Documents/res folder: {custom_path}"
        )

    roundtrip_relative = normalize_relative_path(
        os.path.relpath(target_absolute_path, start=game_res_dir)
    )
    input_relative = normalize_relative_path(custom_path)
    if roundtrip_relative != input_relative:
        raise CPDExportError(
            "Custom relative skeleton path does not resolve under the game's "
            f"Documents/res folder: {custom_path}"
        )

    return relative_path_from_output(target_absolute_path, output_filepath)


def resolve_export_skeleton_path(
    skeleton_preset: str,
    custom_skeleton_path: str,
    output_filepath: str,
    armature_obj: bpy.types.Object,
) -> str:
    game_res_dir = find_export_res_directory(output_filepath)

    if skeleton_preset == "custom":
        return resolve_custom_skeleton_path(
            custom_skeleton_path,
            output_filepath,
            game_res_dir,
        )

    if skeleton_preset in SKELETON_PRESET_PATHS:
        target_absolute_path = os.path.join(
            game_res_dir,
            *SKELETON_PRESET_PATHS[skeleton_preset].split("/"),
        )
        return relative_path_from_output(target_absolute_path, output_filepath)

    stored_skeleton_path = str(armature_obj.get(PROPERTY_SKELETON_PATH, "")).strip()
    if stored_skeleton_path:
        return stored_skeleton_path.replace("\\", "/")

    raise CPDExportError("Skeleton path is empty.")


class IDVMI_OT_Export_Neox_Animation(bpy.types.Operator):
    bl_idname = "idvmi_neox.export_animation"
    bl_label = "Export Animation"
    bl_options = {"REGISTER"}

    filename_ext = ".cpdanimation"
    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.cpdanimation", options={"HIDDEN"})

    skeleton_preset: StringProperty(
        name="Skeleton",
        default="woman",
    )
    custom_skeleton_path: StringProperty(
        name="Custom Skeleton Path",
        default="",
    )
    loop: BoolProperty(
        name="Loop",
        description="Write the animation loop flag",
        default=False,
    )
    fps: IntProperty(
        name="FPS",
        description=(
            "Output FPS. Key times and the frame range are rescaled when this "
            "differs from the scene FPS"
        ),
        default=30,
        min=1,
        max=1000,
    )
    reduce_keys: BoolProperty(
        name="Reduce Redundant Keys",
        description="Remove keys reproduced by linear interpolation or SLERP",
        default=True,
    )
    position_tolerance: FloatProperty(
        name="Position Tolerance",
        default=POSITION_TOLERANCE,
        min=0.0,
        precision=6,
    )
    scale_tolerance: FloatProperty(
        name="Scale Tolerance",
        default=SCALE_TOLERANCE,
        min=0.0,
        precision=6,
    )
    rotation_tolerance_degrees: FloatProperty(
        name="Rotation Tolerance",
        description="Angular key-reduction tolerance in degrees",
        default=ROTATION_TOLERANCE_DEGREES,
        min=0.0,
        precision=4,
    )

    @classmethod
    def poll(cls, context: bpy.types.Context) -> bool:
        obj = context.object
        return bool(
            obj is not None
            and obj.type == "ARMATURE"
            and obj.animation_data is not None
            and obj.animation_data.action is not None
        )

    def invoke(self, context: bpy.types.Context, event):
        return self.execute(context)

    def execute(self, context: bpy.types.Context):
        try:
            armature_obj, _ = get_active_armature_with_action()

            filepath = self.filepath or context.scene.neox_animation_export_selector
            if not filepath:
                raise CPDExportError("Export filepath is empty.")
            filepath = bpy.path.abspath(filepath)
            if os.path.splitext(filepath)[1].lower() != self.filename_ext:
                filepath += self.filename_ext

            skeleton_preset = (
                self.skeleton_preset or context.scene.neox_animation_skeleton_preset
            )
            custom_skeleton_path = (
                self.custom_skeleton_path
                or context.scene.neox_animation_custom_skeleton_path
            )
            relative_skeleton_path = resolve_export_skeleton_path(
                skeleton_preset,
                custom_skeleton_path,
                filepath,
                armature_obj,
            )

            armature_obj[PROPERTY_LOOP] = self.loop

            result = export_cpdanimation(
                filepath=filepath,
                armature_obj=armature_obj,
                skeleton_path=relative_skeleton_path,
                loop=self.loop,
                fps=self.fps,
                reduce_keys=self.reduce_keys,
                position_tolerance=self.position_tolerance,
                scale_tolerance=self.scale_tolerance,
                rotation_tolerance_degrees=self.rotation_tolerance_degrees,
            )
        except Exception as exc:
            self.report({"ERROR"}, f"{type(exc).__name__}: {exc}")
            print(f"[CPD Export] Failed: {type(exc).__name__}: {exc}")
            return {"CANCELLED"}

        context.scene.neox_animation_export_selector = filepath
        context.scene.neox_animation_skeleton_preset = skeleton_preset
        context.scene.neox_animation_custom_skeleton_path = custom_skeleton_path

        for warning in result.warnings[:8]:
            self.report({"WARNING"}, warning)
        if len(result.warnings) > 8:
            self.report(
                {"WARNING"},
                f"{len(result.warnings) - 8} additional warning(s) were printed "
                "to the console.",
            )

        self.report(
            {"INFO"},
            "Exported "
            f"{result.action_name}: {result.bone_count} bones, "
            f"{result.position_key_count}/{result.scale_key_count}/"
            f"{result.rotation_key_count} P/S/R keys.",
        )
        return {"FINISHED"}
