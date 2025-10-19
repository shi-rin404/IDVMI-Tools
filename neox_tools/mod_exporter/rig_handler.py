import os

def rig_handler(export_path, context):
    if context.scene.animconfig_location == "remote":
        rig_path_lib = {
            'woman': ('chr/player/dm65_survivor_w/dm65_survivor_w.animconfig', 'chr/player/dm65_survivor_w/dm65_survivor_w.skeleton'),
            'male': ('chr/player/dm65_survivor_m/h55_survivor_m_zbs/h55_survivor_m_zbs.animconfig', 'chr/player/dm65_survivor_m/h55_survivor_m_zbs/h55_survivor_m_zbs.skeleton'),
            'little_girl': ('chr/player/dm65_survivor_girl/dm65_survivor_girl.animconfig', 'chr/player/dm65_survivor_girl/dm65_survivor_girl.skeleton'),
        }

        animconfig_path = rig_path_lib[context.scene.neox_rig_selector][0]
        skeleton_path = rig_path_lib[context.scene.neox_rig_selector][1]
    elif context.scene.animconfig_location == "local":
        animconfig_path = context.scene.animconfig_path
        skeleton_path = context.scene.animconfig_path

    rig_lib_root_path = os.path.join(os.path.dirname(__file__), "rig_resource")
    gim_path_lib = {
        'woman': os.path.join(rig_lib_root_path, "dm65_survivor_w_yiyaoshi_lv1.gim"),
        'male': os.path.join(rig_lib_root_path, "h55_survivor_m_zbs_lv1.gim"),
        'little_girl': os.path.join(rig_lib_root_path, "dm65_survivor_girl.gim"),
    }

    res = 'res\\'
    rel_path = os.path.relpath(f"{export_path.split(res, 1)[0]}{res}", export_path)
    animconfig_path = os.path.join(rel_path, animconfig_path).replace("\\", "/")
    skeleton_path = os.path.join(rel_path, skeleton_path).replace("\\", "/")

    return {"animconfig": animconfig_path, "skeleton": skeleton_path, "gim": gim_path_lib[context.scene.neox_rig_selector]}

    