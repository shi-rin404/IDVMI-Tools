import base64
import json
import os
import re
import shutil

import bpy
from bpy.types import Object, Operator

from . import shader_textures


def ini_maker_combined(
        Operator: Operator,
        entries: list[dict],
        vb0_hash: str,
        vb_path: os.PathLike,
        ib_path: os.PathLike,
        strides: dict,
        export_path: os.PathLike,
        ini_path: os.PathLike,
        frame_dump_path: os.PathLike,
        context,
        namespace: str = "",
        clean_ini: bool = False,
):
    if not entries:
        raise ValueError("No exported mesh entries were provided")

    obj = entries[0]["obj"]
    vb0_stride = obj["3DMigoto:VB0Stride"]
    ib_format = obj["3DMigoto:IBFormat"]

    buffer_override_content = f"""[TextureOverride.VertexBuffer_{vb0_hash}]
hash = {vb0_hash}
match_priority = 1
vb0 = Resource.VertexBuffer_{vb0_hash}
ib = Resource.IndexBuffer_vb0_{vb0_hash}

"""

    diffuse_paths = {}
    for material_index, entry in enumerate(entries, start=3):
        draw_call = entry["draw_call"]
        t0_path = texture_grabber(entry["obj"])
        diffuse_path = None
        if t0_path:
            diffuse_path = _copy_texture(t0_path, export_path)
            diffuse_paths[draw_call] = diffuse_path
            buffer_override_content += f"[Resource.DiffuseBackup_{draw_call}]\n\n"

        buffer_override_content += f"""[TextureOverride.{draw_call}_{vb0_hash}]
hash = {vb0_hash}
match_priority = {material_index}
match_first_index = {entry["obj"]["3DMigoto:FirstIndex"]}

"""

        if diffuse_path:
            buffer_override_content += f"""Resource.DiffuseBackup_{draw_call} = copy ps-t0
ps-t0 = Resource.Diffuse_{draw_call}

"""

        buffer_override_content += f"""
handling = skip
drawindexed = {entry["index_count"]}, {entry["start_index"]}, 0

"""

        if diffuse_path:
            buffer_override_content += f"""ps-t0 = Resource.DiffuseBackup_{draw_call}

"""

    if context.scene.clear_unused_materials:
        buffer_override_content += f"""[TextureOverride.{vb0_hash}_Clear]
hash = {vb0_hash}
match_priority = 2
handling = skip

"""

    resources_content = f"""[Resource.VertexBuffer_{vb0_hash}]
type = buffer
stride = {vb0_stride}
filename = {os.path.relpath(vb_path, export_path)}

[Resource.IndexBuffer_vb0_{vb0_hash}]
type = buffer
format = {ib_format}
filename = {os.path.relpath(ib_path, export_path)}

"""

    for entry in entries:
        draw_call = entry["draw_call"]
        diffuse_path = diffuse_paths.get(draw_call)
        if not diffuse_path:
            continue
        resources_content += f"""[Resource.Diffuse_{draw_call}]
filename = {os.path.relpath(diffuse_path, export_path)}

"""

    if clean_ini:
        buffer_override_path = ini_path[::-1].replace("mod.ini"[::-1], "BufferOverride.ini"[::-1], 1)[::-1]
        resources_path = ini_path[::-1].replace("mod.ini"[::-1], "Resources.ini"[::-1], 1)[::-1]

        with open(buffer_override_path, "w") as file:
            file.write(f"namespace = {namespace}\n\n{buffer_override_content}")

        with open(resources_path, "w") as file:
            file.write(f"namespace = {namespace}\n\n{resources_content}")
    else:
        with open(ini_path, "w") as file:
            file.write(f"; ======= Overrides:\n\n{buffer_override_content}\n; ======= Resources:\n\n{resources_content}")


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
    ini_maker_many(
        Operator,
        [{
            "draw_call": draw_call,
            "vb0_hash": vb0_hash,
            "vb_path": vb_path,
            "ib_path": ib_path,
            "obj": obj,
        }],
        export_path,
        ini_path,
        frame_dump_path,
        context,
        namespace,
        clean_ini,
    )


