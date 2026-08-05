from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import os
import posixpath
import re
from pathlib import Path
from typing import Callable

import bpy
from mathutils import Matrix, Quaternion, Vector

from .coordinate_axes import GAME_TO_BLENDER, NEOX_TO_BLENDER_BONE_AXES
from .remote_import import (
    RemoteMaterialPackage,
    build_remote_material_package,
    _make_asset_index,
    _normalize_asset_path,
    _resolve_reference,
    _xml_root_from_bytes,
)


SKIN_NAME_PATTERN = re.compile(r"[a-z]+[cde]*_([a-z]+)\.gim", re.IGNORECASE)
ALT_SKIN_NAME_PATTERN = re.compile(r"[a-z]+_[a-z]+_([a-z]+)\.gim", re.IGNORECASE)


@dataclass(frozen=True)
class ExtraPartRequest:
    asset_path: str
    socket: dict


@dataclass(frozen=True)
class ExtraPartImportResult:
    requested_path: str
    mesh_asset_path: str
    armature_name: str | None


def import_extra_parts_for_remote_gim(
    *,
    main_package: RemoteMaterialPackage,
    cache_root: Path,
    operator,
    parse_mesh: Callable,
    import_model: Callable,
) -> list[ExtraPartImportResult]:
    """Import socket object GIM dependencies without applying socket transforms.

    NeoX source matrices are row-major. Transform placement is intentionally not
    implemented in this module yet; this first pass only resolves and imports
    each socket dependency through the existing mesh import pipeline.
    """

    main_armature_obj = _active_armature()
    extra_part_requests, warnings = discover_extra_part_gim_paths(main_package)
    for warning in warnings:
        operator.report({"WARNING"}, warning)

    results: list[ExtraPartImportResult] = []
    for extra_part_request in extra_part_requests:
        try:
            result = _import_single_extra_part(
                extra_part_request=extra_part_request,
                cache_root=cache_root,
                operator=operator,
                parse_mesh=parse_mesh,
                import_model=import_model,
                main_armature_obj=main_armature_obj,
            )
        except Exception as exc:
            operator.report(
                {"WARNING"},
                f"Extra part import skipped: {extra_part_request.asset_path} ({exc})",
            )
            continue
        results.append(result)

    return results


def discover_extra_part_gim_paths(main_package: RemoteMaterialPackage) -> tuple[list[ExtraPartRequest], list[str]]:
    asset_index = _make_asset_index()
    discovered: list[ExtraPartRequest] = []
    warnings: list[str] = []

    for socket in main_package.sockets:
        for child in socket.get("objects", []):
            attributes = child.get("attributes", {})
            uri = str(attributes.get("Uri", "")).strip()
            if not uri.lower().endswith(".gim"):
                continue
            try:
                resolved = _resolve_reference(asset_index, uri, main_package.gim_asset_path)
            except Exception as exc:
                warnings.append(f"Extra part URI could not be resolved: {uri} ({exc})")
                continue
            _append_unique_request(discovered, resolved, socket)

    for predicted_path, socket in _predicted_extra_part_paths(main_package.gim_asset_path, main_package.sockets):
        if asset_index.exists(predicted_path):
            _append_unique_request(discovered, predicted_path, socket)
        else:
            warnings.append(f"Predicted extra part not found: {predicted_path}")

    return discovered, warnings


def _import_single_extra_part(
    *,
    extra_part_request: ExtraPartRequest,
    cache_root: Path,
    operator,
    parse_mesh: Callable,
    import_model: Callable,
    main_armature_obj,
) -> ExtraPartImportResult:
    extra_part_path = extra_part_request.asset_path
    package = build_remote_material_package(extra_part_path, cache_root)
    model = parse_mesh(BytesIO(package.mesh_data), operator)
    if model == {}:
        raise ValueError("model could not be decoded")

    obj_name = os.path.basename(package.mesh_asset_path).rsplit(".", 1)[0]
    before_armatures = {obj.name for obj in bpy.data.objects if obj.type == "ARMATURE"}
    if not import_model(model, obj_name, operator, package, import_sockets=False):
        raise RuntimeError("existing import pipeline returned CANCELLED")

    armature_name = _new_armature_name(before_armatures)
    for warning in package.warnings[:4]:
        operator.report({"WARNING"}, f"{extra_part_path}: {warning}")
    if len(package.warnings) > 4:
        operator.report(
            {"WARNING"},
            f"{extra_part_path}: {len(package.warnings) - 4} more warning(s)",
        )

    extra_armature_obj = bpy.data.objects.get(armature_name) if armature_name else None
    if main_armature_obj is None:
        operator.report({"WARNING"}, "Extra part transform skipped: main armature is not active")
    elif extra_armature_obj is None:
        operator.report({"WARNING"}, f"Extra part transform skipped: {extra_part_path} armature not found")
    else:
        try:
            _bind_extra_part_bounding_bone(
                main_armature_obj=main_armature_obj,
                extra_armature_obj=extra_armature_obj,
                extra_part_path=extra_part_path,
                socket=extra_part_request.socket,
            )
        except Exception as exc:
            operator.report(
                {"WARNING"},
                f"Extra part transform skipped: {extra_part_path} ({exc})",
            )

    return ExtraPartImportResult(
        requested_path=extra_part_path,
        mesh_asset_path=package.mesh_asset_path,
        armature_name=armature_name,
    )


