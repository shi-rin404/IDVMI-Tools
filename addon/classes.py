import importlib

from ..neox_tools.import_ops import (
    IDVMI_FH_Neox_Mesh,
    IDVMI_OT_Grab_Current_Skin_From_Game,
    IDVMI_OT_Import_Neox_Mesh,
)
from ..neox_tools.utils.game_root import IDVMI_OT_Select_Game_Executable
from ..neox_tools.animation_import_ops import IDVMI_OT_Import_Neox_Animation
from ..neox_tools.animation_export_ops import IDVMI_OT_Export_Neox_Animation
from ..neox_tools.export_ops import IDVMI_OT_Export_Neox_Mesh
from ..neox_tools.fx_import.import_ops import IDVMI_OT_Import_Neox_FX
from ..addon import auto_update, ui
from ..neox_tools.mod_exporter.mod_export_ops import IDVMI_OT_Export_Neox_Mod
from ..neox_tools.socket_operations import visualize_socket_ops
from ..neox_tools import dual_form_ops

# '3dm' starts with a digit, so standard relative import syntax is not valid.
# Use importlib to load the subpackage by string name.
_top_pkg = __name__.rsplit('.', 2)[0]  # e.g. 'IDVMI-Tools'
_3dm_import_ops = importlib.import_module('.3dm.import_ops', package=_top_pkg)
_3dm_extract_frame_dump = importlib.import_module('.3dm.extract_frame_dump.extract_frame_dump', package=_top_pkg)
_3dm_set_textures = importlib.import_module('.3dm.set_textures.set_textures', package=_top_pkg)
_3dm_export_ops = importlib.import_module('.3dm.export_mod.export_ops', package=_top_pkg)
IDVMI_OT_Import_3DM = _3dm_import_ops.IDVMI_OT_Import_3DM
IDVMI_OT_Select_VB_Manual = _3dm_import_ops.IDVMI_OT_Select_VB_Manual
IDVMI_OT_Select_FMT_Manual = _3dm_import_ops.IDVMI_OT_Select_FMT_Manual
IDVMI_OT_Import_CB_Pose_Armature = _3dm_import_ops.IDVMI_OT_Import_CB_Pose_Armature
IDVMI_OT_extract_frame_dump = _3dm_extract_frame_dump.IDVMI_OT_extract_frame_dump
IDVMI_OT_set_textures = _3dm_set_textures.IDVMI_OT_set_textures
Export3DMigoto = _3dm_export_ops.Export3DMigoto

classes = [
           dual_form_ops.IDVMI_PG_Dual_Form_Trigger,
           dual_form_ops.IDVMI_UL_Dual_Form_Triggers,
           dual_form_ops.IDVMI_OT_Dual_Form_Add_Trigger,
           dual_form_ops.IDVMI_OT_Dual_Form_Remove_Trigger,
           dual_form_ops.IDVMI_OT_Dual_Form_Add_Regex_Triggers,
           dual_form_ops.IDVMI_OT_Dual_Form_Cycle_Preset_Source,
           dual_form_ops.IDVMI_OT_Dual_Form_Reload_Presets,
           dual_form_ops.IDVMI_OT_Dual_Form_Open_Preset_Folder,
           dual_form_ops.IDVMI_OT_Dual_Form_Import_Preset,
           dual_form_ops.IDVMI_OT_Dual_Form_Export_Preset,
           dual_form_ops.IDVMI_OT_Build_Dual_Form_Skin,
           visualize_socket_ops.IDVMI_OT_Create_Socket_Visuals,
           visualize_socket_ops.IDVMI_OT_Copy_Socket_Visual,
           visualize_socket_ops.IDVMI_OT_Create_Socket,
           visualize_socket_ops.IDVMI_OT_Delete_Socket,
           ui.IDVMI_Neox_tools,
           ui.IDVMI_Neox_Dual_Form_Trigger_Presets,
           ui.IDVMI_Neox_Dual_Form_Trigger_Preset_Import,
           ui.IDVMI_Neox_Dual_Form_Trigger_Preset_Export,
           ui.IDVMI_Neox_Dual_Form_Advanced,
           ui.IDVMI_Neox_Dual_Form_Build,
           ui.IDVMI_Neox_Mesh_Import,
           ui.IDVMI_Neox_Mesh_Export,
           ui.IDVMI_Neox_Animation_Import,
           ui.IDVMI_Neox_Animation_Export,
           ui.IDVMI_3DM_tools,
           auto_update.IDVMI_Update_tools,
           auto_update.IDVMI_Update_tools_Migoto,
           auto_update.IDVMI_OT_Check_Update,
           auto_update.IDVMI_OT_Install_Update,
           IDVMI_OT_extract_frame_dump,
           IDVMI_OT_set_textures,
           Export3DMigoto,
           IDVMI_OT_Select_Game_Executable,
           IDVMI_OT_Import_Neox_Mesh,
           IDVMI_OT_Grab_Current_Skin_From_Game,
           IDVMI_OT_Import_Neox_FX,
           IDVMI_OT_Import_Neox_Animation,
           IDVMI_OT_Export_Neox_Animation,
           IDVMI_OT_Export_Neox_Mesh,
           IDVMI_OT_Export_Neox_Mod,
           IDVMI_OT_Import_3DM,
           IDVMI_OT_Select_VB_Manual,
           IDVMI_OT_Select_FMT_Manual,
           IDVMI_OT_Import_CB_Pose_Armature]

if IDVMI_FH_Neox_Mesh is not None:
    classes.append(IDVMI_FH_Neox_Mesh)
