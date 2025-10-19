import xml.etree.ElementTree as ET
from collections import deque
from . import byte_handler as bh
from .sub_convert_handler import convert_helper as chelper

# Multiple roots support
def tagWrapper(element_tags: list[tuple[str, int]], attribute_map:list):
    roots = []  # multiple roots support
    queue = deque()
    index = 0

    while index < len(element_tags):
        tag, child_number = element_tags[index]
        attributes = attribute_map[index]
        element_tag = ET.Element(tag, attributes)

        if not queue:  # no parent → new root
            roots.append(element_tag)
        else:
            while queue and queue[0][1] == 0:
                queue.popleft()
            parent, remain = queue[0]
            parent.append(element_tag)
            queue[0] = (parent, remain - 1)

        if child_number:
            queue.append((element_tag, child_number))

        index += 1

    return roots  # <-- now returns a list of roots

def xml_to_bfs_list(root: ET.Element):
    """
    Converts the XML tree to BFS sorted [(tag, child_count)] list.
    """
    bfs_list = []
    queue = deque([root])

    while queue:
        node = queue.popleft()
        tag_name = node.tag
        child_count = len(node)
        attributes = dict(node.attrib)

        bfs_list.append((tag_name, child_count, attributes))

        for child in node:
            queue.append(child)

    return bfs_list

def xml_to_custom_bin(bfs_list:list) -> bytearray:
    output = bytearray()

    output += b"\xC1\x59\x41\x0D"

    output += bytearray(8)

    element_definitions = sorted(chelper.deduplicate_definitions(bfs_list, _type='element'))

    output += bh.writeLEB128(len(element_definitions))

    output += b"".join(definition.encode("utf-8") + b"\x00" for definition in element_definitions)    

    attribute_definitions = sorted(chelper.deduplicate_definitions(bfs_list, _type='attribute'))

    output += bh.writeLEB128(len(attribute_definitions))

    output += b"".join(definition.encode("utf-8") + b"\x00" for definition in attribute_definitions)

    attribute_offset_index = len(output)

    output += bytearray(8)

    output += bh.writeLEB128(len(bfs_list))

    for element in bfs_list:
        output += bh.writeLEB128(element_definitions.index(element[0]))
        output += bh.writeLEB128(element[1])

    output[attribute_offset_index:attribute_offset_index+8] = bh.writeuint64(len(output)-12)

    for attribute_chunk in bfs_list:
        output += bh.writeuint8(len(attribute_chunk[2]))
        for attribute in sorted(attribute_chunk[2]):
            output += bh.writeuint8(attribute_definitions.index(attribute))
            attribute_data = attribute_chunk[2][attribute]
            if attribute_data.replace("-", "", 1).isnumeric():
                # Integer
                if "-" in attribute_data or ("Shadow" in attribute and "Bias" in attribute):
                    # Signed Integer
                    output += b"\x05"
                    output += bh.writeint32(int(attribute_data))
                elif attribute == "Id":
                    # Unsigned Integer64
                    output += b"\x08"
                    output += bh.writeuint64(int(attribute_data))
                else:
                    # Unsigned Integer32
                    output += b"\x02"
                    output += bh.writeuint32(int(attribute_data))
            elif all(item.replace(".", "", 1).replace("-","", 1).isnumeric() for item in attribute_data.split(",")):
                # Float Matrix
                output += b"\x06"
                float_matrix = attribute_data.split(",")
                output += bh.writeuint32(len(float_matrix))
                for float_data in float_matrix:
                    output += bh.writefloat32(float(float_data))
            # elif attribute_chunk[2][attribute].isalpha() or any(sign in attribute_chunk[2][attribute] for sign in "_ ()\\/"):
            else:
                # String
                output += b"\x01" + attribute_data.encode("utf-8") + b"\x00"
        output += b"\x01\x00" # End Flag

    output = output[:4] + bh.writeuint64(len(output)) + output[12:]

    return output