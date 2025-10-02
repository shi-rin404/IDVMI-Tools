import bpy
from bpy.types import Operator, Object
import os, re, json, base64, shutil

def ini_maker(
        Operator: Operator,
        draw_call: str,
        vb0_hash: str,
        vb_path: os.PathLike,
        ib_path: os.PathLike,
        export_path: os.PathLike,
        ini_path: os.PathLike,
        frame_dump_path: os.PathLike,
        context,
        obj: Object,   
        namespace: str = "",     
        clean_ini: bool = False,
):
    files = os.listdir(frame_dump_path)
    hashes = {}

    if "TextureUsage.json" in files:
        with open(os.path.join(frame_dump_path, "TextureUsage.json"), "r") as file:
            texture_usage = json.load(file)

        if "t0_hash" not in texture_usage[draw_call]:
            raise Exception("TextureUsage.json format is wrong")
        
        hashes["t0"] = texture_usage[draw_call]["t0"]
        hashes["t9"] = texture_usage.get(draw_call, {}).get("t9") or None
        hashes["t10"] = texture_usage.get(draw_call, {}).get("t10") or None
        hashes["t11"] = texture_usage.get(draw_call, {}).get("t11") or None            
    else:
        for file in files:
            for n in (0,9,10,11):
                result = re.search(fr"{draw_call}(\.\d+-\[.*?\])*-ps-t{n}=([a-f0-9]{{8}})", file)                
                if result:
                    hashes[f"t{n}"] = result.group(2)

    ini_config = {}    
    
    # # # DIFFUSE # # #
    if len(obj.data.materials) > 0:
        material = obj.data.materials[0]
        
        if material and material.use_nodes:
            for node in material.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    t0_path = bpy.path.abspath(node.image.filepath)
                    break
            else:
                t0_path = None

        if t0_path:
            if not os.path.isdir(os.path.join(export_path, "Texture")):
                os.mkdir(os.path.join(export_path, "Texture"))
                
            shutil.copy(t0_path, os.path.join(export_path, "Texture", os.path.basename(t0_path)))
            ini_config["diffuse_exists"] = True
            ini_config["diffuse_path"] = os.path.join(export_path, "Texture", os.path.basename(t0_path))
        else:
            ini_config["diffuse_exists"] = False

    dds_template = "RERTIHwAAAAHEAoABAAAAAQAAAAQAAAAAQAAAAEAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACAAAAAEAAAARFgxMAAAAAAAAAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAABiAAAAAwAAAAAAAAABAAAAAAAAAA"
    metal_slot = context.scene.metal_slot_selector
    normal_slot = context.scene.normal_slot_selector

    # # # METAL # # #
    if not context.scene.custom_metal and hashes[metal_slot]:
        default_metal = "gEAoHA////83a8Hayqqqo="
        with open(os.path.join(export_path, "Texture", f"{hashes[metal_slot]}.dds"), "wb") as file:        
            file.write(
                base64.b64decode(f"{dds_template}{default_metal}")
            )
        ini_config["metal_exists"] = True
        ini_config["metal_path"] = os.path.join(export_path, "Texture", f"{hashes[metal_slot]}.dds")
    elif context.scene.custom_metal and context.scene.metal_selector:
        shutil.copy(bpy.path.abspath(context.scene.metal_selector), os.path.join(export_path, "Texture", os.path.basename(context.scene.metal_selector)))
        ini_config["metal_exists"] = True
        ini_config["metal_path"] = os.path.join(export_path, "Texture", os.path.basename(context.scene.metal_selector))
    else:
        ini_config["metal_exists"] = False

    # # # NORMAL # # #
    if not context.scene.custom_normal and hashes[normal_slot]:
        default_normal = "jsdrvdbrfb/f///6+qqqo="
        with open(os.path.join(export_path, "Texture", f"{hashes[normal_slot]}.dds"), "wb") as file:
            file.write(
                base64.b64decode(f"{dds_template}{default_normal}")
            )
        ini_config["normal_exists"] = True
        ini_config["normal_path"] = os.path.join(export_path, "Texture", f"{hashes[normal_slot]}.dds")
    elif context.scene.custom_normal and context.scene.normal_selector:
        shutil.copy(bpy.path.abspath(context.scene.normal_selector), os.path.join(export_path, "Texture", os.path.basename(context.scene.normal_selector)))
        ini_config["normal_exists"] = True
        ini_config["normal_path"] = os.path.join(export_path, "Texture", os.path.basename(context.scene.normal_selector))
    else:
        ini_config["normal_exists"] = False

    ### INI ###    
    ######## Buffer Override ##########
    buffer_override_content = ""

    if ini_config["diffuse_exists"]:
        buffer_override_content += "[Resource.DiffuseBackup]\n\n"
    if ini_config["metal_exists"]:
        buffer_override_content += "[Resource.MetalBackup]\n\n"
    if ini_config["normal_exists"]:
        buffer_override_content += "[Resource.NormalBackup]\n\n"

    buffer_override_content += f"""[TextureOverride.VertexBuffer_{draw_call}_{vb0_hash}.Draw]
hash = {vb0_hash}
match_first_index = {obj["3DMigoto:FirstIndex"]}\n\n"""

    if ini_config["diffuse_exists"]:
        buffer_override_content += "Resource.DiffuseBackup = copy ps-t0\n"
    if ini_config["metal_exists"]:
        buffer_override_content += f"Resource.MetalBackup = copy ps-{metal_slot}\n"
    if ini_config["normal_exists"]:
        buffer_override_content += f"Resource.NormalBackup = copy ps-{normal_slot}\n"

    buffer_override_content += "\n"

    buffer_override_content += f"""vb0 = Resource.VertexBuffer_{draw_call}_{vb0_hash}
ib = Resource.IndexBuffer_{draw_call}\n"""

    if ini_config["diffuse_exists"]:
        buffer_override_content += f"ps-t0 = Resource.Diffuse_{draw_call}\n"
    if ini_config["metal_exists"]:
        buffer_override_content += f"ps-{metal_slot} = Resource.Metal_{draw_call}\n"
    if ini_config["normal_exists"]:
        buffer_override_content += f"ps-{normal_slot} = Resource.Normal_{draw_call}\n"

    buffer_override_content += "\n"

    buffer_override_content += """handling = skip
drawindexed = auto\n\n"""

    if ini_config["diffuse_exists"]:
        buffer_override_content += "ps-t0 = Resource.DiffuseBackup\n"
    if ini_config["metal_exists"]:
        buffer_override_content += f"ps-{metal_slot} = Resource.MetalBackup\n"
    if ini_config["normal_exists"]:
        buffer_override_content += f"ps-{normal_slot} = Resource.NormalBackup\n"

    buffer_override_content += "\n"

    buffer_override_content += f"""[TextureOverride.VertexBuffer_{vb0_hash}.Delete]
hash = {vb0_hash}
handling = skip\n\n"""

    ######## Resources ##########
    resources_content = ""

    resources_content += f"""[Resource.VertexBuffer_{draw_call}_{vb0_hash}]
type = buffer
stride = {obj['3DMigoto:VB0Stride']}
filename = {os.path.relpath(vb_path, export_path)}\n\n"""

    resources_content += f"""[Resource.IndexBuffer_{draw_call}]
type = buffer
format = {obj['3DMigoto:IBFormat']}
filename = {os.path.relpath(ib_path, export_path)}\n\n"""

    if ini_config["diffuse_exists"]:
        resources_content += f"""[Resource.Diffuse_{draw_call}]
filename = {os.path.relpath(ini_config['diffuse_path'], export_path)}\n\n"""
    if ini_config["metal_exists"]:
        resources_content += f"""[Resource.Metal_{draw_call}]
filename = {os.path.relpath(ini_config['metal_path'], export_path)}\n\n"""
    if ini_config["normal_exists"]:
        resources_content += f"""[Resource.Normal_{draw_call}]
filename = {os.path.relpath(ini_config['normal_path'], export_path)}\n\n"""
        
    ### WRITE ###
    if clean_ini:
        buffer_override_path = ini_path[::-1].replace("mod.ini"[::-1], "BufferOverride.ini"[::-1], 1)[::-1]
        resources_path = ini_path[::-1].replace("mod.ini"[::-1], "Resources.ini"[::-1], 1)[::-1]

        with open(buffer_override_path, "w") as file:
            buffer_override_content = f"namespace = {namespace}\n\n" + buffer_override_content
            file.write(buffer_override_content)

        with open(resources_path, "w") as file:
            resources_content = f"namespace = {namespace}\n\n" + resources_content
            file.write(resources_content)

    else:
        with open(ini_path, "w") as file:
            file.write(f"; ======= Overrides:\n\n{buffer_override_content}\n; ======= Resources:\n\n{resources_content}")