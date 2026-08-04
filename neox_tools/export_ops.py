import struct
import bpy, os , math
import bmesh
from mathutils import Matrix, Vector
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper, axis_conversion
from .coordinate_axes import (
    BLENDER_BONE_TO_NEOX_LOCAL,
    BLENDER_TO_GAME,
    GAME_TO_BLENDER,
    NEOX_LOCAL_TO_BLENDER_BONE,
)
from .export_utils import writeuint8, writeuint16, writeuint32, writefloat


MATRIX_ROUNDTRIP_HARD_LIMIT = 1.0e-4
COLLIDER_WEIGHT_THRESHOLD = 0.5
COLLIDER_EPSILON = 1.0e-6


def _ensure_uint(value: int, bits: int, label: str) -> int:
    """Validate that value fits into an unsigned integer of given size."""
    if isinstance(value, bool):
        value = int(value)
    elif not isinstance(value, int):
        raise ValueError(f"{label} must be an integer, got {type(value).__name__}")

    max_value = (1 << bits) - 1
    if value < 0 or value > max_value:
        raise ValueError(f"{label} ({value}) must be between 0 and {max_value}")
    return value

def _matrix_max_error(a: Matrix, b: Matrix) -> float:
    return max(
        abs(float(a[row][column]) - float(b[row][column]))
        for row in range(4)
        for column in range(4)
    )

def _flatten_row_major(matrix: Matrix) -> list[float]:
    return [
        float(matrix[row][column])
        for row in range(4)
        for column in range(4)
    ]

def _validate_finite_values(values, label: str) -> None:
    for value in values:
        if not math.isfinite(float(value)):
            raise ValueError(f"{label} contains non-finite value: {value}")

def _encode_fixed_name32(name: str) -> bytes:
    encoded = name.encode("utf-8")
    if len(encoded) > 32:
        raise ValueError(
            f"Bone name '{name}' is {len(encoded)} bytes; maximum is 32 bytes."
        )
    return encoded.ljust(32, b"\x00")

def _read_bone_order_hint(armature) -> dict[str, int]:
    names = armature.get("NeoX:BoneOrder", [])
    result = {}
    for name in names:
        name = str(name)
        if name not in result:
            result[name] = len(result)
    return result

def build_export_bone_order(armature, log=None):
    bones = list(armature.data.bones)
    if not bones:
        raise ValueError(f"Armature '{armature.name}' contains no bones.")

    names = [bone.name for bone in bones]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"Duplicate bone names are not supported: {duplicates}")

    order_hint = _read_bone_order_hint(armature)
    data_index = {bone.name: index for index, bone in enumerate(bones)}
    by_name = {bone.name: bone for bone in bones}
    children = {bone.name: [] for bone in bones}
    indegree = {bone.name: 0 for bone in bones}

    for bone in bones:
        if bone.parent is None:
            continue
        if bone.parent.name not in by_name:
            raise ValueError(
                f"Parent '{bone.parent.name}' for bone '{bone.name}' is outside export set."
            )
        children[bone.parent.name].append(bone)
        indegree[bone.name] += 1

    def priority(bone):
        return (
            0 if bone.name in order_hint else 1,
            order_hint.get(bone.name, 2**31 - 1),
            data_index[bone.name],
            bone.name.casefold(),
        )

    import heapq
    heap = []
    for bone in bones:
        if indegree[bone.name] == 0:
            heapq.heappush(heap, (priority(bone), bone.name))

    ordered = []
    while heap:
        _priority, bone_name = heapq.heappop(heap)
        bone = by_name[bone_name]
        ordered.append(bone)

        for child in children[bone_name]:
            indegree[child.name] -= 1
            if indegree[child.name] == 0:
                heapq.heappush(heap, (priority(child), child.name))

    if len(ordered) != len(bones):
        raise ValueError("Bone hierarchy could not be topologically ordered.")

    if len(ordered) > 0xFFFF:
        raise ValueError(f"Bone count {len(ordered)} exceeds uint16 limit.")

    if log is not None:
        roots = sum(1 for bone in ordered if bone.parent is None)
        log.write(
            f"Canonical bone order built: bones={len(ordered)}, roots={roots}\n"
        ); log.flush()

    return ordered

