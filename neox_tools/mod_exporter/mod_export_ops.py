import bpy, os
from ..export_ops import get_armature, export_neox_mesh, parse_blender_meshes
from .texture_handler import texture_handler
from .rig_handler import rig_handler
from .gim_handler import gim_handler
from .mod_json_maker import mod_json_maker

class IDVMI_OT_Export_Neox_Mod(bpy.types.Operator):
    bl_idname = "idvmi_neox.neox_mod_exporter"
    bl_label = "Export NeoX Mod"

    def execute(self, context):
        root = os.path.dirname(__file__)
        log_file = os.path.join(root, "export_per_material_log.txt")
        with open(log_file, "w") as log:
            log.write("--- New export session started ---\n"); log.flush()
        
        with open(log_file, "a") as log:
            export_path = bpy.path.abspath(context.scene.neox_export_selector)

            if export_path.endswith("\\res\\mod") or export_path.endswith("/res/mod"):
                export_path = os.path.join(export_path, context.scene.neox_mod_name)
            
            os.makedirs(export_path, exist_ok=True)

            arm_obj = get_armature(context, self)

            # Export Mesh
            flip_uv_y = context.scene.flip_uv_y        

            mesh_data = parse_blender_meshes(arm_obj, flip_uv_y, self, log)

            if not mesh_data:
                return {'CANCELLED'}

            if not export_neox_mesh(
                bpy.path.abspath(os.path.join(export_path, "main.mesh")),
                mesh_data,
                arm_obj,
                self,
                log
            ):
                return {'CANCELLED'}

            # Export Textures
            if not texture_handler(export_path, context, self):
                return {'CANCELLED'}

            gim_path = gim_handler(
                export_path,
                rig_handler(export_path, context),
                arm_obj
            )

            # Create mod.json
            mod_json_maker(
                gim_path,
                context
            )

            self.report({'INFO'}, f"Export OK → {export_path}")
            return {'FINISHED'}
