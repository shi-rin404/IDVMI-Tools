import os
import xml.etree.ElementTree as ET
from . import convert_handler as ch

def ImportXML(file_path:os.PathLike):
    # Single Root Support
    tree = ET.parse(file_path)
    
    return tree.getroot()

def ExportXML(element_tags:list, attribute_map:list, file_path:os.PathLike) -> None:
    roots = ch.tagWrapper(element_tags, attribute_map)

    if os.path.exists(file_path):
        os.remove(file_path)
     
    with open(file_path, "a") as f:
        for root in roots:
            ET.indent(root, space="    ")
            f.write(f"{ET.tostring(root, encoding='unicode')}\n")

def ExportGim(file_path:os.PathLike, gim_data:bytearray) -> None:
    if os.path.exists(file_path):
        os.remove(file_path)

    with open(file_path, "wb") as f:
        f.write(gim_data)

def ExportUndecodedGim(file_path:os.PathLike, gim_data:ET.Element) -> None:
    # tree = ET.ElementTree(gim_data)
    # tree.write(file_path, encoding="utf-8", xml_declaration=True)
    with open(file_path, "w") as file:
        ET.indent(gim_data, space="    ")
        file.write(f"{ET.tostring(gim_data, encoding='unicode')}\n")