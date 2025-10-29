import bpy

class IDVMI_OT_Export_Neox_Mod(bpy.types.Operator):
    bl_idname = "idvmi_neox.socket_ops"
    bl_label = "Bind to Socket"

    def execute(self, context):