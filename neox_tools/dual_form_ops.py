from __future__ import annotations

import copy
import json
import os
import posixpath
import re
import struct
from pathlib import Path, PurePosixPath
import xml.etree.ElementTree as ET

import bpy
from bpy.props import StringProperty

from .animation_export_ops import (
    DEFAULT_ACCUMULATION_FLAGS,
    VectorKey,
    build_data_payload,
    build_file_bytes,
    build_head_payload,
    build_name_payload,
    pack_section,
)
from .mod_exporter.xml_converter import convert_handler, io_handler, parse_handler


XXH64_PRIME_1 = 11400714785074694791
XXH64_PRIME_2 = 14029467366897019727
XXH64_PRIME_3 = 1609587929392839161
XXH64_PRIME_4 = 9650029242287828579
XXH64_PRIME_5 = 2870177450012600261
MASK64 = 0xFFFFFFFFFFFFFFFF


def _rotl64(value: int, bits: int) -> int:
    return ((value << bits) | (value >> (64 - bits))) & MASK64


def _xxh64_round(acc: int, lane: int) -> int:
    acc = (acc + lane * XXH64_PRIME_2) & MASK64
    acc = _rotl64(acc, 31)
    acc = (acc * XXH64_PRIME_1) & MASK64
    return acc


def _xxh64_merge(acc: int, lane: int) -> int:
    acc ^= _xxh64_round(0, lane)
    acc = (acc * XXH64_PRIME_1 + XXH64_PRIME_4) & MASK64
    return acc


def xxhash64_hex(text: str) -> str:
    data = text.encode("utf-8")
    length = len(data)
    offset = 0

    if length >= 32:
        v1 = (XXH64_PRIME_1 + XXH64_PRIME_2) & MASK64
        v2 = XXH64_PRIME_2
        v3 = 0
        v4 = (-XXH64_PRIME_1) & MASK64
        while offset <= length - 32:
            v1 = _xxh64_round(v1, struct.unpack_from("<Q", data, offset)[0])
            v2 = _xxh64_round(v2, struct.unpack_from("<Q", data, offset + 8)[0])
            v3 = _xxh64_round(v3, struct.unpack_from("<Q", data, offset + 16)[0])
            v4 = _xxh64_round(v4, struct.unpack_from("<Q", data, offset + 24)[0])
            offset += 32
        value = (
            _rotl64(v1, 1)
            + _rotl64(v2, 7)
            + _rotl64(v3, 12)
            + _rotl64(v4, 18)
        ) & MASK64
        value = _xxh64_merge(value, v1)
        value = _xxh64_merge(value, v2)
        value = _xxh64_merge(value, v3)
        value = _xxh64_merge(value, v4)
    else:
        value = XXH64_PRIME_5

    value = (value + length) & MASK64
    while offset <= length - 8:
        lane = struct.unpack_from("<Q", data, offset)[0]
        value ^= _xxh64_round(0, lane)
        value = (_rotl64(value, 27) * XXH64_PRIME_1 + XXH64_PRIME_4) & MASK64
        offset += 8
    if offset <= length - 4:
        value ^= struct.unpack_from("<I", data, offset)[0] * XXH64_PRIME_1
        value = (_rotl64(value, 23) * XXH64_PRIME_2 + XXH64_PRIME_3) & MASK64
        offset += 4
    while offset < length:
        value ^= data[offset] * XXH64_PRIME_5
        value = (_rotl64(value, 11) * XXH64_PRIME_1) & MASK64
        offset += 1

    value ^= value >> 33
    value = (value * XXH64_PRIME_2) & MASK64
    value ^= value >> 29
    value = (value * XXH64_PRIME_3) & MASK64
    value ^= value >> 32
    return f"{value:016x}"


def _normalize_slashes(path: str) -> str:
    return str(path).strip().replace("\\", "/")


def _documents_res_parts(path: Path) -> tuple[list[str], int]:
    parts = list(path.resolve(strict=False).parts)
    lower = [part.lower() for part in parts]
    for index in range(len(lower) - 1):
        if lower[index] == "documents" and lower[index + 1] == "res":
            return parts, index
    raise ValueError(f"Path is not inside Documents/res: {path}")


def _documents_res_relative(path: Path) -> str:
    parts, index = _documents_res_parts(path)
    return "/".join(parts[index + 2 :]).replace("\\", "/")


