from __future__ import annotations

import bisect
import io
import math
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Sequence

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper, axis_conversion
from mathutils import Matrix, Quaternion, Vector


SOURCE_FORWARD = "Z"
SOURCE_UP = "Y"
TARGET_FORWARD = "-Y"
TARGET_UP = "Z"

EXPECTED_SIGNATURE = b"RAWANIMA"
SUPPORTED_PACK_PRS_FLAGS = 0x02

X, Y, Z, W = 0, 1, 2, 3

PAYLOAD_COMPONENTS = {
    0: (W, Z, Y),
    1: (X, W, Z),
    2: (Y, X, W),
    3: (Z, Y, X),
}


@dataclass(frozen=True)
class VectorKey:
    frame: int
    value: Vector


@dataclass(frozen=True)
class QuaternionKey:
    frame: int
    value: Quaternion


@dataclass
class BoneTrack:
    name: str
    position_keys: list[VectorKey]
    scale_keys: list[VectorKey]
    rotation_keys: list[QuaternionKey]

    @property
    def keyed_frames(self) -> list[int]:
        return sorted(
            {
                *(key.frame for key in self.position_keys),
                *(key.frame for key in self.scale_keys),
                *(key.frame for key in self.rotation_keys),
            }
        )


@dataclass
class AnimationHeader:
    fps: float
    duration: float
    loop: bool
    has_position_keys: bool
    has_rotation_keys: bool
    has_scale_keys: bool
    pack_prs_flags: int
    accumulation_flags: tuple[int, int, int, int, int]


@dataclass
class CPDAnimation:
    path: str
    version: int
    checksum: bytes
    skeleton_path: str
    name: str
    header: AnimationHeader
    data_const: int
    tracks: list[BoneTrack]


class CPDFormatError(RuntimeError):
    pass


class IDVMI_OT_Import_Neox_Animation(bpy.types.Operator, ImportHelper):
    bl_idname = "idvmi_neox.import_animation"
    bl_label = "Import Animation"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".cpdanimation"
    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(
        default="*.cpdanimation",
        options={"HIDDEN"},
        maxlen=255,
    )

    use_scene_selector: BoolProperty(
        default=False,
        options={"HIDDEN"},
    )

    import_source: StringProperty(
        default="local",
        options={"HIDDEN"},
    )

    def invoke(self, context, event):
        if self.use_scene_selector:
            return self.execute(context)

        self.filter_glob = "*.cpdanimation"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        import_source = self.import_source or context.scene.neox_animation_import_source

        if import_source == "remote":
            animation_asset_path = context.scene.neox_remote_animation_path.strip().replace("\\", "/")
            if not animation_asset_path:
                self.report({"ERROR"}, "Please enter a remote .cpdanimation asset path")
                return {"CANCELLED"}
            if not animation_asset_path.lower().endswith(".cpdanimation"):
                self.report({"ERROR"}, f"Expected a remote .cpdanimation path: {animation_asset_path}")
                return {"CANCELLED"}

            try:
                from .remote_import import extract_remote_asset_to_cache

                cache_root = Path(__file__).resolve().parent / "remote_import_cache" / "animations"
                animation_path = extract_remote_asset_to_cache(animation_asset_path, cache_root)
            except Exception as exc:
                self.report({"ERROR"}, f"Remote animation import failed: {exc}")
                return {"CANCELLED"}
        else:
            animation_path = self.filepath or context.scene.neox_animation_selector
            animation_path = bpy.path.abspath(animation_path)

            if not animation_path:
                self.report({"ERROR"}, "Please select a .cpdanimation file")
                return {"CANCELLED"}

            if os.path.splitext(animation_path)[1].lower() != ".cpdanimation":
                self.report({"ERROR"}, f"Expected a .cpdanimation file: {animation_path}")
                return {"CANCELLED"}

        if not os.path.isfile(animation_path):
            self.report({"ERROR"}, f"File not found: {animation_path}")
            return {"CANCELLED"}

        try:
            armature_obj = get_active_armature(context)
            action = import_animation(animation_path, armature_obj, context.scene)
        except Exception as exc:
            self.report({"ERROR"}, f"[{type(exc).__name__}] {exc}")
            return {"CANCELLED"}

        if import_source == "remote":
            context.scene.neox_remote_animation_path = animation_asset_path
        else:
            context.scene.neox_animation_selector = animation_path
        self.report({"INFO"}, f"Animation import OK -> {action.name}")
        return {"FINISHED"}


