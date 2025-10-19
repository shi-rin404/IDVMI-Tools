import json, os

def mod_json_maker(gim_path:str, context):
    relative_gim_path = gim_path.split("res\\", 1)[1].replace("\\", "/")

    mod_json_data = {
        "character": "All",
        "name": context.scene.neox_mod_name,
        "skin": relative_gim_path}
    
    with open(os.path.join(os.path.dirname(gim_path), "mod.json"), "w") as json_file:
        json.dump(mod_json_data, json_file, indent=4)