def _new_armature_name(before_armatures: set[str]) -> str | None:
    new_armatures = [
        obj
        for obj in bpy.data.objects
        if obj.type == "ARMATURE" and obj.name not in before_armatures
    ]
    if not new_armatures:
        return None
    return new_armatures[-1].name


def _predicted_extra_part_paths(gim_asset_path: str, sockets: list[dict]) -> list[tuple[str, dict]]:
    gim_asset_path = _normalize_asset_path(gim_asset_path)
    gim_name = posixpath.splitext(posixpath.basename(gim_asset_path))[0]
    gim_folder = posixpath.dirname(gim_asset_path)
    skin_name = _skin_name_from_gim(posixpath.basename(gim_asset_path))
    predicted_paths: list[tuple[str, dict]] = []

    for socket in sockets:
        if _socket_has_gim_object(socket):
            continue
        socket_name = str(socket.get("name", "")).strip()
        object_name = _object_name_from_socket(socket_name, gim_name, skin_name)
        if object_name:
            _append_unique_predicted(predicted_paths, f"{gim_folder}/{gim_name}_{object_name}.gim", socket)

    return predicted_paths


def _bind_extra_part_bounding_bone(
    *,
    main_armature_obj,
    extra_armature_obj,
    extra_part_path: str,
    socket: dict,
) -> None:
    bounding_bone_name = _bounding_bone_name(extra_part_path)
    if not bounding_bone_name:
        raise ValueError("BoundingBoneName is missing")

    bounding_pbone = extra_armature_obj.pose.bones.get(bounding_bone_name)
    if bounding_pbone is None:
        raise ValueError(f"BoundingBoneName bone was not found: {bounding_bone_name}")

    root_pbone = _root_pose_bone_for(bounding_pbone)
    target_world = _socket_target_world_matrix(main_armature_obj, socket)
    target_pose = extra_armature_obj.matrix_world.inverted_safe() @ target_world

    # Move the extra part by its root pose so the bounding bone's rest-space
    # offset is preserved instead of overwriting the bounding bone transform.
    bounding_rest = bounding_pbone.bone.matrix_local.copy()
    root_rest = root_pbone.bone.matrix_local.copy()
    root_target_pose = target_pose @ bounding_rest.inverted_safe() @ root_rest
    _set_pose_bone_basis_from_pose_matrix(root_pbone, root_target_pose)
    bpy.context.view_layer.update()


def _root_pose_bone_for(pbone):
    root = pbone
    while root.parent is not None:
        root = root.parent
    return root


def _set_pose_bone_basis_from_pose_matrix(pbone, target_pose: Matrix) -> None:
    """Set a pose bone by writing its explicit local pose delta.

    Assigning PoseBone.matrix asks Blender to solve the local basis internally.
    Some imported NeoX bones have rest/parent transforms where that implicit
    solve produces unstable manual rotate/move behavior, so calculate the basis
    directly against the parent pose matrix and local rest matrix.
    """

    rest_matrix = pbone.bone.matrix_local.copy()
    if pbone.parent is None:
        matrix_basis = rest_matrix.inverted_safe() @ target_pose
    else:
        parent_pose = pbone.parent.matrix.copy()
        parent_rest = pbone.parent.bone.matrix_local.copy()
        local_rest = parent_rest.inverted_safe() @ rest_matrix
        matrix_basis = local_rest.inverted_safe() @ parent_pose.inverted_safe() @ target_pose

    location, rotation, scale = matrix_basis.decompose()
    rotation.normalize()
    pbone.location = location
    pbone.rotation_mode = "QUATERNION"
    pbone.rotation_quaternion = rotation
    pbone.scale = scale


