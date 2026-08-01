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
        folder_box.prop(scene, "neox_remote_import_sockets", text="Import Sockets")
        if scene.neox_remote_import_sockets:
            folder_box.prop(scene, "neox_socket_filters_enabled", text="Socket Filters")
            if scene.neox_socket_filters_enabled:
                socket_filters = folder_box.box()
                socket_filters.label(text="Socket Filters")
                socket_filters.label(text="Bone Name")
                socket_filters.prop(scene, "neox_socket_filter_bone_names", text="")
                socket_filters.label(text="Socket Name")
                socket_filters.label(text="Match type:")
                socket_filters.prop(scene, "neox_socket_filter_socket_match_type", text="")
                socket_filters.prop(scene, "neox_socket_filter_socket_names", text="")
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
    folder_box.label(text="Import from...")
    folder_box.prop(scene, "neox_animation_import_source", text="")

    if scene.neox_animation_import_source == "remote":
        folder_box.label(text="Remote .cpdanimation path")
        folder_box.prop(scene, "neox_remote_animation_path", text="")
        op = folder_box.operator(
            "idvmi_neox.import_animation",
            text="Import Remote .cpdanimation",
            icon="IMPORT",
        )
        op.filepath = ""
        op.use_scene_selector = True
        op.import_source = "remote"
        return

    folder_box.label(text="NeoX Animation")
    op = folder_box.operator(
        "idvmi_neox.import_animation",
        text="Select .cpdanimation and Import",
        icon="IMPORT",
    )
    op.filepath = ""
    op.use_scene_selector = False
    op.import_source = "local"

def _draw_export_neox_animation(layout, scene, context, folder_box):
    folder_box.label(text="Export Mode")
    folder_box.prop(scene, "neox_animation_export_mode", text="")

    if scene.neox_animation_export_mode == "implement_existing_mod":
        folder_box.label(text="Mod Gim File")
        folder_box.prop(scene, "neox_animation_export_gim_path", text="")
        folder_box.label(text="Animation Name")
        folder_box.prop(scene, "neox_animation_export_animation_name", text="")
    else:
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
    op.export_mode = scene.neox_animation_export_mode
    op.gim_path = scene.neox_animation_export_gim_path
    op.animation_name = scene.neox_animation_export_animation_name
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


def _draw_custom_gim_controls(layout, scene):
    layout.label(text="Gim Location")
    layout.prop(scene, "custom_gim_location", text="")
    if scene.custom_gim_location == "remote":
        layout.label(text="Remote Gim Path")
        layout.prop(scene, "custom_gim_remote_path", text="")
    elif scene.custom_gim_location == "local":
        layout.label(text="Custom Gim File")
        layout.prop(scene, "gim_selector", text="")


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
        _draw_custom_gim_controls(rig_selector, scene)

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
            _draw_custom_gim_controls(rig_selector, scene)


    mod_name = layout.box()
    mod_name.label(text="Mod Name")
    mod_name.prop(context.scene, "neox_mod_name", text="")

    custom_skeleton_box = layout.box()
    custom_skeleton_box.prop(
        scene,
        "neox_mod_export_custom_skeleton",
        text="Export with Custom Skeleton",
    )

    layout.prop(scene, "neox_mod_export_create_mod_json", text="Create mod.json")
    layout.operator("idvmi_neox.neox_mod_exporter", icon="EXPORT")


def _draw_build_dual_form_skin(layout, scene, context, folder_box):
    folder_box.label(text="Main Model Gim File")
    folder_box.prop(scene, "neox_dual_form_main_gim", text="")
    folder_box.label(text="Dual Form Gim File")
    folder_box.prop(scene, "neox_dual_form_dual_gim", text="")

    trigger_box = layout.box()
    trigger_box.label(text="Dual Form Triggers")
    row = trigger_box.row(align=True)
    input_split = row.split(factor=0.84, align=True)
    input_split.prop(scene, "neox_dual_form_trigger_text", text="")
    button_split = input_split.split(factor=0.5, align=True)
    button_split.operator("idvmi_neox.dual_form_add_trigger", text="+")
    button_split.operator("idvmi_neox.dual_form_remove_trigger", text="-")
    trigger_box.template_list(
        "IDVMI_UL_Dual_Form_Triggers",
        "",
        scene,
        "neox_dual_form_triggers",
        scene,
        "neox_dual_form_trigger_index",
        rows=4,
    )
    trigger_box.label(text="Insert Animation Names with Regex")
    regex_row = trigger_box.row(align=True)
    regex_split = regex_row.split(factor=0.88, align=True)
    regex_split.prop(scene, "neox_dual_form_regex_text", text="")
    regex_split.operator("idvmi_neox.dual_form_add_regex_triggers", text="+")

    layout.operator("idvmi_neox.build_dual_form_skin", icon="MODIFIER")


def _draw_socket_operations(layout, scene, context, folder_box): # MODIFIER
    layout.label(text="Action")
    layout.prop(context.scene, "socket_action_selector", text="")

    folder_box = layout.box()
    action = context.scene.socket_action_selector

    if action == "copy_socket":
        folder_box.label(
            text=(
                "Have both models imported. Select source socket first, then "
                "select target armature. Proceed."
            )
        )
        folder_box.operator("idvmi_neox.copy_socket_visual", text="Copy Socket", icon="COPYDOWN")
    elif action == "create_socket":
        folder_box.label(text="Socket Location:")
        folder_box.prop(scene, "socket_create_location", text="")
        if scene.socket_create_location == "object_origin":
            folder_box.label(
                text=(
                    "Switch to Pose Mode. Select the source object from Scene Collection. "
                    "Select the binding bone from 3D Viewport (If you don't select any bone, "
                    "it will bind to armature). Confirm."
                )
            )
        else:
            folder_box.label(
                text=(
                    "Switch to Pose Mode. Select the binding bone. Set the cursor binding "
                    "position. Confirm."
                )
            )
        folder_box.operator("idvmi_neox.create_socket", text="Confirm", icon="EMPTY_AXIS")
    elif action == "delete_socket":
        folder_box.label(text="Select the socket. Confirm.")
        folder_box.operator("idvmi_neox.delete_socket", text="Delete Socket", icon="TRASH")

# Dispatch map to replace if/elif chain
neox_dispatch = {    
    'OPT_Import_Neox_Mesh': _draw_import_neox_mesh,    
    'OPT_Import_Neox_Animation': _draw_import_neox_animation,
    'OPT_Export_Neox_Animation': _draw_export_neox_animation,
    'OPT_NeoX_Mod_Exporter': _draw_export_neox_mod,
    'OPT_Build_Dual_Form_Skin': _draw_build_dual_form_skin,
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
