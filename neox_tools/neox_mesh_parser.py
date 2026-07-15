import struct
import numpy as np
from typing import Any, BinaryIO

def readuint8(f):
    return int(struct.unpack('B', f.read(1))[0])


def readuint16(f):
    return int(struct.unpack('H', f.read(2))[0])


def readuint32(f):
    return struct.unpack('I', f.read(4))[0]


def readfloat(f):
    return struct.unpack('<f', f.read(4))[0]

def read_bone_bounding_info(f) -> dict[str, Any]:
    raw = f.read(28)
    if len(raw) != 28:
        raise EOFError(
            f"Expected 28 BoneBoundingInfo bytes, received {len(raw)}"
        )

    values = struct.unpack("<7f", raw)
    return {
        "center": values[0:3],
        "half_length_x": values[3],
        "radius_y": values[4],
        "radius_z": values[5],
        "bound_radius": values[6],
    }

def read_bone_weight_usage_mask(f, bone_names: list[str]) -> dict[str, Any]:
    bit_count = readuint32(f)
    byte_count = (bit_count + 7) // 8
    flags = f.read(byte_count)

    if len(flags) != byte_count:
        raise EOFError(
            f"Expected {byte_count} usage-mask bytes, received {len(flags)}"
        )

    used_indices = []
    unused_indices = []
    usable_count = min(bit_count, len(bone_names))

    for bone_index in range(usable_count):
        byte_index = bone_index // 8
        bit_index = bone_index % 8
        is_used = bool(flags[byte_index] & (1 << bit_index))

        if is_used:
            used_indices.append(bone_index)
        else:
            unused_indices.append(bone_index)

    return {
        "bit_count": bit_count,
        "flags": flags,
        "raw": bit_count.to_bytes(4, "little") + flags,
        "used_indices": used_indices,
        "unused_indices": unused_indices,
        "used_names": [bone_names[index] for index in used_indices],
        "unused_names": [bone_names[index] for index in unused_indices],
    }