def build_parent_indices(ordered_bones, bone_index: dict[str, int]) -> list[int]:
    parent_indices = []
    for bone in ordered_bones:
        if bone.parent is None:
            parent_indices.append(65535)
        else:
            parent_indices.append(
                _ensure_uint(bone_index[bone.parent.name], 16, f"Parent index for {bone.name}")
            )
    return parent_indices

def blender_bone_to_neox_source_row(bone) -> list[float]:
    blender_rest_global = bone.matrix_local.copy()
    _validate_finite_values(_flatten_row_major(blender_rest_global), f"Bone matrix {bone.name}")

    determinant = blender_rest_global.to_3x3().determinant()
    if determinant <= 0.0:
        raise ValueError(
            f"Bone '{bone.name}' has unsupported reflection or zero determinant."
        )

    _translation, rotation, scale = blender_rest_global.decompose()
    rotation.normalize()
    if max(abs(float(component) - 1.0) for component in scale) > 1.0e-4:
        raise ValueError(
            f"Bone '{bone.name}' has unsupported non-unit rest scale {tuple(scale)}."
        )

    source_global_column = (
        BLENDER_TO_GAME
        @ blender_rest_global
        @ NEOX_LOCAL_TO_BLENDER_BONE
    )
    reconstructed = (
        GAME_TO_BLENDER
        @ source_global_column
        @ BLENDER_BONE_TO_NEOX_LOCAL
    )
    error = _matrix_max_error(blender_rest_global, reconstructed)
    if error > MATRIX_ROUNDTRIP_HARD_LIMIT:
        raise ValueError(
            f"Bone '{bone.name}' matrix round-trip error {error:.8g} exceeds "
            f"{MATRIX_ROUNDTRIP_HARD_LIMIT}."
        )

    return _flatten_row_major(source_global_column.transposed())

def build_bone_matrices(ordered_bones) -> list[list[float]]:
    return [blender_bone_to_neox_source_row(bone) for bone in ordered_bones]

def fit_neox_collision(points: list[Vector]) -> dict[str, object]:
    minimum = Vector((
        min(point.x for point in points),
        min(point.y for point in points),
        min(point.z for point in points),
    ))
    maximum = Vector((
        max(point.x for point in points),
        max(point.y for point in points),
        max(point.z for point in points),
    ))
    center = (minimum + maximum) * 0.5
    offsets = [point - center for point in points]

    collision_x = max(abs(offset.x) for offset in offsets)
    collision_y = max(abs(offset.y) for offset in offsets)
    collision_z = max(abs(offset.z) for offset in offsets)
    bound_radius = max(offset.length for offset in offsets)

    return {
        "center": tuple(float(value) for value in center),
        "collision_x": max(float(collision_x), COLLIDER_EPSILON),
        "collision_y": max(float(collision_y), COLLIDER_EPSILON),
        "collision_z": max(float(collision_z), COLLIDER_EPSILON),
        "bound_radius": float(bound_radius),
    }

def zero_collision_record() -> dict[str, object]:
    return {
        "center": (0.0, 0.0, 0.0),
        "collision_x": 0.0,
        "collision_y": 0.0,
        "collision_z": 0.0,
        "bound_radius": 0.0,
    }

def bone_size_collision_record(bone) -> dict[str, object]:
    length = max(float(getattr(bone, "length", 0.0)), COLLIDER_EPSILON)
    half_length = length * 0.5

    radius_y = max(
        abs(float(getattr(bone, "bbone_x", 0.0))) * 0.5,
        abs(float(getattr(bone, "head_radius", 0.0))),
        abs(float(getattr(bone, "tail_radius", 0.0))),
        COLLIDER_EPSILON,
    )
    radius_z = max(
        abs(float(getattr(bone, "bbone_z", 0.0))) * 0.5,
        abs(float(getattr(bone, "head_radius", 0.0))),
        abs(float(getattr(bone, "tail_radius", 0.0))),
        COLLIDER_EPSILON,
    )

    bound_radius = math.sqrt(
        (half_length * half_length)
        + (radius_y * radius_y)
        + (radius_z * radius_z)
    )

    return {
        "center": (half_length, 0.0, 0.0),
        "collision_x": half_length,
        "collision_y": radius_y,
        "collision_z": radius_z,
        "bound_radius": bound_radius,
    }

