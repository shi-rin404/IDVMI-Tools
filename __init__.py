import bpy
from .addon import ui

bl_info = {
    "name": "Identity V Model Importer Tools",
    "author": "Cookie",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Tool Tab",
    "description": "NeoX Mesh Importer, 3DMigoto Exporter",
    "category": "Object"
}

def register():
    for cls in ui.classes:
        bpy.utils.register_class(cls)
        ui.register_props()

def unregister():
    for cls in reversed(ui.classes):
        bpy.utils.unregister_class(cls)
    
    ui.unregister_props()