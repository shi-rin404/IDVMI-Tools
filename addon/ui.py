import bpy
from ..addon import func_map

class IDVMI_PT_tools(bpy.types.Panel):
    bl_label = "IDVMI Tools"
    bl_idname = "idvmi_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IDVMI Tools"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Action Selection
        layout.prop(scene, "action_selector")
        
        action = scene.action_selector
        func = func_map.dispatch.get(action)
        if func is not None:
            folder_selectors = layout.box() if scene.action_selector not in func_map.no_folder_box else None
            func(layout, scene, context, folder_selectors)