def read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise CPDFormatError(
            f"Unexpected end of file: requested {size} bytes, got {len(data)}."
        )
    return data


def read_u8(stream: BinaryIO) -> int:
    return struct.unpack("<B", read_exact(stream, 1))[0]


def read_u16(stream: BinaryIO) -> int:
    return struct.unpack("<H", read_exact(stream, 2))[0]


def read_u32(stream: BinaryIO) -> int:
    return struct.unpack("<I", read_exact(stream, 4))[0]


def read_u64(stream: BinaryIO) -> int:
    return struct.unpack("<Q", read_exact(stream, 8))[0]


def read_f32(stream: BinaryIO) -> float:
    return struct.unpack("<f", read_exact(stream, 4))[0]


def read_f16(stream: BinaryIO) -> float:
    return struct.unpack("<e", read_exact(stream, 2))[0]


def read_f16_vector3(stream: BinaryIO) -> Vector:
    return Vector((read_f16(stream), read_f16(stream), read_f16(stream)))


def decode_component(code: int) -> float:
    return ((2.0 * code / 1023.0) - 1.0) / math.sqrt(2.0)


def unpack_quaternion(packed: int) -> Quaternion:
    selector = (packed >> 30) & 0x3

    codes = (
        (packed >> 0) & 0x3FF,
        (packed >> 10) & 0x3FF,
        (packed >> 20) & 0x3FF,
    )
    values = [decode_component(code) for code in codes]

    xyzw = [0.0, 0.0, 0.0, 0.0]
    for value, component in zip(values, PAYLOAD_COMPONENTS[selector]):
        xyzw[component] = value

    xyzw[selector] = math.sqrt(
        max(0.0, 1.0 - sum(value * value for value in values))
    )

    x, y, z, w = xyzw
    result = Quaternion((w, x, y, z))
    result.normalize()
    return result


def make_quaternion_track_continuous(keys: list[QuaternionKey]) -> list[QuaternionKey]:
    if not keys:
        return []

    result: list[QuaternionKey] = []
    previous: Quaternion | None = None

    for key in keys:
        current = key.value.copy()
        if previous is not None and previous.dot(current) < 0.0:
            current.negate()

        result.append(QuaternionKey(key.frame, current))
        previous = current

    return result


def parse_header(payload: bytes) -> AnimationHeader:
    stream = io.BytesIO(payload)

    fps = read_f32(stream)
    duration = read_f32(stream)
    loop = bool(read_u8(stream))
    has_position_keys = bool(read_u8(stream))
    has_rotation_keys = bool(read_u8(stream))
    has_scale_keys = bool(read_u8(stream))
    pack_prs_flags = read_u8(stream)
    accumulation_flags = tuple(read_u8(stream) for _ in range(5))

    if fps <= 0.0 or not math.isfinite(fps):
        raise CPDFormatError(f"Invalid FPS value: {fps!r}")

    if duration < 0.0 or not math.isfinite(duration):
        raise CPDFormatError(f"Invalid duration value: {duration!r}")

    if pack_prs_flags != SUPPORTED_PACK_PRS_FLAGS:
        raise CPDFormatError(
            "Unsupported pack_prs_flags: "
            f"0x{pack_prs_flags:02X}; expected "
            f"0x{SUPPORTED_PACK_PRS_FLAGS:02X}."
        )

    return AnimationHeader(
        fps=fps,
        duration=duration,
        loop=loop,
        has_position_keys=has_position_keys,
        has_rotation_keys=has_rotation_keys,
        has_scale_keys=has_scale_keys,
        pack_prs_flags=pack_prs_flags,
        accumulation_flags=accumulation_flags,
    )


def parse_name_section(payload: bytes) -> tuple[str, list[str]]:
    stream = io.BytesIO(payload)
    string_count = read_u32(stream)

    if string_count < 1:
        raise CPDFormatError("NAME section contains no strings.")

    strings: list[str] = []
    for index in range(string_count):
        length = read_u32(stream)
        raw = read_exact(stream, length)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CPDFormatError(
                f"NAME string {index} is not valid UTF-8."
            ) from exc

        strings.append(text.rstrip("\x00"))

    animation_name = strings[0]
    bone_names = strings[1:]

    if not animation_name:
        raise CPDFormatError("Animation name is empty.")

    if any(not name for name in bone_names):
        raise CPDFormatError("NAME section contains an empty bone name.")

    if len(set(bone_names)) != len(bone_names):
        raise CPDFormatError("NAME section contains duplicate bone names.")

    return animation_name, bone_names