def parse_mesh_1(model: dict[str, Any], f: BinaryIO , operator) -> dict[str, Any]:
    _magic_number = f.read(8)

    # Read mesh version
    current_pos = f.tell()
    f.seek(4)
    model['mesh_version'] = readuint8(f)

    f.seek(12)
    model['bone_count'] = readuint8(f)
    f.seek(current_pos)  # Reset to position after magic number

    model['bone_exist'] = readuint32(f)
    model['mesh'] = []
    parent_nodes = []

    if model['bone_exist']:
        if model['bone_exist'] > 1:
            count = readuint8(f)
            f.read(2)
            f.read(count * 4)
        bone_count = readuint16(f)
        
        for _ in range(bone_count):
            parent_node = readuint16(f)
            if parent_node == 65535:
                parent_node = -1
            parent_nodes.append(parent_node)
        model['bone_parent'] = parent_nodes

        bone_names = []
        for _ in range(bone_count):
            bone_name = f.read(32)
            bone_name = bone_name.decode().replace('\0', '')
            bone_names.append(bone_name)
        model['bone_name'] = bone_names

        bone_binding_info = readuint8(f)
        if bone_binding_info:
            model['bounding_info'] = []
            model['bone_bounding_info'] = []
            for _ in range(bone_count):
                bounding_info = read_bone_bounding_info(f)
                model['bone_bounding_info'].append(bounding_info)
                model['bounding_info'].append(bounding_info)

        model['bone_matrix'] = []
        for _ in range(bone_count):
            matrix = [readfloat(f) for _ in range(16)]
            matrix = np.array(matrix).reshape(4, 4)
            model['bone_matrix'].append(matrix)

    if len(list(filter(lambda x: x == -1, parent_nodes))) > 1:
        num = len(model['bone_parent'])
        model['bone_parent'] = list(map(lambda x: num if x == -1 else x, model['bone_parent']))
        model['bone_parent'].append(-1)
        model['bone_name'].append('dummy_root')
        model['bone_matrix'].append(np.identity(4))

    has_binding_info = readuint8(f)
    if has_binding_info != 0:
        raise ValueError(f"Unexpected has_binding_info value {has_binding_info} at position {f.tell()}")

    table_offset = readuint32(f)
    while True:
        lod_new_v = readuint16(f)
        if lod_new_v == 1:
            break
        f.seek(-2, 1)
        mesh_vertex_count = readuint32(f)
        mesh_face_count = readuint32(f)
        uv_layers = readuint8(f)
        color_len = readuint8(f)

        model['mesh'].append((mesh_vertex_count, mesh_face_count, uv_layers, color_len))

    vertex_count = readuint32(f)
    face_count = readuint32(f)

    model['position'] = []
    # vertex position
    for _ in range(vertex_count):
        x = readfloat(f)
        y = readfloat(f)
        z = readfloat(f)
        model['position'].append((x, y, z))

    model['normal'] = []
    # vertex normal
    for _ in range(vertex_count):
        x = readfloat(f)
        y = readfloat(f)
        z = readfloat(f)
        model['normal'].append((x, y, z))

    has_tangent = readuint16(f)
    if has_tangent:
        f.seek(vertex_count * 12, 1)

    model['face'] = []
    # face index table
    for _ in range(face_count):
        v1 = readuint16(f)
        v2 = readuint16(f)
        v3 = readuint16(f)
        model['face'].append((v1, v2, v3))

    model['uv'] = []
    # vertex uv
    for mesh_vertex_count, _, uv_layers, _ in model['mesh']:
        if uv_layers > 0:
            for _ in range(mesh_vertex_count):
                u = readfloat(f)
                v = readfloat(f)
                model['uv'].append((u, v))
            f.read(mesh_vertex_count * 8 * (uv_layers - 1))
        else:
            for _ in range(mesh_vertex_count):
                u = 0.0
                v = 0.0
                model['uv'].append((u, v))        
    
    # vertex color
    for mesh_vertex_count, _, _, color_len in model['mesh']:
        f.read(mesh_vertex_count * 4 * color_len)

    if model['bone_exist']:
        model['vertex_bone'] = []
        for _ in range(vertex_count):
            vertex_bones = [readuint16(f) for _ in range(4)]
            model['vertex_bone'].append(vertex_bones)

        model['vertex_weight'] = []
        for _ in range(vertex_count):
            vertex_weights = [readfloat(f) for _ in range(4)]
            model['vertex_weight'].append(vertex_weights)        

    # footer: BoneWeightUsageMask
    if model['bone_exist']:
        model['bone_weight_usage'] = read_bone_weight_usage_mask(f, model['bone_name'])
        if f.tell() != table_offset:
            raise ValueError(
                "Bone usage mask does not end at table_offset: "
                f"current={f.tell()}, table_offset={table_offset}"
            )
        model['bone_tail'] = model['bone_weight_usage']['raw']
    else:
        bone_tail_size = table_offset - f.tell()
        model['bone_tail'] = f.read(bone_tail_size)
    
    f.seek(table_offset)

    model['lod_data_table'] = f.read(16)

    return model

