import importlib

from ..extract_frame_dump import extract_frame_dump
from ..set_textures import set_textures
from ..export_mod.export_ops import Export3DMigoto
from ..neox_tools.import_ops import IDVMI_FH_Neox_Mesh, IDVMI_OT_Import_Neox_Mesh
from ..neox_tools.animation_import_ops import IDVMI_OT_Import_Neox_Animation
from ..neox_tools.animation_export_ops import IDVMI_OT_Export_Neox_Animation
from ..neox_tools.export_ops import IDVMI_OT_Export_Neox_Mesh
from ..addon import ui
from ..neox_tools.mod_exporter.mod_export_ops import IDVMI_OT_Export_Neox_Mod
from ..neox_tools.socket_operations import bind_gim_ops, copy_socket_ops

# '3dm' starts with a digit, so standard relative import syntax is not valid.
# Use importlib to load the subpackage by string name.
_top_pkg = __name__.rsplit('.', 2)[0]  # e.g. 'IDVMI-Tools'
_3dm_import_ops = importlib.import_module('.3dm.import_ops', package=_top_pkg)
IDVMI_OT_Import_3DM = _3dm_import_ops.IDVMI_OT_Import_3DM
IDVMI_OT_Select_VB_Manual = _3dm_import_ops.IDVMI_OT_Select_VB_Manual
IDVMI_OT_Select_FMT_Manual = _3dm_import_ops.IDVMI_OT_Select_FMT_Manual
IDVMI_OT_Import_CB_Pose_Armature = _3dm_import_ops.IDVMI_OT_Import_CB_Pose_Armature

classes = [
           copy_socket_ops.IDVMI_OT_Copy_Socket,
           bind_gim_ops.IDVMI_OT_Bind_Gim,
           ui.IDVMI_Neox_tools,
           ui.IDVMI_3DM_tools,
           extract_frame_dump.IDVMI_OT_extract_frame_dump,
           set_textures.IDVMI_OT_set_textures,
           Export3DMigoto,
           IDVMI_OT_Import_Neox_Mesh,
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
