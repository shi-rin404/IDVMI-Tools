import bpy

def unregister_props():
    unregister = [
        lambda: delattr(bpy.types.Scene, "neox_mod_name"),
        lambda: delattr(bpy.types.Scene, "skeleton_path"),  
        lambda: delattr(bpy.types.Scene, "animconfig_location"),  
        lambda: delattr(bpy.types.Scene, "animconfig_path"),  
        lambda: delattr(bpy.types.Scene, "animconfig_selector"),
        lambda: delattr(bpy.types.Scene, "gim_selector"),          
        lambda: delattr(bpy.types.Scene, "neox_rig_selector"),
        lambda: delattr(bpy.types.Scene, "flip_uv_y"),
        lambda: delattr(bpy.types.Scene, "neox_mesh_selector"),
        lambda: delattr(bpy.types.Scene, "frame_dump_selector"),
        lambda: delattr(bpy.types.Scene, "export_selector"),
        lambda: delattr(bpy.types.Scene, "clean_ini"),
        lambda: delattr(bpy.types.Scene, "custom_metal"),
        lambda: delattr(bpy.types.Scene, "custom_normal"),
        lambda: delattr(bpy.types.Scene, "metal_selector"),
        lambda: delattr(bpy.types.Scene, "normal_selector"),
        lambda: delattr(bpy.types.Scene, "metal_slot_selector"),
        lambda: delattr(bpy.types.Scene, "normal_slot_selector"),
        lambda: delattr(bpy.types.Scene, "namespace_textbox"),
        lambda: delattr(bpy.types.Scene, "neox_action_selector"),        
        lambda: delattr(bpy.types.Scene, "migoto_action_selector"),        
    ]

    for function in unregister:
        try:
            function()
        except AttributeError:
            pass