def _bounding_bone_name(extra_part_path: str) -> str:
    asset_index = _make_asset_index()
    extracted = asset_index.extract(extra_part_path)
    root = _xml_root_from_bytes(extracted.data, ".gim", extra_part_path)
    return root.attrib.get("BoundingBoneName", "").strip()


def _socket_target_world_matrix(main_armature_obj, socket: dict) -> Matrix:
    binding_bone = str(socket.get("binding_bone", "")).strip()
    local_socket = _socket_local_matrix(socket)

    if binding_bone:
        bone = main_armature_obj.data.bones.get(binding_bone)
        if bone is None:
            raise ValueError(f"BindingBone was not found: {binding_bone}")
        bone_local_socket = NEOX_TO_BLENDER_BONE_AXES.inverted_safe() @ local_socket
        return main_armature_obj.matrix_world @ bone.matrix_local @ bone_local_socket

    return main_armature_obj.matrix_world @ GAME_TO_BLENDER @ local_socket


def _socket_local_matrix(socket: dict) -> Matrix:
    location = _vector(socket.get("local_position"), (0.0, 0.0, 0.0))
    scale = _vector(socket.get("local_scale"), (1.0, 1.0, 1.0))
    rotation_values = socket.get("local_rotation_xyzw")

    if isinstance(rotation_values, (list, tuple)) and len(rotation_values) == 4:
        try:
            rotation = Quaternion(
                (
                    float(rotation_values[3]),
                    float(rotation_values[0]),
                    float(rotation_values[1]),
                    float(rotation_values[2]),
                )
            )
        except (TypeError, ValueError):
            rotation = Quaternion((1.0, 0.0, 0.0, 0.0))
    else:
        rotation = Quaternion((1.0, 0.0, 0.0, 0.0))

    rotation.normalize()
    return Matrix.LocRotScale(location, rotation, scale)


def _vector(values, default) -> Vector:
    if not isinstance(values, (list, tuple)) or len(values) != len(default):
        return Vector(default)
    try:
        return Vector(tuple(float(item) for item in values))
    except (TypeError, ValueError):
        return Vector(default)


def _active_armature():
    active = bpy.context.view_layer.objects.active
    if active is not None and active.type == "ARMATURE":
        return active
    obj = bpy.context.object
    if obj is not None and obj.type == "ARMATURE":
        return obj
    return None


def _object_name_from_socket(socket_name: str, gim_name: str, skin_name: str | None) -> str | None:
    direct_match = re.fullmatch(rf"{re.escape(gim_name)}_([a-z]+)", socket_name, re.IGNORECASE)
    if direct_match:
        return direct_match.group(1)

    if not skin_name:
        return None

    skin_first_match = re.fullmatch(
        rf"(?:const_)?{re.escape(skin_name)}_([a-z]+)",
        socket_name,
        re.IGNORECASE,
    )
    if skin_first_match:
        return skin_first_match.group(1)

    object_first_match = re.fullmatch(
        rf"([a-z]+)_{re.escape(skin_name)}",
        socket_name,
        re.IGNORECASE,
    )
    if object_first_match:
        return object_first_match.group(1)

    return None


def _socket_has_gim_object(socket: dict) -> bool:
    for child in socket.get("objects", []):
        attributes = child.get("attributes", {})
        uri = str(attributes.get("Uri", "")).strip()
        if uri.lower().endswith(".gim"):
            return True
    return False


def _skin_name_from_gim(filename: str) -> str | None:
    lower_name = filename.lower()
    match = SKIN_NAME_PATTERN.fullmatch(lower_name)
    if match:
        return match.group(1)
    match = ALT_SKIN_NAME_PATTERN.fullmatch(lower_name)
    if match:
        return match.group(1)
    return None


def _append_unique_request(values: list[ExtraPartRequest], asset_path: str, socket: dict) -> None:
    normalized = _normalize_asset_path(asset_path)
    existing = {item.asset_path.lower() for item in values}
    if normalized.lower() not in existing:
        values.append(ExtraPartRequest(normalized, socket))


def _append_unique_predicted(values: list[tuple[str, dict]], asset_path: str, socket: dict) -> None:
    normalized = _normalize_asset_path(asset_path)
    existing = {item[0].lower() for item in values}
    if normalized.lower() not in existing:
        values.append((normalized, socket))
