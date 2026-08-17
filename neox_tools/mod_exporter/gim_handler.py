#####################################
# GIM ENCODE TOGGLE
#====================================
ENCODE_GIM_FILE = False
#####################################

import os
import xml.etree.ElementTree as ET
from .xml_converter import parse_handler, convert_handler, io_handler
from ..socket_operations.visualize_socket_ops import (
    custom_sockets_for_export,
    deleting_sockets_for_export,
)


def _next_socket_index(socket_objects: ET.Element) -> int:
    max_index = -1
    for child in socket_objects:
        _prefix, separator, suffix = child.tag.rpartition("_")
        if separator and suffix.isdigit():
            max_index = max(max_index, int(suffix))
    return max_index + 1


def _append_custom_sockets(decoded_gim_data: ET.Element, armature) -> int:
    custom_sockets = custom_sockets_for_export(armature)
    if not custom_sockets:
        return 0

    socket_objects = decoded_gim_data.find("SocketObject")
    if socket_objects is None:
        socket_objects = ET.SubElement(decoded_gim_data, "SocketObject")

    socket_index = _next_socket_index(socket_objects)
    for socket in custom_sockets:
        attributes = dict(socket.get("attributes", {}))
        binding_bone = str(socket.get("binding_bone", "")).strip()
        attributes["BindingBone"] = binding_bone
        attributes.setdefault("Name", str(socket.get("name", "")).strip() or f"Socket_{socket_index}")
        attributes.setdefault("BindType", str(socket.get("bind_type", "7")))
        attributes.setdefault("BindingFlag", str(socket.get("binding_flag", "2")))
        attributes.setdefault("PlayRatePolicy", "1")
        attributes.setdefault("PreloadingLevel", "4294967295")
        attributes.setdefault("SubmeshSortIdx", "4294967295")
        attributes.setdefault("SyncVo", "false")

        socket_element = ET.SubElement(
            socket_objects,
            f"Socket_{socket_index}",
            attributes,
        )
        socket_index += 1

        for child in socket.get("objects", []):
            child_tag = str(child.get("tag", "Object") or "Object")
            child_attributes = dict(child.get("attributes", {}))
            ET.SubElement(socket_element, child_tag, child_attributes)

    return len(custom_sockets)


def _delete_marked_sockets(decoded_gim_data: ET.Element, armature) -> int:
    deleting = deleting_sockets_for_export(armature)
    if not deleting:
        return 0

    socket_objects = decoded_gim_data.find("SocketObject")
    if socket_objects is None:
        return 0

    deleted = 0
    for socket_element in list(socket_objects):
        socket_name = str(socket_element.attrib.get("Name", "")).strip()
        binding_bone = str(socket_element.attrib.get("BindingBone", "")).strip()
        if (binding_bone, socket_name) in deleting:
            socket_objects.remove(socket_element)
            deleted += 1

    return deleted


def _clean_export_only_gim_references(decoded_gim_data: ET.Element) -> None:
    decoded_gim_data.attrib.pop("Mesh", None)

    mtg_file = decoded_gim_data.find("MtgFile")
    if mtg_file is not None:
        mtg_file.attrib["MtgPath"] = ""


def gim_handler(export_path:str, rig_info:dict, armature, asset_stem="main"):
    # element_tags, attribute_map = parse_handler.parseCustomBinFormat(rig_info["gim"])
    if parse_handler.typeFile(rig_info["gim"]) == "Binary":
        element_tags, attribute_map = parse_handler.parseCustomBinFormat(rig_info["gim"])
        decoded_gim_data:list[ET.Element] = convert_handler.tagWrapper(element_tags, attribute_map)[0]
    else:
        decoded_gim_data:list[ET.Element] = ET.parse(rig_info["gim"]).getroot()

    # Mesh
    n = 0
    submesh = decoded_gim_data.find("SubMesh")
    submesh.clear()
    for child in armature.children_recursive:        
        if child.type == 'MESH':            
            # The offset is 50 for NeoX3 fix
            ET.SubElement(submesh, f"Sub{n}", {"BoundingCenter":"0.0001,15.3959,0.3956", "BoundingHalf":"1.2504,1.3515,0.8151", "ForceBatch":"false", "IsSkin4S":"false", "MtlIdx":f"{n}", "Name":child.name, "RenderGroup":"0", "RenderOffset":"0", "ShadowBias":"0", "ShadowNormalBias":"0"})
            n += 1

    # Rig
    decoded_gim_data.find("SkeletonFile").find("FileName").attrib["Value"] = rig_info["skeleton"]
    decoded_gim_data.find("AnimationConfigFile").find("FileName").attrib["Value"] = rig_info["animconfig"]
    _clean_export_only_gim_references(decoded_gim_data)
    _delete_marked_sockets(decoded_gim_data, armature)
    _append_custom_sockets(decoded_gim_data, armature)
    
    if ENCODE_GIM_FILE:
        io_handler.ExportGim(
                file_path=os.path.join(export_path, f"{asset_stem}.gim"),
                gim_data=convert_handler.xml_to_custom_bin(
                    convert_handler.xml_to_bfs_list(decoded_gim_data)
                )
            )
    else: 
        io_handler.ExportUndecodedGim(
            file_path=os.path.join(export_path, f"{asset_stem}.gim"),
            gim_data=decoded_gim_data
        )
    
    return os.path.join(export_path, f"{asset_stem}.gim")
