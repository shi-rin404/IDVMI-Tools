from typing import BinaryIO, Callable, Literal
import os
from . import byte_handler as bh
from .sub_parse_handler import attributeFunctions as af

def typeFile(file_path:os.PathLike) -> Literal['Binary', 'XML']:
    with open(file_path, "rb") as f:
        first_four_chars = f.read(4)
        
        if first_four_chars == b"\xC1\x59\x41\x0D":
            return 'Binary'
        elif first_four_chars.decode(encoding="utf-8") == "<Neo":
            return 'XML'
        else:
            raise Exception("File format error. Check your file <:")

def readUnknownLenInt(value:list[bytes]) -> int:
    bytes_value = b"".join(value)

    readFunctions = {1: bh.readuint8, 2: bh.readuint16, 4: bh.readuint32, 8: bh.readuint64}

    data_size = len(bytes_value)

    if data_size in readFunctions:
        return readFunctions[data_size](bytes_value)
    else:
        raise ValueError("Unsupported parameter amount format")

# def getParameterAmount(file:BinaryIO) -> int:
#     parameter_amount = []

#     while parameter_amount == [] or not parameter_amount[-1].isalpha():
#         parameter_amount.append(file.read(1))
    
#     parameter_amount.pop(-1)
#     file.seek(-1, 1)

#     if parameter_amount[-1] == b"\x01":
#         parameter_amount.pop(-1)

#     return readUnknownLenInt(parameter_amount)

def getParameters(parameter_amount:int, file:BinaryIO) -> list:
    parameter_found = 0
    theParameterName = []
    parameter_list = []

    while parameter_found < parameter_amount:
        while theParameterName == [] or theParameterName[-1] != b"\x00":
            theParameterName.append(file.read(1))
        for n, byte in enumerate(theParameterName[:-1]):
            theParameterName[n] = byte.decode(encoding="utf-8")
        parameter_list.append("".join(theParameterName[:-1]))
        theParameterName.clear()
        parameter_found += 1

    return parameter_list

def getElementTags(element_list:list, element_amount:int, file:BinaryIO) -> list:
    element_tags = []

    for _ in range(element_amount):
        element_ID, child_count = bh.readLEB128(file), bh.readLEB128(file)
        element_tags.append((element_list[element_ID], child_count)) # {element_name : child_count}
    
    return element_tags

def getAttributes(element_list_len:int, attribute_list:list, file:BinaryIO):
    data_types = {b"\x01": af.stringAttribute, b"\x02":af.unsignedInteger32Attribute, b"\x05":af.signedInteger32Attribute, b"\x06":af.matrixAttribute, b"\x08":af.unsignedInteger64Attribute}
    collected_attributes = []
    for element_number in range(element_list_len):
        attribute_amount = file.read(1)[0]
        collected_attributes.append({})
        for attribute_number in range(attribute_amount):
                attribute_ID = file.read(1)[0]
                data_type = file.read(1)
                if data_type in data_types:
                    collected_attributes[element_number][attribute_list[attribute_ID]] = data_types[data_type](file)
                else:
                    print(f"New data type code: {data_type.hex().upper()}")
                    print("Report this issue. Cya!")
                    import sys
                    sys.exit()
        if file.read(2) == b"\x01\x00":
            continue
        else:
            raise Exception("wtf")

    return collected_attributes

def parseCustomBinFormat(filepath:os.PathLike) -> tuple:
    
    with open(filepath, "rb") as f:
   
        if not f.read(4) == b"\xC1\x59\x41\x0D":
            raise ValueError("Invalid file format")
        
        file_size = f.read(8) # uint64
        
        # element_list = getParameters(getParameterAmount(f), f)
        element_def_amount = bh.readLEB128(f)

        element_list = getParameters(element_def_amount, f)

        # attribute_list = getParameters(getParameterAmount(f), f)
        attribute_def_amount = bh.readLEB128(f)
        attribute_list = getParameters(attribute_def_amount, f)
                
        attributes_offset = f.read(8) # uint64 | starts from 12th index (after header), so you can reach attributes if you go to 12 + {attributes_offset}th index.
        
        tag_amount = bh.readLEB128(f)
        # tag_amount = bh.readuint8(f.read(1))

        element_tags = getElementTags(element_list, tag_amount, f)

        attribute_map = getAttributes(tag_amount, attribute_list, f)

    return element_tags, attribute_map