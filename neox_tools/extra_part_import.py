from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import os
import posixpath
import re
from pathlib import Path
from typing import Callable

import bpy
from mathutils import Matrix, Vector

from .coordinate_axes import NEOX_LOCAL_TO_BLENDER_BONE
from .remote_import import (
    RemoteMaterialPackage,
    build_local_material_package,
    build_remote_material_package,
    _LocalReferenceResolver,
    _asset_reference_for_local_path,
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
    """Import socket object GIM dependencies and place them at object level."""

    main_armature_obj = _active_armature()
    extra_part_requests, warnings = discover_extra_part_gim_paths(main_package)
    for warning in warnings:
        operator.report({"WARNING"}, warning)

    results: list[ExtraPartImportResult] = []
    for extra_part_request in extra_part_requests:
        try:
            result = _import_single_extra_part(
                extra_part_request=extra_part_request,
                operator=operator,
                parse_mesh=parse_mesh,
                import_model=import_model,
                main_armature_obj=main_armature_obj,
                package_builder=lambda asset_path: build_remote_material_package(asset_path, cache_root),
            )
        except Exception as exc:
            operator.report(
                {"WARNING"},
                f"Extra part import skipped: {extra_part_request.asset_path} ({exc})",
            )
            continue
        results.append(result)

    return results


def import_extra_parts_for_local_gim(
    *,
    main_package: RemoteMaterialPackage,
    cache_root: Path,
    operator,
    parse_mesh: Callable,
    import_model: Callable,
) -> list[ExtraPartImportResult]:
    """Import Loading=4 socket object GIM dependencies from a local GIM package."""

    main_armature_obj = _active_armature()
    extra_part_requests, warnings = discover_local_extra_part_gim_paths(main_package, cache_root)
    for warning in warnings:
        operator.report({"WARNING"}, warning)

    results: list[ExtraPartImportResult] = []
    for extra_part_request in extra_part_requests:
        try:
            result = _import_single_extra_part(
                extra_part_request=extra_part_request,
                operator=operator,
                parse_mesh=parse_mesh,
                import_model=import_model,
                main_armature_obj=main_armature_obj,
                package_builder=lambda input_path: build_local_material_package(input_path, cache_root),
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
            if not _is_loading_gim_object(attributes):
                continue
            uri = str(attributes.get("Uri", "")).strip()
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


def discover_local_extra_part_gim_paths(
    main_package: RemoteMaterialPackage,
    cache_root: Path,
) -> tuple[list[ExtraPartRequest], list[str]]:
    discovered: list[ExtraPartRequest] = []
    warnings: list[str] = []

    gim_path_text = str(main_package.gim_asset_path or "").strip()
    if not gim_path_text:
        return discovered, ["Extra part import skipped: source GIM file is missing"]

    gim_path = Path(gim_path_text)
    resolver = _LocalReferenceResolver(cache_root)
    base_identifier = _asset_reference_for_local_path(gim_path)

    for socket in main_package.sockets:
        for child in socket.get("objects", []):
            attributes = child.get("attributes", {})
            if not _is_loading_gim_object(attributes):
                continue
            uri = str(attributes.get("Uri", "")).strip()
            try:
                resolved = resolver.resolve_file(uri, gim_path, base_identifier)
            except Exception as exc:
                warnings.append(f"Extra part URI could not be resolved: {uri} ({exc})")
                continue
            _append_unique_request(discovered, str(resolved.path), socket)

    return discovered, warnings


def _import_single_extra_part(
    *,
    extra_part_request: ExtraPartRequest,
    operator,
    parse_mesh: Callable,
    import_model: Callable,
    main_armature_obj,
    package_builder: Callable[[str], RemoteMaterialPackage],
) -> ExtraPartImportResult:
    extra_part_path = extra_part_request.asset_path
    package = package_builder(extra_part_path)
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
            _place_extra_part_from_socket(
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


def _place_extra_part_from_socket(
    *,
    main_armature_obj,
    extra_armature_obj,
    extra_part_path: str,
    socket: dict,
) -> None:
    base_world = _socket_base_world_matrix(main_armature_obj, socket)
    local_position = _socket_local_position_blender_offset(socket)
    extra_armature_obj.location = base_world @ local_position
    bpy.context.view_layer.update()
    _clear_extra_part_custom_properties(extra_armature_obj)
    _log_extra_part_placement(
        extra_part_path=extra_part_path,
        socket=socket,
        extra_armature_obj=extra_armature_obj,
        base_world=base_world,
        local_position=local_position,
    )


def _clear_id_properties(owner) -> None:
    for key in list(owner.keys()):
        del owner[key]


def _clear_extra_part_custom_properties(extra_armature_obj) -> None:
    _clear_id_properties(extra_armature_obj)
    if extra_armature_obj.pose is not None:
        for pbone in extra_armature_obj.pose.bones:
            _clear_id_properties(pbone)
    for child in extra_armature_obj.children_recursive:
        _clear_id_properties(child)


def _import_log_path() -> Path:
    return Path(__file__).resolve().parent / "import_per_material_log.txt"


def _log(message: str) -> None:
    with _import_log_path().open("a", encoding="utf-8") as log:
        log.write(f"{message}\n")


def _load_gim_root(extra_part_path: str):
    local_path = Path(extra_part_path)
    if local_path.is_file():
        return _xml_root_from_bytes(local_path.read_bytes(), ".gim", str(local_path))

    asset_index = _make_asset_index()
    extracted = asset_index.extract(extra_part_path)
    return _xml_root_from_bytes(extracted.data, ".gim", extra_part_path)


def _gim_debug_metadata(extra_part_path: str) -> dict[str, object]:
    root = _load_gim_root(extra_part_path)
    submesh_centers: list[tuple[str, str]] = []
    submesh = root.find(".//SubMesh")
    if submesh is not None:
        for child in submesh:
            submesh_centers.append(
                (
                    child.attrib.get("Name", child.tag),
                    child.attrib.get("BoundingCenter", ""),
                )
            )

    return {
        "bounding_bone": root.attrib.get("BoundingBoneName", "").strip(),
        "bounding_info": root.attrib.get("BoundingInfo", "").strip(),
        "submesh_centers": submesh_centers,
    }


def _matrix_summary(matrix: Matrix) -> str:
    location, rotation, scale = matrix.decompose()
    rotation.normalize()
    return (
        f"location={tuple(round(float(value), 6) for value in location)}, "
        f"rotation_xyzw={tuple(round(float(value), 6) for value in (rotation.x, rotation.y, rotation.z, rotation.w))}, "
        f"scale={tuple(round(float(value), 6) for value in scale)}"
    )


def _log_extra_part_placement(
    *,
    extra_part_path: str,
    socket: dict,
    extra_armature_obj,
    base_world: Matrix,
    local_position: Vector,
) -> None:
    try:
        metadata = _gim_debug_metadata(extra_part_path)
    except Exception as exc:
        metadata = {
            "bounding_bone": "<metadata unavailable>",
            "bounding_info": f"<metadata unavailable: {type(exc).__name__}: {exc}>",
            "submesh_centers": [],
        }

    _log("--- Extra Part Placement ---")
    _log(f"extra part: {extra_part_path}")
    _log(f"armature: {extra_armature_obj.name}")
    _log(f"socket: {socket.get('name', '')}")
    _log(f"binding bone: {socket.get('binding_bone', '')}")
    _log(f"local position: {socket.get('local_position', [])}")
    _log(f"local rotation xyzw: {socket.get('local_rotation_xyzw', [])}")
    _log(f"local scale: {socket.get('local_scale', [])}")
    _log(f"base object transform: {_matrix_summary(base_world)}")
    _log(f"converted local position: {_vector_summary(local_position)}")
    _log(f"base @ local position: {_vector_summary(base_world @ local_position)}")
    _log(f"child BoundingBoneName: {metadata['bounding_bone']}")
    _log(f"child BoundingInfo: {metadata['bounding_info']}")
    _log(f"child SubMesh BoundingCenter: {metadata['submesh_centers']}")
    _log(f"final object transform: {_matrix_summary(extra_armature_obj.matrix_world)}")


def _socket_base_world_matrix(main_armature_obj, socket: dict) -> Matrix:
    binding_bone = str(socket.get("binding_bone", "")).strip()
    if binding_bone:
        bone = main_armature_obj.data.bones.get(binding_bone)
        if bone is None:
            raise ValueError(f"BindingBone was not found: {binding_bone}")
        return main_armature_obj.matrix_world @ bone.matrix_local

    return main_armature_obj.matrix_world


def _vector_summary(vector: Vector) -> str:
    return str(tuple(round(float(value), 6) for value in vector))


def _socket_local_position_blender_offset(socket: dict):
    values = socket.get("local_position", [])
    if len(values) != 3:
        return Vector((0.0, 0.0, 0.0))

    position = Vector(float(value) for value in values)
    return position @ NEOX_LOCAL_TO_BLENDER_BONE.to_3x3().inverted()


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

    guajian_match = re.fullmatch(
        rf"guajian_{re.escape(skin_name)}_([a-z]+)",
        socket_name,
        re.IGNORECASE,
    )
    if guajian_match:
        return guajian_match.group(1)

    return None


def _socket_has_gim_object(socket: dict) -> bool:
    for child in socket.get("objects", []):
        attributes = child.get("attributes", {})
        if _is_loading_gim_object(attributes):
            return True
    return False


def _is_loading_gim_object(attributes: dict) -> bool:
    uri = str(attributes.get("Uri", "")).strip()
    loading = str(attributes.get("Loading", "")).strip()
    return loading == "4" and uri.lower().endswith(".gim")


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
