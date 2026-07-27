from .neox_mesh_parser import parse_mesh_1, parse_mesh_2, parse_mesh_3
from .remote_import import RemoteMaterialPackage, build_remote_material_package
import bpy
from io import BytesIO
import json
import os
import statistics
import struct
from pathlib import Path
from mathutils import Matrix, Vector
from math import isfinite
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper, axis_conversion
from math import pi

NEOX_TO_BLENDER_BONE_AXES = Matrix((
    (0.0, 1.0, 0.0, 0.0),
    (-1.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
))

class IDVMI_OT_Import_Neox_Mesh(bpy.types.Operator, ImportHelper):
    bl_idname = "idvmi_neox.neox_importer"
    bl_label = "Import NeoX Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".mesh"
    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(
        default="*.mesh",
        options={'HIDDEN'},
        maxlen=255,
    )

    use_scene_selector: BoolProperty(
        default=False,
        options={'HIDDEN'},
    )
    import_source: StringProperty(
        default="local",
        options={'HIDDEN'},
    )

    def invoke(self, context, event):
        if self.import_source == "remote":
            return self.execute(context)

        if self.use_scene_selector:
            return self.execute(context)

        self.filter_glob = "*.mesh"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        root = os.path.dirname(__file__)
        log_file = os.path.join(root, "import_per_material_log.txt")
        with open(log_file, "w") as log:
            log.write("--- New import session started ---\n")

        if self.import_source == "remote":
            return self._execute_remote(context)

        mesh_path = self.filepath or context.scene.neox_mesh_selector
        mesh_path = bpy.path.abspath(mesh_path)

        if not mesh_path:
            self.report({'ERROR'}, "Please select a .mesh file")
            return {'CANCELLED'}

        if os.path.splitext(mesh_path)[1].lower() != ".mesh":
            self.report({'ERROR'}, f"Expected a .mesh file: {mesh_path}")
            return {'CANCELLED'}

        if not os.path.isfile(mesh_path):
            self.report({'ERROR'}, f"File not found: {mesh_path}")
            return {'CANCELLED'}

        context.scene.neox_mesh_selector = mesh_path

        with open(mesh_path, "rb") as mesh_file:
            model = _parse_neox_mesh(mesh_file, self)


        if model == {}:
            self.report({'ERROR'}, "Model can't be decoded")
            return {'CANCELLED'}


        obj_name = os.path.basename(mesh_path).rsplit(".", 1)[0]
        if import_per_material(model, obj_name, self):
            self.report({'INFO'}, f"Import OK -> {mesh_path}")
            return {'FINISHED'}
        else:
            return {'CANCELLED'}

    def _execute_remote(self, context):
        gim_asset_path = context.scene.neox_remote_gim_path.strip()
        if not gim_asset_path:
            self.report({'ERROR'}, "Please enter a remote .gim asset path")
            return {'CANCELLED'}

        cache_root = Path(__file__).resolve().parent / "remote_import_cache"
        try:
            package = build_remote_material_package(gim_asset_path, cache_root)
        except Exception as e:
            log_file = os.path.join(os.path.dirname(__file__), "import_per_material_log.txt")
            with open(log_file, "a") as log:
                import traceback
                log.write("--- Remote import failed while building material package ---\n")
                traceback.print_exc(file=log)
            self.report({'ERROR'}, f"Remote import failed: {e}")
            return {'CANCELLED'}

        model = _parse_neox_mesh(BytesIO(package.mesh_data), self)
        if model == {}:
            self.report({'ERROR'}, "Model can't be decoded")
            return {'CANCELLED'}

        context.scene.neox_remote_gim_path = gim_asset_path
        obj_name = os.path.basename(package.mesh_asset_path).rsplit(".", 1)[0]
        if import_per_material(
            model,
            obj_name,
            self,
            package,
            import_sockets=context.scene.neox_remote_import_sockets,
        ):
            for warning in package.warnings[:8]:
                self.report({'WARNING'}, warning)
            if len(package.warnings) > 8:
                self.report({'WARNING'}, f"{len(package.warnings) - 8} more remote import warning(s)")
            self.report({'INFO'}, f"Remote import OK -> {package.mesh_asset_path}")
            return {'FINISHED'}
        return {'CANCELLED'}


def menu_func_import(self, context):
    op = self.layout.operator(
        IDVMI_OT_Import_Neox_Mesh.bl_idname,
        text="NeoX Mesh (.mesh)",
    )
    op.filepath = ""


if hasattr(bpy.types, "FileHandler"):
    class IDVMI_FH_Neox_Mesh(bpy.types.FileHandler):
        bl_idname = "IDVMI_FH_neox_mesh"
        bl_label = "NeoX Mesh"
        bl_import_operator = IDVMI_OT_Import_Neox_Mesh.bl_idname
        bl_file_extensions = ".mesh"

        @classmethod
        def poll_drop(cls, context):
            return context.area and context.area.type == 'VIEW_3D'
else:
    # Blender 3.6 does not expose Python file drop handlers. In that version,
    # .mesh files dropped into the viewport may be claimed by Blender's built-in
    # image drop handler, so the supported path is File > Import > NeoX Mesh.
    IDVMI_FH_Neox_Mesh = None

def _parse_neox_mesh(mesh_file, operator):
    is_parser_tried = {parse_mesh_1: False, parse_mesh_2: False, parse_mesh_3: False}

    for parser in is_parser_tried:
        try:
            operator.report({'INFO'}, f"Trying {parser.__name__}...")
            model = {}
            mesh_file.seek(0)
            is_parser_tried[parser] = True
            parser(model, mesh_file, operator)
            if 'vertex_weight' in model:
                check_weights(model['vertex_weight'], operator)
            return model
        except Exception as e:
            operator.report({'ERROR'}, f"[{type(e).__name__}] {e}")
            continue
    return {}

def check_weights(weight_data, operator):
    for weights in weight_data:
        for weight in weights:
            if type(weight) != float or weight > 1.0 or weight < 0.0:
                operator.report({'ERROR'}, f"Incorrect weights. Example weight: {weight}")
                return False
    return True

def source_row_matrix_to_blender_global(matrix_4, game_to_blender: Matrix) -> Matrix:
    rows = matrix_4.tolist() if hasattr(matrix_4, "tolist") else matrix_4
    source_global = Matrix(rows).transposed()
    return game_to_blender @ source_global

def make_edit_bone_rest_matrix(converted_global: Matrix) -> tuple[Matrix, Vector]:
    location, rotation, scale = converted_global.decompose()
    rotation.normalize()

    rigid_global = Matrix.Translation(location) @ rotation.to_matrix().to_4x4()
    edit_bone_matrix = rigid_global @ NEOX_TO_BLENDER_BONE_AXES
    return edit_bone_matrix, Vector(scale)

def build_local_rest_matrices(global_matrices: list[Matrix], parent_indices: list[int]) -> list[Matrix]:
    local_matrices = []

    for bone_index, global_matrix in enumerate(global_matrices):
        parent_index = parent_indices[bone_index]

        if parent_index in (-1, 65535):
            local_matrix = global_matrix.copy()
        else:
            parent_global = global_matrices[parent_index]
            local_matrix = parent_global.inverted_safe() @ global_matrix

        local_matrices.append(local_matrix)

    return local_matrices

def build_children_by_parent(parent_indices: list[int]) -> dict[int, list[int]]:
    children = {}

    for child_index, parent_index in enumerate(parent_indices):
        if parent_index in (-1, 65535):
            continue
        children.setdefault(parent_index, []).append(child_index)

    return children

def calculate_projected_bone_length(
    bone_index: int,
    edit_bone_matrices: list[Matrix],
    children_by_parent: dict[int, list[int]],
    minimum_length: float = 0.01,
    minimum_alignment: float = 0.80,
) -> float | None:
    matrix = edit_bone_matrices[bone_index]
    head = matrix.to_translation()
    direction = matrix.to_3x3() @ Vector((0.0, 1.0, 0.0))

    if direction.length < 1.0e-8:
        return None

    direction.normalize()
    candidates = []

    for child_index in children_by_parent.get(bone_index, []):
        child_head = edit_bone_matrices[child_index].to_translation()
        offset = child_head - head

        if offset.length < 1.0e-8:
            continue

        normalized_offset = offset.normalized()
        signed_alignment = normalized_offset.dot(direction)
        positive_projection = offset.dot(direction)

        candidates.append((signed_alignment, positive_projection, offset.length))

    positive_candidates = [
        item
        for item in candidates
        if item[0] >= minimum_alignment and item[1] >= minimum_length
    ]

    if not positive_candidates:
        return None

    _alignment, projected_length, _distance = max(
        positive_candidates,
        key=lambda item: item[0],
    )
    return projected_length

def is_unit_scale(scale: Vector, epsilon: float = 1.0e-5) -> bool:
    return (
        abs(scale.x - 1.0) <= epsilon
        and abs(scale.y - 1.0) <= epsilon
        and abs(scale.z - 1.0) <= epsilon
    )

def trs_reconstruction_error(matrix: Matrix) -> float:
    location, rotation, scale = matrix.decompose()
    rotation.normalize()

    reconstructed = (
        Matrix.Translation(location)
        @ rotation.to_matrix().to_4x4()
        @ Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0))
    )

    return max(
        abs(matrix[row][column] - reconstructed[row][column])
        for row in range(4)
        for column in range(4)
    )