def parse_mesh_2(model, f, operator):
    _magic_number = f.read(8)

    # Read mesh version
    current_pos = f.tell()
    f.seek(4)
    model['mesh_version'] = readuint8(f)

    f.seek(12)
    model['bone_count'] = readuint8(f)
    f.seek(current_pos)  # Reset to position after magic number

    model['bone_exist'] = readuint32(f)
    model['mesh'] = []

    if model['bone_exist']:
        if model['bone_exist'] == 1 or model['bone_exist'] == 4:
            count = readuint8(f)
            f.read(2)
            f.read(count * 4)
        bone_count = readuint16(f)
        parent_nodes = []
        for _ in range(bone_count):
            parent_node = readuint8(f)
            if parent_node == 255:
                parent_node = -1
            parent_nodes.append(parent_node)
        model['bone_parent'] = parent_nodes

        bone_names = []
        for _ in range(bone_count):
            bone_name = f.read(32)
            bone_name = bone_name.decode().replace('\0', '').replace(' ', '_')
            bone_names.append(bone_name)
        model['bone_name'] = bone_names

        bone_extra_info = readuint8(f)
        if bone_extra_info:
            model['bounding_info'] = []
            model['bone_bounding_info'] = []
            for _ in range(bone_count):
                bounding_info = read_bone_bounding_info(f)
                model['bone_bounding_info'].append(bounding_info)
                model['bounding_info'].append(bounding_info)

        model['bone_matrix'] = []
        for _ in range(bone_count):
            matrix = [readfloat(f) for _ in range(16)]
            matrix = np.array(matrix).reshape(4, 4)
            model['bone_matrix'].append(matrix)

        if len(list(filter(lambda x: x == -1, parent_nodes))) > 1:
            num = len(model['bone_parent'])
            model['bone_parent'] = list(map(lambda x: num if x == -1 else x, model['bone_parent']))
            model['bone_parent'].append(-1)
            model['bone_name'].append('dummy_root')
            model['bone_matrix'].append(np.identity(4))

        _flag = readuint8(f)
        assert _flag == 0

    table_offset = readuint32(f)

    while True:
        flag = readuint16(f)
        if flag == 1:
            break
        f.seek(-2, 1)
        mesh_vertex_count = readuint32(f)
        mesh_face_count = readuint32(f)
        uv_layers = readuint8(f)
        color_len = readuint8(f)

        model['mesh'].append((mesh_vertex_count, mesh_face_count, uv_layers, color_len))

    vertex_count = readuint32(f)
    face_count = readuint32(f)

    model['position'] = []
    # vertex position
    for _ in range(vertex_count):
        x = readfloat(f)
        y = readfloat(f)
        z = readfloat(f)
        model['position'].append((x, y, z))

    model['normal'] = []
    # vertex normal
    for _ in range(vertex_count):
        x = readfloat(f)
        y = readfloat(f)
        z = readfloat(f)
        model['normal'].append((x, y, z))

    _flag = readuint16(f)
    if _flag:
        f.seek(vertex_count * 12, 1)

    model['face'] = []
    # face index table
    for _ in range(face_count):
        v1 = readuint16(f)
        v2 = readuint16(f)
        v3 = readuint16(f)
        model['face'].append((v1, v2, v3))

    model['uv'] = []
    # vertex uv
    for mesh_vertex_count, _, uv_layers, _ in model['mesh']:
        if uv_layers > 0:
            for _ in range(mesh_vertex_count):
                u = readfloat(f)
                v = readfloat(f)
                model['uv'].append((u, v))
            f.read(mesh_vertex_count * 8 * (uv_layers - 1))
        else:
            for _ in range(mesh_vertex_count):
                u = 0.0
                v = 0.0
                model['uv'].append((u, v))

    # vertex color
    for mesh_vertex_count, _, _, color_len in model['mesh']:
        f.read(mesh_vertex_count * 4 * color_len)

    if model['bone_exist']:
        model['vertex_bone'] = []
        for _ in range(vertex_count):
            vertex_bones = [readuint8(f) for _ in range(4)]
            model['vertex_bone'].append(vertex_bones)

        model['vertex_weight'] = []

        for _ in range(vertex_count):
            vertex_weights = [readfloat(f) for _ in range(4)]
            model['vertex_weight'].append(vertex_weights)

    # footer: BoneWeightUsageMask
    if model['bone_exist']:
        model['bone_weight_usage'] = read_bone_weight_usage_mask(f, model['bone_name'])
        if f.tell() != table_offset:
            raise ValueError(
                "Bone usage mask does not end at table_offset: "
                f"current={f.tell()}, table_offset={table_offset}"
            )
        model['bone_tail'] = model['bone_weight_usage']['raw']
    else:
        bone_tail_size = table_offset - f.tell()
        model['bone_tail'] = f.read(bone_tail_size)

    f.seek(table_offset)

    model['lod_data_table'] = f.read(16)

    return model

