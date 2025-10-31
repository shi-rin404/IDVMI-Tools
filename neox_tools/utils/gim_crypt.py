from xml.etree import ElementTree as ET
from ...neox_tools.mod_exporter.xml_converter import parse_handler, convert_handler

def decode_gim_file(gim_path:str) -> ET.Element:
    """Decodes a GIM file and returns its XML ElementTree representation."""
    element_tags, attribute_map = parse_handler.parseCustomBinFormat(gim_path)

    roots = convert_handler.tagWrapper(element_tags, attribute_map)
    return roots[0]  # Assuming single root for GIM files

def encode_gim_file(root: ET.Element) -> bytearray:
    """Encodes an XML ElementTree representation of a GIM file back to its binary format."""
    bfs_list = convert_handler.xml_to_bfs_list(root)
    binary_data = convert_handler.xml_to_custom_bin(bfs_list)
    return binary_data