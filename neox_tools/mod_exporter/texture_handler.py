#####################################
# GIM ENCODE TOGGLE
#====================================
ENCODE_GIM_FILE = False
#
#####################################

from copy import deepcopy
import importlib
import re, shutil, os, base64
import xml.etree.ElementTree as ET
import bpy
from ..export_ops import get_armature
from .original_material_grabber import load_original_material_template, make_asset_index
from .xml_converter import io_handler, convert_handler

_top_pkg = __package__.split(".neox_tools.", 1)[0]
shader_textures = importlib.import_module(".3dm.export_mod.shader_textures", package=_top_pkg)
ini_maker = importlib.import_module(".3dm.export_mod.ini_maker", package=_top_pkg)

_MULTI_MATCH_RE = re.compile(r"(?:[a-z]*?_[cde]_[a-z]*?)_(?:[a-z0-9]+_(mask|nor))", re.IGNORECASE)

def _log(log, message: str) -> None:
    if log is None:
        return
    log.write(f"{message}\n")
    log.flush()


def _primary_mesh_material(mesh_obj):
    if len(mesh_obj.data.materials) == 0:
        return None
    return mesh_obj.data.materials[0]

def _texture_path_from_image_node(node):
    if node is None or node.type != 'TEX_IMAGE' or not node.image:
        return None

    path = bpy.path.abspath(node.image.filepath)
    if path and os.path.isfile(path):
        return path
    return None

def _material_texture_path_for_shader(material, shader_key: str):
    if not material or not material.use_nodes:
        return None

    nodes = material.node_tree.nodes
    preferred_names = (
        f"{shader_key}_Shader_Image",
        f"{shader_key}_Image",
    )
    for node_name in preferred_names:
        texture_path = _texture_path_from_image_node(nodes.get(node_name))
        if texture_path:
            return texture_path

    for node in nodes:
        if node.type != 'TEX_IMAGE':
            continue
        if node.label == shader_key or node.name.startswith(shader_key):
            texture_path = _texture_path_from_image_node(node)
            if texture_path:
                return texture_path
    return None

def _shader_textures_from_mesh_materials(mesh_obj, log=None):
    found = {}
    material = _primary_mesh_material(mesh_obj)
    if material is None:
        _log(log, f"    shader material lookup: {mesh_obj.name} has no material slot 0")
        return found

    for shader_key in ("TexNormal", "TexMetal"):
        texture_path = _material_texture_path_for_shader(material, shader_key)
        if texture_path:
            found[shader_key] = texture_path
            _log(log, f"    {shader_key}: using slot 0 material node -> {texture_path}")

    return found

def _material_param_table(material_root):
    param_table = material_root.find(".//Material/ParamTable")
    if param_table is None:
        param_table = material_root.find(".//ParamTable")
    return param_table

def _set_material_texture_value(material_root, shader_key: str, value: str) -> None:
    param_table = _material_param_table(material_root)
    if param_table is None:
        material = material_root.find(".//Material")
        if material is None:
            material = ET.SubElement(material_root, "Material")
        param_table = ET.SubElement(material, "ParamTable")

    texture = param_table.find(shader_key)
    if texture is None:
        texture = ET.SubElement(param_table, shader_key)
    texture.attrib["Value"] = value

def _set_material_name(material_root, name: str) -> None:
    material = material_root.find(".//Material")
    if material is None:
        material = ET.SubElement(material_root, "Material")
    material.attrib["Name"] = name

