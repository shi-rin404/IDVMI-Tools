from .neox_mesh_parser import parse_mesh_1, parse_mesh_2, parse_mesh_3
import bpy
import os
from mathutils import Matrix, Vector
from math import isfinite
from bpy_extras.io_utils import axis_conversion
from math import pi

class IDVMI_OT_Import_Neox_Mesh(bpy.types.Operator):
    bl_idname = "idvmi_neox.neox_importer"
    bl_label = "Import NeoX Mesh"

    def execute(self, context):
        root = os.path.dirname(__file__)
        log_file = os.path.join(root, "import_per_material_log.txt")
        with open(log_file, "w") as log:
            log.write("--- New import session started ---\n")

        mesh_path = bpy.path.abspath(context.scene.neox_mesh_selector)

        with open(mesh_path, "rb") as mesh_file:           
            is_parser_tried = {parse_mesh_1: False, parse_mesh_2: False, parse_mesh_3: False}

            for parser in is_parser_tried:
                try:
                    model = {}
                    mesh_file.seek(0)
                    is_parser_tried[parser] = True
                    parser(model, mesh_file, self)
                    check_weights(model['vertex_weight'], self)
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
    
def check_weights(weight_data, operator):
    for weights in weight_data:
        for weight in weights:
            if type(weight) != float or weight > 1.0 or weight < 0.0:
                operator.report({'ERROR'}, f"Incorrect weights. Example weight: {weight}")            
                return False
    return True
        
def import_per_material(model, obj_name: str, operator):
    root = os.path.dirname(__file__)
    log_file = os.path.join(root, "import_per_material_log.txt")

    with open(log_file, "a") as log:
        log.write(f"--- Starting import for {obj_name} ---\n"); log.flush()

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

        # """ USAGE: bone_index[name] = bone_index """
        bone_index = {bone_name: bone_index for bone_index, bone_name in enumerate(model['bone_name'])}
        
        # """ USAGE: bone_namer[index] = bone_name """
        bone_namer = {bone_index: bone_name for bone_index, bone_name in enumerate(model['bone_name'])}
        
        # """ USAGE: parent_names[index] = parent_name """
        parent_names = [model['bone_name'][n] if n != -1 else None for n in model['bone_parent']]

        # -- Bones --
        log.write("Creating bones...\n"); log.flush()
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
        log.write("Setting bone heads...\n"); log.flush()
        for idx, bone_name in enumerate(model['bone_name']):
            matrix_4 = model['bone_matrix'][idx]
            bone = armature_obj.data.edit_bones.new(bone_name)
            bone.head = matrix_to_blender(matrix_4)
            # Set temporary tail (will be corrected later)
            bone.tail = bone.head + Vector((0, 0, 0.1))
        log.write("Bone heads set.\n"); log.flush()


        # Set bone hierarchy and tails
        log.write("Setting bone hierarchy and tails...\n"); log.flush()
        for bone_name in model['bone_name']:                  
            edit_bone = armature_obj.data.edit_bones[bone_name]        

            # Set parent (be explicit so index 0 isn't treated as falsy)
            parent_name = find_parent(bone_name)
            if parent_name is not None:
                edit_bone.parent = armature_obj.data.edit_bones[parent_name]
            else:            
                bpy.ops.object.mode_set(mode='OBJECT')
                bpy.context.view_layer.update()
                bpy.ops.object.mode_set(mode='EDIT')

            # Set tail to first child's head, or offset from head if no child
            child_index = find_child(bone_name)
            if child_index is not None and child_index < len(model['bone_name']):
                child_name = bone_namer[child_index]
                if child_name in armature_obj.data.edit_bones:
                    edit_bone.tail = armature_obj.data.edit_bones[child_name].head
            else:
                # No child found, set tail to offset from head
                edit_bone.tail = edit_bone.head + Vector((0, 0, 0.1))
        log.write("Bone hierarchy and tails set.\n"); log.flush()

        # Custom Properties - CRASH ANALYZER BLOCK
        log.write("Setting custom properties on pose bones...\n"); log.flush()
        try:
            log.write("Switching to POSE mode...\n"); log.flush()
            bpy.ops.object.mode_set(mode='POSE')
            log.write("...switched to POSE mode successfully.\n"); log.flush()

            bounding_info = model.get('bounding_info')
            if bounding_info is None:
                log.write("WARNING: 'bounding_info' not found in model data. Skipping pose bone properties.\n"); log.flush()
            else:
                log.write(f"Found 'bounding_info' with {len(bounding_info)} entries. Armature has {len(armature_obj.pose.bones)} pose bones.\n"); log.flush()
                
                for n, pbone in enumerate(armature_obj.pose.bones):
                    # log.write(f"  Processing pose bone {n} ('{pbone.name}')...\n"); log.flush()
                    
                    if n < len(bounding_info):
                        info_to_assign = bounding_info[n]
                        # log.write(f"    Assigning bounding info (type: {type(info_to_assign)}): {info_to_assign}\n"); log.flush()
                        
                        # It's safer to assign custom properties in OBJECT mode.
                        # While it often works in POSE mode, it can be a source of instability.
                        bpy.ops.object.mode_set(mode='OBJECT')
                        pbone.id_data["NeoX:BoundingInfo"] = str(info_to_assign) # Assign as string for max safety
                        bpy.ops.object.mode_set(mode='POSE')

                        # log.write(f"    ...assignment successful for bone {n}.\n"); log.flush()
                    else:
                        operator.report({'WARNING'}, f"Not enough bounding_info entries for bone {n}. Stopping property assignment.")
                        log.write(f"    WARNING: Not enough bounding_info entries for bone {n}. Stopping property assignment.\n"); log.flush()
                        break 
            
            log.write("Switching back to OBJECT mode...\n"); log.flush()
            bpy.ops.object.mode_set(mode='OBJECT')
            log.write("...switched to OBJECT mode successfully.\n"); log.flush()

        except Exception as e:
            log.write(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"); log.flush()
            log.write(f"CRITICAL PYTHON ERROR while setting pose bone properties:\n"); log.flush()
            log.write(f"ERROR: {e}\n"); log.flush()
            import traceback
            traceback.print_exc(file=log)
            log.flush()
            log.write(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n"); log.flush()
            # Switch back to object mode to prevent leaving Blender in a weird state
            bpy.ops.object.mode_set(mode='OBJECT')   

            operator.report({'ERROR'}, f"Critical error while setting pose bone properties, error: {e}")
            return {'CANCELLED'}
       
        # Set armature custom properties
        log.write("Setting custom properties on armature...\n"); log.flush()
        armature_obj['NeoX:BoneOrder'] = model['bone_name']
        armature_obj['NeoX:BoundingInfo'] = True
        armature_obj['Neox:BoneMatrix'] = model['bone_matrix']
        
        armature_obj['NeoX:BoneTail'] = model['bone_tail']
        armature_obj['NeoX:LODTable'] = model['lod_data_table']
        log.write("Custom properties set.\n"); log.flush()

        # Convert matrix for 3D operations
        _3D_Matrix = M_game_to_blender.to_3x3()
        
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
        print(f"Successfully imported model: {obj_name}")
        return armature_obj

