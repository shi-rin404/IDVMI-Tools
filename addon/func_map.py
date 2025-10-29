import bpy

def _draw_extract_frame(layout, scene, context, folder_box):
    folder_box.label(text="Frame Dump Folder")
    folder_box.prop(scene, "frame_dump_selector", text="")
    layout.operator("idvmi_migoto.extract_frame_dump", icon="FILE_REFRESH")

def _draw_set_textures(layout, scene, context, folder_box):
    folder_box.label(text="Frame Dump Folder")
    folder_box.prop(scene, "frame_dump_selector", text="")
    layout.operator("idvmi_migoto.set_textures", icon="FILE_REFRESH")


def _draw_export_mod(layout, scene, context, folder_box):
    folder_box.label(text="Frame Dump Folder")
    folder_box.prop(scene, "frame_dump_selector", text="")
    folder_box.label(text="Export Folder")
    folder_box.prop(scene, "export_selector", text="")

    slot_selectors = layout.box()
    slot_selectors.prop(scene, "metal_slot_selector")
    slot_selectors.prop(scene, "normal_slot_selector")

    # Options
    layout.prop(context.scene, "clean_ini", text="Clean INI")
    if context.scene.clean_ini:
        box_clean_ini = layout.box()
        box_clean_ini.label(text="Namespace Name")
        box_clean_ini.prop(context.scene, "namespace_textbox", text="")
    layout.prop(context.scene, "custom_metal", text="Custom Metal-Map")
    if context.scene.custom_metal:
        box_custom_metal = layout.box()
        box_custom_metal.label(text="Custom Metal-Map Path")
        box_custom_metal.prop(scene, "metal_selector", text="")
    layout.prop(context.scene, "custom_normal", text="Custom Normal-Map")
    if context.scene.custom_normal:
        box_custom_normal = layout.box()
        box_custom_normal.label(text="Custom Normal-Map Path")
        box_custom_normal.prop(scene, "normal_selector", text="")
    layout.operator("idvmi_migoto.export_mod_migoto", icon="EXPORT")


def _draw_import_neox_mesh(layout, scene, context, folder_box):
    folder_box.label(text="NeoX Mesh")
    folder_box.prop(scene, "neox_mesh_selector", text="")
    layout.operator("idvmi_neox.neox_importer", icon="IMPORT")

def _draw_export_neox_mesh(layout, scene, context, folder_box):
    layout.prop(context.scene, "flip_uv_y", text="Flip UV (Y axis)")
    layout.operator("idvmi_neox.neox_exporter", icon="EXPORT")

def _draw_export_neox_mod(layout, scene, context, folder_box):
    folder_box.label(text="Export Folder")
    folder_box.prop(scene, "export_selector", text="")

    mesh_options = layout.box()
    mesh_options.prop(context.scene, "flip_uv_y", text="Flip UV (Y axis)")

    mod_options = layout.box()
    mod_options.prop(scene, "neox_rig_selector")

    # Rig
    if context.scene.neox_rig_selector == 'custom':
        # Hunter Rig
        rig_selector = layout.box()
        rig_selector.label(text="Reference Gim File")
        rig_selector.prop(scene, "gim_selector", text="")    

        rig_selector.label(text="Skeleton Path")
        rig_selector.prop(context.scene, "skeleton_path", text="")

        rig_selector.label(text="AnimConfig Location")
        rig_selector.prop(context.scene, "animconfig_location", text="")

        
        if context.scene.animconfig_location == "remote":
            rig_selector.label(text="AnimConfig Path")
            rig_selector.prop(context.scene, "animconfig_path", text="")
        elif context.scene.animconfig_location == "local":              
            rig_selector.label(text="AnimConfig File")
            rig_selector.prop(context.scene, "animconfig_selector", text="")
    else:
        mod_options.prop(scene, "custom_gim_bool", text="Custom Gim")

        if scene.custom_gim_bool:
            rig_selector = layout.box()
            rig_selector.label(text="Reference Gim File")
            rig_selector.prop(scene, "gim_selector", text="") 


    mod_name = layout.box()
    mod_name.label(text="Mod Name")
    mod_name.prop(context.scene, "neox_mod_name", text="")

    layout.operator("idvmi_neox.neox_mod_exporter", icon="EXPORT")

def _draw_socket_operations(layout, scene, context, folder_box): # MODIFIER
    folder_box.label(text="Gim File")
    folder_box.prop(scene, "socket_default_gim_selector", text="")
    folder_box.label(text="Socket Name")
    folder_box.prop(scene, "default_socket_name", text="")
    folder_box.label(text="Gim Path")
    folder_box.prop(scene, "gim_path", text="")
    folder_box.operator("idvmi_neox.socket_ops", icon="MODIFIER")

# Dispatch map to replace if/elif chain
neox_dispatch = {    
    'OPT_Import_Neox_Mesh': _draw_import_neox_mesh,    
    'OPT_NeoX_Mod_Exporter': _draw_export_neox_mod,
    'OPT_Export_Neox_Mesh': _draw_export_neox_mesh,
    'OPT_Socket_Operations': _draw_socket_operations,
}

_3dm_dispatch = {
    'OPT_Extract_Frame': _draw_extract_frame,
    'OPT_Set_Textures': _draw_set_textures,
    'OPT_Export_Mod': _draw_export_mod,
}

no_folder_box = [
    'OPT_Export_Neox_Mesh',
]