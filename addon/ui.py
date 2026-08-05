import bpy
from ..addon import func_map

class IDVMI_Neox_tools(bpy.types.Panel):
    bl_label = "IDVMI Neox"
    bl_idname = "idvmi_neox"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IDVMI Neox"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Action Selection
        layout.prop(scene, "neox_action_selector")
        if scene.neox_action_selector in {"OPT_NeoX_Mesh", "OPT_NeoX_Animation"}:
            return
        
        try:
            action = scene.neox_action_selector
            func = func_map.neox_dispatch.get(action)
            if func is not None:
                folder_selectors = layout.box() if scene.neox_action_selector not in func_map.no_folder_box else None
                func(layout, scene, context, folder_selectors)
        except Exception as e:
            layout.label(text=str(e))


class IDVMI_Neox_Mesh_Import(bpy.types.Panel):
    bl_label = "Import"
    bl_idname = "idvmi_neox_mesh_import"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IDVMI Neox"
    bl_parent_id = "idvmi_neox"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "neox_action_selector", "") == "OPT_NeoX_Mesh"

    def draw(self, context):
        func_map._draw_import_neox_mesh(self.layout, context.scene, context, self.layout)


class IDVMI_Neox_Mesh_Export(bpy.types.Panel):
    bl_label = "Export"
    bl_idname = "idvmi_neox_mesh_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IDVMI Neox"
    bl_parent_id = "idvmi_neox"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "neox_action_selector", "") == "OPT_NeoX_Mesh"

    def draw(self, context):
        func_map._draw_export_neox_mesh(self.layout, context.scene, context, self.layout)


class IDVMI_Neox_Animation_Import(bpy.types.Panel):
    bl_label = "Import"
    bl_idname = "idvmi_neox_animation_import"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IDVMI Neox"
    bl_parent_id = "idvmi_neox"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "neox_action_selector", "") == "OPT_NeoX_Animation"

    def draw(self, context):
        func_map._draw_import_neox_animation(self.layout, context.scene, context, self.layout)


class IDVMI_Neox_Animation_Export(bpy.types.Panel):
    bl_label = "Export"
    bl_idname = "idvmi_neox_animation_export"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IDVMI Neox"
    bl_parent_id = "idvmi_neox"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return getattr(context.scene, "neox_action_selector", "") == "OPT_NeoX_Animation"

    def draw(self, context):
        func_map._draw_export_neox_animation(self.layout, context.scene, context, self.layout)


class IDVMI_3DM_tools(bpy.types.Panel):
    bl_label = "IDVMI Migoto"
    bl_idname = "idvmi_migoto"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IDVMI Migoto"

    def draw(self, context):
        layout = self.layout
        scene = context.scene


        # Action Selection
        layout.prop(scene, "migoto_action_selector")
        
        action = scene.migoto_action_selector
        func = func_map._3dm_dispatch.get(action)
        if func is not None:
            folder_selectors = layout.box() if scene.migoto_action_selector not in func_map.no_folder_box else None
            func(layout, scene, context, folder_selectors)
