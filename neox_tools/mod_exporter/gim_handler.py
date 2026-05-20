#####################################
# GIM ENCODE TOGGLE
#====================================
ENCODE_GIM_FILE = False
#####################################

import os
import xml.etree.ElementTree as ET
from .xml_converter import parse_handler, convert_handler, io_handler

def gim_handler(export_path:str, rig_info:dict, armature):
    # element_tags, attribute_map = parse_handler.parseCustomBinFormat(rig_info["gim"])
    if parse_handler.typeFile(rig_info["gim"]) == "Binary":
        element_tags, attribute_map = parse_handler.parseCustomBinFormat(rig_info["gim"])
        decoded_gim_data:list[ET.Element] = convert_handler.tagWrapper(element_tags, attribute_map)[0]
    else:
        decoded_gim_data:list[ET.Element] = ET.parse(rig_info["gim"])    

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
    
    if ENCODE_GIM_FILE:
        io_handler.ExportGim(
                file_path=os.path.join(export_path, "main.gim"),
                gim_data=convert_handler.xml_to_custom_bin(
                    convert_handler.xml_to_bfs_list(decoded_gim_data)
                )
            )
    else: 
        io_handler.ExportUndecodedGim(
            file_path=os.path.join(export_path, "main.gim"),
            gim_data=decoded_gim_data
        )
    
    return os.path.join(export_path, "main.gim")