def _mod_root_relative(path: Path) -> str:
    relative = _documents_res_relative(path)
    pieces = [piece for piece in relative.split("/") if piece]
    if len(pieces) >= 2 and pieces[0].lower() == "mod":
        return "/".join(pieces[:2])
    return posixpath.dirname(relative)


def _safe_output_path(root: Path, relative_path: str) -> Path:
    normalized = posixpath.normpath(_normalize_slashes(relative_path))
    if normalized.startswith("../") or normalized == ".." or normalized.startswith("/"):
        raise ValueError(f"Path escapes output folder: {relative_path}")
    return root.joinpath(*PurePosixPath(normalized).parts)


def _relative_from_file(from_path: Path, target: str) -> str:
    from_dir = posixpath.dirname(_documents_res_relative(from_path))
    return posixpath.relpath(_normalize_slashes(target), start=from_dir).replace("\\", "/")


def _load_gim(path: Path) -> tuple[ET.Element, bool]:
    if parse_handler.typeFile(str(path)) == "Binary":
        element_tags, attribute_map = parse_handler.parseCustomBinFormat(str(path))
        return convert_handler.tagWrapper(element_tags, attribute_map)[0], True
    return ET.parse(path).getroot(), False


def _write_gim(path: Path, root: ET.Element, binary: bool) -> None:
    if binary:
        io_handler.ExportGim(
            str(path),
            convert_handler.xml_to_custom_bin(convert_handler.xml_to_bfs_list(root)),
        )
    else:
        io_handler.ExportUndecodedGim(str(path), root)


def _file_value(root: ET.Element, section: str) -> str:
    node = root.find(section)
    if node is None:
        return ""
    file_name = node.find("FileName")
    if file_name is None:
        return ""
    return _normalize_slashes(file_name.attrib.get("Value", ""))


def _set_file_value(root: ET.Element, section: str, value: str) -> None:
    node = root.find(section)
    if node is None:
        node = ET.SubElement(root, section)
    file_name = node.find("FileName")
    if file_name is None:
        file_name = ET.SubElement(node, "FileName")
    file_name.attrib["Value"] = value


def _resolve_reference_from_gim(gim_path: Path, reference: str) -> tuple[str, bool, Path | None]:
    reference = _normalize_slashes(reference)
    if not reference:
        raise ValueError(f"Empty gim reference in {gim_path}")
    if reference.lower().startswith("chr/"):
        return reference.strip("/"), True, None

    gim_relative_dir = posixpath.dirname(_documents_res_relative(gim_path))
    target_relative = posixpath.normpath(posixpath.join(gim_relative_dir, reference)).replace("\\", "/")
    mod_root = _mod_root_relative(gim_path)
    is_remote = not (
        target_relative == mod_root
        or target_relative.startswith(f"{mod_root}/")
    )

    if is_remote:
        return target_relative.strip("/"), True, None

    local_path = gim_path.parent / Path(reference.replace("/", os.sep))
    return _documents_res_relative(local_path), False, local_path


def _load_animconfig(asset_index, gim_path: Path, reference: str) -> tuple[ET.Element, str, bool, Path | None]:
    resolved, is_remote, local_path = _resolve_reference_from_gim(gim_path, reference)
    if is_remote:
        data = asset_index.extract(resolved).data
        return ET.fromstring(data.decode("utf-8-sig")), resolved, True, None
    return ET.parse(local_path).getroot(), resolved, False, local_path


def _load_skeleton_bone_names(
    asset_index,
    skeleton_reference: str,
    is_remote: bool,
    local_path: Path | None,
) -> tuple[list[str], int]:
    if is_remote:
        data = asset_index.extract(skeleton_reference).data
        source = skeleton_reference
    else:
        if local_path is None:
            raise ValueError(f"Local skeleton path is missing: {skeleton_reference}")
        data = local_path.read_bytes()
        source = str(local_path)

    try:
        skeleton = json.loads(data.decode("utf-8-sig"))
    except Exception as exc:
        raise ValueError(f"Skeleton is not valid JSON: {source}") from exc

    joint_names = skeleton.get("joint_names")
    parent_indices = skeleton.get("joint_parent_indices")
    if not isinstance(joint_names, list) or not all(isinstance(name, str) and name for name in joint_names):
        raise ValueError(f"Skeleton has no valid joint_names: {source}")
    if not isinstance(parent_indices, list) or len(parent_indices) != len(joint_names):
        raise ValueError(f"Skeleton joint_parent_indices does not match joint_names: {source}")

    root_indices = [
        index
        for index, parent_index in enumerate(parent_indices)
        if int(parent_index) == -1
    ]
    if not root_indices:
        raise ValueError(f"Skeleton has no root bone with parent index -1: {source}")
    return joint_names, root_indices[0]


