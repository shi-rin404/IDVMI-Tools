import bpy, os , math, json
from mathutils import Matrix, Vector
from bpy.props import StringProperty
from bpy_extras.io_utils import ExportHelper, axis_conversion
from .export_utils import writeuint8, writeuint16, writeuint32, writefloat

class IDVMI_OT_Export_Neox_Mesh(bpy.types.Operator, ExportHelper):
    bl_idname = "idvmi_tools.neox_exporter"
    bl_label = "Export NeoX Mesh"

    # ExportHelper parametreleri
    filename_ext = ".mesh"
    filter_glob: StringProperty(
        default="*.mesh",
        options={'HIDDEN'},
        maxlen=255,
    )

    def execute(self, context):
        export_path = bpy.path.abspath(self.filepath)
        flip_uv_y = context.scene.flip_uv_y

        # skeleton_path = bpy.path.abspath(context.scene.neox_skeleton_selector)

        # if not skeleton_path.strip():
        #     self.report({'ERROR'}, "Please select skeleton file to export!")
        #     return {'CANCELLED'}

        bpy.ops.object.mode_set(mode='OBJECT')
        arm_obj = context.active_object    

        if arm_obj.type != 'ARMATURE':     
            while arm_obj:
                if arm_obj.type != 'ARMATURE':
                    arm_obj = arm_obj.parent
                else:
                    break
            if not arm_obj:
                self.report({'ERROR'}, "Please select an armature that has mesh(es)")
                return {'CANCELLED'}

        export_neox_mesh(
            export_path,
            parse_blender_meshes(arm_obj, flip_uv_y),
            arm_obj,
            self
        )

        self.report({'INFO'}, f"Export OK → {export_path}")
        return {'FINISHED'}

# def parse_skeleton(skeleton_path, operator):
#     with open(skeleton_path, "r") as skeleton:
#         parsed = json.load(skeleton)

#     return parsed