def decode_bone_bounding_info(value) -> tuple[float, float, float, float, float, float, float]:
    if isinstance(value, dict):
        center = value.get("center")
        if center is None:
            raise ValueError("Missing 'center' field.")

        center_values = list(center)
        if len(center_values) != 3:
            raise ValueError(
                f"Expected 'center' to contain 3 float values, got {len(center_values)}."
            )

        missing_fields = [
            field_name
            for field_name in (
                "half_length_x",
                "radius_y",
                "radius_z",
                "bound_radius",
            )
            if field_name not in value
        ]
        if missing_fields:
            raise ValueError(
                "Missing field(s): " + ", ".join(missing_fields)
            )

        return (
            float(center_values[0]),
            float(center_values[1]),
            float(center_values[2]),
            float(value["half_length_x"]),
            float(value["radius_y"]),
            float(value["radius_z"]),
            float(value["bound_radius"]),
        )

    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    else:
        values = list(value)
        if len(values) == 28:
            raw = bytes(_ensure_byte(int(item)) for item in values)
        elif len(values) == 7:
            return tuple(float(item) for item in values)
        else:
            raise ValueError(
                f"Expected 28 serialized bytes or 7 float values, got {len(values)} values."
            )

    if len(raw) != 28:
        raise ValueError(f"Expected 28 serialized BoneBoundingInfo bytes, got {len(raw)}.")

    return struct.unpack("<7f", raw)

def set_bone_collision_properties(pbone, bounding_values) -> None:
    pbone["NeoX:Bone:CollisionCenter"] = tuple(bounding_values[0:3])
    pbone["NeoX:Bone:CollisionX"] = float(bounding_values[3])
    pbone["NeoX:Bone:CollisionY"] = float(bounding_values[4])
    pbone["NeoX:Bone:CollisionZ"] = float(bounding_values[5])
    pbone["NeoX:Bone:CollisionBoundRadius"] = float(bounding_values[6])

    if "NeoX:BoundingInfo" in pbone:
        del pbone["NeoX:BoundingInfo"]

def _ensure_byte(value: int) -> int:
    if value < 0 or value > 255:
        raise ValueError(f"Serialized BoneBoundingInfo byte out of range: {value}")
    return value

def _remote_material_for_mesh(package: RemoteMaterialPackage, mesh_index: int) -> dict[str, str] | None:
    if not package.materials:
        return None

    material_index = package.submesh_mtl_indices.get(mesh_index)
    if material_index is None:
        material_index = mesh_index if mesh_index < len(package.materials) else 0

    if material_index < 0 or material_index >= len(package.materials):
        return None
    return package.materials[material_index]