def has_named_vertex_group(mesh_objects, bone_name: str) -> bool:
    return any(
        mesh_obj.type == "MESH"
        and mesh_obj.vertex_groups.get(bone_name) is not None
        for mesh_obj in mesh_objects
    )

def collect_collision_points(armature, mesh_objects, bone) -> list[Vector]:
    result = []
    bone_to_neox = BLENDER_BONE_TO_NEOX_LOCAL.to_3x3()
    bone_matrix_inv = bone.matrix_local.inverted_safe()
    armature_world_inv = armature.matrix_world.inverted_safe()

    for mesh_obj in mesh_objects:
        vertex_group = mesh_obj.vertex_groups.get(bone.name)
        if vertex_group is None:
            continue

        mesh_to_armature = armature_world_inv @ mesh_obj.matrix_world
        for vertex in mesh_obj.data.vertices:
            accepted = False
            for group in vertex.groups:
                if (
                    group.group == vertex_group.index
                    and group.weight >= COLLIDER_WEIGHT_THRESHOLD
                ):
                    accepted = True
                    break

            if not accepted:
                continue

            point_armature = mesh_to_armature @ vertex.co
            point_blender_bone = bone_matrix_inv @ point_armature
            result.append(bone_to_neox @ point_blender_bone)

    return result

def update_collision_preview_properties(
    armature,
    bone_name: str,
    record: dict[str, object],
    log=None,
) -> None:
    try:
        pbone = armature.pose.bones.get(bone_name)
        if pbone is None:
            return

        pbone["NeoX:Bone:CollisionCenter"] = tuple(record["center"])
        pbone["NeoX:Bone:CollisionX"] = float(record["collision_x"])
        pbone["NeoX:Bone:CollisionY"] = float(record["collision_y"])
        pbone["NeoX:Bone:CollisionZ"] = float(record["collision_z"])
        pbone["NeoX:Bone:CollisionBoundRadius"] = float(record["bound_radius"])
    except Exception as exc:
        if log is not None:
            log.write(
                "WARNING: Collision preview properties could not be updated "
                f"for bone '{bone_name}': {type(exc).__name__}: {exc}\n"
            )
            log.flush()

def build_collision_records(
    armature,
    mesh_objects,
    ordered_bones,
    used_bone_names: set[str],
    operator,
    log,
) -> list[dict[str, object]]:
    records = []
    zero_count = 0
    bone_size_fallback_count = 0

    for bone in ordered_bones:
        points = collect_collision_points(armature, mesh_objects, bone)
        if points:
            record = fit_neox_collision(points)
        elif has_named_vertex_group(mesh_objects, bone.name):
            record = bone_size_collision_record(bone)
            bone_size_fallback_count += 1
            log.write(
                f"WARNING: Bone '{bone.name}' has a vertex group but no collider "
                f"points at weight >= {COLLIDER_WEIGHT_THRESHOLD}; using bone-size "
                "collision fallback.\n"
            )
            log.flush()
        else:
            record = zero_collision_record()
            zero_count += 1

        update_collision_preview_properties(armature, bone.name, record, log)
        records.append(record)

    log.write(
        "Collision records rebuilt: "
        f"records={len(records)}, zero={zero_count}, "
        f"bone_size_fallback={bone_size_fallback_count}, "
        f"threshold={COLLIDER_WEIGHT_THRESHOLD}\n"
    ); log.flush()
    return records

def encode_collision_record(record: dict[str, object]) -> bytes:
    center = tuple(record["center"])
    if len(center) != 3:
        raise ValueError("Collision center must contain exactly 3 values.")
    return struct.pack(
        "<7f",
        float(center[0]),
        float(center[1]),
        float(center[2]),
        float(record["collision_x"]),
        float(record["collision_y"]),
        float(record["collision_z"]),
        float(record["bound_radius"]),
    )