def parse_blender_meshes(armature, flip_uv_y) -> dict:
    # --- Eksen dönüşümleri ---
    M_blender_to_game = axis_conversion(
    from_forward='-Y', from_up='Z',   # Blender’ın yönleri
    to_forward='Z',   to_up='Y'      # oyunun yönleri
    ).to_4x4()

    M_blender_to_game = Matrix.Rotation(math.pi, 4, 'X') @ M_blender_to_game

    M_vert = M_blender_to_game.to_3x3()
    M_norm  = (M_blender_to_game.inverted().transposed()).to_3x3()

    mesh_data = {}

    
    mesh_data['bone_tail'] = armature['NeoX:BoneTail']
    mesh_data['bone_name'] = armature['NeoX:BoneOrder'] 
    mesh_data['bone_parent'] = []   
    mesh_data['bone_original_matrix'] = armature['Neox:BoneMatrix']

    bone_index = {name: idx for idx, name in enumerate(mesh_data['bone_name'])}
    # bone_name = {idx: name for idx, name in enumerate(mesh_data['bone_name'])}

    bones = {}

    for bone in armature.data.bones:
        if bone.name in mesh_data['bone_name']:
            bones[bone_index[bone.name]] = bone    

    for n in range(len(bones)):
        bone = bones[n]
        if bone.parent:
            mesh_data['bone_parent'].append(bone_index[bone.parent.name])
        else:
            mesh_data['bone_parent'].append(65535)
        # M_game_global = M_blender_to_game.inverted() @ armature.matrix_world @ bone.matrix_local @ M_blender_to_game
        # mesh_data['bone_original_matrix'].append(M_game_global.transposed())

    mesh_data['mesh'] = []
    for child in armature.children_recursive:
        if child.type == 'MESH':
            positions = [v.co.copy() @ M_vert for v in child.data.vertices]
            normals = [v.normal.copy() @ M_vert for v in child.data.vertices]

            child.data.calc_loop_triangles()
            faces = [tri.vertices for tri in child.data.loop_triangles]

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
            # uv_vertex: vertex başına 2-float

            child.data.calc_tangents()  # aktif UV üzerinden

            acc = [Vector((0,0,0)) for _ in child.data.vertices]
            cnt = [0]*len(child.data.vertices)
            for l in child.data.loops:
                acc[l.vertex_index] += l.tangent
                cnt[l.vertex_index] += 1
            vert_tangent = [(acc[i]/cnt[i]).normalized() if cnt[i] else Vector((1,0,0))
                            for i in range(len(child.data.vertices))]
            
            vert_tangent = [t @ M_vert for t in vert_tangent]  # w=0 mantığıyla

            vgroups = list(child.vertex_groups)

            # Bone isimlerinden sıra: name -> bone_idx (zaten var)
            # bone_index = {b.name:i for i,b in enumerate(armature.data.bones)}

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

            # for v in child.data.vertices:
                # (rank, bone_index, weight) listesi topla
                # triples = []
                # for g in v.groups:
                #     bi = vg_to_bone.get(g.group, -1)
                #     if bi >= 0 and g.weight > 0.0:
                        # r = vg_rank.get(g.group, 10**9)  # eşleşmeyen en sona
                        # triples.append((r, bi, g.weight))

                # Ağırlığa göre top-k seç
                # triples.sort(key=lambda t: t[2], reverse=True)   # weight desc
                # triples = triples[:topk]

                # Pad
                # while len(triples) < topk:
                #     triples.append((10**9, 65535, 0.0))  # rank büyük, weight 0

                # Çıktıyı bone sırasına (rank) göre sabitle
                # triples.sort(key=lambda t: t[0])     # rank asc

                # idxs = [t[1] for t in triples]
                # wts  = [t[2] for t in triples]

                # Normalize
                # s = sum(wts)
                # if s > 0.0:
                #     wts = [w/s for w in wts]
                
                # joints.append(idxs)
                # weights.append(wts)
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
                        vertex_weights.append(group.weight)
                        vertex_joints.append(bone_index[vertex_group_names[group.group]])
                joints.append(vertex_joints)
                weights.append(vertex_weights)

            # if child.name.endswith("0"):
            #     seen = {}
            #     for n, double in enumerate(zip(joints, weights)):
            #         _joints, _weights = double
            #         for joint, weight in zip(_joints, _weights):
            #             if joint not in seen:
            #                 seen[joint] = []
            #             if joint != 65535:
            #                 seen[joint].append(weight)
                
            #     max_key = max(seen, key=lambda k: len(seen[k]))
            #     bone_name = {bone_index[key]:key for key in bone_index}
            #     operator.report({'INFO'}, f"{max_key} = {bone_name[max_key]}")
            
            mesh_data['mesh'].append({'position': positions, 'normal': normals, 'tangent': vert_tangent, 'face': faces, 'uv': uv_vertex, 'vertex_joint': joints, 'vertex_joint_weight': weights})
    
    return mesh_data
    
