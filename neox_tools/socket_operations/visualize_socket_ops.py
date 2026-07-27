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


def _new_marker(collection, armature_obj, socket: dict, index: int, bone_name: str = ""):
    socket_name = str(socket.get("name", "")).strip()
    marker_name = f"Socket_{index:03d}_{_safe_name(socket_name, 'Unnamed')}"
    marker = bpy.data.objects.new(marker_name, None)
    marker.empty_display_type = "ARROWS"
    marker.empty_display_size = 0.35
    marker.hide_viewport = False
    marker.hide_select = False
    marker.hide_render = True
    marker.matrix_world = _socket_marker_matrix(armature_obj, socket, bone_name)
    marker["NeoX:SocketName"] = socket_name
    marker["NeoX:SocketBindingBone"] = bone_name
    marker["NeoX:SocketParentType"] = "bone" if bone_name else "armature_origin"
    marker["NeoX:Socket"] = json.dumps(socket, ensure_ascii=False, separators=(",", ":"))
    collection.objects.link(marker)
    return marker


def _socket_groups_for_armature(armature_obj):
    root_sockets = _load_socket_list(armature_obj, "NeoX:RootSockets")
    bound_sockets_by_bone = []

    for pbone in armature_obj.pose.bones:
        sockets = _load_socket_list(pbone, "NeoX:Sockets")
        if sockets:
            bound_sockets_by_bone.append((pbone.name, sockets))

    return root_sockets, bound_sockets_by_bone


def create_socket_visuals_for_armature(context, armature_obj, report_warning=None):
    root_sockets, bound_sockets_by_bone = _socket_groups_for_armature(armature_obj)
    if not root_sockets and not bound_sockets_by_bone:
        raise ValueError("Armature has no serialized NeoX socket data")

    root_collection = _create_root_collection(context, armature_obj)
    marker_count = 0

    for socket in root_sockets:
        _new_marker(root_collection, armature_obj, socket, marker_count)
        marker_count += 1

    for bone_name, sockets in bound_sockets_by_bone:
        bone_collection = bpy.data.collections.new(_safe_name(bone_name, "Bone"))
        bone_collection.hide_viewport = False
        bone_collection.hide_render = True
        root_collection.children.link(bone_collection)
        _ensure_layer_collection_visible(context, bone_collection)
        for socket in sockets:
            try:
                _new_marker(
                    bone_collection,
                    armature_obj,
                    socket,
                    marker_count,
                    bone_name,
                )
            except KeyError:
                if report_warning is not None:
                    report_warning(f"Socket parent bone not found: {bone_name}")
                continue
            marker_count += 1

    context.view_layer.update()
    return marker_count, root_collection.name


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
