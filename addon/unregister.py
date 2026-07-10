import bpy

def unregister_props():
    unregister = [
        lambda: delattr(bpy.types.Scene, "socket_source_gim_selector"),
        lambda: delattr(bpy.types.Scene, "socket_action_selector"),
        lambda: delattr(bpy.types.Scene, "custom_gim_bool"),
        lambda: delattr(bpy.types.Scene, "gim_path"),
        lambda: delattr(bpy.types.Scene, "default_socket_name"),         
        lambda: delattr(bpy.types.Scene, "socket_default_gim_selector"),
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
        lambda: delattr(bpy.types.Scene, "migoto_cb_pose_start_index"),
        lambda: delattr(bpy.types.Scene, "migoto_cb_pose_end_index"),
        lambda: delattr(bpy.types.Scene, "migoto_cb_pose_batch_import_related_meshes"),
        lambda: delattr(bpy.types.Scene, "migoto_mesh_import_mode"),
        lambda: delattr(bpy.types.Scene, "migoto_use_standard_model_format"),
        lambda: delattr(bpy.types.Scene, "migoto_import_all_related_txt"),
        lambda: delattr(bpy.types.Scene, "migoto_import_all_related_buf"),
        lambda: delattr(bpy.types.Scene, "migoto_import_all_related"),
    ]

    for function in unregister:
        try:
            function()
        except AttributeError:
            pass
