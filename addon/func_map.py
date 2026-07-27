import bpy

def _draw_import_3dm(layout, scene, context, folder_box):
    layout.prop(scene, "migoto_mesh_import_mode")
    layout.prop(scene, "flip_uv_y", text="Mirror UV Y")
    if scene.migoto_mesh_import_mode == 'BUF':
        layout.prop(scene, "migoto_use_standard_model_format", text="Use standard model format")
        layout.prop(scene, "migoto_import_all_related_buf", text="Import All Relative Files")
    else:
        layout.prop(scene, "migoto_import_all_related_txt", text="Import All Relative Files")
    layout.operator("idvmi_migoto.import_3dm", icon="IMPORT")


def _draw_import_cb_pose(layout, scene, context, folder_box):
    row = layout.row(align=True)
    row.prop(scene, "migoto_cb_pose_start_index")
    row.prop(scene, "migoto_cb_pose_end_index")
    layout.prop(scene, "migoto_cb_pose_batch_import_related_meshes", text="Batch Import Relates Meshes")
    layout.operator("idvmi_migoto.import_cb_pose_armature", icon="ARMATURE_DATA")


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
    folder_box.label(text="3DM Export Folder")
    folder_box.prop(scene, "migoto_export_selector", text="")

    slot_selectors = layout.box()
    slot_selectors.prop(scene, "flip_uv_y", text="Mirror UV Y")
    metal_row = slot_selectors.row(align=True)
    metal_row.label(text="Metal: t")
    metal_row.prop(scene, "metal_slot_selector", text="")
    normal_row = slot_selectors.row(align=True)
    normal_row.label(text="Normal: t")
    normal_row.prop(scene, "normal_slot_selector", text="")

    # Options
    layout.prop(context.scene, "export_all_relative_meshes", text="Export All Relative Meshes")
    layout.prop(context.scene, "clear_unused_materials", text="Clear Unused Materials")
    layout.prop(context.scene, "migoto_save_fmt_file", text="Save *.fmt file")
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
    folder_box.label(text="Import from...")
    folder_box.prop(scene, "neox_mesh_import_source", text="")

    if scene.neox_mesh_import_source == "remote":
        folder_box.label(text="Remote .gim path")
        folder_box.prop(scene, "neox_remote_gim_path", text="")
        op = folder_box.operator(
            "idvmi_neox.neox_importer",
            text="Import Remote .gim",
            icon="IMPORT",
        )
        op.filepath = ""
        op.use_scene_selector = True
        op.import_source = "remote"
        return

    folder_box.label(text="NeoX Mesh")
    op = folder_box.operator(
        "idvmi_neox.neox_importer",
        text="Select .mesh and Import",
        icon="IMPORT",
    )
    op.filepath = ""
    op.use_scene_selector = False
    op.import_source = "local"

def _draw_import_neox_animation(layout, scene, context, folder_box):
    folder_box.label(text="NeoX Animation")
    op = folder_box.operator(
        "idvmi_neox.import_animation",
        text="Select .cpdanimation and Import",
        icon="IMPORT",
    )
    op.filepath = ""
    op.use_scene_selector = False

def _draw_export_neox_animation(layout, scene, context, folder_box):
    folder_box.label(text="NeoX Animation")
    folder_box.prop(scene, "neox_animation_export_selector", text="")
    folder_box.prop(scene, "neox_animation_skeleton_preset")
    if scene.neox_animation_skeleton_preset == 'custom':
        folder_box.label(text="Custom Skeleton Path")
        folder_box.prop(scene, "neox_animation_custom_skeleton_path", text="")

    options = layout.box()
    options.prop(scene, "neox_animation_export_loop")
    options.prop(scene, "neox_animation_export_fps")
    options.prop(scene, "neox_animation_export_reduce_keys")

    if scene.neox_animation_export_reduce_keys:
        tolerances = layout.box()
        tolerances.prop(scene, "neox_animation_position_tolerance")
        tolerances.prop(scene, "neox_animation_scale_tolerance")
        tolerances.prop(scene, "neox_animation_rotation_tolerance_degrees")

    op = layout.operator("idvmi_neox.export_animation", icon="EXPORT")
    op.filepath = scene.neox_animation_export_selector
    op.skeleton_preset = scene.neox_animation_skeleton_preset
    op.custom_skeleton_path = scene.neox_animation_custom_skeleton_path
    op.loop = scene.neox_animation_export_loop
    op.fps = scene.neox_animation_export_fps
    op.reduce_keys = scene.neox_animation_export_reduce_keys
    op.position_tolerance = scene.neox_animation_position_tolerance
    op.scale_tolerance = scene.neox_animation_scale_tolerance
    op.rotation_tolerance_degrees = scene.neox_animation_rotation_tolerance_degrees

def _draw_export_neox_mesh(layout, scene, context, folder_box):
    layout.prop(context.scene, "flip_uv_y", text="Flip UV (Y axis)")
    layout.operator("idvmi_neox.neox_exporter", icon="EXPORT")

