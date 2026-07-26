import os, bpy, shutil
from pathlib import Path


def _resolve_custom_gim_path(context):
    if context.scene.custom_gim_location == "remote":
        gim_asset_path = context.scene.custom_gim_remote_path.strip()
        if not gim_asset_path:
            raise ValueError("Please enter a remote .gim asset path")
        if not gim_asset_path.lower().endswith(".gim"):
            raise ValueError("Remote Gim path must point to a .gim file")

        from ..remote_import import extract_remote_asset_to_cache

        cache_root = Path(__file__).resolve().parents[1] / "remote_import_cache" / "export_gim"
        return extract_remote_asset_to_cache(gim_asset_path, cache_root)

    return bpy.path.abspath(context.scene.gim_selector)

def rig_handler(export_path, context, custom_skeleton=False):
    rig_path_lib = {
            'woman': ('chr/player/dm65_survivor_w/dm65_survivor_w.animconfig', 'chr/player/dm65_survivor_w/dm65_survivor_w.skeleton'),
            'male': ('chr/player/dm65_survivor_m/h55_survivor_m_zbs/h55_survivor_m_zbs.animconfig', 'chr/player/dm65_survivor_m/h55_survivor_m_zbs/h55_survivor_m_zbs.skeleton'),
            'little_girl': ('chr/player/dm65_survivor_girl/dm65_survivor_girl.animconfig', 'chr/player/dm65_survivor_girl/dm65_survivor_girl.skeleton'),
            'custom': tuple(path.strip().replace("\\", "/") for path in map(bpy.path.abspath, (context.scene.animconfig_path, context.scene.skeleton_path)))
        }    

    res = 'res\\'
    rel_path = os.path.relpath(f"{export_path.split(res, 1)[0]}{res}", export_path)

    if context.scene.neox_rig_selector != 'custom' or context.scene.animconfig_location == "remote":
        animconfig_path = rig_path_lib[context.scene.neox_rig_selector][0]
        animconfig_path = os.path.join(rel_path, animconfig_path).replace("\\", "/")
    elif context.scene.animconfig_location == "local":
        animconfig_path = bpy.path.abspath(context.scene.animconfig_selector)
        new_animconfig_path = os.path.join(export_path, os.path.basename(animconfig_path))
        shutil.copy(animconfig_path, new_animconfig_path)
        animconfig_path = os.path.basename(new_animconfig_path)

    if custom_skeleton:
        skeleton_path = "main.skeleton"
    else:
        skeleton_path = rig_path_lib[context.scene.neox_rig_selector][1]
        skeleton_path = os.path.join(rel_path, skeleton_path).replace("\\", "/")

    rig_lib_root_path = os.path.join(os.path.dirname(__file__), "rig_resource")
    preset_gim_path_lib = {
        'woman': os.path.join(rig_lib_root_path, "dm65_survivor_w_yiyaoshi_lv1.gim"),
        'male': os.path.join(rig_lib_root_path, "h55_survivor_m_zbs_lv1.gim"),
        'little_girl': os.path.join(rig_lib_root_path, "dm65_survivor_girl.gim"),
    }

    if context.scene.neox_rig_selector == 'custom':
        gim_path = bpy.path.abspath(context.scene.gim_selector)
    elif context.scene.custom_gim_bool:
        gim_path = _resolve_custom_gim_path(context)
    else:
        gim_path = preset_gim_path_lib[context.scene.neox_rig_selector]

    return {"animconfig": animconfig_path, "skeleton": skeleton_path, "gim": gim_path}
