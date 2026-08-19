import bpy
from ..neox_tools.utils.game_root import get_documents_mod_directory
from ..neox_tools import dual_form_ops

CPD_ANIMATION_LOOP_PROPERTY = "NeoX:CPDAnimation:loop"
LOOP_VALUE_KEY = "_idvmi_neox_animation_export_loop_value"
LOOP_SOURCE_KEY = "_idvmi_neox_animation_export_loop_source"


def _active_armature_loop_source():
    obj = bpy.context.object
    if obj is not None and obj.type == "ARMATURE":
        return obj
    return None


def get_neox_animation_export_loop(scene):
    obj = _active_armature_loop_source()
    source_name = obj.name if obj is not None else ""

    if scene.get(LOOP_SOURCE_KEY, None) == source_name and LOOP_VALUE_KEY in scene:
        return bool(scene[LOOP_VALUE_KEY])

    if obj is not None:
        return bool(obj.get(CPD_ANIMATION_LOOP_PROPERTY, False))

    return False


def set_neox_animation_export_loop(scene, value):
    obj = _active_armature_loop_source()
    scene[LOOP_SOURCE_KEY] = obj.name if obj is not None else ""
    scene[LOOP_VALUE_KEY] = bool(value)


def register_props():
    bpy.types.Scene.socket_action_selector = bpy.props.EnumProperty(
        name="Action",
        description="Select your socket action",
        items=[
            ('copy_socket', "Copy Socket", "Copy a visual socket to the target armature"),
            ('create_socket', "Create Socket", "Create a socket from an object origin or cursor"),
            ('delete_socket', "Delete Socket", "Delete the selected visual socket"),
        ],
        default='copy_socket'
    )

    bpy.types.Scene.socket_create_location = bpy.props.EnumProperty(
        name="Socket Location",
        description="Select where the new socket should be created from",
        items=[
            ('object_origin', "Object Origin", "Use the selected object's world transform"),
            ('cursor', "Cursor", "Use the 3D cursor location"),
        ],
        default='object_origin'
    )

    bpy.types.Scene.custom_gim_bool = bpy.props.BoolProperty(
        name="Custom Gim",
        description="You can choose custom gim to export your mod",
        default=False
    )

    bpy.types.Scene.custom_gim_location = bpy.props.EnumProperty(
        name="Gim Location",
        description="Remote = game asset path | Local = local .gim file",
        items=[
            ('remote', "Remote File", "Official game .gim asset"),
            ('local', "Local File", "Local .gim file"),
        ],
        default='local'
    )

    bpy.types.Scene.custom_gim_remote_path = bpy.props.StringProperty(
        name="Remote Gim Path",
        description="Game asset path to a .gim prefab file",
        default=""
    )

    bpy.types.Scene.neox_mod_export_custom_skeleton = bpy.props.BoolProperty(
        name="Export with Custom Skeleton",
        description="Generate and bind a custom .skeleton during NeoX mod export",
        default=False
    )

    bpy.types.Scene.neox_mod_export_create_mod_json = bpy.props.BoolProperty(
        name="Create mod.json",
        description="Create mod.json next to the exported NeoX gim",
        default=True
    )

    bpy.types.Scene.neox_mod_export_grab_original_materials = bpy.props.BoolProperty(
        name="Grab Original Materials",
        description="Use original NeoX material files and copy their main textures when possible",
        default=True
    )

    bpy.types.Scene.neox_dual_form_main_gim = bpy.props.StringProperty(
        name="Main Gim File",
        description="Main form .gim file exported by Export NeoX Mod",
        subtype='FILE_PATH',
        default=""
    )

    bpy.types.Scene.neox_dual_form_dual_gim = bpy.props.StringProperty(
        name="Dual Form Gim File",
        description="Dual form .gim file exported by Export NeoX Mod",
        subtype='FILE_PATH',
        default=""
    )

    bpy.types.Scene.neox_dual_form_trigger_text = bpy.props.StringProperty(
        name="Dual Form Trigger",
        description="Animation name that should enable the dual form",
        default=""
    )

    bpy.types.Scene.neox_dual_form_triggers = bpy.props.CollectionProperty(
        type=dual_form_ops.IDVMI_PG_Dual_Form_Trigger
    )

    bpy.types.Scene.neox_dual_form_trigger_index = bpy.props.IntProperty(
        name="Dual Form Trigger Index",
        default=-1
    )

    bpy.types.Scene.neox_dual_form_regex_text = bpy.props.StringProperty(
        name="Regex",
        description="One regex per line. Matching animation names from the selected main gim animconfig will be added.",
        default=""
    )

    bpy.types.Scene.neox_dual_form_animation_name_cache = bpy.props.CollectionProperty(
        type=dual_form_ops.IDVMI_PG_Dual_Form_Trigger
    )

    bpy.types.Scene.neox_dual_form_animation_name_cache_source = bpy.props.StringProperty(
        name="Animation Name Cache Source",
        default=""
    )

    bpy.types.Scene.neox_dual_form_preset_source_filter = bpy.props.EnumProperty(
        name="Preset Source",
        description="Filter dual form trigger presets by source folder",
        items=[
            ("all", "All", "Show default and user presets"),
            ("defaults", "Defaults", "Show presets from defaults/presets/dual_form"),
            ("user", "User", "Show presets from user/presets/dual_form"),
        ],
        default="all",
    )

    bpy.types.Scene.neox_dual_form_preset_selector = bpy.props.EnumProperty(
        name="Preset",
        description="Dual form trigger preset JSON file",
        items=dual_form_ops.dual_form_preset_items,
    )

    bpy.types.Scene.neox_dual_form_preset_export_type = bpy.props.EnumProperty(
        name="Preset Type",
        description="Select which dual form trigger preset type to export",
        items=[
            ("trigger_list", "Trigger List", "Export the current trigger list"),
            ("regex", "Regex", "Export the current regex textbox"),
        ],
        default="trigger_list",
    )

    bpy.types.Scene.neox_dual_form_create_unexisting_animations = bpy.props.BoolProperty(
        name="Create Unexisting Animations",
        description="Don't skip if a trigger doesn't find in animconfig. Patch it to work properly. Recommended for non-humanoid models and models using real time physics.",
        default=False,
    )

    bpy.types.Scene.neox_rig_selector = bpy.props.EnumProperty(
        name="Rig Selector",
        description="Select your character rig",
        items=[            
            ('woman', "Woman", "Woman rig"),
            ('male', "Male", "Male rig"),
            ('little_girl', "Little Girl", "Little Girl rig"),
            ('custom', "Custom", "Custom rig"),
        ],
        default='woman'
    )  

    bpy.types.Scene.neox_mod_name = bpy.props.StringProperty(
        name="Mod Name",
        description="Name of your mod",
        default="Moddie"
    )

    bpy.types.Scene.animconfig_selector = bpy.props.StringProperty(
        name="AnimConfig File Selector",
        description="Select a .animconfig file",
        subtype='FILE_PATH',
        default=""     # .blend'e göre relatif
    )

    bpy.types.Scene.animconfig_path = bpy.props.StringProperty(
        name="AnimConfig File Path",
        description="Insert the animconfig path of your character. You can use forward or backward slash.",
        default="chr/boss/h55_placeholder/h55_placeholder.animconfig"
    )

    bpy.types.Scene.animconfig_location = bpy.props.EnumProperty(
        name="AnimConfig Location",
        description="Remote = Official | Local = Custom",
        items=[            
            ('remote', "Remote File", "Official animations"),
            ('local', "Local File", "Local animations"),
            ('customize_remote', "Customize Remote File", "Copy and localize a remote animconfig"),
        ],
        default='remote'
    )    

    bpy.types.Scene.skip_unnecessary_animconfig_files = bpy.props.BoolProperty(
        name="Skip Unnecessary Files",
        description="Only write the local animconfig and point animations to remote relative paths",
        default=True
    )

    bpy.types.Scene.skeleton_path = bpy.props.StringProperty(
        name="Skeleton File Path",
        description="Insert the skeleton path of your character. You can use forward or backward slash.",
        default="chr/boss/h55_placeholder/h55_placeholder.skeleton"
    )

    bpy.types.Scene.gim_selector = bpy.props.StringProperty(
        name="Gim File Selector",
        description="Select a binary .gim or NeoX XML file",
        subtype='FILE_PATH',
        default=""     # .blend'e göre relatif
    )  

    bpy.types.Scene.flip_uv_y = bpy.props.BoolProperty(
        name="Flip UV (Y axis)",
        description="Mirrors the UV on Y axis",
        default=True
    )

    bpy.types.Scene.neox_mesh_selector = bpy.props.StringProperty(
        name="NeoX Mesh Selector",
        description="Select a .gim, .mesh, or .mtg file",
        subtype='FILE_PATH',
        default=""     # .blend'e göre relatif
    )

    bpy.types.Scene.neox_mesh_import_source = bpy.props.EnumProperty(
        name="Import Source",
        description="Select where the NeoX mesh import should read from",
        items=[
            ('remote', "Remote file", "Import from a game asset .gim, .mesh, or .mtg path"),
            ('local', "Local file", "Import from a local .gim, .mesh, or .mtg file"),
        ],
        default='remote'
    )

    bpy.types.Scene.neox_remote_gim_path = bpy.props.StringProperty(
        name="Remote NeoX Path",
        description="Game asset path to a .gim, .mesh, or .mtg file",
        default=""
    )

    bpy.types.Scene.neox_remote_import_extra_parts = bpy.props.BoolProperty(
        name="Import Extra Parts",
        description="Import socket object .gim dependencies as separate meshes",
        default=True
    )

    bpy.types.Scene.neox_remote_import_sockets = bpy.props.BoolProperty(
        name="Import Sockets",
        description="Serialize socket metadata from the remote .gim onto the imported armature",
        default=False
    )

    bpy.types.Scene.neox_socket_filters_enabled = bpy.props.BoolProperty(
        name="Socket Filters",
        description="Filter imported socket metadata by bone or socket name",
        default=True
    )

    bpy.types.Scene.neox_socket_filter_bone_names = bpy.props.StringProperty(
        name="Bone Name",
        description=(
            "Split bone names by using comma (,). Space character will also "
            "consider as bone name. Case insensitive."
        ),
        default=""
    )

    bpy.types.Scene.neox_socket_filter_socket_names = bpy.props.StringProperty(
        name="Socket Name",
        description=(
            "Split socket names by using comma (,). Space character will also "
            "consider as socket name. Case insensitive."
        ),
        default=""
    )

    bpy.types.Scene.neox_socket_filter_socket_match_type = bpy.props.EnumProperty(
        name="Socket Match Type",
        description="How socket name filters should be matched",
        items=[
            ('contains', "Contains text", "Match sockets containing the text"),
            ('exact', "Exact name", "Match sockets with the exact name"),
        ],
        default='contains'
    )

    bpy.types.Scene.neox_animation_selector = bpy.props.StringProperty(
        name="NeoX Animation Selector",
        description="Select a .cpdanimation file",
        subtype='FILE_PATH',
        default=""
    )

    bpy.types.Scene.neox_animation_import_source = bpy.props.EnumProperty(
        name="Import Source",
        description="Select where the NeoX animation import should read from",
        items=[
            ('remote', "Remote file", "Import from a game asset .cpdanimation path"),
            ('local', "Local file", "Import from a local .cpdanimation file"),
        ],
        default='remote'
    )

    bpy.types.Scene.neox_remote_animation_path = bpy.props.StringProperty(
        name="Remote Animation Path",
        description="Game asset path to a .cpdanimation file",
        default=""
    )

    bpy.types.Scene.neox_fx_selector = bpy.props.StringProperty(
        name="NeoX FX Selector",
        description="Select a NeoX FX .json, JSON .pse, or binary .bpse file",
        subtype='FILE_PATH',
        default=""
    )

    bpy.types.Scene.neox_animation_export_mode = bpy.props.EnumProperty(
        name="Export Mode",
        description="Select how the NeoX animation export should be written",
        items=[
            ('implement_existing_mod', "Implement to Existing Mod", "Export and bind the animation into a selected mod gim"),
            ('raw_export', "Raw Export", "Write a standalone .cpdanimation file"),
        ],
        default='implement_existing_mod'
    )

    bpy.types.Scene.neox_animation_export_gim_path = bpy.props.StringProperty(
        name="Mod Gim File",
        description="Prefab .gim file of the existing NeoX mod",
        subtype='FILE_PATH',
        default=""
    )

    bpy.types.Scene.neox_animation_export_animation_name = bpy.props.StringProperty(
        name="Animation Name",
        description="Animconfig animation name to create or replace. Empty uses the active Action name.",
        default=""
    )

    bpy.types.Scene.neox_animation_export_selector = bpy.props.StringProperty(
        name="NeoX Animation Export Selector",
        description="Select a .cpdanimation export path",
        subtype='FILE_PATH',
        default=get_documents_mod_directory()
    )

    bpy.types.Scene.neox_animation_skeleton_preset = bpy.props.EnumProperty(
        name="Skeleton",
        description="Select the skeleton path written into the animation file",
        items=[
            ('woman', "Woman", "chr/player/dm65_survivor_w/dm65_survivor_w.skeleton"),
            ('male', "Male", "chr/player/dm65_survivor_m/h55_survivor_m_zbs/h55_survivor_m_zbs.skeleton"),
            ('custom', "Custom", "Use custom path"),
        ],
        default='woman'
    )

    bpy.types.Scene.neox_animation_custom_skeleton_path = bpy.props.StringProperty(
        name="Custom Skeleton Path",
        description="Absolute path, or a relative path under the game's Documents/res folder",
        default=""
    )

    bpy.types.Scene.neox_animation_export_loop = bpy.props.BoolProperty(
        name="Loop",
        description="Write the animation loop flag",
        get=get_neox_animation_export_loop,
        set=set_neox_animation_export_loop
    )

    bpy.types.Scene.neox_animation_export_fps = bpy.props.IntProperty(
        name="FPS",
        description="Output FPS",
        default=30,
        min=1,
        max=1000
    )

    bpy.types.Scene.neox_animation_export_reduce_keys = bpy.props.BoolProperty(
        name="Reduce Redundant Keys",
        description="Remove keys reproduced by interpolation",
        default=True
    )

    bpy.types.Scene.neox_animation_position_tolerance = bpy.props.FloatProperty(
        name="Position Tolerance",
        default=1.0e-4,
        min=0.0,
        precision=6
    )

    bpy.types.Scene.neox_animation_scale_tolerance = bpy.props.FloatProperty(
        name="Scale Tolerance",
        default=1.0e-4,
        min=0.0,
        precision=6
    )

    bpy.types.Scene.neox_animation_rotation_tolerance_degrees = bpy.props.FloatProperty(
        name="Rotation Tolerance",
        description="Angular key-reduction tolerance in degrees",
        default=0.05,
        min=0.0,
        precision=4
    )

    bpy.types.Scene.neox_action_selector = bpy.props.EnumProperty(
        name="Action Selector",
        description="Select the action you want to do",
        items=[
            ('OPT_NeoX_Mesh', "Import/Export Mesh", "Import or export NeoX mesh files"),
            ('OPT_NeoX_Animation', "Import/Export Animation", "Import or export NeoX animation files"),
            ('OPT_NeoX_Mod_Exporter', "Export NeoX Mod", "Exports NeoX mod"),
            ('OPT_Build_Dual_Form_Skin', "Build Dual Form Skin", "Build a dual-form skin from exported NeoX gim files"),
            ('OPT_Socket_Operations', "Socket Operations", "Socket editor GUI"),
            ('OPT_Import_Neox_FX', "Import FX (Under Development)", "Import a JSON-converted NeoX BPSE FX preview"),
        ],
        default='OPT_NeoX_Mesh'
    )

    bpy.types.Scene.migoto_action_selector = bpy.props.EnumProperty(
        name="Action Selector",
        description="Select the action you want to do",
        items=[
            ('OPT_Import_3DM', "Import 3DM Mesh", "Import a mesh from VB/IB buffer files"),
            ('OPT_Import_CB_Pose', "Import CB Pose", "Import a constant-buffer pose armature from .txt or .buf files"),
            ('OPT_Extract_Frame', "Extract Frame Dump (3DM)", "Auto selects the character materials. Selected materials will be copied into \"YourDumpFolder\\Character\" "),
            ('OPT_Set_Textures', "Set Textures (3DM)", "Auto sets t0 textures to your dumped mesh objects. Skips unvisible ones."),
            ('OPT_Export_Mod', "Export 3DM Mod", "Select a folder to extract your mod"),
        ],
        default='OPT_Import_3DM'
    )

    bpy.types.Scene.migoto_cb_pose_start_index = bpy.props.IntProperty(
        name="Start Index",
        description="First constant-buffer float4 row/register to import",
        default=0,
        min=0,
    )

    bpy.types.Scene.migoto_cb_pose_end_index = bpy.props.IntProperty(
        name="End Index",
        description="Last constant-buffer float4 row/register to import",
        default=1019,
        min=0,
    )

    bpy.types.Scene.migoto_cb_pose_batch_import_related_meshes = bpy.props.BoolProperty(
        name="Batch Import Relates Meshes",
        description="Also apply CB pose imports to scene meshes sharing a vbN hash with the selected mesh objects",
        default=False,
    )

    bpy.types.Scene.migoto_mesh_import_mode = bpy.props.EnumProperty(
        name="Import Type",
        description="Select the 3DMigoto mesh buffer type",
        items=[
            ('TXT', "Import *.txt", "Import text frame-analysis buffers"),
            ('BUF', "Import *.buf", "Import binary buffers using a .fmt layout"),
        ],
        default='TXT',
    )

    bpy.types.Scene.migoto_use_standard_model_format = bpy.props.BoolProperty(
        name="Use standard model format",
        description="Use the built-in default .fmt layout for .buf imports",
        default=False,
    )

    bpy.types.Scene.migoto_import_all_related_txt = bpy.props.BoolProperty(
        name="Import All Relative Files",
        description="Import every text draw call that shares the same IB and vb0 as the selected file",
        default=True,
    )

    bpy.types.Scene.migoto_import_all_related_buf = bpy.props.BoolProperty(
        name="Import All Relative Files",
        description="Import every binary draw call that shares the same IB and vb0 as the selected file",
        default=False,
    )

    # Tek ortak klasör seçici: N-Panel'de çizilecek
    bpy.types.Scene.frame_dump_selector = bpy.props.StringProperty(
        name="Frame Dump Folder Selector",
        description="Select a folder",
        subtype='DIR_PATH',
        default=""     # .blend'e göre relatif
    )

    bpy.types.Scene.migoto_export_selector = bpy.props.StringProperty(
        name="3DM Export Folder Selector",
        description="Select a 3DM mod export folder",
        subtype='DIR_PATH',
        default=""
    )

    bpy.types.Scene.neox_export_selector = bpy.props.StringProperty(
        name="NeoX Export Folder Selector",
        description="Select a NeoX mod export folder",
        subtype='DIR_PATH',
        default=get_documents_mod_directory()
    )

    bpy.types.Scene.clean_ini = bpy.props.BoolProperty(
        name="Clean INI",
        description="It makes your mod folder modular. May cause mod conflicts if it get a namespace name that is already taken.",
        default=False
    )

    bpy.types.Scene.export_all_relative_meshes = bpy.props.BoolProperty(
        name="Export All Relative Meshes",
        description="Export every visible imported 3DM mesh that shares the selected mesh vb0 hash",
        default=True,
    )

    bpy.types.Scene.clear_unused_materials = bpy.props.BoolProperty(
        name="Clear Unused Materials",
        description="Skip unprocessed draw calls that share the exported vb0 hash",
        default=True,
    )

    bpy.types.Scene.migoto_save_fmt_file = bpy.props.BoolProperty(
        name="Save *.fmt file",
        description="Save a 3DMigoto .fmt layout reference file during export",
        default=False,
    )

    bpy.types.Scene.namespace_textbox = bpy.props.StringProperty(
        name="Namespace Name",
        description="Specify an unique custom ID for your mod. If you pick a name that already taken by another mod, both the mods will conflict",
        default=""
    )

    bpy.types.Scene.custom_metal = bpy.props.BoolProperty(
        name="Custom Metal Texture",
        description="Enables custom metal-map texture",
        default=False
    )

    bpy.types.Scene.custom_normal = bpy.props.BoolProperty(
        name="Custom Normal Texture",
        description="Enables custom normal-map texture",
        default=False
    )

    bpy.types.Scene.metal_selector = bpy.props.StringProperty(
        name="Metal-Map Selector",
        description="Select a folder",
        subtype='FILE_PATH',
        default=""     # .blend'e göre relatif
    )

    bpy.types.Scene.normal_selector = bpy.props.StringProperty(
        name="Normal-Map Selector",
        description="Select a folder",
        subtype='FILE_PATH',
        default=""     # .blend'e göre relatif
    )

    bpy.types.Scene.metal_slot_selector = bpy.props.IntProperty(
        name="Metal",
        description="Metal texture t slot",
        default=6,
        min=0,
    )    

    bpy.types.Scene.normal_slot_selector = bpy.props.IntProperty(
        name="Normal",
        description="Normal texture t slot",
        default=7,
        min=0,
    )

    bpy.types.Scene.idvmi_update_status = bpy.props.StringProperty(
        name="Update Status",
        default=""
    )

    bpy.types.Scene.idvmi_update_latest_version = bpy.props.StringProperty(
        name="Latest Version",
        default=""
    )

    bpy.types.Scene.idvmi_update_latest_url = bpy.props.StringProperty(
        name="Latest Release URL",
        default=""
    )

    bpy.types.Scene.idvmi_update_download_url = bpy.props.StringProperty(
        name="Latest Download URL",
        default=""
    )

    bpy.types.Scene.idvmi_update_available = bpy.props.BoolProperty(
        name="Update Available",
        default=False
    )