def _resolve_animation_reference(
    asset_index,
    animconfig_reference: str,
    animation_reference: str,
) -> str:
    normalized = _normalize_slashes(animation_reference).strip("/")
    lowered = normalized.lower()
    if lowered.startswith("chr/") or lowered.startswith("mod/"):
        return normalized

    base_dir = posixpath.dirname(animconfig_reference.strip("/"))
    return posixpath.normpath(posixpath.join(base_dir, normalized)).replace("\\", "/")


def _write_animconfig(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="\t")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=False)


def _safe_file_stem(value: str, fallback: str) -> str:
    stem = "".join(
        char if char.isalnum() or char in ("_", "-") else "_"
        for char in str(value).strip()
    ).strip("_")
    return stem or fallback


def _write_disabled_cpdanimation(
    path: Path,
    skeleton_path: str,
    bone_names: list[str],
    root_index: int,
) -> None:
    from mathutils import Vector

    class _Track:
        def __init__(self, name: str, is_root: bool):
            self.name = name
            self.position_keys = []
            self.scale_keys = [VectorKey(0, Vector((0.0, 0.0, 0.0)))] if is_root else []
            self.rotation_keys = []

    if not bone_names:
        raise ValueError("Disabled animation requires at least one skeleton bone")
    if root_index < 0 or root_index >= len(bone_names):
        raise ValueError(f"Invalid disabled animation root index: {root_index}")

    head = pack_section(
        b"HEAD",
        build_head_payload(30, 0.0, True, DEFAULT_ACCUMULATION_FLAGS),
    )
    tracks = [
        _Track(bone_name, index == root_index)
        for index, bone_name in enumerate(bone_names)
    ]
    data = pack_section(b"DATA", build_data_payload(tracks))
    name = pack_section(b"NAME", build_name_payload("disabled", bone_names))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_file_bytes(skeleton_path, head, data, name, None))


def _write_disabled_cpdanimation_for_folder(
    folder: Path,
    skeleton_documents_res_path: str,
    bone_names: list[str],
    root_index: int,
) -> Path:
    cpdanimation_path = folder / "disabled.cpdanimation"
    skeleton_cpdanimation_relative_path = _relative_from_file(
        cpdanimation_path,
        skeleton_documents_res_path,
    )
    _write_disabled_cpdanimation(
        cpdanimation_path,
        skeleton_cpdanimation_relative_path,
        bone_names,
        root_index,
    )
    return cpdanimation_path


def _load_animation_text_for_copy(
    asset_index,
    output_path: Path,
    source_reference: str,
    animation_reference: str,
) -> tuple[str, str]:
    official = _resolve_animation_reference(
        asset_index,
        source_reference,
        animation_reference,
    )
    try:
        data = asset_index.extract(official).data
    except Exception as exc:
        raise ValueError(f"Animation asset could not be found by asset finder: {official}") from exc

    try:
        return data.decode("utf-8-sig"), official
    except Exception as exc:
        raise ValueError(f"Animation asset is not valid UTF-8 JSON text: {official}") from exc


def _localize_animation_dependency_text(raw_text: str, cpdanimation_path: Path) -> str:
    dependency = _documents_res_relative(cpdanimation_path)
    match = re.search(r'("Dependices"\s*:\s*)\[(.*?)\]', raw_text, flags=re.DOTALL)
    if match is None:
        raise ValueError("Animation JSON has no _FileHeader.Dependices array")

    line_start = raw_text.rfind("\n", 0, match.start()) + 1
    line_indent = raw_text[line_start:match.start()]
    item_indent = f"{line_indent}\t"
    replacement = (
        f'{match.group(1)}[\n'
        f"{item_indent}{json.dumps(dependency, ensure_ascii=False)}\n"
        f"{line_indent}]"
    )
    return raw_text[:match.start()] + replacement + raw_text[match.end():]


def _write_disabled_animation_copy(
    asset_index,
    output_path: Path,
    animation_folder: Path,
    source_reference: str,
    animation_reference: str,
    animation_name: str,
    cpdanimation_path: Path,
    used_file_names: set[str],
) -> str:
    animation_text, official = _load_animation_text_for_copy(
        asset_index,
        output_path,
        source_reference,
        animation_reference,
    )
    animation_text = _localize_animation_dependency_text(animation_text, cpdanimation_path)

    stem = _safe_file_stem(animation_name, Path(official).stem or "disabled")
    file_name = f"{stem}.animation"
    counter = 1
    while file_name.lower() in used_file_names:
        file_name = f"{stem}_{counter}.animation"
        counter += 1
    used_file_names.add(file_name.lower())

    output_animation_path = animation_folder / file_name
    output_animation_path.write_text(animation_text, encoding="utf-8")
    return f"{animation_folder.name}/{file_name}"