def texture_handler(export_path, context, operator, asset_stem="main", log=None):
    armature = get_armature(context, operator)
    grab_original_materials = bool(getattr(context.scene, "neox_mod_export_grab_original_materials", True))
    original_material_asset_index = None
    _log(log, "--- Export Textures ---")
    _log(log, f"Texture export path: {export_path}")
    _log(log, f"Grab original materials: {grab_original_materials}")
    if grab_original_materials:
        try:
            original_material_asset_index = make_asset_index()
            _log(log, "Original material asset index created.")
        except Exception as exc:
            grab_original_materials = False
            operator.report({'WARNING'}, f"Original material grab disabled: {exc}")
            _log(log, f"Original material grab disabled: {type(exc).__name__}: {exc}")
    
    material_template_path = os.path.join(os.path.dirname(__file__), "tex_resource", "initial_shading.mtl")
    default_material_data = ET.parse(material_template_path)

    materials_root_path = os.path.join(export_path, "materials")
    if not os.path.exists(materials_root_path):
        os.mkdir(materials_root_path)

    texture_root_path = os.path.join(export_path, "textures")
    if not os.path.exists(texture_root_path):
        os.mkdir(texture_root_path)

    material_files = []
    for child in armature.children_recursive:
        if child.type == 'MESH':
            _log(log, f"  Mesh: {child.name}")
            if not os.path.exists(os.path.join(texture_root_path, child.name)):
                os.mkdir(os.path.join(texture_root_path, child.name))
                _log(log, f"    texture directory created: {os.path.join(texture_root_path, child.name)}")

            material_data = None
            if grab_original_materials:
                try:
                    material_data = load_original_material_template(
                        child,
                        operator,
                        original_material_asset_index,
                        log,
                    )
                except Exception as exc:
                    operator.report(
                        {'WARNING'},
                        f"Original material grab failed for {child.name}: {exc}",
                    )
                    _log(log, f"    original material grab failed: {type(exc).__name__}: {exc}")

            if material_data is not None:
                _log(log, "    material source: original .mtl template")
            else:
                material_data = deepcopy(default_material_data.getroot())
                _log(log, "    material source: built-in default template")

            base_diffuse_path = (
                _material_texture_path_for_shader(_primary_mesh_material(child), "Tex0")
                or ini_maker.texture_grabber(child)
            )

            if base_diffuse_path:
                tex_file_name = os.path.basename(base_diffuse_path)
                destination = os.path.join(texture_root_path, child.name, tex_file_name)
                shutil.copy(base_diffuse_path, destination)
                _log(log, f"    Tex0: using Blender material texture -> {base_diffuse_path}")
                _log(log, f"    Tex0: copied to -> {destination}")

                diffuse_path = destination
            else:
                diffuse_path = None
                _log(log, "    Tex0: no Blender texture found; writing placeholder path")

            _set_material_name(material_data, child.name)

            if diffuse_path:
                _set_material_texture_value(
                    material_data,
                    "Tex0",
                    diffuse_path.split("res\\", 1)[1].replace("\\", "/"),
                )
            else:
                _set_material_texture_value(material_data, "Tex0", "please/add/texture.dds")
            
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
                        _log(log, f"    {shader_key}: directory candidate -> {found_textures[shader_key]}")
                    elif len(candidates) > 1:
                        for fname in candidates:
                            m = _MULTI_MATCH_RE.search(os.path.splitext(fname)[0])
                            if m and m.group(1).lower() == keyword:
                                found_textures[shader_key] = os.path.join(source_dir, fname)
                                _log(log, f"    {shader_key}: selected from {len(candidates)} directory candidates -> {found_textures[shader_key]}")
                                break

            found_textures.update(_shader_textures_from_mesh_materials(child, log))

            shader_defaults = {"TexMetal": shader_textures.default_metal, "TexNormal": shader_textures.default_normal}
            for shader_key in shader_defaults:
                if shader_key in found_textures:
                    src = found_textures[shader_key]
                    tex_path = os.path.join(texture_root_path, child.name, os.path.basename(src))
                    shutil.copy(src, tex_path)
                    _log(log, f"    {shader_key}: copied from -> {src}")
                    _log(log, f"    {shader_key}: copied to -> {tex_path}")
                else:
                    tex_path = os.path.join(texture_root_path, child.name, f"{child.name}_{shader_key}.dds")
                    with open(tex_path, "wb") as tex_file:
                        tex_file.write(base64.b64decode(shader_defaults[shader_key]))
                    _log(log, f"    {shader_key}: using built-in default -> {tex_path}")

                _set_material_texture_value(
                    material_data,
                    shader_key,
                    tex_path.split("res\\", 1)[1].replace("\\", "/"),
                )

            with open(os.path.join(materials_root_path, f"{child.name}.mtl"), "wb") as mat_file:
                mat_file.write(ET.tostring(material_data, encoding='utf-8', method='xml'))
            _log(log, f"    material output: {os.path.join(materials_root_path, f'{child.name}.mtl')}")
            material_files.append(f"{child.name}.mtl")

    material_group_template_path = os.path.join(os.path.dirname(__file__), "tex_resource", "initial_material_group.mtg")
    material_group_path = os.path.join(export_path, f"{asset_stem}.mtg")

    material_group = ET.parse(material_group_template_path).getroot()
    
    material_group.find("MaterialGroup").attrib["Name"] = asset_stem

    material_group.find("MaterialGroup").attrib["MaterialCount"] = str(len(material_files))
    _log(log, f"Material group count: {len(material_files)}")

    try:
        for n, file in enumerate(material_files):
            rel_path = os.path.join(materials_root_path, file).split("res\\", 1)[1].replace("\\", "/")
            ET.SubElement(material_group.find("MaterialGroup"), f"Material_{n}", attrib={"Path": rel_path}) # The offset is 50 for NeoX3 Fix
            _log(log, f"  Material_{n}: {rel_path}")
    except IndexError:
        operator.report({'ERROR'}, "Your mod folder should be inside of 'res' folder!")
        _log(log, "ERROR: Mod folder is not inside a res folder.")
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
    _log(log, f"Material group output: {material_group_path}")
    
    return True
