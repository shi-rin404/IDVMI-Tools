from .neox_mesh_parser import parse_mesh_1, parse_mesh_2
import bpy
import os
from mathutils import Matrix, Vector
from bpy_extras.io_utils import axis_conversion
from math import pi

class IDVMI_OT_Import_Neox_Mesh(bpy.types.Operator):
    bl_idname = "idvmi_tools.neox_importer"
    bl_label = "Import NeoX Mesh"

    def execute(self, context):
        mesh_path = bpy.path.abspath(context.scene.neox_mesh_selector)

        with open(mesh_path, "rb") as mesh_file:           
            is_parser_tried = {parse_mesh_1: False, parse_mesh_2: False}

            for parser in is_parser_tried:
                try:
                    model = {}
                    mesh_file.seek(0)
                    is_parser_tried[parser] = True
                    parser(model, mesh_file, self)
                    for weights in model['vertex_weight']:
                        for weight in weights:
                            if type(weight) != float or weight > 1.0 or weight < 0.0:
                                self.report({'ERROR'}, f"Incorrect weights")            
                                continue
                    break
                except Exception as e:
                    self.report({'ERROR'}, f"{e}")                    
                    model = {}
                    continue

        if model == {}:
            self.report({'ERROR'}, "Model can't be decoded")
            return {'CANCELLED'}
            

        obj_name = os.path.basename(mesh_path).rsplit(".", 1)[0]
        import_per_material(model, obj_name, self)
        
        self.report({'INFO'}, f"Import OK → {mesh_path}")
        return {'FINISHED'}
    
        
