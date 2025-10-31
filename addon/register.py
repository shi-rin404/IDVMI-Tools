import bpy
from ..neox_tools.utils.game_dir_detector import check_game_directory

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
        ],
        default='remote'
    )    

    bpy.types.Scene.skeleton_path = bpy.props.StringProperty(
        name="Skeleton File Path",
        description="Insert the skeleton path of your character. You can use forward or backward slash.",
        default="chr/boss/h55_placeholder/h55_placeholder.skeleton"
    )

    bpy.types.Scene.gim_selector = bpy.props.StringProperty(
        name="Gim File Selector",
        description="Select a .gim file",
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

    bpy.types.Scene.neox_action_selector = bpy.props.EnumProperty(
        name="Action Selector",
        description="Select the action you want to do",
        items=[            
            ('OPT_Import_Neox_Mesh', "Import NeoX Mesh", "Imports .mesh file"),            
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
            ('OPT_Extract_Frame', "Extract Frame Dump (3DM)", "Auto selects the character materials. Selected materials will be copied into \"YourDumpFolder\\Character\" "),
            ('OPT_Set_Textures', "Set Textures (3DM)", "Auto sets t0 textures to your dumped mesh objects. Skips unvisible ones."),
            ('OPT_Export_Mod', "Export 3DM Mod", "Select a folder to extract your mod"),                        
        ],
        default='OPT_Extract_Frame'
    )

    # Tek ortak klasör seçici: N-Panel'de çizilecek
    bpy.types.Scene.frame_dump_selector = bpy.props.StringProperty(
        name="Frame Dump Folder Selector",
        description="Select a folder",
        subtype='DIR_PATH',
        default=""     # .blend'e göre relatif
    )

    bpy.types.Scene.export_selector = bpy.props.StringProperty(
        name="Export Folder Selector",
        description="Select a folder",
        subtype='DIR_PATH',
        default=check_game_directory()     # .blend'e göre relatif
    )

    bpy.types.Scene.clean_ini = bpy.props.BoolProperty(
        name="Clean INI",
        description="It makes your mod folder modular. May cause mod conflicts if it get a namespace name that is already taken.",
        default=False
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

    bpy.types.Scene.metal_slot_selector = bpy.props.EnumProperty(
        name="Metal",
        description="Select the action you want to do",
        items=[            
            ('t9', "Slot: t9", "Old metal slot"),
            ('t10', "Slot: t10", "New metal slot"),
            ('t11', "Slot: t11", "Newest metal slot"),
        ],
        default='t10'
    )    

    bpy.types.Scene.normal_slot_selector = bpy.props.EnumProperty(
        name="Normal",
        description="Select the action you want to do",
        items=[            
            ('t10', "Slot: t10", "Old normal slot"),
            ('t11', "Slot: t11", "New normal slot"),
        ],
        default='t11'
    )    