class IDVMI_OT_Export_Neox_Mesh(bpy.types.Operator, ExportHelper):
    bl_idname = "idvmi_neox.neox_exporter"
    bl_label = "Export NeoX Mesh"

    # ExportHelper parametreleri
    filename_ext = ".mesh"
    filter_glob: StringProperty(
        default="*.mesh",
        options={'HIDDEN'},
        maxlen=255,
    )

    def execute(self, context):
        root = os.path.dirname(__file__)
        log_file = os.path.join(root, "export_per_material_log.txt")
        with open(log_file, "w") as log:
            log.write("--- New export session started ---\n"); log.flush()

        with open(log_file, "a") as log:
            export_path = bpy.path.abspath(self.filepath)
            flip_uv_y = context.scene.flip_uv_y

            log.write(f"Export path: {export_path}\n"); log.flush()
            log.write(f"Flip UV_Y: {flip_uv_y}\n"); log.flush()


            arm_obj = get_armature(context, self)
            if not arm_obj:
                log.write("Armature could not be found. Aborting.\n"); log.flush()
                return {'CANCELLED'}

            log.write(f"Armature found: {arm_obj.name}\n"); log.flush()

            mesh_data = parse_blender_meshes(arm_obj, flip_uv_y, self, log)

            if not mesh_data:
                log.write("Parsing Blender meshes failed. Aborting.\n"); log.flush()
                return {'CANCELLED'}

            log.write("Blender mesh data parsed successfully.\n"); log.flush()

            if not export_neox_mesh(
                export_path,
                mesh_data,
                arm_obj,
                self,
                log
            ):
                log.write("Exporting NeoX mesh failed. Aborting.\n"); log.flush()
                return {'CANCELLED'}

            self.report({'INFO'}, f"Export OK → {export_path}")
            log.write(f"--- Export finished successfully: {export_path} ---\n\n"); log.flush()
            return {'FINISHED'}

def get_armature(context, operator):
    bpy.ops.object.mode_set(mode='OBJECT')
    arm_obj = context.active_object

    if arm_obj is None:
        operator.report({'ERROR'}, "Please select an armature that has mesh(es)")
        return None

    if arm_obj.type != 'ARMATURE':
        while arm_obj:
            if arm_obj.type != 'ARMATURE':
                arm_obj = arm_obj.parent
            else:
                break
        if not arm_obj:
            operator.report({'ERROR'}, "Please select an armature that has mesh(es)")
            return None

    return arm_obj

def collect_weighted_bone_names_from_mesh_data(mesh_data: dict, epsilon: float = 1.0e-8) -> set[str]:
    bone_names = list(mesh_data['bone_name'])
    weighted_bone_names = set()

    for mesh_info in mesh_data['mesh']:
        vertex_joints = mesh_info['vertex_joint']
        vertex_weights = mesh_info['vertex_joint_weight']

        if len(vertex_joints) != len(vertex_weights):
            raise ValueError("vertex_joint and vertex_joint_weight length mismatch")

        for joints, weights in zip(vertex_joints, vertex_weights):
            if len(joints) != len(weights):
                raise ValueError("Joint and weight component count mismatch")

            for joint_index, weight in zip(joints, weights):
                if joint_index == 65535:
                    continue

                if joint_index < 0 or joint_index >= len(bone_names):
                    raise ValueError(f"Invalid weighted bone index: {joint_index}")

                if weight > epsilon:
                    weighted_bone_names.add(bone_names[joint_index])

    return weighted_bone_names