def _rewrite_animconfig(
    asset_index,
    source_root: ET.Element,
    source_reference: str,
    output_path: Path,
    skeleton_path: str,
    skeleton_bone_names: list[str],
    skeleton_root_index: int,
    trigger_names: set[str],
    active_on_trigger: bool,
) -> None:
    output_root = output_path.parent
    animation_folder = output_root / f"animations_{output_path.stem}"
    animation_folder.mkdir(parents=True, exist_ok=True)
    disabled_cpdanimation_path = _write_disabled_cpdanimation_for_folder(
        animation_folder,
        skeleton_path,
        skeleton_bone_names,
        skeleton_root_index,
    )
    used_disabled_file_names: set[str] = set()

    root = copy.deepcopy(source_root)
    for animation in root.findall("./AnimationList/Animation"):
        name = animation.attrib.get("Name", "").strip()
        file_name = animation.attrib.get("FileName", "").strip()
        if not file_name:
            continue

        is_trigger = name.lower() in trigger_names
        is_active = is_trigger if active_on_trigger else not is_trigger
        if is_active:
            try:
                official = _resolve_animation_reference(
                    asset_index,
                    source_reference,
                    file_name,
                )
                animation.attrib["FileName"] = _relative_from_file(output_path, official)
            except Exception:
                local_target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(source_reference), _normalize_slashes(file_name))
                ).replace("\\", "/")
                animation.attrib["FileName"] = posixpath.relpath(
                    local_target,
                    start=posixpath.dirname(_documents_res_relative(output_path)),
                ).replace("\\", "/")
        else:
            animation.attrib["FileName"] = _write_disabled_animation_copy(
                asset_index,
                output_path,
                animation_folder,
                source_reference,
                file_name,
                name,
                disabled_cpdanimation_path,
                used_disabled_file_names,
            )

    _write_animconfig(output_path, root)


def _next_socket_index(socket_objects: ET.Element) -> int:
    max_index = -1
    for child in socket_objects:
        _prefix, separator, suffix = child.tag.rpartition("_")
        if separator and suffix.isdigit():
            max_index = max(max_index, int(suffix))
    return max_index + 1


def _add_dual_form_socket(main_root: ET.Element, main_gim_path: Path, dual_gim_path: Path) -> None:
    socket_objects = main_root.find("SocketObject")
    if socket_objects is None:
        socket_objects = ET.SubElement(main_root, "SocketObject")

    socket_index = _next_socket_index(socket_objects)
    object_name = dual_gim_path.stem
    socket = ET.SubElement(
        socket_objects,
        f"Socket_{socket_index}",
        {
            "BindType": "7",
            "BindingBone": "",
            "BindingFlag": "2",
            "LocalPosition": "0.0000,0.0000,0.0000",
            "LocalRotation": "0.0000,0.0000,0.0000,1.0000",
            "LocalScale": "1.0000,1.0000,1.0000",
            "Name": object_name,
            "PlayRatePolicy": "1",
            "PreloadingLevel": "4294967295",
            "SubmeshSortIdx": "4294967295",
            "SyncVo": "false",
        },
    )
    ET.SubElement(
        socket,
        "Object",
        {
            "CastShadow": "true",
            "Id": xxhash64_hex(object_name),
            "Inherit": "263",
            "Loading": "4",
            "Name": object_name,
            "Uri": _documents_res_relative(dual_gim_path),
        },
    )


class IDVMI_PG_Dual_Form_Trigger(bpy.types.PropertyGroup):
    name: StringProperty(name="Animation")


class IDVMI_UL_Dual_Form_Triggers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name)


class IDVMI_OT_Dual_Form_Add_Trigger(bpy.types.Operator):
    bl_idname = "idvmi_neox.dual_form_add_trigger"
    bl_label = "Add Dual Form Trigger"

    def execute(self, context):
        text = context.scene.neox_dual_form_trigger_text.strip()
        if not text:
            return {"CANCELLED"}
        existing = {item.name.lower() for item in context.scene.neox_dual_form_triggers}
        if text.lower() not in existing:
            item = context.scene.neox_dual_form_triggers.add()
            item.name = text
            context.scene.neox_dual_form_trigger_index = len(context.scene.neox_dual_form_triggers) - 1
        context.scene.neox_dual_form_trigger_text = ""
        return {"FINISHED"}


