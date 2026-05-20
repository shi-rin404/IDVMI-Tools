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
                    self.report({'INFO'}, f"Trying {parser.__name__}...")
                    model = {}
                    mesh_file.seek(0)
                    is_parser_tried[parser] = True
                    parser(model, mesh_file, self)
                    if 'vertex_weight' in model:
                        check_weights(model['vertex_weight'], self)
                    break
                except Exception as e:
                    self.report({'ERROR'}, f"[{type(e).__name__}] {e}")                    
                    model = {}
                    continue


        if model == {}:
            self.report({'ERROR'}, "Model can't be decoded")
            return {'CANCELLED'}
            

        obj_name = os.path.basename(mesh_path).rsplit(".", 1)[0]
        if import_per_material(model, obj_name, self):        
            self.report({'INFO'}, f"Import OK → {mesh_path}")
            return {'FINISHED'}
        else:
            return {'CANCELLED'}
    
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

                    # Set bone position
                    matrix_4 = model['bone_matrix'][idx]
                    bone.head = matrix_to_blender(matrix_4)
                    print(matrix_to_blender(matrix_4))

                    # Set temporary tail (will be corrected later)
                    bone.tail = bone.head + Vector((0, 0, 0.1))
                except Exception as e:
                    log.write(f"  !! FAILED to create bone '{bone_name}': {e}\n"); log.flush()
                    operator.report({'ERROR'}, f"[{type(e).__name__}] {e}")
                    import traceback
                    traceback.print_exc(file=log)
                    return False               

            log.write("Bone heads set.\n"); log.flush()

            # Set bone hierarchy and tails
            log.write("Setting bone hierarchy and tails...\n"); log.flush()
            for bone_name in model['bone_name']:
                if bone_name not in armature_obj.data.edit_bones:
                    log.write(f"  Skipping hierarchy for '{bone_name}' as it was not created.\n"); log.flush()
                    continue

                edit_bone = armature_obj.data.edit_bones[bone_name]        

                # Set parent (be explicit so index 0 isn't treated as falsy)
                parent_name = find_parent(bone_name)
                if parent_name is not None:
                    if parent_name in armature_obj.data.edit_bones:
                        edit_bone.parent = armature_obj.data.edit_bones[parent_name]
                    else:
                        log.write(f"  !! Parent bone '{parent_name}' for bone '{bone_name}' not found in armature. Skipping parenting.\n"); log.flush()
                        operator.report({'ERROR'}, f"Parent bone '{parent_name}' not found in armature.")
                        return False
                # else:            // Commented out because of weird C-level crashes
                #     bpy.ops.object.mode_set(mode='OBJECT')
                #     bpy.context.view_layer.update()
                #     bpy.ops.object.mode_set(mode='EDIT')

                # Set tail to first child's head, or offset from head if no child
                child_index = find_child(bone_name)
                if child_index is not None and child_index < len(model['bone_name']):
                    child_name = bone_namer[child_index]
                    if child_name in armature_obj.data.edit_bones:
                        edit_bone.tail = armature_obj.data.edit_bones[child_name].head
                else:
                    # No child found, set tail to offset from head
                    edit_bone.tail = edit_bone.head + Vector((0, 0, 0.1))

                # Blender silently deletes zero-length bones when leaving edit mode.
                # Guard against that by enforcing a minimum length.
                if (edit_bone.tail - edit_bone.head).length < 1e-5:
                    fallback_offset = Vector((0.0, 0.05, 0.0))
                    edit_bone.tail = edit_bone.head + fallback_offset
                    log.write(f"  Adjusted tail for '{bone_name}' to avoid zero-length (added {fallback_offset}).\n"); log.flush()

            log.write("Bone hierarchy and tails set.\n"); log.flush()

            # --- Finalize Armature and Switch to Object Mode ---
            log.write("Switching to OBJECT mode and updating depsgraph...\n"); log.flush()
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
                bpy.context.view_layer.update() # Force update after hierarchy changes
                log.write("...done.\n"); log.flush()
            except Exception:
                operator.report({'ERROR'}, f"Error while switching to OBJECT mode: {e}")
                return False

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
                        if n < len(bounding_info):
                            info_to_assign = bounding_info[n]
                            # log.write(f"  Processing pose bone {n} ('{pbone.name}'): Assigning {str(info_to_assign)}\n"); log.flush()
                            pbone["NeoX:BoundingInfo"] = info_to_assign
                        else:
                            log.write(f"  WARNING: Not enough bounding_info entries for bone {n}.\n"); log.flush()
                            break

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
            armature_obj['NeoX:LODTable'] = model['lod_data_table']
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
        print(f"Successfully imported model: {obj_name}")
        # return armature_obj
        return True
