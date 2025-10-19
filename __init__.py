import bpy
from .addon.register import register_props
from .addon.unregister import unregister_props
from .addon.classes import classes

bl_info = {
    "name": "Identity V Model Importer Tools",
    "author": "Cookie",
    "version": (2, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Tool Tab",
    "description": "NeoX Mesh Importer/Exporter, 3DMigoto Mod Exporter",
    "category": "Object"
}

def register():
    for cls in classes:
        bpy.utils.register_class(cls)    
    register_props()

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)    
    unregister_props()