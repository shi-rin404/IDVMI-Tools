import bpy
from ..neox_tools.utils.game_dir_detector import check_game_directory

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
    bpy.types.Scene.socket_source_gim_selector = bpy.props.StringProperty(
        name="Source Gim File Selector",
        description="Select a .gim file",
        subtype='FILE_PATH',
        default=""     # .blend'e göre relatif
    )

    bpy.types.Scene.socket_action_selector = bpy.props.EnumProperty(
        name="Socket Action Selector",
        description="Select your socket action",
        items=[            
            ('bind_gim', "Bind Gim", "Bind your gim to a specific socket"),
            ('copy_socket', "Copy Socket", "Copy a socket from another gim"),
        ],
        default='bind_gim'
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

    bpy.types.Scene.gim_path = bpy.props.StringProperty(
        name="Gim File Path",
        description="Insert the gim path of your character. You can use forward or backward slash.",
        default=""
    )

    bpy.types.Scene.default_socket_name = bpy.props.StringProperty(
        name="Socket Name",
        description="Name of your socket",
        default=""
    )

    bpy.types.Scene.socket_default_gim_selector = bpy.props.StringProperty(
        name="Gim File Selector",
        description="Select a .gim file",
        subtype='FILE_PATH',
        default=""     # .blend'e göre relatif
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
        description="Select a .mesh file",
        subtype='FILE_PATH',
        default=""     # .blend'e göre relatif
    )

    bpy.types.Scene.neox_mesh_import_source = bpy.props.EnumProperty(
        name="Import Source",
        description="Select where the NeoX mesh import should read from",
        items=[
            ('remote', "Remote file", "Import from a game asset .gim path"),
            ('local', "Local file", "Import from a local .mesh file"),
        ],
        default='local'
    )

    bpy.types.Scene.neox_remote_gim_path = bpy.props.StringProperty(
        name="Remote Gim Path",
        description="Game asset path to a .gim prefab file",
        default=""
    )

    bpy.types.Scene.neox_remote_import_sockets = bpy.props.BoolProperty(
        name="Import Sockets",
        description="Serialize socket metadata from the remote .gim onto the imported armature",
        default=False
    )

    bpy.types.Scene.neox_animation_selector = bpy.props.StringProperty(
        name="NeoX Animation Selector",
        description="Select a .cpdanimation file",
        subtype='FILE_PATH',
        default=""
    )

    bpy.types.Scene.neox_animation_export_selector = bpy.props.StringProperty(
        name="NeoX Animation Export Selector",
        description="Select a .cpdanimation export path",
        subtype='FILE_PATH',
        default=check_game_directory()
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
            ('OPT_Import_Neox_Mesh', "Import NeoX Mesh", "Imports .mesh file"),            
            ('OPT_Import_Neox_Animation', "Import Animation", "Imports .cpdanimation file"),
            ('OPT_Export_Neox_Animation', "Export Animation", "Exports .cpdanimation file"),
            ('OPT_NeoX_Mod_Exporter', "Export NeoX Mod", "Exports NeoX mod"),
            ('OPT_Export_Neox_Mesh', "Export NeoX Mesh", "Exports .mesh file"),
            ('OPT_Socket_Operations', "Socket Operations", "Socket editor GUI"),
        ],
        default='OPT_Import_Neox_Mesh'
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
        default=check_game_directory()
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