def encode_bone_weight_usage_mask(
    export_bone_order: list[str],
    weighted_bone_names: set[str],
) -> bytes:
    bone_name_to_index = {
        bone_name: index for index, bone_name in enumerate(export_bone_order)
    }

    missing_weighted_bones = weighted_bone_names - set(export_bone_order)
    if missing_weighted_bones:
        raise ValueError(
            "Weighted bones are missing from export order: "
            f"{sorted(missing_weighted_bones)}"
        )

    bit_count = len(export_bone_order)
    byte_count = (bit_count + 7) // 8
    flags = bytearray(byte_count)

    for bone_name in weighted_bone_names:
        bone_index = bone_name_to_index[bone_name]
        byte_index = bone_index // 8
        bit_index = bone_index % 8
        flags[byte_index] |= 1 << bit_index

    return bit_count.to_bytes(4, "little") + bytes(flags)

def parse_blender_meshes(armature, flip_uv_y, operator, log) -> dict:
    log.write("--- Starting mesh parsing ---\n"); log.flush()
    # --- Eksen dönüşümleri ---
    log.write("Performing axis conversion...\n"); log.flush()
    M_blender_to_game = axis_conversion(
    from_forward='-Y', from_up='Z',   # Blender’ın yönleri
    to_forward='Z',   to_up='Y'      # oyunun yönleri
    ).to_4x4()

    M_blender_to_game = Matrix.Rotation(math.pi, 4, 'X') @ M_blender_to_game

    M_vert = M_blender_to_game.to_3x3()
    log.write("Axis conversion done.\n"); log.flush()

    mesh_data = {}


    log.write("Reading bone data from armature...\n"); log.flush()
    try:
        ordered_bones = build_export_bone_order(armature, log)
        bone_index = {bone.name: idx for idx, bone in enumerate(ordered_bones)}
        mesh_data['bone_name'] = [bone.name for bone in ordered_bones]
        mesh_data['bone_parent'] = build_parent_indices(ordered_bones, bone_index)
        mesh_data['bone_matrix'] = build_bone_matrices(ordered_bones)
    except Exception as exc:
        log.write(f"ERROR: Failed to build export bones: {exc}\n"); log.flush()
        operator.report({'ERROR'}, f"Failed to build export bones: {exc}")
        return False

    log.write(f"Found {len(mesh_data['bone_name'])} bones.\n"); log.flush()

    mesh_data['mesh'] = []
    mesh_objects = []
    log.write("Starting to process child meshes...\n"); log.flush()
    for child in armature.children_recursive:
        if child.type == 'MESH':
            mesh_objects.append(child)
            log.write(f"  Processing mesh: {child.name}\n"); log.flush()

            log.write("    Transforming positions and normals...\n"); log.flush()
            positions = [v.co.copy() @ M_vert for v in child.data.vertices]
            normals = [v.normal.copy() @ M_vert for v in child.data.vertices]
            log.write("    ...done.\n"); log.flush()

            log.write("    Processing UVs...\n"); log.flush()
            uv_layer = child.data.uv_layers.active.data

            uv_sum = [Vector((0.0, 0.0)) for _ in child.data.vertices]
            uv_cnt = [0]*len(child.data.vertices)

            for l in child.data.loops:
                uv = uv_layer[l.index].uv
                vi = l.vertex_index
                uv_sum[vi] += uv
                uv_cnt[vi] += 1

            if not flip_uv_y:
                uv_vertex = [ (uv_sum[i] / uv_cnt[i]) if uv_cnt[i] else Vector((0.0,0.0))
                        for i in range(len(child.data.vertices)) ]
            else:
                uv_vertex = [
        Vector((uv_sum[i].x / uv_cnt[i],
                1.0 - (uv_sum[i].y / uv_cnt[i])))  # Y ekseninde mirror
        if uv_cnt[i] else Vector((0.0, 0.0))
        for i in range(len(child.data.vertices))
]
            log.write("    ...done.\n"); log.flush()
            # uv_vertex: vertex başına 2-float

            # Sadece n-gon'ları üçgenle
            log.write("    Triangulating n-gons...\n"); log.flush()
            bpy.ops.object.mode_set(mode='OBJECT')

            bm = bmesh.new()
            bm.from_mesh(child.data)

            ngons = [f for f in bm.faces if len(f.verts) > 4]
            triangulated_face_count = len(ngons)
            if ngons:
                bmesh.ops.triangulate(
                    bm, faces=ngons,
                    quad_method='BEAUTY',   # tri/quad varsa dokunmuyor
                    ngon_method='BEAUTY'
                )
                bm.to_mesh(child.data)

            bm.free()
            bpy.ops.object.mode_set(mode='EDIT')

            child.data.update()
            if triangulated_face_count:
                log.write(f"    {child.name}: triangulated {triangulated_face_count} n-gons\n"); log.flush()
                operator.report({'INFO'}, f"{child.name}: triangulated {triangulated_face_count} n-gons")
            else:
                log.write(f"    {child.name}: no n-gons to triangulate.\n"); log.flush()
                operator.report({'INFO'}, str(child.name))
            log.write("    ...done.\n"); log.flush()

            log.write("    Calculating tangents and faces...\n"); log.flush()
            child.data.calc_loop_triangles()
            child.data.calc_tangents()  # aktif UV ?zerinde
            faces = [tri.vertices for tri in child.data.loop_triangles]

            acc = [Vector((0,0,0)) for _ in child.data.vertices]
            cnt = [0]*len(child.data.vertices)
            for l in child.data.loops:
                acc[l.vertex_index] += l.tangent
                cnt[l.vertex_index] += 1
            vert_tangent = [(acc[i]/cnt[i]).normalized() if cnt[i] else Vector((1,0,0))
                            for i in range(len(child.data.vertices))]

            vert_tangent = [t @ M_vert for t in vert_tangent]  # w=0 mantığıyla
            log.write("    ...done.\n"); log.flush()

            log.write("    Processing vertex groups and weights...\n"); log.flush()
            vgroups = list(child.vertex_groups)

            # VG'leri bone sırasına göre sırala (eşleşmeyenler sona)
            sorted_vgroups = sorted(
                vgroups,
                key=lambda vg: bone_index.get(vg.name, len(mesh_data['bone_name']))
            )

            # vg.index -> (bone_index, rank) haritaları
            vg_to_bone = {vg.index: bone_index.get(vg.name, -1) for vg in child.vertex_groups}
            vg_rank    = {vg.index: (i if vg.name in bone_index else len(mesh_data['bone_name']) + i)
                        for i, vg in enumerate(sorted_vgroups)}

            topk = 4
            joints  = []
            weights = []

            vertex_group_names = {}
            for vertex_group in child.vertex_groups:
                vertex_group_names[vertex_group.index] = vertex_group.name

            current_weights = {}
            for n, vertex in enumerate(child.data.vertices):
                current_weights[n] = []
                for group in vertex.groups:
                    if group.weight > 0.0:
                        current_weights[n].append(group)

                while len(current_weights[n]) > 4:
                    smallest = None
                    for group in current_weights[n]:
                        if smallest == None or group.weight < smallest.weight:
                            smallest = group
                    current_weights[n].remove(smallest)

                while len(current_weights[n]) < 4:
                    current_weights[n].append(None)

            joints = []
            weights = []
            for vertex_index in current_weights:
                vertex_joints = []
                vertex_weights = []
                for group in current_weights[vertex_index]:
                    if group == None:
                        vertex_weights.append(0.0)
                        vertex_joints.append(65535)
                    else:
                        try:
                            vertex_weights.append(group.weight)
                            vertex_joints.append(bone_index[vertex_group_names[group.group]])
                        except KeyError:
                            log.write(f"    ERROR: Mesh bone name '{vertex_group_names[group.group]}' not found in armature. Aborting.\n"); log.flush()
                            operator.report({'ERROR'}, f"Mesh bone names are not equal with the armature: {vertex_group_names[group.group]}")
                            return False
                joints.append(vertex_joints)
                weights.append(vertex_weights)
            log.write("    ...done.\n"); log.flush()

            mesh_data['mesh'].append({'position': positions, 'normal': normals, 'tangent': vert_tangent, 'face': faces, 'uv': uv_vertex, 'vertex_joint': joints, 'vertex_joint_weight': weights})
            log.write(f"  Finished processing mesh: {child.name}\n"); log.flush()

    try:
        log.write("Rebuilding bone usage mask from final vertex weights...\n"); log.flush()
        weighted_bone_names = collect_weighted_bone_names_from_mesh_data(mesh_data)
        mesh_data['bone_weight_usage'] = encode_bone_weight_usage_mask(
            list(mesh_data['bone_name']),
            weighted_bone_names,
        )

        log.write("Rebuilding collision records from current mesh vertices...\n"); log.flush()
        mesh_data['bounding_info'] = build_collision_records(
            armature,
            mesh_objects,
            ordered_bones,
            weighted_bone_names,
            operator,
            log,
        )
    except Exception as exc:
        log.write(f"ERROR: Failed to rebuild skeletal export metadata: {exc}\n"); log.flush()
        operator.report({'ERROR'}, f"Failed to rebuild skeletal metadata: {exc}")
        return False

    mesh_data['mesh_objects'] = mesh_objects

    log.write("--- Finished mesh parsing ---\n"); log.flush()
    return mesh_data

