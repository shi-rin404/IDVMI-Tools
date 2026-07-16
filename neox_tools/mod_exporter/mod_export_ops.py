import importlib
import importlib.util
from pathlib import Path

import bpy, os
from ..export_ops import get_armature, export_neox_mesh, parse_blender_meshes
from .texture_handler import texture_handler
from .rig_handler import rig_handler
from .gim_handler import gim_handler
from .mod_json_maker import mod_json_maker


def _is_documents_res_mod_path(path):
    parts = [
        part.lower()
        for part in os.path.normpath(path).replace("\\", "/").rstrip("/").split("/")
        if part
    ]
    return parts[-3:] == ["documents", "res", "mod"]


def _load_skeleton_exporter_module():
    try:
        return importlib.import_module("neox_skeleton_exporter")
    except ModuleNotFoundError:
        addons_dir = Path(__file__).resolve().parents[3]
        module_path = addons_dir / "neox_skeleton_exporter" / "__init__.py"
        if not module_path.is_file():
            raise FileNotFoundError(
                f"neox_skeleton_exporter was not found at {module_path}"
            )

        spec = importlib.util.spec_from_file_location(
            "neox_skeleton_exporter",
            module_path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def _validate_armature_matches_imported_bone_order(armature):
    if "NeoX:BoneOrder" not in armature:
        raise ValueError(
            "Armature does not contain NeoX:BoneOrder. Disable custom-skeleton "
            "export only for armatures imported from a compatible NeoX mesh."
        )

    current_names = {bone.name for bone in armature.data.bones}
    imported_names = {str(name) for name in armature["NeoX:BoneOrder"]}

    if len(current_names) != len(armature.data.bones):
        raise ValueError("Armature contains duplicate bone names.")

    if len(imported_names) != len(armature["NeoX:BoneOrder"]):
        raise ValueError("NeoX:BoneOrder contains duplicate bone names.")

    if len(current_names) != len(imported_names) or current_names != imported_names:
        missing_from_armature = sorted(imported_names - current_names)
        extra_in_armature = sorted(current_names - imported_names)
        raise ValueError(
            "Armature bones are incompatible with NeoX:BoneOrder. "
            f"missing={missing_from_armature}, extra={extra_in_armature}"
        )


def _validate_vertex_groups_match_armature(armature):
    bone_names = {bone.name for bone in armature.data.bones}
    mismatches = []

    for child in armature.children_recursive:
        if child.type != "MESH":
            continue

        for vertex_group in child.vertex_groups:
            if vertex_group.name not in bone_names:
                mismatches.append(f"{child.name}:{vertex_group.name}")

    if mismatches:
        raise ValueError(
            "Mesh vertex groups not found in armature bones: "
            + ", ".join(sorted(mismatches))
        )


def _export_custom_skeleton(export_path, armature):
    skeleton_path = os.path.join(export_path, "main.skeleton")
    skeleton_module = _load_skeleton_exporter_module()
    skeleton_module.export_neox_skeleton(
        armature=armature,
        filepath=skeleton_path,
        skeleton_name="main",
    )
    return skeleton_path


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

            if _is_documents_res_mod_path(export_path):
                export_path = os.path.join(export_path, context.scene.neox_mod_name)
            
            os.makedirs(export_path, exist_ok=True)

            arm_obj = get_armature(context, self)
            if not arm_obj:
                return {'CANCELLED'}

            custom_skeleton = context.scene.neox_mod_export_custom_skeleton

            try:
                if custom_skeleton:
                    _validate_vertex_groups_match_armature(arm_obj)
                else:
                    _validate_armature_matches_imported_bone_order(arm_obj)
            except Exception as exc:
                log.write(f"ERROR: {exc}\n"); log.flush()
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}

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

            if custom_skeleton:
                try:
                    _export_custom_skeleton(export_path, arm_obj)
                except Exception as exc:
                    log.write(f"ERROR: Custom skeleton export failed: {exc}\n"); log.flush()
                    self.report({'ERROR'}, f"Custom skeleton export failed: {exc}")
                    return {'CANCELLED'}

            gim_path = gim_handler(
                export_path,
                rig_handler(export_path, context, custom_skeleton=custom_skeleton),
                arm_obj
            )

            # Create mod.json
            mod_json_maker(
                gim_path,
                context
            )

            self.report({'INFO'}, f"Export OK → {export_path}")
            return {'FINISHED'}