def parse_position_track(
    stream: BinaryIO,
    *,
    allow_unsorted_keys: bool = False,
    warnings: list[str] | None = None,
    bone_name: str = "",
) -> list[VectorKey]:
    count = read_u16(stream)
    result: list[VectorKey] = []

    previous_frame = -1
    for _ in range(count):
        frame = read_u16(stream)
        value = read_f16_vector3(stream)

        if frame < previous_frame:
            message = "Position keyframes are not sorted."
            if not allow_unsorted_keys:
                raise CPDFormatError(message)
            if warnings is not None:
                label = f"{bone_name}: " if bone_name else ""
                warnings.append(f"{label}{message}")

        result.append(VectorKey(frame, value))
        previous_frame = frame

    return result


def parse_scale_track(
    stream: BinaryIO,
    *,
    allow_unsorted_keys: bool = False,
    warnings: list[str] | None = None,
    bone_name: str = "",
) -> list[VectorKey]:
    count = read_u16(stream)
    result: list[VectorKey] = []

    previous_frame = -1
    for _ in range(count):
        frame = read_u16(stream)
        value = read_f16_vector3(stream)

        if frame < previous_frame:
            message = "Scale keyframes are not sorted."
            if not allow_unsorted_keys:
                raise CPDFormatError(message)
            if warnings is not None:
                label = f"{bone_name}: " if bone_name else ""
                warnings.append(f"{label}{message}")

        result.append(VectorKey(frame, value))
        previous_frame = frame

    return result


def parse_rotation_track(
    stream: BinaryIO,
    *,
    allow_unsorted_keys: bool = False,
    warnings: list[str] | None = None,
    bone_name: str = "",
) -> list[QuaternionKey]:
    count = read_u16(stream)
    result: list[QuaternionKey] = []

    previous_frame = -1
    for _ in range(count):
        frame = read_u16(stream)
        packed = read_u32(stream)

        if frame < previous_frame:
            message = "Rotation keyframes are not sorted."
            if not allow_unsorted_keys:
                raise CPDFormatError(message)
            if warnings is not None:
                label = f"{bone_name}: " if bone_name else ""
                warnings.append(f"{label}{message}")

        result.append(QuaternionKey(frame, unpack_quaternion(packed)))
        previous_frame = frame

    return make_quaternion_track_continuous(result)


def parse_data_section(
    payload: bytes,
    bone_names: Sequence[str],
    *,
    allow_unsorted_keys: bool = False,
    warnings: list[str] | None = None,
) -> tuple[int, list[BoneTrack]]:
    stream = io.BytesIO(payload)
    data_const = read_u8(stream)

    if data_const != 0x04:
        raise CPDFormatError(
            f"Unexpected DATA constant 0x{data_const:02X}; expected 0x04."
        )

    tracks: list[BoneTrack] = []
    for bone_name in bone_names:
        tracks.append(
            BoneTrack(
                name=bone_name,
                position_keys=parse_position_track(
                    stream,
                    allow_unsorted_keys=allow_unsorted_keys,
                    warnings=warnings,
                    bone_name=bone_name,
                ),
                scale_keys=parse_scale_track(
                    stream,
                    allow_unsorted_keys=allow_unsorted_keys,
                    warnings=warnings,
                    bone_name=bone_name,
                ),
                rotation_keys=parse_rotation_track(
                    stream,
                    allow_unsorted_keys=allow_unsorted_keys,
                    warnings=warnings,
                    bone_name=bone_name,
                ),
            )
        )

    remainder = stream.read()
    if any(byte != 0 for byte in remainder):
        print(
            "[CPD] Warning: DATA alignment contains non-zero bytes: "
            f"{len(remainder)} byte(s)."
        )

    return data_const, tracks