def _ensure_textured_material(name: str, image_path: str | None, operator, shader_tag: str):
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True

    nodes = material.node_tree.nodes
    links = material.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (200, 0)
        output = next((node for node in nodes if node.type == 'OUTPUT_MATERIAL'), None)
        if output is None:
            output = nodes.new("ShaderNodeOutputMaterial")
            output.location = (400, 0)
        links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    texture = nodes.get(f"{shader_tag}_Image")
    if texture is None:
        texture = next((node for node in nodes if node.type == 'TEX_IMAGE'), None)
    if texture is None:
        texture = nodes.new("ShaderNodeTexImage")
        texture.location = (-400, 0)
    texture.name = f"{shader_tag}_Image"
    texture.label = shader_tag

    if image_path and os.path.isfile(image_path):
        try:
            texture.image = bpy.data.images.load(image_path, check_existing=True)
        except TypeError:
            texture.image = bpy.data.images.load(image_path)
        except Exception as e:
            operator.report({'WARNING'}, f"Texture could not be loaded: {image_path} ({e})")
        else:
            texture.image.alpha_mode = 'NONE' if shader_tag == "Tex0" else 'CHANNEL_PACKED'
            if shader_tag != "Tex0":
                try:
                    texture.image.colorspace_settings.name = 'Non-Color'
                except Exception:
                    pass

    has_color_link = any(
        link.from_node == texture
        and link.from_socket.name == "Color"
        and link.to_node == bsdf
        and link.to_socket.name == "Base Color"
        for link in links
    )
    if not has_color_link:
        links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])

    return material


def _remove_links_to_socket(links, socket) -> None:
    if socket is None:
        return
    for link in list(links):
        if link.to_socket == socket:
            links.remove(link)


def _get_or_create_node(nodes, node_type: str, name: str, location: tuple[int, int]):
    node = nodes.get(name)
    if node is not None and node.bl_idname != node_type:
        nodes.remove(node)
        node = None
    if node is None:
        node = nodes.new(node_type)
    node.name = name
    node.label = name
    node.location = location
    return node


def _set_image_node_image(node, image_path: str | None, operator) -> bool:
    if not image_path or not os.path.isfile(image_path):
        return False
    try:
        image = bpy.data.images.load(image_path, check_existing=True)
    except TypeError:
        image = bpy.data.images.load(image_path)
    except Exception as e:
        operator.report({'WARNING'}, f"Texture could not be loaded: {image_path} ({e})")
        return False

    image.alpha_mode = 'CHANNEL_PACKED'
    try:
        image.colorspace_settings.name = 'Non-Color'
    except Exception:
        pass
    node.image = image
    return True


def _link_if_absent(links, from_socket, to_socket) -> None:
    if from_socket is None or to_socket is None:
        return
    for link in links:
        if link.from_socket == from_socket and link.to_socket == to_socket:
            return
    _remove_links_to_socket(links, to_socket)
    links.new(from_socket, to_socket)


def _configure_remote_shader_maps(tex0_material, material_info: dict[str, str], operator) -> None:
    if tex0_material is None or not tex0_material.use_nodes:
        return

    nodes = tex0_material.node_tree.nodes
    links = tex0_material.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    diffuse_node = nodes.get("Tex0_Image")
    if bsdf is None or diffuse_node is None:
        return

    diffuse_color = diffuse_node.outputs.get("Color")
    base_color = bsdf.inputs.get("Base Color")

    metal_path = material_info.get("TexMetal")
    if metal_path:
        metal_node = _get_or_create_node(nodes, "ShaderNodeTexImage", "TexMetal_Shader_Image", (-760, -260))
        if _set_image_node_image(metal_node, metal_path, operator):
            separate_metal = _get_or_create_node(nodes, "ShaderNodeSeparateRGB", "TexMetal_Separate_RGB", (-520, -260))
            _link_if_absent(links, metal_node.outputs["Color"], separate_metal.inputs["Image"])
            _link_if_absent(links, separate_metal.outputs["R"], bsdf.inputs.get("Metallic"))
            _link_if_absent(links, separate_metal.outputs["B"], bsdf.inputs.get("Roughness"))

            if diffuse_color is not None and base_color is not None:
                multiply = _get_or_create_node(nodes, "ShaderNodeMixRGB", "TexMetal_Light_Multiply", (-190, 30))
                multiply.blend_type = 'MULTIPLY'
                multiply.inputs["Fac"].default_value = 1.0
                _remove_links_to_socket(links, multiply.inputs["Color1"])
                _remove_links_to_socket(links, multiply.inputs["Color2"])
                links.new(diffuse_color, multiply.inputs["Color1"])
                links.new(separate_metal.outputs["G"], multiply.inputs["Color2"])
                _link_if_absent(links, multiply.outputs["Color"], base_color)

    normal_path = material_info.get("TexNormal")
    if normal_path:
        normal_node = _get_or_create_node(nodes, "ShaderNodeTexImage", "TexNormal_Shader_Image", (-760, -620))
        if _set_image_node_image(normal_node, normal_path, operator):
            separate_normal = _get_or_create_node(nodes, "ShaderNodeSeparateRGB", "TexNormal_Separate_RGB", (-520, -620))
            combine_normal = _get_or_create_node(nodes, "ShaderNodeCombineRGB", "TexNormal_Combine_RGB", (-300, -590))
            value_one = _get_or_create_node(nodes, "ShaderNodeValue", "TexNormal_Z_One", (-520, -790))
            normal_map = _get_or_create_node(nodes, "ShaderNodeNormalMap", "TexNormal_Normal_Map", (-80, -560))

            value_one.outputs["Value"].default_value = 1.0
            normal_map.inputs["Strength"].default_value = 1.0

            _link_if_absent(links, normal_node.outputs["Color"], separate_normal.inputs["Image"])
            _link_if_absent(links, separate_normal.outputs["R"], combine_normal.inputs["R"])
            _link_if_absent(links, separate_normal.outputs["G"], combine_normal.inputs["G"])
            _link_if_absent(links, value_one.outputs["Value"], combine_normal.inputs["B"])
            _link_if_absent(links, combine_normal.outputs["Image"], normal_map.inputs["Color"])
            _link_if_absent(links, normal_map.outputs["Normal"], bsdf.inputs.get("Normal"))

            alpha_socket = bsdf.inputs.get("Alpha")
            if alpha_socket is not None:
                _link_if_absent(links, separate_normal.outputs["B"], alpha_socket)
                tex0_material.blend_method = 'HASHED'
                if hasattr(tex0_material, "shadow_method"):
                    tex0_material.shadow_method = 'HASHED'