def import_per_material(model, obj_name: str, operator):
    # --- Axis conversation ---
    M_game_to_blender = axis_conversion(
        from_forward='Z', from_up='Y',   # game 
        to_forward='-Y',   to_up='Z'      # blender
    ).to_4x4()    

    # -- Armature --
    armature_data = bpy.data.armatures.new(obj_name)
    # armature_data.display_type = 'STICK'

    armature_obj = bpy.data.objects.new(obj_name, armature_data)
    bpy.context.collection.objects.link(armature_obj)
    bpy.context.view_layer.objects.active = armature_obj

    # """ USAGE: bone_index[name] = bone_index """
    bone_index = {bone_name: bone_index for bone_index, bone_name in enumerate(model['bone_name'])}
    
    # """ USAGE: bone_namer[index] = bone_name """
    bone_namer = {bone_index: bone_name for bone_index, bone_name in enumerate(model['bone_name'])}
    
    # """ USAGE: parent_names[index] = parent_name """
    parent_names = [model['bone_name'][n] if n != -1 else None for n in model['bone_parent']] 

    # -- Bones --
    def matrix_to_blender(matrix_4):
        """Convert 4x4 matrix to Blender coordinate system and extract translation"""
        return (M_game_to_blender @ Matrix(matrix_4.tolist()).transposed()).to_translation()

    def find_child(bone_name: str):
        """Find first child of a bone"""
        try:
            return parent_names.index(bone_name)
        except ValueError:
            return None

    def find_parent(bone_name: str):
        """Find parent name of a bone"""
        if bone_name in bone_index:
            return parent_names[bone_index[bone_name]]
        return None

    bpy.ops.object.mode_set(mode='EDIT')

    # Create all bones first (heads only)
    for bone_name, matrix_4 in zip(model['bone_name'], model['bone_matrix']):        
        bone = armature_obj.data.edit_bones.new(bone_name)
        bone.head = matrix_to_blender(matrix_4)
        # Set temporary tail (will be corrected later)
        bone.tail = bone.head + Vector((0, 0, 0.1))    

    # Set bone hierarchy and tails
    for bone_name in model['bone_name']:  

        edit_bone = armature_obj.data.edit_bones[bone_name]
        
        if bone_name == "biped":
            bpy.ops.object.mode_set(mode='OBJECT')
            operator.report({'INFO'}, f"{'biped' in armature_obj.data.bones}")
            bpy.ops.object.mode_set(mode='EDIT')

        # Set parent
        parent_name = find_parent(bone_name)
        if parent_name:
            edit_bone.parent = armature_obj.data.edit_bones[parent_name]
        else:
            edit_bone.parent = None            

        # Set tail to first child's head, or offset from head if no child
        child_index = find_child(bone_name)
        if child_index is not None and child_index < len(model['bone_name']):
            child_name = bone_namer[child_index]
            if child_name in armature_obj.data.edit_bones:
                edit_bone.tail = armature_obj.data.edit_bones[child_name].head
        else:
            # No child found, set tail to offset from head
            edit_bone.tail = edit_bone.head + Vector((0, 0, 0.1))

    # bpy.ops.object.mode_set(mode='OBJECT')

    # """ USAGE: armature_bone_index[name] = bone_index """
    # armature_bone_index = {bone.name: bone_index for bone_index, bone in enumerate(armature_obj.data.bones)}
    
    # """ USAGE: armature_bone_namer[index] = bone_name """
    # armature_bone_namer = {bone_index: bone.name for bone_index, bone in enumerate(armature_obj.data.bones)}

    # Custom Properties
    bpy.ops.object.mode_set(mode='POSE')

    
    for n, pbone in enumerate(armature_obj.pose.bones):
        if n < len(model['bounding_info']):
            pbone["NeoX:BoundingInfo"] = model['bounding_info'][n]

    bpy.ops.object.mode_set(mode='OBJECT')    

    # Set armature custom properties
    armature_obj['NeoX:BoneOrder'] = model['bone_name']
    armature_obj['NeoX:BoundingInfo'] = True
    armature_obj['Neox:BoneMatrix'] = model['bone_matrix']
    
    armature_obj['NeoX:BoneTail'] = model['bone_tail']
    armature_obj['NeoX:LODTable'] = model['lod_data_table']

    # Convert matrix for 3D operations
    _3D_Matrix = M_game_to_blender.to_3x3()
    
    # Meshes
    current_vertex_index = 0
    current_face_index = 0
    
    for mesh_index, mesh_info in enumerate(model['mesh']):
        mesh_vertex_count, mesh_face_count, uv_ch_count, has_color = mesh_info

        mesh_data = bpy.data.meshes.new(f"{obj_name}_{mesh_index}")
        mesh_obj = bpy.data.objects.new(f"{obj_name}_{mesh_index}", mesh_data)
        bpy.context.collection.objects.link(mesh_obj)
    
        # Position & Normal - FIX: Convert tuples to Vector properly
        vertices = []
        normals = []
        
        for vertex_index in range(current_vertex_index, current_vertex_index + mesh_vertex_count):
            # Convert position and normal to Vectors, then apply transformation
            pos_vector = Vector(model['position'][vertex_index])
            norm_vector = Vector(model['normal'][vertex_index])
            
            vertices.append((_3D_Matrix @ pos_vector)[:])  # Convert back to tuple
            normals.append((_3D_Matrix @ norm_vector)[:])   # Convert back to tuple

        # Faces - FIX: Adjust face indices to be relative to current mesh
        faces = []
        for face_index in range(current_face_index, current_face_index + mesh_face_count):
            # Adjust face indices to be relative to current mesh vertices
            original_face = model['face'][face_index]
            adjusted_face = [idx - current_vertex_index for idx in original_face]
            faces.append(adjusted_face)

        current_face_index += mesh_face_count

        # Create mesh geometry
        mesh_data.from_pydata(vertices, [], faces)
        
        # FIX: Set custom normals properly
        mesh_data.use_auto_smooth = True
        # mesh_data.auto_smooth_angle = 3.14159  # 180 degrees
        mesh_data.auto_smooth_angle = pi  # 180 degrees
        
        mesh_data.calc_loop_triangles()
        mesh_data.calc_normals_split()
        mesh_data.normals_split_custom_set_from_vertices(normals)
        mesh_data.update()

        # UV Mapping - FIX: Proper UV assignment
        if 'uv' in model and model['uv']:
            if not mesh_obj.data.uv_layers:
                mesh_obj.data.uv_layers.new()

            uv_layer = mesh_obj.data.uv_layers.active.data

            # Map vertex UVs to loops (face corners)
            for face in mesh_data.polygons:
                for loop_idx in face.loop_indices:
                    vertex_idx = mesh_data.loops[loop_idx].vertex_index
                    global_vertex_idx = current_vertex_index + vertex_idx
                    
                    if global_vertex_idx < len(model['uv']):
                        uv_layer[loop_idx].uv = model['uv'][global_vertex_idx]

        # Create Vertex Groups for all bones
        for bone_name in model['bone_name']:
            if bone_name not in mesh_obj.vertex_groups:
                mesh_obj.vertex_groups.new(name=bone_name)

        # FIX: Assign vertex weights properly
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
                if joint == 65535:
                    continue
                    
                group_name = bone_namer[joint]
                
                vertex_group = mesh_obj.vertex_groups[group_name]                   
                vertex_group.add([local_vertex_index], weight, 'ADD')

        current_vertex_index += mesh_vertex_count

        # --- 3) ARMATURE_AUTO YOK. Sadece modifier + parent ekle ---
        modifier = mesh_obj.modifiers.new(name="Armature", type='ARMATURE')
        modifier.object = armature_obj
        modifier.use_vertex_groups = True
        modifier.use_bone_envelopes = False

        mesh_obj.parent = armature_obj  # opsiyonel, sadece hiyerarşi için

    print(f"Successfully imported model: {obj_name}")
    return armature_obj