def parse_cpdanimation(
    path: str,
    *,
    allow_unsorted_keys: bool = False,
    warnings: list[str] | None = None,
) -> CPDAnimation:
    actual_size = os.path.getsize(path)

    with open(path, "rb") as stream:
        signature = read_exact(stream, 8)
        if signature != EXPECTED_SIGNATURE:
            raise CPDFormatError(
                f"Invalid signature: {signature!r}; expected {EXPECTED_SIGNATURE!r}."
            )

        file_size_field = read_u64(stream)
        version = read_u32(stream)
        header_offset = read_u32(stream)
        checksum = read_exact(stream, 16)

        skeleton_path_length = read_u32(stream)
        skeleton_path_raw = read_exact(stream, skeleton_path_length)
        try:
            skeleton_path = skeleton_path_raw.decode("utf-8").rstrip("\x00")
        except UnicodeDecodeError as exc:
            raise CPDFormatError("Skeleton path is not valid UTF-8.") from exc

        if file_size_field != actual_size:
            raise CPDFormatError(
                "File-size mismatch: header says "
                f"{file_size_field}, actual size is {actual_size}."
            )

        if header_offset < stream.tell():
            raise CPDFormatError(
                f"Header offset 0x{header_offset:X} points inside the file preamble."
            )

        stream.seek(header_offset)

        sections: dict[bytes, bytes] = {}
        while stream.tell() < actual_size:
            remaining = actual_size - stream.tell()
            if remaining == 0:
                break
            if remaining < 8:
                raise CPDFormatError(
                    f"Incomplete section header at file offset 0x{stream.tell():X}."
                )

            section_name = read_exact(stream, 4)
            section_length = read_u32(stream)
            section_payload = read_exact(stream, section_length)

            if section_name in sections:
                raise CPDFormatError(f"Duplicate section {section_name!r}.")
            sections[section_name] = section_payload

    for required in (b"HEAD", b"DATA", b"NAME"):
        if required not in sections:
            raise CPDFormatError(f"Missing required section {required!r}.")

    header = parse_header(sections[b"HEAD"])
    animation_name, bone_names = parse_name_section(sections[b"NAME"])
    data_const, tracks = parse_data_section(
        sections[b"DATA"],
        bone_names,
        allow_unsorted_keys=allow_unsorted_keys,
        warnings=warnings,
    )

    return CPDAnimation(
        path=path,
        version=version,
        checksum=checksum,
        skeleton_path=skeleton_path,
        name=animation_name,
        header=header,
        data_const=data_const,
        tracks=tracks,
    )


def id_property_matrix_to_source_column(value: object) -> Matrix:
    flat = [float(component) for component in value]  # type: ignore[arg-type]
    if len(flat) != 16:
        raise RuntimeError(
            f"Expected 16 matrix elements in Neox:BoneMatrix, got {len(flat)}."
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


def make_trs_matrix(
    translation: Vector,
    rotation: Quaternion,
    scale: Vector,
) -> Matrix:
    return (
        Matrix.Translation(translation)
        @ rotation.to_matrix().to_4x4()
        @ Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0))
    )


def sample_vector_track(keys: Sequence[VectorKey], frame: int, default: Vector) -> Vector:
    if not keys:
        return default.copy()
    if len(keys) == 1 or frame <= keys[0].frame:
        return keys[0].value.copy()
    if frame >= keys[-1].frame:
        return keys[-1].value.copy()

    frames = [key.frame for key in keys]
    right_index = bisect.bisect_right(frames, frame)
    left = keys[right_index - 1]
    right = keys[right_index]

    if frame == left.frame:
        return left.value.copy()
    if right.frame == left.frame:
        return right.value.copy()

    factor = (frame - left.frame) / (right.frame - left.frame)
    return left.value.lerp(right.value, factor)


def sample_quaternion_track(
    keys: Sequence[QuaternionKey],
    frame: int,
    default: Quaternion,
) -> Quaternion:
    if not keys:
        return default.copy()
    if len(keys) == 1 or frame <= keys[0].frame:
        return keys[0].value.copy()
    if frame >= keys[-1].frame:
        return keys[-1].value.copy()

    frames = [key.frame for key in keys]
    right_index = bisect.bisect_right(frames, frame)
    left = keys[right_index - 1]
    right = keys[right_index]

    if frame == left.frame:
        return left.value.copy()
    if right.frame == left.frame:
        return right.value.copy()

    factor = (frame - left.frame) / (right.frame - left.frame)
    result = left.value.slerp(right.value, factor)
    result.normalize()
    return result


