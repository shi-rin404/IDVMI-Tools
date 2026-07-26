import bpy
from .addon.register import register_props
from .addon.unregister import unregister_props
from .addon.classes import classes
from .neox_tools.import_ops import menu_func_import

bl_info = {
    "name": "Identity V Model Importer Tools",
    "author": "Cookie",
    "version": (8, 0, 1),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Tool Tab",
    "description": "NeoX Mesh Importer/Exporter, 3DMigoto Mod Exporter",
    "category": "Object"
}

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    register_props()
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)

def unregister():
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    except ValueError:
        pass

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    unregister_props()