def _assign_remote_material_slots(mesh_obj, mesh_index: int, package: RemoteMaterialPackage | None, operator, log) -> None:
    if package is None:
        return

    material_info = _remote_material_for_mesh(package, mesh_index)
    if material_info is None:
        log.write(f"    No remote material metadata for mesh {mesh_index}.\n"); log.flush()
        return

    materials_by_tag = {}
    for slot_index, tag in enumerate(("Tex0", "TexNormal", "TexMetal")):
        material = _ensure_textured_material(
            f"{tag}_{mesh_obj.name}",
            material_info.get(tag),
            operator,
            tag,
        )
        materials_by_tag[tag] = material
        if len(mesh_obj.data.materials) <= slot_index:
            mesh_obj.data.materials.append(material)
        else:
            mesh_obj.data.materials[slot_index] = material

    _configure_remote_shader_maps(materials_by_tag.get("Tex0"), material_info, operator)


def _socket_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _delete_custom_properties(id_owner, property_names: tuple[str, ...]) -> None:
    for property_name in property_names:
        if property_name in id_owner:
            del id_owner[property_name]


def _socket_filter_values(value: str) -> set[str]:
    return {
        item.lower()
        for item in str(value or "").split(",")
        if item != ""
    }


def _socket_matches_filters(
    socket: dict,
    bone_filters: set[str],
    socket_filters: set[str],
    socket_match_type: str,
) -> bool:
    binding_bone = str(socket.get("binding_bone", "")).lower()
    socket_name = str(socket.get("name", "")).lower()
    if bone_filters and binding_bone not in bone_filters:
        return False
    if socket_filters:
        if socket_match_type == "exact":
            return socket_name in socket_filters
        return any(value in socket_name for value in socket_filters)
    return True


def _filtered_sockets(
    package: RemoteMaterialPackage,
    filters_enabled: bool,
    bone_filter_text: str,
    socket_filter_text: str,
    socket_match_type: str,
) -> list[dict]:
    if not filters_enabled:
        return package.sockets

    bone_filters = _socket_filter_values(bone_filter_text)
    socket_filters = _socket_filter_values(socket_filter_text)
    if not bone_filters and not socket_filters:
        return package.sockets
    return [
        socket
        for socket in package.sockets
        if _socket_matches_filters(socket, bone_filters, socket_filters, socket_match_type)
    ]


def _serialize_remote_sockets(
    armature_obj,
    package: RemoteMaterialPackage,
    log,
    *,
    filters_enabled: bool = True,
    bone_filter_text: str = "",
    socket_filter_text: str = "",
    socket_match_type: str = "contains",
) -> int:
    root_sockets = []
    unresolved_sockets = []
    sockets_by_bone: dict[str, list[dict]] = {}
    sockets = _filtered_sockets(
        package,
        filters_enabled,
        bone_filter_text,
        socket_filter_text,
        socket_match_type,
    )

    for pbone in armature_obj.pose.bones:
        _delete_custom_properties(
            pbone,
            (
                "NeoX:Sockets",
                "NeoX:SocketCount",
            ),
        )

    _delete_custom_properties(
        armature_obj,
        (
            "NeoX:Sockets",
            "NeoX:SocketCount",
            "NeoX:RootSockets",
            "NeoX:RootSocketCount",
            "NeoX:UnresolvedSockets",
            "NeoX:UnresolvedSocketCount",
        ),
    )

    for socket in sockets:
        binding_bone = str(socket.get("binding_bone", "")).strip()
        if not binding_bone:
            root_sockets.append(socket)
        elif armature_obj.pose.bones.get(binding_bone) is not None:
            sockets_by_bone.setdefault(binding_bone, []).append(socket)
        else:
            unresolved_sockets.append(socket)

    for bone_name, sockets in sockets_by_bone.items():
        pbone = armature_obj.pose.bones[bone_name]
        pbone["NeoX:Sockets"] = _socket_json(sockets)
        pbone["NeoX:SocketCount"] = len(sockets)

    armature_obj["NeoX:SocketSchemaVersion"] = 1
    armature_obj["NeoX:SocketSourceGim"] = package.gim_asset_path
    armature_obj["NeoX:RootSockets"] = _socket_json(root_sockets)
    armature_obj["NeoX:RootSocketCount"] = len(root_sockets)
    armature_obj["NeoX:UnresolvedSockets"] = _socket_json(unresolved_sockets)
    armature_obj["NeoX:UnresolvedSocketCount"] = len(unresolved_sockets)

    log.write(
        "Serialized NeoX sockets: "
        f"bound={sum(len(items) for items in sockets_by_bone.values())}, "
        f"root={len(root_sockets)}, unresolved={len(unresolved_sockets)}, "
        f"filtered_out={len(package.sockets) - len(sockets)}.\n"
    )
    log.flush()
    return len(unresolved_sockets)


def _select_active_armature(armature_obj) -> None:
    try:
        if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass

    try:
        bpy.ops.object.select_all(action='DESELECT')
    except Exception:
        pass

    armature_obj.select_set(True)
    bpy.context.view_layer.objects.active = armature_obj
    bpy.context.view_layer.update()


def _create_socket_visuals_for_imported_armature(armature_obj, operator, log) -> None:
    from .socket_operations.visualize_socket_ops import create_socket_visuals_for_armature

    _select_active_armature(armature_obj)
    marker_count, collection_name = create_socket_visuals_for_armature(
        bpy.context,
        armature_obj,
        lambda message: operator.report({'WARNING'}, message),
    )
    log.write(
        f"Created {marker_count} socket visual marker(s) in {collection_name}.\n"
    )
    log.flush()
    operator.report(
        {'INFO'},
        f"Created {marker_count} socket visual marker(s) in {collection_name}",
    )


