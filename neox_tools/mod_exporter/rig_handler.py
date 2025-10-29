import os, bpy, shutil

def rig_handler(export_path, context):
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

    skeleton_path = rig_path_lib[context.scene.neox_rig_selector][1]
    skeleton_path = os.path.join(rel_path, skeleton_path).replace("\\", "/")

    rig_lib_root_path = os.path.join(os.path.dirname(__file__), "rig_resource")
    gim_path_lib = {
        'woman': os.path.join(rig_lib_root_path, "dm65_survivor_w_yiyaoshi_lv1.gim"),
        'male': os.path.join(rig_lib_root_path, "h55_survivor_m_zbs_lv1.gim"),
        'little_girl': os.path.join(rig_lib_root_path, "dm65_survivor_girl.gim"),
        'custom': bpy.path.abspath(context.scene.gim_selector)
    }

    gim_selection = 'custom' if context.scene.custom_gim_bool else context.scene.neox_rig_selector

    return {"animconfig": animconfig_path, "skeleton": skeleton_path, "gim": gim_path_lib[gim_selection]}