def ini_maker_many(
        Operator: Operator,
        entries: list[dict],
        export_path: os.PathLike,
        ini_path: os.PathLike,
        frame_dump_path: os.PathLike,
        context,
        namespace: str = "",
        clean_ini: bool = False,
):
    files = os.listdir(frame_dump_path)
    texture_usage = _load_texture_usage(frame_dump_path, files)
    multi_entry = len(entries) > 1

    buffer_override_content = ""
    resources_content = ""
    delete_override_content = ""
    delete_override_hashes = set()

    for entry in entries:
        entry_overrides, entry_resources = _make_ini_entry(
            entry["draw_call"],
            entry["vb0_hash"],
            entry["vb_path"],
            entry["ib_path"],
            export_path,
            frame_dump_path,
            files,
            texture_usage,
            context,
            entry["obj"],
            backup_suffix=f"_{entry['draw_call']}" if multi_entry else "",
        )
        buffer_override_content += entry_overrides
        resources_content += entry_resources

        vb0_hash = entry["vb0_hash"]
        if vb0_hash not in delete_override_hashes:
            delete_override_content += f"""[TextureOverride.VertexBuffer_{vb0_hash}.Delete]
hash = {vb0_hash}
handling = skip

"""
            delete_override_hashes.add(vb0_hash)

    buffer_override_content += delete_override_content

    if clean_ini:
        buffer_override_path = ini_path[::-1].replace("mod.ini"[::-1], "BufferOverride.ini"[::-1], 1)[::-1]
        resources_path = ini_path[::-1].replace("mod.ini"[::-1], "Resources.ini"[::-1], 1)[::-1]

        with open(buffer_override_path, "w") as file:
            file.write(f"namespace = {namespace}\n\n{buffer_override_content}")

        with open(resources_path, "w") as file:
            file.write(f"namespace = {namespace}\n\n{resources_content}")
    else:
        with open(ini_path, "w") as file:
            file.write(f"; ======= Overrides:\n\n{buffer_override_content}\n; ======= Resources:\n\n{resources_content}")


def _make_ini_entry(
        draw_call: str,
        vb0_hash: str,
        vb_path: os.PathLike,
        ib_path: os.PathLike,
        export_path: os.PathLike,
        frame_dump_path: os.PathLike,
        files: list[str],
        texture_usage: dict | None,
        context,
        obj: Object,
        backup_suffix: str = "",
):
    hashes = _texture_hashes(draw_call, files, texture_usage)
    ini_config = {}

    t0_path = texture_grabber(obj)
    if t0_path:
        ini_config["diffuse_exists"] = True
        ini_config["diffuse_path"] = _copy_texture(t0_path, export_path)
    else:
        ini_config["diffuse_exists"] = False

    metal_slot = f"t{context.scene.metal_slot_selector}"
    normal_slot = f"t{context.scene.normal_slot_selector}"
    metal_hash = hashes.get(metal_slot)
    normal_hash = hashes.get(normal_slot)

    if not context.scene.custom_metal and metal_hash:
        metal_path = os.path.join(_ensure_texture_dir(export_path), f"{metal_hash}.dds")
        with open(metal_path, "wb") as file:
            file.write(base64.b64decode(shader_textures.default_metal))
        ini_config["metal_exists"] = True
        ini_config["metal_path"] = metal_path
    elif context.scene.custom_metal and context.scene.metal_selector:
        ini_config["metal_exists"] = True
        ini_config["metal_path"] = _copy_texture(bpy.path.abspath(context.scene.metal_selector), export_path)
    else:
        ini_config["metal_exists"] = False

    if not context.scene.custom_normal and normal_hash:
        normal_path = os.path.join(_ensure_texture_dir(export_path), f"{normal_hash}.dds")
        with open(normal_path, "wb") as file:
            file.write(base64.b64decode(shader_textures.default_normal))
        ini_config["normal_exists"] = True
        ini_config["normal_path"] = normal_path
    elif context.scene.custom_normal and context.scene.normal_selector:
        ini_config["normal_exists"] = True
        ini_config["normal_path"] = _copy_texture(bpy.path.abspath(context.scene.normal_selector), export_path)
    else:
        ini_config["normal_exists"] = False

    diffuse_backup = f"Resource.DiffuseBackup{backup_suffix}"
    metal_backup = f"Resource.MetalBackup{backup_suffix}"
    normal_backup = f"Resource.NormalBackup{backup_suffix}"

    buffer_override_content = ""

    if ini_config["diffuse_exists"]:
        buffer_override_content += f"[{diffuse_backup}]\n\n"
    if ini_config["metal_exists"]:
        buffer_override_content += f"[{metal_backup}]\n\n"
    if ini_config["normal_exists"]:
        buffer_override_content += f"[{normal_backup}]\n\n"

    buffer_override_content += f"""[TextureOverride.VertexBuffer_{draw_call}_{vb0_hash}.Draw]
hash = {vb0_hash}
match_first_index = {obj["3DMigoto:FirstIndex"]}

"""

    if ini_config["diffuse_exists"]:
        buffer_override_content += f"{diffuse_backup} = copy ps-t0\n"
    if ini_config["metal_exists"]:
        buffer_override_content += f"{metal_backup} = copy ps-{metal_slot}\n"
    if ini_config["normal_exists"]:
        buffer_override_content += f"{normal_backup} = copy ps-{normal_slot}\n"

    buffer_override_content += "\n"

    buffer_override_content += f"""vb0 = Resource.VertexBuffer_{draw_call}_{vb0_hash}
ib = Resource.IndexBuffer_{draw_call}
"""

    if ini_config["diffuse_exists"]:
        buffer_override_content += f"ps-t0 = Resource.Diffuse_{draw_call}\n"
    if ini_config["metal_exists"]:
        buffer_override_content += f"ps-{metal_slot} = Resource.Metal_{draw_call}\n"
    if ini_config["normal_exists"]:
        buffer_override_content += f"ps-{normal_slot} = Resource.Normal_{draw_call}\n"

    buffer_override_content += "\n"
    buffer_override_content += "handling = skip\ndrawindexed = auto\n\n"

    if ini_config["diffuse_exists"]:
        buffer_override_content += f"ps-t0 = {diffuse_backup}\n"
    if ini_config["metal_exists"]:
        buffer_override_content += f"ps-{metal_slot} = {metal_backup}\n"
    if ini_config["normal_exists"]:
        buffer_override_content += f"ps-{normal_slot} = {normal_backup}\n"

    buffer_override_content += "\n"

    resources_content = f"""[Resource.VertexBuffer_{draw_call}_{vb0_hash}]
type = buffer
stride = {obj['3DMigoto:VB0Stride']}
filename = {os.path.relpath(vb_path, export_path)}

[Resource.IndexBuffer_{draw_call}]
type = buffer
format = {obj['3DMigoto:IBFormat']}
filename = {os.path.relpath(ib_path, export_path)}

"""

    if ini_config["diffuse_exists"]:
        resources_content += f"""[Resource.Diffuse_{draw_call}]
filename = {os.path.relpath(ini_config['diffuse_path'], export_path)}

"""
    if ini_config["metal_exists"]:
        resources_content += f"""[Resource.Metal_{draw_call}]
filename = {os.path.relpath(ini_config['metal_path'], export_path)}

"""
    if ini_config["normal_exists"]:
        resources_content += f"""[Resource.Normal_{draw_call}]
filename = {os.path.relpath(ini_config['normal_path'], export_path)}

"""

    return buffer_override_content, resources_content