class IDVMI_OT_Dual_Form_Remove_Trigger(bpy.types.Operator):
    bl_idname = "idvmi_neox.dual_form_remove_trigger"
    bl_label = "Remove Dual Form Trigger"

    def execute(self, context):
        index = context.scene.neox_dual_form_trigger_index
        triggers = context.scene.neox_dual_form_triggers
        if index < 0 or index >= len(triggers):
            return {"CANCELLED"}
        triggers.remove(index)
        context.scene.neox_dual_form_trigger_index = min(index, len(triggers) - 1)
        return {"FINISHED"}


class IDVMI_OT_Build_Dual_Form_Skin(bpy.types.Operator):
    bl_idname = "idvmi_neox.build_dual_form_skin"
    bl_label = "Build Dual Form Skin"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        main_gim_path = Path(bpy.path.abspath(context.scene.neox_dual_form_main_gim))
        dual_gim_path = Path(bpy.path.abspath(context.scene.neox_dual_form_dual_gim))
        if not main_gim_path.is_file() or not dual_gim_path.is_file():
            self.report({"ERROR"}, "Select both main and dual form .gim files")
            return {"CANCELLED"}

        try:
            from .remote_import import _make_asset_index

            asset_index = _make_asset_index()
            main_root, main_binary = _load_gim(main_gim_path)
            dual_root, dual_binary = _load_gim(dual_gim_path)
            main_anim_ref = _file_value(main_root, "AnimationConfigFile")
            dual_anim_ref = _file_value(dual_root, "AnimationConfigFile")
            main_skeleton, main_skeleton_remote, main_skeleton_local = _resolve_reference_from_gim(
                main_gim_path,
                _file_value(main_root, "SkeletonFile"),
            )
            dual_skeleton, dual_skeleton_remote, dual_skeleton_local = _resolve_reference_from_gim(
                dual_gim_path,
                _file_value(dual_root, "SkeletonFile"),
            )
            main_skeleton_bones, main_skeleton_root_index = _load_skeleton_bone_names(
                asset_index,
                main_skeleton,
                main_skeleton_remote,
                main_skeleton_local,
            )
            dual_skeleton_bones, dual_skeleton_root_index = _load_skeleton_bone_names(
                asset_index,
                dual_skeleton,
                dual_skeleton_remote,
                dual_skeleton_local,
            )

            main_anim_root, main_source_ref, _main_remote, _main_local = _load_animconfig(
                asset_index,
                main_gim_path,
                main_anim_ref,
            )
            dual_anim_root, dual_source_ref, _dual_remote, _dual_local = _load_animconfig(
                asset_index,
                dual_gim_path,
                dual_anim_ref,
            )

            trigger_names = {
                item.name.strip().lower()
                for item in context.scene.neox_dual_form_triggers
                if item.name.strip()
            }
            if not trigger_names:
                self.report({"ERROR"}, "Add at least one dual form trigger animation")
                return {"CANCELLED"}

            main_output_animconfig = main_gim_path.with_suffix(".animconfig")
            dual_output_animconfig = dual_gim_path.with_suffix(".animconfig")
            _rewrite_animconfig(
                asset_index,
                main_anim_root,
                main_source_ref,
                main_output_animconfig,
                main_skeleton,
                main_skeleton_bones,
                main_skeleton_root_index,
                trigger_names,
                active_on_trigger=False,
            )
            _rewrite_animconfig(
                asset_index,
                dual_anim_root,
                dual_source_ref,
                dual_output_animconfig,
                dual_skeleton,
                dual_skeleton_bones,
                dual_skeleton_root_index,
                trigger_names,
                active_on_trigger=True,
            )

            _set_file_value(main_root, "AnimationConfigFile", main_output_animconfig.name)
            _set_file_value(dual_root, "AnimationConfigFile", dual_output_animconfig.name)
            _add_dual_form_socket(main_root, main_gim_path, dual_gim_path)
            _write_gim(main_gim_path, main_root, main_binary)
            _write_gim(dual_gim_path, dual_root, dual_binary)
        except Exception as exc:
            self.report({"ERROR"}, f"Dual form build failed: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, "Dual form skin built")
        return {"FINISHED"}