def import_per_material(
    model,
    obj_name: str,
    operator,
    remote_material_package: RemoteMaterialPackage | None = None,
    *,
    import_sockets: bool = False,
):
    root = os.path.dirname(__file__)
    log_file = os.path.join(root, "import_per_material_log.txt")

    with open(log_file, "a") as log:
        log.write(f"--- Starting import for {obj_name} ---\n"); log.flush()

        # --- Dummy root fix ---
        if 'bone_name' in model:
            if 'dummy_root' in model['bone_name']:
                log.write("Found 'dummy_root', cleaning up model data...\n"); log.flush()

                dummy_root_index = model['bone_name'].index('dummy_root')

                # Remove dummy root from core lists
                model['bone_name'].pop(dummy_root_index)
                model['bone_matrix'].pop(dummy_root_index)

                old_bone_parents = model['bone_parent']
                new_bone_parents = []

                # Repath parent indices, excluding the dummy root's own parent entry
                for i, parent_idx in enumerate(old_bone_parents):
                    if i == dummy_root_index:
                        continue # Skip the dummy root itself

                    new_parent_idx = parent_idx
                    if parent_idx == dummy_root_index or parent_idx == 65535:
                        new_parent_idx = -1 # Was parented to dummy_root, now a
                    elif parent_idx > dummy_root_index:
                        new_parent_idx -= 1 # Parent index shifted down

                    new_bone_parents.append(new_parent_idx)

                model['bone_parent'] = new_bone_parents

                # Update vertex bone indices (joints) since bone indices have shifted
                if 'vertex_bone' in model:
                    for joints in model['vertex_bone']:
                        for i in range(len(joints)):
                            joint_idx = joints[i]
                            if joint_idx == dummy_root_index or joint_idx == 65535:
                                joints[i] = 65535 # Set to invalid index, as it should not be weighted
                            elif joint_idx > dummy_root_index:
                                joints[i] -= 1

            log.write("...cleanup complete.\n"); log.flush()

        # --- Axis conversation ---
        log.write("Performing axis conversion...\n"); log.flush()
        M_game_to_blender = axis_conversion(
            from_forward='Z', from_up='Y',   # game
            to_forward='-Y',   to_up='Z'      # blender
        ).to_4x4()
        log.write("Axis conversion done.\n"); log.flush()

        # -- Armature --
        log.write("Creating armature...\n"); log.flush()
        armature_data = bpy.data.armatures.new(obj_name)
        # armature_data.display_type = 'STICK'

        armature_obj = bpy.data.objects.new(obj_name, armature_data)
        bpy.context.collection.objects.link(armature_obj)
        bpy.context.view_layer.objects.active = armature_obj
        log.write("Armature created.\n"); log.flush()

        # Convert matrix for 3D operations
        _3D_Matrix = M_game_to_blender.to_3x3()

        if 'bone_name' in model:
            # """ USAGE: bone_index[name] = bone_index """
            bone_index = {bone_name: bone_index for bone_index, bone_name in enumerate(model['bone_name'])}

            # """ USAGE: bone_namer[index] = bone_name """
            bone_namer = {bone_index: bone_name for bone_index, bone_name in enumerate(model['bone_name'])}

            # -- Bones --
            log.write("Creating bones...\n"); log.flush()

            bone_count = len(model['bone_name'])
            if len(model['bone_matrix']) != bone_count:
                operator.report(
                    {'ERROR'},
                    f"Bone matrix count mismatch: {len(model['bone_matrix'])} vs {bone_count}"
                )
                return False

            if len(model['bone_parent']) != bone_count:
                operator.report(
                    {'ERROR'},
                    f"Bone parent count mismatch: {len(model['bone_parent'])} vs {bone_count}"
                )
                return False

            for idx, parent_index in enumerate(model['bone_parent']):
                if parent_index in (-1, 65535):
                    continue
                if parent_index < 0 or parent_index >= bone_count:
                    operator.report(
                        {'ERROR'},
                        f"Parent index {parent_index} for bone {idx} is out of range."
                    )
                    return False

            converted_global_matrices = []
            edit_bone_matrices = []
            global_rest_scales = []

            for raw_matrix in model['bone_matrix']:
                converted_global = source_row_matrix_to_blender_global(
                    raw_matrix,
                    M_game_to_blender,
                )
                edit_matrix, global_scale = make_edit_bone_rest_matrix(
                    converted_global
                )
                converted_global_matrices.append(converted_global)
                edit_bone_matrices.append(edit_matrix)
                global_rest_scales.append(global_scale)

            local_rest_matrices = build_local_rest_matrices(
                converted_global_matrices,
                model['bone_parent'],
            )
            local_rest_scales = []
            for local_matrix in local_rest_matrices:
                _location, _rotation, local_scale = local_matrix.decompose()
                local_rest_scales.append(Vector(local_scale))

            non_unit_scale_count = 0
            negative_determinant_count = 0
            reconstruction_warning_count = 0
            for idx, bone_name in enumerate(model['bone_name']):
                local_scale = local_rest_scales[idx]
                if not is_unit_scale(local_scale):
                    non_unit_scale_count += 1
                    log.write(
                        "WARNING: "
                        f"Bone '{bone_name}' has non-unit local rest scale "
                        f"{tuple(local_scale)}. Blender EditBone cannot represent "
                        "arbitrary three-axis rest scale; value was preserved as "
                        "custom property.\n"
                    ); log.flush()

                determinant = converted_global_matrices[idx].to_3x3().determinant()
                if determinant < 0.0:
                    negative_determinant_count += 1
                    log.write(
                        f"WARNING: Bone '{bone_name}' has negative determinant "
                        f"{determinant:.8g}; reflection or negative scale cannot be "
                        "represented exactly by EditBone rotation.\n"
                    ); log.flush()

                reconstruction_error = trs_reconstruction_error(
                    converted_global_matrices[idx]
                )
                if reconstruction_error > 1.0e-5:
                    reconstruction_warning_count += 1
                    log.write(
                        f"WARNING: Bone '{bone_name}' may contain shear or unsupported "
                        f"affine transform; reconstruction error={reconstruction_error:.8g}\n"
                    ); log.flush()

            children_by_parent = build_children_by_parent(model['bone_parent'])
            resolved_lengths = {}
            for idx in range(bone_count):
                length = calculate_projected_bone_length(
                    idx,
                    edit_bone_matrices,
                    children_by_parent,
                )
                if length is not None:
                    resolved_lengths[idx] = length

            median_length = statistics.median(resolved_lengths.values()) if resolved_lengths else 0.1

            bpy.ops.object.mode_set(mode='EDIT')

            # Create all bones from full rest matrices first.
            log.write("Setting bone rest matrices...\n"); log.flush()
            for idx, bone_name in enumerate(model['bone_name']):
                log.write(f"  Processing bone {idx}: '{bone_name}'\n"); log.flush()

                if not bone_name or not isinstance(bone_name, str):
                    log.write(f"  !! SKIPPING bone {idx}: Invalid name ('{bone_name}'). Not a string or empty.\n"); log.flush()
                    operator.report({'ERROR'}, f"Invalid bone name: {bone_name} | Not a string or empty.")
                    return False

                try:
                    # Create the bone
                    bone = armature_obj.data.edit_bones.new(bone_name)

                    # VERIFY: Check if Blender renamed the bone, which indicates a duplicate.
                    # This is the most reliable way to detect duplicates.
                    if bone.name != bone_name:
                        log.write(f"  !! ABORTING: Bone '{bone_name}' was renamed to '{bone.name}' by Blender.\n"); log.flush()
                        log.write(f"  This is likely caused by a duplicate bone name in the source file.\n"); log.flush()
                        # Clean up the wrongly named bone before aborting
                        armature_obj.data.edit_bones.remove(bone)
                        operator.report({'ERROR'}, f"Duplicate bone name found: '{bone_name}'")
                        return False

                    bone.matrix = edit_bone_matrices[idx]
                    bone.length = 0.1
                except Exception as e:
                    log.write(f"  !! FAILED to create bone '{bone_name}': {e}\n"); log.flush()
                    operator.report({'ERROR'}, f"[{type(e).__name__}] {e}")
                    import traceback
                    traceback.print_exc(file=log)
                    return False

            log.write("Bone rest matrices set.\n"); log.flush()

            # Set bone hierarchy without connecting heads to parent tails.
            log.write("Setting bone hierarchy and lengths...\n"); log.flush()
            for idx, bone_name in enumerate(model['bone_name']):
                if bone_name not in armature_obj.data.edit_bones:
                    log.write(f"  Skipping hierarchy for '{bone_name}' as it was not created.\n"); log.flush()
                    continue

                edit_bone = armature_obj.data.edit_bones[bone_name]

                parent_index = model['bone_parent'][idx]
                if parent_index not in (-1, 65535):
                    parent_name = model['bone_name'][parent_index]
                    if parent_name not in armature_obj.data.edit_bones:
                        log.write(f"  !! Parent bone '{parent_name}' for bone '{bone_name}' not found in armature. Skipping parenting.\n"); log.flush()
                        operator.report({'ERROR'}, f"Parent bone '{parent_name}' not found in armature.")
                        return False
                    edit_bone.parent = armature_obj.data.edit_bones[parent_name]
                    edit_bone.use_connect = False

                edit_bone.matrix = edit_bone_matrices[idx]

                if idx in resolved_lengths:
                    length = resolved_lengths[idx]
                else:
                    parent_index = model['bone_parent'][idx]
                    if parent_index not in (-1, 65535) and parent_index in resolved_lengths:
                        length = max(resolved_lengths[parent_index] * 0.35, 0.01)
                    else:
                        length = max(median_length * 0.25, 0.01)

                edit_bone.length = length

            if resolved_lengths:
                min_length = min(resolved_lengths.values())
                max_length = max(resolved_lengths.values())
            else:
                min_length = median_length
                max_length = median_length

            log.write(
                "Bone rest summary: "
                f"count={bone_count}, "
                f"projected_lengths={len(resolved_lengths)}, "
                f"min_length={min_length:.8g}, "
                f"max_length={max_length:.8g}, "
                f"median_length={median_length:.8g}, "
                f"non_unit_local_scale={non_unit_scale_count}, "
                f"negative_determinant={negative_determinant_count}, "
                f"reconstruction_warnings={reconstruction_warning_count}\n"
            ); log.flush()

            log.write("Bone hierarchy and lengths set.\n"); log.flush()

            # --- Finalize Armature and Switch to Object Mode ---
            log.write("Switching to OBJECT mode and updating depsgraph...\n"); log.flush()
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
                bpy.context.view_layer.update() # Force update after hierarchy changes
                log.write("...done.\n"); log.flush()
            except Exception as e:
                operator.report({'ERROR'}, f"Error while switching to OBJECT mode: {e}")
                return False

            log.write("Setting rest scale custom properties on bones...\n"); log.flush()
            try:
                for idx, bone_name in enumerate(model['bone_name']):
                    bone_data = armature_obj.data.bones[bone_name]
                    bone_data["NeoX:RestScale"] = tuple(local_rest_scales[idx])
                    bone_data["NeoX:GlobalRestScale"] = tuple(global_rest_scales[idx])
                log.write("Rest scale custom properties set.\n"); log.flush()
            except Exception as e:
                log.write(f"CRITICAL PYTHON ERROR while setting rest scale properties: {e}\n"); log.flush()
                import traceback
                traceback.print_exc(file=log)
                operator.report({'ERROR'}, f"Error while setting rest scale properties: {e}")
                return False

            # Custom Properties - CRASH ANALYZER BLOCK
            log.write("Setting custom properties on pose bones...\n"); log.flush()

            try:
                log.write("Switching to POSE mode...\n"); log.flush()
                bpy.ops.object.mode_set(mode='POSE')
                log.write("...switched to POSE mode successfully.\n"); log.flush()

                bounding_info = model.get("bounding_info")

                if bounding_info is None:
                    log.write(
                        "WARNING: 'bounding_info' not found in model data. "
                        "Skipping pose bone properties.\n"
                    )
                    log.flush()
                else:
                    bone_names = model["bone_name"]

                    if len(bounding_info) != len(bone_names):
                        operator.report(
                            {"ERROR"},
                            (
                                "BoundingInfo count mismatch: "
                                f"{len(bounding_info)} records for "
                                f"{len(bone_names)} bones."
                            ),
                        )
                        return False

                    log.write(
                        f"Assigning {len(bounding_info)} BoundingInfo records "
                        "using source bone names.\n"
                    )
                    log.flush()

                    for source_index, bone_name in enumerate(bone_names):
                        pbone = armature_obj.pose.bones.get(bone_name)

                        if pbone is None:
                            log.write(
                                f"ERROR: Pose bone '{bone_name}' was not found "
                                f"for source index {source_index}.\n"
                            )
                            log.flush()

                            operator.report(
                                {"ERROR"},
                                f"Pose bone not found: {bone_name}",
                            )
                            return False

                        try:
                            bounding_values = decode_bone_bounding_info(
                                bounding_info[source_index]
                            )
                        except ValueError as e:
                            log.write(
                                f"ERROR: BoundingInfo[{source_index}] for "
                                f"'{bone_name}' is invalid: {e}\n"
                            )
                            log.flush()

                            operator.report(
                                {"ERROR"},
                                f"Invalid BoundingInfo for '{bone_name}': {e}",
                            )
                            return False

                        set_bone_collision_properties(pbone, bounding_values)

                log.write("Final switch back to OBJECT mode...\n"); log.flush()
                bpy.ops.object.mode_set(mode='OBJECT')
                log.write("...switched to OBJECT mode successfully.\n"); log.flush()


            except Exception as e:
                log.write(f"CRITICAL PYTHON ERROR while setting pose bone properties:\n"); log.flush()
                log.write(f"ERROR: {e}\n"); log.flush()
                import traceback
                traceback.print_exc(file=log)
                # Switch back to object mode to prevent leaving Blender in a weird state

                bpy.ops.object.mode_set(mode='OBJECT')
                operator.report({'ERROR'}, f"Error while setting pose bone properties: {e}")
                return False

            # Set armature custom properties
            log.write("Setting custom properties on armature...\n"); log.flush()
            armature_obj['NeoX:BoneOrder'] = model['bone_name']
            armature_obj['NeoX:BoundingInfo'] = True
            armature_obj['Neox:BoneMatrix'] = model['bone_matrix']

            armature_obj['NeoX:BoneTail'] = model['bone_tail']
            if 'bone_weight_usage' in model:
                armature_obj['NeoX:BoneWeightUsageBitCount'] = (
                    model['bone_weight_usage']['bit_count']
                )
                armature_obj['NeoX:BoneWeightUsageFlags'] = list(
                    model['bone_weight_usage']['flags']
                )
            armature_obj['NeoX:LODTable'] = model['lod_data_table']
            if import_sockets and remote_material_package is not None:
                unresolved_count = _serialize_remote_sockets(
                    armature_obj,
                    remote_material_package,
                    log,
                    filters_enabled=bpy.context.scene.neox_socket_filters_enabled,
                    bone_filter_text=bpy.context.scene.neox_socket_filter_bone_names,
                    socket_filter_text=bpy.context.scene.neox_socket_filter_socket_names,
                    socket_match_type=bpy.context.scene.neox_socket_filter_socket_match_type,
                )
                if unresolved_count:
                    operator.report(
                        {'WARNING'},
                        f"{unresolved_count} socket(s) reference missing bones.",
                    )
            log.write("Custom properties set.\n"); log.flush()

            # Validate armature
            if len(model['bone_name']) != len(armature_data.bones):
                log.write("!!! Bone count mismatch after creation. Aborting. !!!\n"); log.flush()
                log.write(f"Expected {len(model['bone_name'])} bones based on source file, but Blender created {len(armature_data.bones)} bones.\n"); log.flush()
                operator.report({'ERROR'}, f"Expected {len(model['bone_name'])} bones based on source file, but Blender created {len(armature_data.bones)} bones."); log.flush()

                model_bones = set(model['bone_name'])
                armature_bones = {bone.name for bone in armature_data.bones}

                missing_bones = model_bones - armature_bones
                if missing_bones:
                    sorted_missing = sorted(list(missing_bones))
                    log.write(f"Bones in source file but NOT in Blender armature: {sorted_missing}\n"); log.flush()
                    for bone_name in sorted_missing:
                        operator.report({'ERROR'}, f"Validation failed: Bone '{bone_name}' was not created in armature.")

                extra_bones = armature_bones - model_bones
                if extra_bones:
                    sorted_extra = sorted(list(extra_bones))
                    log.write(f"Bones in Blender armature but NOT in source file: {sorted_extra}\n"); log.flush()
                    operator.report({'WARNING'}, f"Extra bones found in armature: {sorted_extra}")

                # Also check for duplicates in the original model data, which is a likely cause
                from collections import Counter
                dupes = [name for name, count in Counter(model['bone_name']).items() if count > 1]
                if dupes:
                    log.write(f"!!! Found duplicate bone names in the source file data: {dupes} !!!\n"); log.flush()
                    operator.report({'ERROR'}, f"Duplicate bone names in source file: {dupes}. This is a likely cause of the error.")

                operator.report({'ERROR'}, "Armature validation failed. Check log for details.")
                return False

        # Meshes
        log.write("Processing meshes...\n"); log.flush()
        current_vertex_index = 0
        current_face_index = 0

        for mesh_index, mesh_info in enumerate(model['mesh']):
            log.write(f"  Processing mesh {mesh_index}...\n"); log.flush()
            mesh_vertex_count, mesh_face_count, uv_ch_count, has_color = mesh_info

            mesh_data = bpy.data.meshes.new(f"{obj_name}_{mesh_index}")
            mesh_obj = bpy.data.objects.new(f"{obj_name}_{mesh_index}", mesh_data)
            bpy.context.collection.objects.link(mesh_obj)

            # Position & Normal - FIX: Convert tuples to Vector properly
            log.write(f"    Processing {mesh_vertex_count} vertices and normals...\n"); log.flush()
            vertices = []
            normals = []

            for vertex_index in range(current_vertex_index, current_vertex_index + mesh_vertex_count):
                # Convert position and normal to Vectors, then apply transformation
                pos_vector = Vector(model['position'][vertex_index])
                norm_vector = Vector(model['normal'][vertex_index])

                vertices.append((_3D_Matrix @ pos_vector)[:])  # Convert back to tuple
                normals.append((_3D_Matrix @ norm_vector)[:])   # Convert back to tuple
            log.write(f"    ...done.\n"); log.flush()

            # Faces - FIX: Adjust face indices to be relative to current mesh
            log.write(f"    Processing {mesh_face_count} faces...\n"); log.flush()
            faces = []
            for face_index in range(current_face_index, current_face_index + mesh_face_count):
                # Adjust face indices to be relative to current mesh vertices
                original_face = model['face'][face_index]
                adjusted_face = [idx - current_vertex_index for idx in original_face]
                faces.append(adjusted_face)

            current_face_index += mesh_face_count
            log.write(f"    ...done.\n"); log.flush()


            # Create mesh geometry
            log.write(f"    Creating mesh geometry...\n"); log.flush()
            mesh_data.from_pydata(vertices, [], faces)
            mesh_data.update()
            log.write(f"    ...done.\n"); log.flush()
            # Validate mesh to remove degenerate geometry that can crash later C-APIs
            try:
                mesh_data.validate()
            except Exception:
                log.write(f"    Mesh {mesh_index} validation failed, continuing defensively.\n"); log.flush()
                pass

            # FIX: Set custom normals properly (safe)
            log.write(f"    Setting custom normals...\n"); log.flush()
            mesh_data.use_auto_smooth = True
            mesh_data.auto_smooth_angle = pi  # 180 degrees

            # Ensure we're in OBJECT mode before calling low-level mesh APIs
            # bpy.ops.object.mode_set(mode='OBJECT')

            mesh_data.calc_loop_triangles()
            mesh_data.calc_normals_split()

            # Safety check + exception handling to avoid C-level crash
            try:
                # Build sanitized list of mathutils.Vector normals
                normals_vec = []
                for n in normals:
                    try:
                        v = Vector(n) if not isinstance(n, Vector) else n
                    except Exception:
                        raise ValueError("Normal is not convertible to Vector")

                    if len(v) < 3:
                        raise ValueError("Normal has fewer than 3 components")

                    # check for finite components (no inf/nan)
                    if not all(isfinite(float(c)) for c in (v[0], v[1], v[2])):
                        raise ValueError("Normal contains non-finite component")

                    # ensure exactly 3 components per Vector (Blender expects 3)
                    normals_vec.append(Vector((float(v[0]), float(v[1]), float(v[2]))))

                # Only call the C-API if counts match and data was sanitized
                if len(normals_vec) == len(mesh_data.vertices):
                    mesh_data.normals_split_custom_set_from_vertices(normals_vec)
                else:
                    log.write(f"    Normals count mismatch for mesh {mesh_index}: {len(normals_vec)} vs {len(mesh_data.vertices)}\n"); log.flush()
                    operator.report(
                        {'WARNING'},
                        f"Normals count mismatch for mesh {mesh_index}: {len(normals_vec)} vs {len(mesh_data.vertices)}"
                    )
            except Exception as e:
                log.write(f"    ERROR: Skipping custom normals for mesh {mesh_index}: {e}\n"); log.flush()
                operator.report({'ERROR'}, f"Skipping custom normals for mesh {mesh_index}: {e}")
            log.write(f"    ...done.\n"); log.flush()

            mesh_data.update()

            # UV Mapping - FIX: Proper UV assignment
            if 'uv' in model and model['uv']:
                log.write(f"    Applying UV map...\n"); log.flush()
                if not mesh_obj.data.uv_layers:
                    mesh_obj.data.uv_layers.new()

                uv_layer = mesh_obj.data.uv_layers.active.data

                # Map vertex UVs to loops (face corners)
                for face in mesh_data.polygons:
                    for loop_idx in face.loop_indices:
                        vertex_idx = mesh_data.loops[loop_idx].vertex_index
                        global_vertex_idx = current_vertex_index + vertex_idx

                        if global_vertex_idx < len(model['uv']):
                            # uv_layer[loop_idx].uv = model['uv'][global_vertex_idx]
                            u, v = model['uv'][global_vertex_idx]
                            uv_layer[loop_idx].uv = (u, 1.0 - v)
                log.write(f"    ...done.\n"); log.flush()

            _assign_remote_material_slots(
                mesh_obj,
                mesh_index,
                remote_material_package,
                operator,
                log,
            )

            if 'bone_name' in model:
                # Create Vertex Groups for all bones
                log.write(f"    Creating vertex groups...\n"); log.flush()
                for bone_name in model['bone_name']:
                    if bone_name not in mesh_obj.vertex_groups:
                        mesh_obj.vertex_groups.new(name=bone_name)
                log.write(f"    ...done.\n"); log.flush()


                # FIX: Assign vertex weights properly
                log.write(f"    Assigning vertex weights...\n"); log.flush()
                # Process only vertices belonging to this mesh
                mesh_vertex_data = model['vertex_bone'][current_vertex_index:current_vertex_index + mesh_vertex_count]
                mesh_weight_data = model['vertex_weight'][current_vertex_index:current_vertex_index + mesh_vertex_count]

                for local_vertex_index, (joints, weights) in enumerate(zip(mesh_vertex_data, mesh_weight_data)):
                    """
                    joint => uint16(4)
                    weight => float(4)
                    """
                    for joint, weight in zip(joints, weights):
                        # Skip invalid joints (65535 = -1 as uint16)
                        if joint == 65535 or joint == 255:
                            continue

                        group_name = bone_namer[joint]

                        vertex_group = mesh_obj.vertex_groups[group_name]
                        vertex_group.add([local_vertex_index], weight, 'ADD')

                current_vertex_index += mesh_vertex_count
                log.write(f"    ...done.\n"); log.flush()


                # --- 3) ARMATURE_AUTO YOK. Sadece modifier + parent ekle ---
                log.write(f"    Adding armature modifier and parenting...\n"); log.flush()
                modifier = mesh_obj.modifiers.new(name="Armature", type='ARMATURE')
                modifier.object = armature_obj
                modifier.use_vertex_groups = True
                modifier.use_bone_envelopes = False

                mesh_obj.parent = armature_obj  # opsiyonel, sadece hiyerarşi için
                log.write(f"    ...done.\n"); log.flush()
                log.write(f"  ...finished mesh {mesh_index}.\n"); log.flush()


        log.write(f"--- Successfully imported model: {obj_name} ---\n\n"); log.flush()
        if import_sockets and remote_material_package is not None:
            try:
                _create_socket_visuals_for_imported_armature(
                    armature_obj,
                    operator,
                    log,
                )
            except Exception as e:
                log.write(f"Socket visual creation failed: {e}\n")
                log.flush()
                operator.report(
                    {'WARNING'},
                    f"Socket visual creation failed: {e}",
                )

        print(f"Successfully imported model: {obj_name}")
        # return armature_obj
        return True