def get_active_armature(context) -> bpy.types.Object:
    obj = context.view_layer.objects.active
    if obj is None or obj.type != "ARMATURE":
        raise RuntimeError("The active object must be the target armature.")

    if "NeoX:BoneOrder" not in obj:
        raise RuntimeError('Active armature has no "NeoX:BoneOrder" property.')
    if "Neox:BoneMatrix" not in obj:
        raise RuntimeError('Active armature has no "Neox:BoneMatrix" property.')

    return obj


def build_source_rest_data(
    armature_obj: bpy.types.Object,
    animation_bone_names: Sequence[str],
) -> tuple[
    dict[str, Matrix],
    dict[str, Matrix],
    dict[str, tuple[Vector, Quaternion, Vector]],
]:
    source_order = list(armature_obj["NeoX:BoneOrder"])
    source_matrices = armature_obj["Neox:BoneMatrix"]

    if len(source_order) != len(source_matrices):
        raise RuntimeError(
            "NeoX:BoneOrder and Neox:BoneMatrix have different lengths: "
            f"{len(source_order)} vs {len(source_matrices)}."
        )

    source_index = {str(name): index for index, name in enumerate(source_order)}
    if len(source_index) != len(source_order):
        raise RuntimeError("NeoX:BoneOrder contains duplicate names.")

    missing_from_source_properties = [
        name for name in animation_bone_names if name not in source_index
    ]
    if missing_from_source_properties:
        raise RuntimeError(
            "Animation bone(s) are absent from NeoX:BoneOrder: "
            + ", ".join(missing_from_source_properties)
        )

    source_global_rest: dict[str, Matrix] = {}
    for name in animation_bone_names:
        source_global_rest[name] = id_property_matrix_to_source_column(
            source_matrices[source_index[name]]
        )

    axis_matrix = axis_conversion(
        from_forward=SOURCE_FORWARD,
        from_up=SOURCE_UP,
        to_forward=TARGET_FORWARD,
        to_up=TARGET_UP,
    ).to_4x4()

    correction: dict[str, Matrix] = {}
    source_local_rest_trs: dict[str, tuple[Vector, Quaternion, Vector]] = {}

    for name in animation_bone_names:
        blender_bone = armature_obj.data.bones[name]
        source_global = source_global_rest[name]

        converted_source_rest = axis_matrix @ source_global
        blender_global_rest = blender_bone.matrix_local.copy()

        correction[name] = converted_source_rest.inverted_safe() @ blender_global_rest

        if blender_bone.parent is None:
            source_local = source_global.copy()
        else:
            parent_name = blender_bone.parent.name
            if parent_name not in source_global_rest:
                raise RuntimeError(
                    f"Animation bone '{name}' has parent '{parent_name}', but that "
                    "parent is not present in the animation track list."
                )
            source_local = (
                source_global_rest[parent_name].inverted_safe() @ source_global
            )

        location, rotation, scale = source_local.decompose()
        rotation.normalize()
        source_local_rest_trs[name] = (location, rotation, scale)

    return source_global_rest, correction, source_local_rest_trs


def replace_action(armature_obj: bpy.types.Object, action_name: str) -> bpy.types.Action:
    armature_obj.animation_data_create()

    existing = bpy.data.actions.get(action_name)
    if existing is not None:
        if armature_obj.animation_data.action == existing:
            armature_obj.animation_data.action = None
        bpy.data.actions.remove(existing, do_unlink=True)

    action = bpy.data.actions.new(action_name)
    armature_obj.animation_data.action = action
    return action


def set_scene_fps(scene: bpy.types.Scene, fps: float) -> None:
    rounded = max(1, int(round(fps)))
    scene.render.fps = rounded
    scene.render.fps_base = rounded / fps


def set_action_interpolation_linear(action: bpy.types.Action) -> None:
    for fcurve in action.fcurves:
        for keyframe in fcurve.keyframe_points:
            keyframe.interpolation = "LINEAR"


def store_cpdanimation_header_properties(
    armature_obj: bpy.types.Object,
    animation: CPDAnimation,
) -> None:
    prefix = "NeoX:CPDAnimation:"
    header = animation.header

    armature_obj[f"{prefix}checksum"] = animation.checksum.hex()
    armature_obj[f"{prefix}SkeletonPath"] = animation.skeleton_path
    armature_obj[f"{prefix}fps"] = header.fps
    armature_obj[f"{prefix}loop"] = header.loop
    armature_obj[f"{prefix}has_position_keys"] = header.has_position_keys
    armature_obj[f"{prefix}has_rotation_keys"] = header.has_rotation_keys
    armature_obj[f"{prefix}has_scale_keys"] = header.has_scale_keys
    armature_obj[f"{prefix}pack_prs_flags"] = header.pack_prs_flags
    armature_obj[f"{prefix}accumulation_flags"] = list(header.accumulation_flags)


