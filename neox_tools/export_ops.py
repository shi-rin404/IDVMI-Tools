import struct
import bpy, os , math
import bmesh
from mathutils import Matrix, Vector
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper, axis_conversion
from .export_utils import writeuint8, writeuint16, writeuint32, writefloat


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

    if arm_obj.type != 'ARMATURE':     
        while arm_obj:
            if arm_obj.type != 'ARMATURE':
                arm_obj = arm_obj.parent
            else:
                break
        if not arm_obj:
            operator.report({'ERROR'}, "Please select an armature that has mesh(es)")
            return {'CANCELLED'}
    
    return arm_obj

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
    mesh_data['bone_tail'] = armature['NeoX:BoneTail']
    mesh_data['bone_name'] = armature['NeoX:BoneOrder'] 
    mesh_data['bone_parent'] = []   
    mesh_data['bone_original_matrix'] = armature['Neox:BoneMatrix']

    bone_index = {name: idx for idx, name in enumerate(mesh_data['bone_name'])}

    bones = {}

    for bone in armature.data.bones:
        if bone.name in mesh_data['bone_name']:
            bones[bone_index[bone.name]] = bone

    for n in range(len(bones)):
        try:
            bone = bones[n]
        except:
            operator.report({'ERROR'}, f"{sorted([key for key in bones])}")
            return False
        if bone.parent:
            mesh_data['bone_parent'].append(bone_index[bone.parent.name])
        else:
            mesh_data['bone_parent'].append(65535)
    log.write(f"Found {len(mesh_data['bone_name'])} bones.\n"); log.flush()

    mesh_data['mesh'] = []
    log.write("Starting to process child meshes...\n"); log.flush()
    for child in armature.children_recursive:
        if child.type == 'MESH':
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
                            operator.report({'ERROR'}, "Mesh bone names are not equal with the armature.")
                            return False
                joints.append(vertex_joints)
                weights.append(vertex_weights)
            log.write("    ...done.\n"); log.flush()
            
            mesh_data['mesh'].append({'position': positions, 'normal': normals, 'tangent': vert_tangent, 'face': faces, 'uv': uv_vertex, 'vertex_joint': joints, 'vertex_joint_weight': weights})
            log.write(f"  Finished processing mesh: {child.name}\n"); log.flush()
    
    log.write("--- Finished mesh parsing ---\n"); log.flush()
    return mesh_data
    
def export_neox_mesh(export_path:os.PathLike, mesh_data:dict, arm_obj, operator, log):
    log.write("--- Starting binary export ---\n"); log.flush()
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
                file_data += mesh_data['bone_name'][n].encode('utf-8').ljust(32, b"\x00")
            log.write("...done.\n"); log.flush()

            log.write("Writing bounding info...\n"); log.flush()
            if "NeoX:BoundingInfo" not in arm_obj or not arm_obj["NeoX:BoundingInfo"]:
                file_data += writeuint8(_ensure_uint(0, 8, "Bounding info flag"))
            else:
                file_data += writeuint8(_ensure_uint(1, 8, "Bounding info flag"))
                bpy.context.view_layer.objects.active = arm_obj
                arm_obj.select_set(True)
                bpy.ops.object.mode_set(mode='POSE')
                for pbone in arm_obj.pose.bones:
                    try:
                        for coordinate in pbone["NeoX:BoundingInfo"]:
                            file_data += writefloat(coordinate)
                    except KeyError:
                        log.write("ERROR: Bounding info mismatch. Adding/Deleting bones isn't supported for now.\n"); log.flush()
                        operator.report({'ERROR'}, "Adding/Deleting bones isn't supported for now")
                        return False
                bpy.ops.object.mode_set(mode='OBJECT')
            log.write("...done.\n"); log.flush()

            log.write("Writing bone matrices...\n"); log.flush()
            for matrixes in arm_obj['Neox:BoneMatrix']:
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

            log.write("Writing footer data (BoneTail, LODTable)...\n"); log.flush()
            file_data += arm_obj['NeoX:BoneTail']

            current_offset = _ensure_uint(len(file_data), 32, "Data table offset")
            file_data[table_offset:table_offset+4] = writeuint32(current_offset)
            file_data += arm_obj['NeoX:LODTable']
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
