import bpy
from .addon import version_hotfixes
from .addon.register import register_props
from .addon.unregister import unregister_props
from .addon.classes import classes
from .neox_tools.import_ops import menu_func_import

bl_info = {
    "name": "Identity V Model Importer Tools",
    "author": "Cookie",
    "version": (9, 1, 8),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Tool Tab",
    "description": "NeoX Mesh Importer/Exporter, 3DMigoto Mod Exporter",
    "category": "Object"
}

def register():
    try:
        version_hotfixes.run_local_version_hotfixes()
    except (OSError, ValueError) as exc:
        print(f"IDVMI version hotfix failed: {exc}")

    for cls in classes:
        bpy.utils.register_class(cls)
    register_props()
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)

def unregister():
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    except ValueError:
        pass

    unregister_props()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