def import_animation(
    animation_path: str,
    armature_obj: bpy.types.Object,
    scene: bpy.types.Scene,
) -> bpy.types.Action:
    animation = parse_cpdanimation(animation_path)
    animation_bone_names = [track.name for track in animation.tracks]

    missing_armature_bones = [
        name for name in animation_bone_names if name not in armature_obj.data.bones
    ]
    if missing_armature_bones:
        raise RuntimeError(
            "Animation bone(s) are absent from the active armature: "
            + ", ".join(missing_armature_bones)
        )

    _, correction, source_local_rest_trs = build_source_rest_data(
        armature_obj,
        animation_bone_names,
    )

    if armature_obj.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    for pose_bone in armature_obj.pose.bones:
        pose_bone.matrix_basis = Matrix.Identity(4)
        pose_bone.rotation_mode = "QUATERNION"

    action = replace_action(armature_obj, animation.name)
    set_scene_fps(scene, animation.header.fps)

    max_keyed_frame = 0
    previous_blender_rotation: dict[str, Quaternion] = {}

    for track in animation.tracks:
        keyed_frames = track.keyed_frames
        if not keyed_frames:
            continue

        pose_bone = armature_obj.pose.bones[track.name]
        pose_bone.rotation_mode = "QUATERNION"

        rest_location, rest_rotation, rest_scale = source_local_rest_trs[track.name]
        rest_local_matrix = make_trs_matrix(
            rest_location,
            rest_rotation,
            rest_scale,
        )
        rest_local_inverse = rest_local_matrix.inverted_safe()

        bone_correction = correction[track.name]
        bone_correction_inverse = bone_correction.inverted_safe()

        for frame in keyed_frames:
            source_location = sample_vector_track(
                track.position_keys,
                frame,
                rest_location,
            )
            source_rotation = sample_quaternion_track(
                track.rotation_keys,
                frame,
                rest_rotation,
            )
            source_scale = sample_vector_track(
                track.scale_keys,
                frame,
                rest_scale,
            )

            source_animated_local = make_trs_matrix(
                source_location,
                source_rotation,
                source_scale,
            )

            source_local_delta = rest_local_inverse @ source_animated_local
            blender_matrix_basis = (
                bone_correction_inverse
                @ source_local_delta
                @ bone_correction
            )

            location, rotation, scale = blender_matrix_basis.decompose()
            rotation.normalize()

            previous = previous_blender_rotation.get(track.name)
            if previous is not None and previous.dot(rotation) < 0.0:
                rotation.negate()
            previous_blender_rotation[track.name] = rotation.copy()

            pose_bone.location = location
            pose_bone.rotation_quaternion = rotation
            pose_bone.scale = scale

            pose_bone.keyframe_insert(
                data_path="location",
                frame=frame,
                group=track.name,
            )
            pose_bone.keyframe_insert(
                data_path="rotation_quaternion",
                frame=frame,
                group=track.name,
            )
            pose_bone.keyframe_insert(
                data_path="scale",
                frame=frame,
                group=track.name,
            )

            max_keyed_frame = max(max_keyed_frame, frame)

    set_action_interpolation_linear(action)

    duration_frame = int(round(animation.header.duration * animation.header.fps))
    scene.frame_start = 0
    scene.frame_end = max(max_keyed_frame, duration_frame)
    scene.frame_set(0)
    bpy.context.view_layer.update()

    print("[CPD] Import complete")
    print(f"[CPD] File: {animation.path}")
    print(f"[CPD] Action: {action.name}")
    print(f"[CPD] Skeleton path: {animation.skeleton_path}")
    print(f"[CPD] Bones: {len(animation.tracks)}")
    print(f"[CPD] FPS: {animation.header.fps:g}")
    print(f"[CPD] Duration: {animation.header.duration:g} s")
    print(f"[CPD] Frame range: 0-{scene.frame_end}")
    print(
        "[CPD] accumulation_flags ignored: "
        + " ".join(f"0x{value:02X}" for value in animation.header.accumulation_flags)
    )

    store_cpdanimation_header_properties(armature_obj, animation)

    return action
