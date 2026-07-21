#####################################
# GIM ENCODE TOGGLE
#====================================
ENCODE_GIM_FILE = False
#
#####################################

from copy import deepcopy
import re, shutil, os, base64
import xml.etree.ElementTree as ET
import bpy
from ...export_mod import shader_textures
from ...export_mod import ini_maker
from ..export_ops import get_armature
from .xml_converter import io_handler, convert_handler

_MULTI_MATCH_RE = re.compile(r"(?:[a-z]*?_[cde]_[a-z]*?)_(?:[a-z0-9]+_(mask|nor))", re.IGNORECASE)

def _material_texture_path(material):
    if not material or not material.use_nodes:
        return None

    for node in material.node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image:
            path = bpy.path.abspath(node.image.filepath)
            if path and os.path.isfile(path):
                return path
    return None

def _shader_textures_from_mesh_materials(mesh_obj):
    expected_names = {
        f"TexNormal_{mesh_obj.name}": "TexNormal",
        f"TexMetal_{mesh_obj.name}": "TexMetal",
    }
    found = {}

    for material in mesh_obj.data.materials:
        if material is None or material.name not in expected_names:
            continue

        texture_path = _material_texture_path(material)
        if texture_path:
            found[expected_names[material.name]] = texture_path

    return found

def texture_handler(export_path, context, operator):
    armature = get_armature(context, operator)
    
    material_template_path = os.path.join(os.path.dirname(__file__), "tex_resource", "initial_shading.mtl")
    default_material_data = ET.parse(material_template_path)

    materials_root_path = os.path.join(export_path, "materials")
    if not os.path.exists(materials_root_path):
        os.mkdir(materials_root_path)

    texture_root_path = os.path.join(export_path, "textures")
    if not os.path.exists(texture_root_path):
        os.mkdir(texture_root_path)

    for child in armature.children_recursive:
        if not os.path.exists(os.path.join(texture_root_path, child.name)):
            os.mkdir(os.path.join(texture_root_path, child.name))
        
        if child.type == 'MESH':
            base_diffuse_path:str|None = ini_maker.texture_grabber(child)

            if base_diffuse_path:
                tex_file_name = os.path.basename(base_diffuse_path)
                destination = os.path.join(texture_root_path, child.name, tex_file_name)
                shutil.copy(base_diffuse_path, destination)

                diffuse_path = destination
            else:
                diffuse_path = None

            material_data = deepcopy(default_material_data.getroot())
            
            material_data.find("Material").attrib["Name"] = child.name

            if diffuse_path:
                material_data.find("Material").find("ParamTable").find("Tex0").attrib["Value"] = diffuse_path.split("res\\", 1)[1].replace("\\", "/")
            else:
                material_data.find("Material").find("ParamTable").find("Tex0").attrib["Value"] = "please/add/texture.dds"
            
            found_textures = {}
            if base_diffuse_path:
                source_dir = os.path.dirname(base_diffuse_path)
                diffuse_stem = os.path.splitext(os.path.basename(base_diffuse_path))[0]
                base_prefix = diffuse_stem.rsplit("_", 1)[0]  # e.g. "yiyaoshi_e_yuhuo_body01"

                keywords = {"mask": "TexMetal", "nor": "TexNormal"}
                for keyword, shader_key in keywords.items():
                    candidates = [f for f in os.listdir(source_dir) if f.startswith(base_prefix) and keyword in f]
                    if len(candidates) == 1:
                        found_textures[shader_key] = os.path.join(source_dir, candidates[0])
                    elif len(candidates) > 1:
                        for fname in candidates:
                            m = _MULTI_MATCH_RE.search(os.path.splitext(fname)[0])
                            if m and m.group(1).lower() == keyword:
                                found_textures[shader_key] = os.path.join(source_dir, fname)
                                break

            found_textures.update(_shader_textures_from_mesh_materials(child))

            shader_defaults = {"TexMetal": shader_textures.default_metal, "TexNormal": shader_textures.default_normal}
            for shader_key in shader_defaults:
                if shader_key in found_textures:
                    src = found_textures[shader_key]
                    tex_path = os.path.join(texture_root_path, child.name, os.path.basename(src))
                    shutil.copy(src, tex_path)
                else:
                    tex_path = os.path.join(texture_root_path, child.name, f"{child.name}_{shader_key}.dds")
                    with open(tex_path, "wb") as tex_file:
                        tex_file.write(base64.b64decode(shader_defaults[shader_key]))

                material_data.find("Material").find("ParamTable").find(shader_key).attrib["Value"] = tex_path.split("res\\", 1)[1].replace("\\", "/")

            with open(os.path.join(materials_root_path, f"{child.name}.mtl"), "wb") as mat_file:
                mat_file.write(ET.tostring(material_data, encoding='utf-8', method='xml'))

    material_group_template_path = os.path.join(os.path.dirname(__file__), "tex_resource", "initial_material_group.mtg")
    material_group_path = os.path.join(export_path, "main.mtg")

    material_group = ET.parse(material_group_template_path).getroot()
    
    material_group.find("MaterialGroup").attrib["Name"] = "main"

    def get_mtl_files():
        ret = []
        for file in os.scandir(materials_root_path):
            if file.name.endswith(".mtl"):
                ret.append(file.name)
        return ret

    mtl_files = get_mtl_files()
    material_group.find("MaterialGroup").attrib["MaterialCount"] = str(len(mtl_files))

    try:
        for n, file in enumerate(mtl_files):
            rel_path = os.path.join(materials_root_path, file).split("res\\", 1)[1].replace("\\", "/")
            ET.SubElement(material_group.find("MaterialGroup"), f"Material_{n}", attrib={"Path": rel_path}) # The offset is 50 for NeoX3 Fix
    except IndexError:
        operator.report({'ERROR'}, "Your mod folder should be inside of 'res' folder!")
        return False

    
    
    if ENCODE_GIM_FILE:
        io_handler.ExportGim(
        material_group_path,
        convert_handler.xml_to_custom_bin(convert_handler.xml_to_bfs_list(material_group))
        )
    else: 
        io_handler.ExportUndecodedGim(
            file_path=material_group_path,
            gim_data=material_group
        )
    
    return True