def _load_texture_usage(frame_dump_path, files):
    texture_usage_path = None
    if "TextureUsage.json" in files:
        texture_usage_path = os.path.join(frame_dump_path, "TextureUsage.json")
    else:
        character_usage_path = os.path.join(frame_dump_path, "Character", "TextureUsage.json")
        if os.path.isfile(character_usage_path):
            texture_usage_path = character_usage_path

    if not texture_usage_path:
        return None

    with open(texture_usage_path, "r") as file:
        return json.load(file)


def _texture_hashes(draw_call, files, texture_usage):
    hashes = {}

    if texture_usage:
        for slot, texture_hash in texture_usage.get(draw_call, {}).items():
            if re.fullmatch(r"t\d+", slot) and texture_hash:
                hashes[slot] = texture_hash
        return hashes

    for file in files:
        result = re.search(fr"{draw_call}(\.\d+-\[.*?\])*-ps-t(\d+)=([a-f0-9]{{8}})", file, re.IGNORECASE)
        if result:
            hashes[f"t{result.group(2)}"] = result.group(3)

    return hashes


def _ensure_texture_dir(export_path):
    texture_dir = os.path.join(export_path, "Texture")
    if not os.path.isdir(texture_dir):
        os.mkdir(texture_dir)
    return texture_dir


def _copy_texture(texture_path, export_path):
    texture_dir = _ensure_texture_dir(export_path)
    destination = os.path.join(texture_dir, os.path.basename(texture_path))
    if os.path.abspath(texture_path) != os.path.abspath(destination):
        shutil.copy(texture_path, destination)
    return destination


def texture_grabber(obj):
    if len(obj.data.materials) > 0:
        material = obj.data.materials[0]

        if material and material.use_nodes:
            for node in material.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    return bpy.path.abspath(node.image.filepath)

    return None