def export_neox_mesh(export_path:os.PathLike, mesh_data:dict, arm_obj, operator):
    bpy.ops.object.mode_set(mode='OBJECT')
        
    with open(export_path, "wb") as file:
        file_data = bytearray()

        file_data += b"\x34\x80\xC8\xBB" # Magic Number
        file_data += b"\x04\x00\x05\x00" # File Version
        file_data += writeuint32(1) # Bone Exist [file_version_mask + patch_version + mesh_type(skeletal)]

        bone_count = len(mesh_data['bone_name'])
        file_data += writeuint16(bone_count)

        for parent_idx in mesh_data['bone_parent']:
            file_data += writeuint16(parent_idx)
        # for parent in arm_obj['NeoX:BoneParent']:
        #     parent = 65535 if parent == -1 else parent
        #     file_data += writeuint16(parent)

        for n in range(bone_count):
            file_data += mesh_data['bone_name'][n].encode('utf-8').ljust(32, b"\x00")
        # for n in range(bone_count):
        #     file_data += arm_obj['NeoX:BoneOrder'][n].encode('utf-8').ljust(32, b"\x00")

        if "NeoX:BoundingInfo" not in arm_obj or not arm_obj["NeoX:BoundingInfo"]:
            file_data += writeuint8(0)
        else:
            file_data += writeuint8(1)
            bpy.context.view_layer.objects.active = arm_obj
            arm_obj.select_set(True)
            bpy.ops.object.mode_set(mode='POSE')
            for pbone in arm_obj.pose.bones:
                try:
                    for coordinate in pbone["NeoX:BoundingInfo"]:
                        file_data += writefloat(coordinate)
                except KeyError:
                    operator.report({'ERROR'}, "Adding/Deleting bones isn't supported for now")
                    return {'CANCELLED'}
            bpy.ops.object.mode_set(mode='OBJECT')

        # for bone in mesh_data['bone_original_matrix']:
        #     for coordinate in bone:                
        #         x, y, z, w = coordinate
        #         file_data += writefloat(x)
        #         file_data += writefloat(y)
        #         file_data += writefloat(z)
        #         file_data += writefloat(w)
        for matrixes in arm_obj['Neox:BoneMatrix']:
            for matrix in matrixes:
                file_data += writefloat(matrix)

        file_data += writeuint8(0) # has_binding_info
        table_offset = len(file_data)
        file_data += writeuint32(0) # table_offset // will be updated

        vertex_count = 0
        face_count = 0

        for mesh_info in mesh_data['mesh']:
            vtx_count = len(mesh_info['position'])
            file_data += writeuint32(vtx_count)
            vertex_count += vtx_count

            fce_count = len(mesh_info['face'])
            file_data += writeuint32(fce_count)
            face_count += fce_count

            file_data += writeuint8(1) # uv_channel_count
            file_data += writeuint8(0) # has_color

        file_data += writeuint16(1) # lod_new_v
        file_data += writeuint32(vertex_count)
        file_data += writeuint32(face_count)

        for mesh_info in mesh_data['mesh']:
            for position in mesh_info['position']:
                x, y, z = position
                file_data += writefloat(x)
                file_data += writefloat(y)
                file_data += writefloat(z)
        # for positions in arm_obj['NeoX:OriginalPositions']:
        #     for position in positions:
        #         file_data += writefloat(position)

        for mesh_info in mesh_data['mesh']:
            for normal in mesh_info['normal']:
                x, y, z = normal
                file_data += writefloat(x)
                file_data += writefloat(y)
                file_data += writefloat(z)        

        file_data += writeuint16(1) # has tangent
        for mesh_info in mesh_data['mesh']:
            for tangent in mesh_info['tangent']:
                x, y, z = tangent
                file_data += writefloat(x)
                file_data += writefloat(y)
                file_data += writefloat(z)

        first_index = 0
        for mesh_info in mesh_data['mesh']:
            for face in mesh_info['face']:
                v1, v2, v3 = face
                file_data += writeuint16(v1 + first_index)
                file_data += writeuint16(v2 + first_index)
                file_data += writeuint16(v3 + first_index)
            first_index += len(mesh_info['position'])
        # for positions in arm_obj['NeoX:OriginalFaces']:
        #     for position in positions:
        #         file_data += writeuint16(position)

        for mesh_info in mesh_data['mesh']:
            for uv in mesh_info['uv']:
                u, v = uv
                file_data += writefloat(u)
                file_data += writefloat(v)

        # vertex color skipped

        for mesh_info in mesh_data['mesh']:
            for vertex_joint in mesh_info['vertex_joint']:
                vg1, vg2, vg3, vg4 = vertex_joint
                file_data += writeuint16(vg1)
                file_data += writeuint16(vg2)
                file_data += writeuint16(vg3)
                file_data += writeuint16(vg4)
        # for joints in arm_obj['NeoX:OriginalJoints']:
        #     for joint in joints:
        #         file_data += writeuint16(joint)

        for mesh_info in mesh_data['mesh']:
            for vertex_joint_weight in mesh_info['vertex_joint_weight']:
                vg1, vg2, vg3, vg4 = vertex_joint_weight                
                file_data += writefloat(vg1)
                file_data += writefloat(vg2)
                file_data += writefloat(vg3)
                file_data += writefloat(vg4)
        # for weights in arm_obj['NeoX:OriginalWeights']:
        #     for weight in weights:
        #         file_data += writefloat(weight)

        file_data += arm_obj['NeoX:BoneTail']

        file_data[table_offset:table_offset+4] = writeuint32(len(file_data))
        file_data += arm_obj['NeoX:LODTable']

        file.write(file_data)