def _draw_export_neox_mod(layout, scene, context, folder_box):
    folder_box.label(text="NeoX Export Folder")
    folder_box.prop(scene, "neox_export_selector", text="")

    mesh_options = layout.box()
    mesh_options.prop(context.scene, "flip_uv_y", text="Flip UV (Y axis)")

    mod_options = layout.box()
    mod_options.prop(scene, "neox_rig_selector")
    use_custom_skeleton = scene.neox_mod_export_custom_skeleton

    # Rig
    if context.scene.neox_rig_selector == 'custom':
        # Hunter Rig
        rig_selector = layout.box()
        rig_selector.label(text="Reference Gim File")
        rig_selector.prop(scene, "gim_selector", text="")    

        if not use_custom_skeleton:
            rig_selector.label(text="Skeleton Path")
            rig_selector.prop(context.scene, "skeleton_path", text="")

        rig_selector.label(text="AnimConfig Location")
        rig_selector.prop(context.scene, "animconfig_location", text="")

        
        if context.scene.animconfig_location == "remote":
            rig_selector.label(text="AnimConfig Path")
            rig_selector.prop(context.scene, "animconfig_path", text="")
        elif context.scene.animconfig_location == "customize_remote":
            rig_selector.label(text="Remote AnimConfig Path")
            rig_selector.prop(context.scene, "animconfig_path", text="")
            rig_selector.prop(context.scene, "skip_unnecessary_animconfig_files")
        elif context.scene.animconfig_location == "local":              
            rig_selector.label(text="AnimConfig File")
            rig_selector.prop(context.scene, "animconfig_selector", text="")
    else:
        mod_options.prop(scene, "custom_gim_bool", text="Custom Gim")

        if scene.custom_gim_bool:
            rig_selector = layout.box()
            rig_selector.label(text="Gim Location")
            rig_selector.prop(scene, "custom_gim_location", text="")
            if scene.custom_gim_location == "remote":
                rig_selector.label(text="Remote Gim Path")
                rig_selector.prop(scene, "custom_gim_remote_path", text="")
            elif scene.custom_gim_location == "local":
                rig_selector.label(text="Reference Gim File")
                rig_selector.prop(scene, "gim_selector", text="")


    mod_name = layout.box()
    mod_name.label(text="Mod Name")
    mod_name.prop(context.scene, "neox_mod_name", text="")

    custom_skeleton_box = layout.box()
    custom_skeleton_box.prop(
        scene,
        "neox_mod_export_custom_skeleton",
        text="Export with Custom Skeleton",
    )

    layout.operator("idvmi_neox.neox_mod_exporter", icon="EXPORT")

def _draw_socket_operations(layout, scene, context, folder_box): # MODIFIER
    layout.prop(context.scene, "socket_action_selector", text="Socket Action")        

    folder_box = layout.box()
    def bind_gim_to_socket(_folder_box_):
        _folder_box_.label(text="Gim File")
        _folder_box_.prop(scene, "socket_default_gim_selector", text="")
        _folder_box_.label(text="Socket Name")
        _folder_box_.prop(scene, "default_socket_name", text="")
        _folder_box_.label(text="Gim Path")
        _folder_box_.prop(scene, "gim_path", text="")
        
        layout.operator("idvmi_neox.bind_gim", icon="MODIFIER")

    def copy_socket(_folder_box_):
        _folder_box_.label(text="From (Gim File)")
        _folder_box_.prop(scene, "socket_source_gim_selector", text="")
        _folder_box_.label(text="To (Gim File)")
        _folder_box_.prop(scene, "socket_default_gim_selector", text="")
        _folder_box_.label(text="Socket Name")
        _folder_box_.prop(scene, "default_socket_name", text="")

        layout.operator("idvmi_neox.copy_socket", icon="MODIFIER")

    socket_dispatch = {
        'bind_gim': bind_gim_to_socket,
        'copy_socket': copy_socket
    }

    action = context.scene.socket_action_selector
    func = socket_dispatch.get(action)
    if func is not None:
        func(folder_box)

# Dispatch map to replace if/elif chain
neox_dispatch = {    
    'OPT_Import_Neox_Mesh': _draw_import_neox_mesh,    
    'OPT_Import_Neox_Animation': _draw_import_neox_animation,
    'OPT_Export_Neox_Animation': _draw_export_neox_animation,
    'OPT_NeoX_Mod_Exporter': _draw_export_neox_mod,
    'OPT_Export_Neox_Mesh': _draw_export_neox_mesh,
    'OPT_Socket_Operations': _draw_socket_operations,
}

_3dm_dispatch = {
    'OPT_Import_3DM': _draw_import_3dm,
    'OPT_Import_CB_Pose': _draw_import_cb_pose,
    'OPT_Extract_Frame': _draw_extract_frame,
    'OPT_Set_Textures': _draw_set_textures,
    'OPT_Export_Mod': _draw_export_mod,
}

no_folder_box = [
    'OPT_Import_3DM',
    'OPT_Import_CB_Pose',
    'OPT_Export_Neox_Mesh',
    'OPT_Socket_Operations'
]