def export_neox_mesh(export_path:os.PathLike, mesh_data:dict, arm_obj, operator, log):
    log.write("--- Starting binary export ---\n"); log.flush()
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode='OBJECT')

    try:
        with open(export_path, "wb") as file:
            file_data = bytearray()

            log.write("Writing header...\n"); log.flush()
            file_data += b"\x34\x80\xC8\xBB" # Magic Number
            file_data += b"\x04\x00\x05\x00" # File Version
            file_data += writeuint32(_ensure_uint(1, 32, "Bone metadata flags")) # Bone Exist [file_version_mask + patch_version + mesh_type(skeletal)]
            log.write("...done.\n"); log.flush()

            log.write("Writing bone data...\n"); log.flush()
            bone_count = len(mesh_data['bone_name'])
            file_data += writeuint16(_ensure_uint(bone_count, 16, "Bone count"))

            for idx, parent_idx in enumerate(mesh_data['bone_parent']):
                label = f"Bone parent index {idx}"
                file_data += writeuint16(_ensure_uint(parent_idx, 16, label))

            for n in range(bone_count):
                file_data += _encode_fixed_name32(mesh_data['bone_name'][n])
            log.write("...done.\n"); log.flush()

            log.write("Writing bounding info...\n"); log.flush()
            collision_records = mesh_data.get('bounding_info')
            if not collision_records:
                file_data += writeuint8(_ensure_uint(0, 8, "Bounding info flag"))
            else:
                if len(collision_records) != bone_count:
                    raise ValueError(
                        f"Collision record count {len(collision_records)} does not match "
                        f"bone count {bone_count}."
                    )
                file_data += writeuint8(_ensure_uint(1, 8, "Bounding info flag"))
                for record in collision_records:
                    file_data += encode_collision_record(record)
            log.write("...done.\n"); log.flush()

            log.write("Writing bone matrices...\n"); log.flush()
            for matrixes in mesh_data['bone_matrix']:
                if len(matrixes) != 16:
                    raise ValueError(
                        f"Bone matrix record contains {len(matrixes)} floats; expected 16."
                    )
                for matrix in matrixes:
                    file_data += writefloat(matrix)
            log.write("...done.\n"); log.flush()

            file_data += writeuint8(_ensure_uint(0, 8, "Binding info flag")) # has_binding_info
            table_offset = len(file_data)
            file_data += writeuint32(_ensure_uint(0, 32, "LOD table offset placeholder")) # table_offset // will be updated

            log.write("Writing mesh info headers...\n"); log.flush()
            vertex_count = 0
            face_count = 0

            for mesh_index, mesh_info in enumerate(mesh_data['mesh']):
                vtx_count = len(mesh_info['position'])
                file_data += writeuint32(_ensure_uint(vtx_count, 32, f"Vertex count for mesh {mesh_index}"))
                vertex_count += vtx_count

                fce_count = len(mesh_info['face'])
                file_data += writeuint32(_ensure_uint(fce_count, 32, f"Face count for mesh {mesh_index}"))
                face_count += fce_count

                file_data += writeuint8(_ensure_uint(1, 8, f"UV channel count for mesh {mesh_index}")) # uv_channel_count
                file_data += writeuint8(_ensure_uint(0, 8, f"Color flag for mesh {mesh_index}")) # has_color
            log.write("...done.\n"); log.flush()

            log.write("Writing LOD and total counts...\n"); log.flush()
            file_data += writeuint16(_ensure_uint(1, 16, "LOD section flag")) # lod_new_v
            file_data += writeuint32(_ensure_uint(vertex_count, 32, "Total vertex count"))
            file_data += writeuint32(_ensure_uint(face_count, 32, "Total face count"))
            log.write("...done.\n"); log.flush()

            log.write("Writing vertex positions...\n"); log.flush()
            for mesh_info in mesh_data['mesh']:
                for position in mesh_info['position']:
                    for point in position:
                        file_data += writefloat(point)
            log.write("...done.\n"); log.flush()

            log.write("Writing vertex normals...\n"); log.flush()
            for mesh_info in mesh_data['mesh']:
                for normal in mesh_info['normal']:
                    for point in normal:
                        file_data += writefloat(point)
            log.write("...done.\n"); log.flush()

            log.write("Writing vertex tangents...\n"); log.flush()
            file_data += writeuint16(_ensure_uint(1, 16, "Tangent section flag")) # has tangent
            for mesh_index, mesh_info in enumerate(mesh_data['mesh']):
                for tangent in mesh_info['tangent']:
                    for point in tangent:
                        file_data += writefloat(point)
            log.write("...done.\n"); log.flush()

            log.write("Writing face indices...\n"); log.flush()
            first_index = 0
            for mesh_index, mesh_info in enumerate(mesh_data['mesh']):
                for face in mesh_info['face']:
                    for point in face:
                        final_index = point + first_index
                        file_data += writeuint16(_ensure_uint(final_index, 16, f"Face index for mesh {mesh_index}"))
                first_index += len(mesh_info['position'])
            log.write("...done.\n"); log.flush()

            log.write("Writing UVs...\n"); log.flush()
            for mesh_info in mesh_data['mesh']:
                for uv in mesh_info['uv']:
                    for point in uv:
                        file_data += writefloat(point)
            log.write("...done.\n"); log.flush()

            log.write("Writing vertex joints...\n"); log.flush()
            # vertex color skipped
            for mesh_index, mesh_info in enumerate(mesh_data['mesh']):
                for vertex_idx, vertex_joint in enumerate(mesh_info['vertex_joint']):
                    for joint in vertex_joint:
                        label = f"Joint index for mesh {mesh_index}, vertex {vertex_idx}"
                        file_data += writeuint16(_ensure_uint(joint, 16, label))
            log.write("...done.\n"); log.flush()

            log.write("Writing vertex weights...\n"); log.flush()
            for mesh_info in mesh_data['mesh']:
                for vertex_joint_weight in mesh_info['vertex_joint_weight']:
                    for weight in vertex_joint_weight:
                        file_data += writefloat(weight)
            log.write("...done.\n"); log.flush()

            log.write("Writing footer data (BoneWeightUsageMask, LODTable)...\n"); log.flush()
            file_data += mesh_data['bone_weight_usage']

            current_offset = _ensure_uint(len(file_data), 32, "Data table offset")
            file_data[table_offset:table_offset+4] = writeuint32(current_offset)
            lod_table = arm_obj.get('NeoX:LODTable', bytes(16))
            file_data += bytes(lod_table)
            log.write("...done.\n"); log.flush()

            log.write(f"Total file size: {len(file_data)} bytes. Writing to disk...\n"); log.flush()
            file.write(file_data)
            log.write("...done.\n"); log.flush()

    except Exception as e:
        import traceback
        log.write(f"CRITICAL PYTHON ERROR during binary export:\n"); log.flush()
        log.write(f"ERROR: {e}\n"); log.flush()
        traceback.print_exc(file=log)
        operator.report({'ERROR'}, f"[export_neox_mesh] {str(e)}")
        return False

    log.write("--- Finished binary export successfully ---\n"); log.flush()
    return True
