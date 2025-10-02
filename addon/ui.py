import bpy
from ..extract_frame_dump import extract_frame_dump
from ..set_textures import set_textures
from ..export_mod.export_ops import Export3DMigoto
from ..neox_tools.import_ops import IDVMI_OT_Import_Neox_Mesh
from ..neox_tools.export_ops import IDVMI_OT_Export_Neox_Mesh

class IDVMI_PT_tools(bpy.types.Panel):
    bl_label = "IDVMI Tools"
    bl_idname = "idvmi_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "IDVMI Tools"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Action Selection
        layout.prop(scene, "action_selector")

        if scene.action_selector != 'OPT_Export_Neox_Mesh': folder_selectors = layout.box()                                       

        # Sadece Extract seçiliyken: klasör seçici (tek satır + klasör ikonu)
        if scene.action_selector == 'OPT_Extract_Frame':
            folder_selectors.label(text="Frame Dump Folder")
            folder_selectors.prop(scene, "frame_dump_selector", text="")            
            layout.operator("idvmi_tools.extract_frame_dump", icon="FILE_REFRESH")

        elif scene.action_selector == 'OPT_Set_Textures':
            folder_selectors.label(text="Frame Dump Folder")
            folder_selectors.prop(scene, "frame_dump_selector", text="")
            layout.operator("idvmi_tools.set_textures", icon="FILE_REFRESH")

        elif scene.action_selector == 'OPT_Export_Mod':
            folder_selectors.label(text="Frame Dump Folder")
            folder_selectors.prop(scene, "frame_dump_selector", text="")
            folder_selectors.label(text="Export Folder")
            folder_selectors.prop(scene, "export_selector", text="")            

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
            layout.operator("idvmi_tools.export_mod_migoto", icon="EXPORT")    
        
        elif scene.action_selector == 'OPT_Import_Neox_Mesh':
            # neox_box_import = layout.box()
            folder_selectors.label(text="NeoX Mesh")
            folder_selectors.prop(scene, "neox_mesh_selector", text="")
            layout.operator("idvmi_tools.neox_importer", icon="IMPORT")

        elif scene.action_selector == 'OPT_Export_Neox_Mesh':
            # neox_box_import = layout.box()
            layout.prop(context.scene, "flip_uv_y", text="Flip UV (Y axis)")
            layout.operator("idvmi_tools.neox_exporter", icon="EXPORT")

classes = (IDVMI_PT_tools, extract_frame_dump.IDVMI_OT_extract_frame_dump, set_textures.IDVMI_OT_set_textures, Export3DMigoto, IDVMI_OT_Import_Neox_Mesh, IDVMI_OT_Export_Neox_Mesh)

def register_props():
    bpy.types.Scene.flip_uv_y = bpy.props.BoolProperty(
        name="Flip UV (Y axis)",
        description="Mirrors the UV on Y axis",
        default=False
    )

    bpy.types.Scene.neox_mesh_selector = bpy.props.StringProperty(
        name="NeoX Mesh Selector",
        description="Select a .mesh file",
        subtype='FILE_PATH',
        default=""     # .blend'e göre relatif
    )

    bpy.types.Scene.action_selector = bpy.props.EnumProperty(
        name="Action Selector",
        description="Select the action you want to do",
        items=[            
            ('OPT_Extract_Frame', "Extract Frame Dump", "Auto selects the character materials. Selected materials will be copied into \"YourDumpFolder\\Character\" "),
            ('OPT_Set_Textures', "Set Textures", "Auto sets t0 textures to your dumped mesh objects. Skips unvisible ones."),
            ('OPT_Export_Mod', "Export Mod", "Select a folder to extract your mod"),            
            ('OPT_Import_Neox_Mesh', "Import NeoX Mesh", "Imports .mesh file"),
            ('OPT_Export_Neox_Mesh', "Export NeoX Mesh", "Exports .mesh file"),
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
        default=""     # .blend'e göre relatif
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
            ('t10', "Slot: t10", "New metal slot-1"),
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
    
def unregister_props():
    if hasattr(bpy.types.Scene, "flip_uv_y"):
        del bpy.types.Scene.flip_uv_y
    if hasattr(bpy.types.Scene, "neox_mesh_selector"):
        del bpy.types.Scene.neox_mesh_selector

    if hasattr(bpy.types.Scene, "frame_dump_selector"):
        del bpy.types.Scene.frame_dump_selector
    if hasattr(bpy.types.Scene, "export_selector"):
        del bpy.types.Scene.export_selector
    if hasattr(bpy.types.Scene, "clean_ini"):
        del bpy.types.Scene.clean_ini

    if hasattr(bpy.types.Scene, "custom_metal"):  
        del bpy.types.Scene.custom_metal
    if hasattr(bpy.types.Scene, "custom_normal"):  
        del bpy.types.Scene.custom_normal
        
    if hasattr(bpy.types.Scene, "metal_selector"):  
        del bpy.types.Scene.metal_selector
    if hasattr(bpy.types.Scene, "normal_selector"):  
        del bpy.types.Scene.normal_selector
        
    if hasattr(bpy.types.Scene, "metal_slot_selector"):  
        del bpy.types.Scene.metal_slot_selector
    if hasattr(bpy.types.Scene, "normal_slot_selector"):  
        del bpy.types.Scene.normal_slot_selector

    if hasattr(bpy.types.Scene, "namespace_textbox"):
        del bpy.types.Scene.namespace_textbox
    if hasattr(bpy.types.Scene, "action_selector"):
        del bpy.types.Scene.action_selector