import json
import re

import bpy
from bpy_extras.io_utils import axis_conversion
from mathutils import Matrix, Quaternion, Vector


NEOX_TO_BLENDER_BONE_AXES = Matrix(
    (
        (0.0, 1.0, 0.0, 0.0),
        (-1.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )
)

GAME_TO_BLENDER = axis_conversion(
    from_forward="Z",
    from_up="Y",
    to_forward="-Y",
    to_up="Z",
).to_4x4()

CUSTOM_BONE_SOCKETS_PROPERTY = "NeoX:CustomSockets"
CUSTOM_ROOT_SOCKETS_PROPERTY = "NeoX:CustomRootSockets"
DELETING_SOCKETS_PROPERTY = "NeoX:DeletingBones"

IMPORTED_ARMATURE_SOCKET_PROPERTIES = (
    "NeoX:Sockets",
    "NeoX:SocketCount",
    "NeoX:RootSockets",
    "NeoX:RootSocketCount",
    "NeoX:UnresolvedSockets",
    "NeoX:UnresolvedSocketCount",
    "NeoX:SocketSchemaVersion",
    "NeoX:SocketSourceGim",
)

IMPORTED_POSE_BONE_SOCKET_PROPERTIES = (
    "NeoX:Sockets",
    "NeoX:SocketCount",
)


def _safe_name(value: str, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    return text[:48] or fallback


def _collection_name_for(armature_obj) -> str:
    return f"{armature_obj.name}_NeoX_Sockets"


def _remove_collection_tree(collection) -> None:
    for child in list(collection.children):
        _remove_collection_tree(child)

    for obj in list(collection.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    bpy.data.collections.remove(collection)


def _collection_contains(root_collection, target_collection) -> bool:
    if root_collection == target_collection:
        return True
    return any(
        _collection_contains(child, target_collection)
        for child in root_collection.children
    )


def _find_layer_collection(layer_collection, target_collection):
    if layer_collection.collection == target_collection:
        return layer_collection
    for child in layer_collection.children:
        found = _find_layer_collection(child, target_collection)
        if found is not None:
            return found
    return None


def _ensure_layer_collection_visible(context, collection) -> None:
    context.view_layer.update()
    layer_collection = _find_layer_collection(
        context.view_layer.layer_collection,
        collection,
    )
    if layer_collection is None:
        return
    layer_collection.exclude = False
    layer_collection.hide_viewport = False


def _parent_collection_for(context, armature_obj):
    scene_root = context.scene.collection

    for collection in armature_obj.users_collection:
        if _collection_contains(scene_root, collection):
            return collection

    if context.collection and _collection_contains(scene_root, context.collection):
        return context.collection

    return scene_root


def _create_root_collection(context, armature_obj):
    collection_name = _collection_name_for(armature_obj)
    existing = bpy.data.collections.get(collection_name)
    if existing is not None:
        _remove_collection_tree(existing)

    collection = bpy.data.collections.new(collection_name)
    collection.hide_viewport = False
    collection.hide_render = True
    parent_collection = _parent_collection_for(context, armature_obj)
    parent_collection.children.link(collection)
    _ensure_layer_collection_visible(context, collection)
    return collection


def _load_socket_list(id_owner, property_name: str) -> list[dict]:
    raw = id_owner.get(property_name)
    if not raw:
        return []

    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return []

    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _write_socket_list(id_owner, property_name: str, sockets: list[dict]) -> None:
    if sockets:
        id_owner[property_name] = json.dumps(sockets, ensure_ascii=False, separators=(",", ":"))
    elif property_name in id_owner:
        del id_owner[property_name]


def _delete_properties(id_owner, property_names: tuple[str, ...]) -> None:
    for property_name in property_names:
        if property_name in id_owner:
            del id_owner[property_name]


def clear_imported_socket_properties(armature_obj) -> None:
    _delete_properties(armature_obj, IMPORTED_ARMATURE_SOCKET_PROPERTIES)
    for pbone in armature_obj.pose.bones:
        _delete_properties(pbone, IMPORTED_POSE_BONE_SOCKET_PROPERTIES)


def _custom_socket_owner(armature_obj, socket: dict):
    binding_bone = str(socket.get("binding_bone", "")).strip()
    if not binding_bone:
        return armature_obj, CUSTOM_ROOT_SOCKETS_PROPERTY

    pbone = armature_obj.pose.bones.get(binding_bone)
    if pbone is None:
        raise ValueError(f"Target armature does not contain binding bone: {binding_bone}")
    return pbone, CUSTOM_BONE_SOCKETS_PROPERTY


def append_custom_socket(armature_obj, socket: dict) -> None:
    owner, property_name = _custom_socket_owner(armature_obj, socket)
    sockets = _load_socket_list(owner, property_name)
    sockets.append(socket)
    _write_socket_list(owner, property_name, sockets)


def remove_custom_socket(armature_obj, socket: dict) -> bool:
    removed = False
    candidates = [(armature_obj, CUSTOM_ROOT_SOCKETS_PROPERTY)]
    candidates.extend(
        (pbone, CUSTOM_BONE_SOCKETS_PROPERTY)
        for pbone in armature_obj.pose.bones
    )

    target_key = _socket_identity_key(socket)
    for owner, property_name in candidates:
        sockets = _load_socket_list(owner, property_name)
        if not sockets:
            continue

        filtered = [
            item
            for item in sockets
            if _socket_identity_key(item) != target_key
        ]
        if len(filtered) != len(sockets):
            _write_socket_list(owner, property_name, filtered)
            removed = True

    return removed


def _socket_delete_record(socket: dict) -> dict:
    attributes = socket.get("attributes", {})
    socket_name = str(socket.get("name", "") or attributes.get("Name", "")).strip()
    binding_bone = str(socket.get("binding_bone", "") or attributes.get("BindingBone", "")).strip()
    return {
        "name": socket_name,
        "binding_bone": binding_bone,
    }


def append_socket_deletion(armature_obj, socket: dict) -> None:
    record = _socket_delete_record(socket)
    owner = armature_obj
    binding_bone = record["binding_bone"]
    if binding_bone:
        pbone = armature_obj.pose.bones.get(binding_bone)
        if pbone is not None:
            owner = pbone

    records = _load_socket_list(owner, DELETING_SOCKETS_PROPERTY)
    target_key = _socket_identity_key(record)
    if all(_socket_identity_key(item) != target_key for item in records):
        records.append(record)
        _write_socket_list(owner, DELETING_SOCKETS_PROPERTY, records)


def deleting_sockets_for_export(armature_obj) -> set[tuple[str, str]]:
    records = list(_load_socket_list(armature_obj, DELETING_SOCKETS_PROPERTY))
    for pbone in armature_obj.pose.bones:
        for record in _load_socket_list(pbone, DELETING_SOCKETS_PROPERTY):
            copied = dict(record)
            copied["binding_bone"] = pbone.name
            records.append(copied)

    deleting = set()
    for record in records:
        normalized = _socket_delete_record(record)
        deleting.add((normalized["binding_bone"], normalized["name"]))
    return deleting


def custom_sockets_for_export(armature_obj) -> list[dict]:
    sockets = list(_load_socket_list(armature_obj, CUSTOM_ROOT_SOCKETS_PROPERTY))
    for pbone in armature_obj.pose.bones:
        for socket in _load_socket_list(pbone, CUSTOM_BONE_SOCKETS_PROPERTY):
            copied = dict(socket)
            attributes = dict(copied.get("attributes", {}))
            attributes["BindingBone"] = pbone.name
            copied["binding_bone"] = pbone.name
            copied["attributes"] = attributes
            sockets.append(copied)
    return sockets


def _socket_identity_key(socket: dict) -> tuple:
    return (
        str(socket.get("name", "")),
        str(socket.get("binding_bone", "")),
        tuple(socket.get("local_position", [])),
        tuple(socket.get("local_rotation_xyzw", [])),
        tuple(socket.get("local_scale", [])),
    )


def _vector(values, default) -> Vector:
    if not isinstance(values, (list, tuple)) or len(values) != len(default):
        return Vector(default)
    try:
        return Vector(tuple(float(item) for item in values))
    except (TypeError, ValueError):
        return Vector(default)


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
    rotation_matrix = GAME_TO_BLENDER @ rotation.to_matrix().to_4x4()
    _rotation_location, rotation, _rotation_scale = rotation_matrix.decompose()
    rotation.normalize()
    return Matrix.LocRotScale(location, rotation, scale)


def _socket_marker_matrix(armature_obj, socket: dict, bone_name: str = "") -> Matrix:
    local_socket = _socket_local_matrix(socket)

    if bone_name:
        bone = armature_obj.data.bones.get(bone_name)
        if bone is None:
            raise KeyError(bone_name)
        bone_local_socket = NEOX_TO_BLENDER_BONE_AXES.inverted_safe() @ local_socket
        return armature_obj.matrix_world @ bone.matrix_local @ bone_local_socket

    return armature_obj.matrix_world @ GAME_TO_BLENDER @ local_socket


def _new_marker(collection, armature_obj, socket: dict, index: int, bone_name: str = "", *, custom: bool = False):
    socket_name = str(socket.get("name", "")).strip()
    marker_name = f"Socket_{index:03d}_{_safe_name(socket_name, 'Unnamed')}"
    marker_matrix = _socket_marker_matrix(armature_obj, socket, bone_name)
    marker = bpy.data.objects.new(marker_name, None)
    marker.empty_display_type = "ARROWS"
    marker.empty_display_size = 0.35
    marker.hide_viewport = False
    marker.hide_select = False
    marker.hide_render = True
    marker.matrix_world = marker_matrix
    marker["NeoX:SocketName"] = socket_name
    marker["NeoX:SocketBindingBone"] = bone_name
    marker["NeoX:SocketParentType"] = "bone" if bone_name else "armature_origin"
    marker["NeoX:Socket"] = json.dumps(socket, ensure_ascii=False, separators=(",", ":"))
    marker["NeoX:CustomSocket"] = bool(custom)
    collection.objects.link(marker)

    if bone_name:
        marker.parent = armature_obj
        marker.parent_type = "BONE"
        marker.parent_bone = bone_name
        marker.matrix_world = marker_matrix
    else:
        marker.parent = armature_obj
        marker.matrix_parent_inverse = armature_obj.matrix_world.inverted_safe()
        marker.matrix_world = marker_matrix

    return marker


def _socket_groups_for_armature(armature_obj):
    root_sockets = _load_socket_list(armature_obj, "NeoX:RootSockets")
    bound_sockets_by_bone = []

    for pbone in armature_obj.pose.bones:
        sockets = _load_socket_list(pbone, "NeoX:Sockets")
        if sockets:
            bound_sockets_by_bone.append((pbone.name, sockets))

    return root_sockets, bound_sockets_by_bone


def _custom_socket_groups_for_armature(armature_obj):
    root_sockets = _load_socket_list(armature_obj, CUSTOM_ROOT_SOCKETS_PROPERTY)
    bound_sockets_by_bone = []

    for pbone in armature_obj.pose.bones:
        sockets = _load_socket_list(pbone, CUSTOM_BONE_SOCKETS_PROPERTY)
        if sockets:
            bound_sockets_by_bone.append((pbone.name, sockets))

    return root_sockets, bound_sockets_by_bone


def create_socket_visuals_for_armature(context, armature_obj, report_warning=None):
    root_sockets, bound_sockets_by_bone = _socket_groups_for_armature(armature_obj)
    custom_root_sockets, custom_bound_sockets_by_bone = _custom_socket_groups_for_armature(armature_obj)

    if (
        not root_sockets
        and not bound_sockets_by_bone
        and not custom_root_sockets
        and not custom_bound_sockets_by_bone
    ):
        raise ValueError("Armature has no serialized NeoX socket data")

    root_collection = _create_root_collection(context, armature_obj)
    marker_count = 0

    for socket in root_sockets:
        _new_marker(root_collection, armature_obj, socket, marker_count)
        marker_count += 1

    for socket in custom_root_sockets:
        _new_marker(root_collection, armature_obj, socket, marker_count, custom=True)
        marker_count += 1

    sockets_by_bone: dict[str, list[tuple[dict, bool]]] = {}
    for bone_name, sockets in bound_sockets_by_bone:
        sockets_by_bone.setdefault(bone_name, []).extend((socket, False) for socket in sockets)
    for bone_name, sockets in custom_bound_sockets_by_bone:
        sockets_by_bone.setdefault(bone_name, []).extend((socket, True) for socket in sockets)

    for bone_name, sockets in sockets_by_bone.items():
        bone_collection = bpy.data.collections.new(_safe_name(bone_name, "Bone"))
        bone_collection.hide_viewport = False
        bone_collection.hide_render = True
        root_collection.children.link(bone_collection)
        _ensure_layer_collection_visible(context, bone_collection)
        for socket, custom in sockets:
            try:
                _new_marker(
                    bone_collection,
                    armature_obj,
                    socket,
                    marker_count,
                    bone_name,
                    custom=custom,
                )
            except KeyError:
                if report_warning is not None:
                    report_warning(f"Socket parent bone not found: {bone_name}")
                continue
            marker_count += 1

    context.view_layer.update()
    clear_imported_socket_properties(armature_obj)
    return marker_count, root_collection.name


def _selected_socket_object(context):
    for obj in context.selected_objects:
        if obj.get("NeoX:Socket"):
            return obj
    if context.object is not None and context.object.get("NeoX:Socket"):
        return context.object
    return None


def _active_armature(context):
    obj = context.object
    if obj is not None and obj.type == "ARMATURE":
        return obj
    active = context.view_layer.objects.active
    if active is not None and active.type == "ARMATURE":
        return active
    return None


def _selected_source_object(context, armature_obj):
    for obj in context.selected_objects:
        if obj != armature_obj and not obj.get("NeoX:Socket"):
            return obj
    return None


def _active_binding_bone(context, armature_obj):
    pbone = getattr(context, "active_pose_bone", None)
    if pbone is not None and armature_obj.pose.bones.get(pbone.name) is not None:
        return pbone.name
    return ""


def _format_vector(values, precision: int = 4) -> str:
    return ",".join(f"{float(value):.{precision}f}" for value in values)


def _socket_from_game_matrix(name: str, binding_bone: str, game_matrix: Matrix) -> dict:
    location, rotation, scale = game_matrix.decompose()
    rotation.normalize()
    local_position = [float(item) for item in location]
    local_rotation_xyzw = [float(rotation.x), float(rotation.y), float(rotation.z), float(rotation.w)]
    local_scale = [float(item) for item in scale]
    attributes = {
        "BindType": "7",
        "BindingBone": binding_bone,
        "BindingFlag": "2",
        "LocalPosition": _format_vector(local_position),
        "LocalRotation": _format_vector(local_rotation_xyzw),
        "LocalScale": _format_vector(local_scale),
        "Name": name,
        "PlayRatePolicy": "1",
        "PreloadingLevel": "4294967295",
        "SubmeshSortIdx": "4294967295",
        "SyncVo": "false",
    }
    return {
        "tag": "",
        "name": name,
        "parent_type": "bone" if binding_bone else "armature_origin",
        "binding_bone": binding_bone,
        "bind_type": "7",
        "binding_flag": "2",
        "local_position": local_position,
        "local_rotation_xyzw": local_rotation_xyzw,
        "local_scale": local_scale,
        "attributes": attributes,
        "objects": [],
    }


def _socket_with_game_matrix(base_socket: dict, binding_bone: str, game_matrix: Matrix) -> dict:
    name = str(base_socket.get("name", "") or base_socket.get("attributes", {}).get("Name", "")).strip()
    generated = _socket_from_game_matrix(name, binding_bone, game_matrix)
    socket = dict(base_socket)
    attributes = dict(socket.get("attributes", {}))
    attributes.update(generated["attributes"])
    socket.update(
        {
            "name": generated["name"],
            "parent_type": generated["parent_type"],
            "binding_bone": generated["binding_bone"],
            "bind_type": generated["bind_type"],
            "binding_flag": generated["binding_flag"],
            "local_position": generated["local_position"],
            "local_rotation_xyzw": generated["local_rotation_xyzw"],
            "local_scale": generated["local_scale"],
            "attributes": attributes,
        }
    )
    socket.setdefault("objects", [])
    return socket


def _world_matrix_to_game_socket_matrix(armature_obj, world_matrix: Matrix, binding_bone: str) -> Matrix:
    if binding_bone:
        bone = armature_obj.data.bones[binding_bone]
        bone_local_socket = (armature_obj.matrix_world @ bone.matrix_local).inverted_safe() @ world_matrix
        return NEOX_TO_BLENDER_BONE_AXES @ bone_local_socket
    return GAME_TO_BLENDER.inverted_safe() @ armature_obj.matrix_world.inverted_safe() @ world_matrix


def _ensure_socket_collection(context, armature_obj):
    collection_name = _collection_name_for(armature_obj)
    collection = bpy.data.collections.get(collection_name)
    if collection is None:
        collection = _create_root_collection(context, armature_obj)
    return collection


def _collection_for_bone(context, root_collection, bone_name: str):
    if not bone_name:
        return root_collection
    safe_name = _safe_name(bone_name, "Bone")
    collection = root_collection.children.get(safe_name)
    if collection is None:
        collection = bpy.data.collections.new(safe_name)
        collection.hide_viewport = False
        collection.hide_render = True
        root_collection.children.link(collection)
        _ensure_layer_collection_visible(context, collection)
    return collection


def _add_custom_socket_marker(context, armature_obj, socket: dict) -> None:
    root_collection = _ensure_socket_collection(context, armature_obj)
    bone_name = str(socket.get("binding_bone", "")).strip()
    collection = _collection_for_bone(context, root_collection, bone_name)
    marker_count = sum(1 for obj in root_collection.all_objects if obj.get("NeoX:Socket"))
    _new_marker(collection, armature_obj, socket, marker_count, bone_name, custom=True)
    context.view_layer.update()


class IDVMI_OT_Create_Socket_Visuals(bpy.types.Operator):
    bl_idname = "idvmi_neox.create_socket_visuals"
    bl_label = "Create/Refresh Socket Visuals"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == "ARMATURE"

    def execute(self, context):
        armature_obj = context.object
        try:
            marker_count, collection_name = create_socket_visuals_for_armature(
                context,
                armature_obj,
                lambda message: self.report({"WARNING"}, message),
            )
        except ValueError:
            self.report({"ERROR"}, "Active armature has no serialized NeoX socket data")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"Created {marker_count} socket visual marker(s) in {collection_name}",
        )
        return {"FINISHED"}


class IDVMI_OT_Copy_Socket_Visual(bpy.types.Operator):
    bl_idname = "idvmi_neox.copy_socket_visual"
    bl_label = "Copy Socket"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        armature_obj = _active_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "Select the target armature as the active object")
            return {"CANCELLED"}

        source_obj = _selected_socket_object(context)
        if source_obj is None:
            self.report({"ERROR"}, "Select a source socket object")
            return {"CANCELLED"}

        try:
            source_socket = json.loads(str(source_obj["NeoX:Socket"]))
        except (KeyError, TypeError, ValueError):
            self.report({"ERROR"}, "Selected source object has no valid NeoX socket data")
            return {"CANCELLED"}

        binding_bone = str(source_socket.get("binding_bone", "")).strip()
        if binding_bone and armature_obj.pose.bones.get(binding_bone) is None:
            self.report({"ERROR"}, f"Target armature does not contain bone: {binding_bone}")
            return {"CANCELLED"}

        source_armature = (
            source_obj.parent
            if source_obj.parent is not None and source_obj.parent.type == "ARMATURE"
            else armature_obj
        )
        source_binding_bone = (
            binding_bone
            if binding_bone and source_armature.pose.bones.get(binding_bone) is not None
            else ""
        )
        game_matrix = _world_matrix_to_game_socket_matrix(
            source_armature,
            source_obj.matrix_world.copy(),
            source_binding_bone,
        )
        socket = _socket_with_game_matrix(source_socket, binding_bone, game_matrix)
        append_custom_socket(armature_obj, socket)
        _add_custom_socket_marker(context, armature_obj, socket)
        self.report({"INFO"}, f"Copied socket: {socket.get('name', '')}")
        return {"FINISHED"}


class IDVMI_OT_Create_Socket(bpy.types.Operator):
    bl_idname = "idvmi_neox.create_socket"
    bl_label = "Create Socket"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        armature_obj = _active_armature(context)
        if armature_obj is None:
            self.report({"ERROR"}, "Select the target armature as the active object")
            return {"CANCELLED"}

        binding_bone = _active_binding_bone(context, armature_obj)
        location_mode = context.scene.socket_create_location

        if location_mode == "object_origin":
            source_obj = _selected_source_object(context, armature_obj)
            if source_obj is None:
                self.report({"ERROR"}, "Select a source object and the target armature")
                return {"CANCELLED"}
            socket_name = source_obj.name
            source_matrix = source_obj.matrix_world.copy()
        else:
            socket_name = "cursor"
            source_matrix = Matrix.Translation(context.scene.cursor.location)

        game_matrix = _world_matrix_to_game_socket_matrix(
            armature_obj,
            source_matrix,
            binding_bone,
        )
        socket = _socket_from_game_matrix(socket_name, binding_bone, game_matrix)
        append_custom_socket(armature_obj, socket)
        _add_custom_socket_marker(context, armature_obj, socket)

        if binding_bone:
            self.report({"INFO"}, f"Created socket '{socket_name}' on {binding_bone}")
        else:
            self.report({"INFO"}, f"Created root socket '{socket_name}'")
        return {"FINISHED"}


class IDVMI_OT_Delete_Socket(bpy.types.Operator):
    bl_idname = "idvmi_neox.delete_socket"
    bl_label = "Delete Socket"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        socket_obj = _selected_socket_object(context)
        if socket_obj is None:
            self.report({"ERROR"}, "Select a socket object")
            return {"CANCELLED"}

        socket = None
        try:
            socket = json.loads(str(socket_obj["NeoX:Socket"]))
        except (KeyError, TypeError, ValueError):
            pass

        armature_obj = socket_obj.parent if socket_obj.parent and socket_obj.parent.type == "ARMATURE" else _active_armature(context)
        removed_property = False
        added_delete_record = False
        is_custom_marker = bool(socket_obj.get("NeoX:CustomSocket"))
        if armature_obj is not None and socket is not None:
            if is_custom_marker:
                removed_property = remove_custom_socket(armature_obj, socket)
            else:
                append_socket_deletion(armature_obj, socket)
                added_delete_record = True

        name = socket_obj.name
        bpy.data.objects.remove(socket_obj, do_unlink=True)
        if removed_property:
            self.report({"INFO"}, f"Deleted socket and export property: {name}")
        elif added_delete_record:
            self.report({"INFO"}, f"Marked socket for export deletion: {name}")
        else:
            self.report({"INFO"}, f"Deleted socket object: {name}")
        return {"FINISHED"}