def parse_mesh_3(model: dict[str, Any], f: BinaryIO, operator) -> dict[str, Any]:
    """Internal robust parsing implementation."""
    _magic_number = f.read(8)

    # Read mesh version
    current_pos = f.tell()
    f.seek(4)
    model['mesh_version'] = readuint8(f)

    f.seek(12)
    model['bone_count'] = readuint8(f)
    f.seek(current_pos)  # Reset to position after magic number

    model['bone_exist'] = readuint32(f)
    model['mesh'] = []

    if model['bone_exist']:
        if model['bone_exist'] > 1:
            count = readuint8(f)
            f.read(2)
            f.read(count * 4)
        bone_count = readuint16(f)
        
        parent_nodes = []
        for _ in range(bone_count):
            parent_node = readuint8(f)
            if parent_node == 255:
                parent_node = -1
            parent_nodes.append(parent_node)
        model['bone_parent'] = parent_nodes

        bone_names = []
        for _ in range(bone_count):
            bone_name = f.read(32)
            bone_name = bone_name.decode().replace('\0', '')
            bone_names.append(bone_name)
        model['bone_name'] = bone_names

        bone_binding_info = readuint8(f)
        if bone_binding_info:
            model['bounding_info'] = []
            model['bone_bounding_info'] = []
            for _ in range(bone_count):
                bounding_info = read_bone_bounding_info(f)
                model['bone_bounding_info'].append(bounding_info)
                model['bounding_info'].append(bounding_info)

        model['bone_matrix'] = []
        for _ in range(bone_count):
            matrix = [readfloat(f) for _ in range(16)]
            matrix = np.array(matrix).reshape(4, 4)
            model['bone_matrix'].append(matrix)

        if len(list(filter(lambda x: x == -1, parent_nodes))) > 1:
            num = len(model['bone_parent'])
            model['bone_parent'] = list(map(lambda x: num if x == -1 else x, model['bone_parent']))
            model['bone_parent'].append(-1)
            model['bone_name'].append('dummy_root')
            model['bone_matrix'].append(np.identity(4))

        _flag = readuint8(f)
        if _flag != 0:
            raise ValueError(f"Unexpected _flag value {_flag} at position {f.tell()}")
        
    table_offset = readuint32(f)

    while True:
        flag = readuint16(f)
        if flag == 1:
            break
        f.seek(-2, 1)
        mesh_vertex_count = readuint32(f)
        mesh_face_count = readuint32(f)
        uv_layers = readuint8(f)
        color_len = readuint8(f)

        model['mesh'].append((mesh_vertex_count, mesh_face_count, uv_layers, color_len))

    vertex_count = readuint32(f)
    face_count = readuint32(f)

    model['position'] = []
    # vertex position
    for _ in range(vertex_count):
        x = readfloat(f)
        y = readfloat(f)
        z = readfloat(f)
        model['position'].append((x, y, z))

    model['normal'] = []
    # vertex normal
    for _ in range(vertex_count):
        x = readfloat(f)
        y = readfloat(f)
        z = readfloat(f)
        model['normal'].append((x, y, z))

    _flag = readuint16(f)
    if _flag:
        f.seek(vertex_count * 12, 1)

    model['face'] = []
    # face index table
    for _ in range(face_count):
        v1 = readuint16(f)
        v2 = readuint16(f)
        v3 = readuint16(f)
        model['face'].append((v1, v2, v3))

    model['uv'] = []
    # vertex uv
    for mesh_vertex_count, _, uv_layers, _ in model['mesh']:
        if uv_layers > 0:
            for _ in range(mesh_vertex_count):
                u = readfloat(f)
                v = readfloat(f)
                model['uv'].append((u, v))
            f.read(mesh_vertex_count * 8 * (uv_layers - 1))
        else:
            for _ in range(mesh_vertex_count):
                u = 0.0
                v = 0.0
                model['uv'].append((u, v))

    # vertex color
    for mesh_vertex_count, _, _, color_len in model['mesh']:
        f.read(mesh_vertex_count * 4 * color_len)

    if model['bone_exist']:
        model['vertex_bone'] = []
        for _ in range(vertex_count):
            vertex_bones = [readuint8(f) for _ in range(4)]
            model['vertex_bone'].append(vertex_bones)

        model['vertex_weight'] = []
        for _ in range(vertex_count):
            vertex_weights = [readfloat(f) for _ in range(4)]
            model['vertex_weight'].append(vertex_weights)

    # footer: BoneWeightUsageMask
    if model['bone_exist']:
        model['bone_weight_usage'] = read_bone_weight_usage_mask(f, model['bone_name'])
        if f.tell() != table_offset:
            raise ValueError(
                "Bone usage mask does not end at table_offset: "
                f"current={f.tell()}, table_offset={table_offset}"
            )
        model['bone_tail'] = model['bone_weight_usage']['raw']
    else:
        bone_tail_size = table_offset - f.tell()
        model['bone_tail'] = f.read(bone_tail_size)
    
    f.seek(table_offset)

    model['lod_data_table'] = f.read(16)

    return model
