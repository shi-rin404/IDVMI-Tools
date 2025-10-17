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
            for _ in range(bone_count):                
                model['bounding_info'].append(tuple(readfloat(f) for _ in range(7)))

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

        # with open("C:\\Users\\Shirin\\AppData\\Roaming\\Blender Foundation\\Blender\\3.6\scripts\\addons\\IDVMI_Tools\\neox_tools\\joints.txt", "w") as www:
        #     www.write(f"{model['vertex_bone']}")

        model['vertex_weight'] = []
        for _ in range(vertex_count):
            vertex_weights = [readfloat(f) for _ in range(4)]
            model['vertex_weight'].append(vertex_weights)        

        # with open("C:\\Users\\Shirin\\AppData\\Roaming\\Blender Foundation\\Blender\\3.6\scripts\\addons\\IDVMI_Tools\\neox_tools\\weights.txt", "w") as www:
        #     www.write(f"{model['vertex_weight']}")

    # footer
    bone_tail_size = table_offset - f.tell()
    model['bone_tail'] = f.read(bone_tail_size)
    
    f.seek(table_offset)

    model['lod_data_table'] = f.read(16)

    return model

def parse_mesh_2(model, f, operator):
    # model = {}
    # with open(path, 'rb') as f:
    # with io.BytesIO(path) as f:
        # try:
    _magic_number = f.read(8)

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
            parent_node = readuint16(f)
            if parent_node == 65535:
                parent_node = -1
            parent_nodes.append(parent_node)
        model['bone_parent'] = parent_nodes

        bone_names = []
        for _ in range(bone_count):
            bone_name = f.read(32)
            bone_name = bone_name.decode().replace('\0', '')#.replace(' ', '_')
            bone_names.append(bone_name)
        model['bone_name'] = bone_names
        
        bone_binding_info = readuint8(f)        
        if bone_binding_info:
            model['bounding_info'] = []
            for _ in range(bone_count):                
                model['bounding_info'].append(tuple(readfloat(f) for _ in range(7)))

        model['bone_matrix'] = []
        for i in range(bone_count):
            matrix = [readfloat(f) for _ in range(16)]
            matrix = np.array(matrix).reshape(4, 4)
            model['bone_matrix'].append(matrix)

        if len(list(filter(lambda x: x == -1, parent_nodes))) > 1:
            num = len(model['bone_parent'])
            model['bone_parent'] = list(map(lambda x: num if x == -1 else x, model['bone_parent']))
            model['bone_parent'].append(-1)
            model['bone_name'].append('dummy_root')
            model['bone_matrix'].append(np.identity(4))

        # _flag = readuint8(f)  # 00
        # assert _flag == 0

        has_binding_info = readuint8(f)  # 00
        if has_binding_info != 0:
            print(f"Debug: Read _flag value {has_binding_info} at position {f.tell()}")
            raise ValueError(f"Unexpected _flag value {has_binding_info} at position {f.tell()}")

    table_offset = readuint32(f)

    while True:
        lod_new_v = readuint16(f)
        if lod_new_v == 1:
            break
        f.seek(-2, 1)
        mesh_vertex_count = readuint32(f)
        mesh_face_count = readuint32(f)
        uv_ch_count = readuint8(f)
        has_color = readuint8(f)

        model['mesh'].append((mesh_vertex_count, mesh_face_count, uv_ch_count, has_color))

    vertex_count = readuint32(f)
    face_count = readuint32(f)

    print(f"LOD Table offset: {table_offset}")
    print(f"Vertex count: {vertex_count}")
    print(f"face count: {face_count}")

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
        model['tangent'] = []
        for _ in range(vertex_count):
            model['tangent'].append(tuple(readfloat(f) for _ in range(3)))
        # f.seek(vertex_count * 12, 1)

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
    model['vertex_color'] = []
    for mesh_vertex_count, _, _, color_len in model['mesh']:
        f.read(mesh_vertex_count * 4 * color_len)
        # if color_len > 0:
        #     model['vertex_color'].append(tuple(tuple(readuint8(f) for __ in range(4)) for _ in range(color_len) for _ in range(mesh_vertex_count)))

    if model['bone_exist']:
        model['vertex_bone'] = []
        for _ in range(vertex_count):
            vertex_joints = [readuint16(f) for _ in range(4)]
            model['vertex_bone'].append(vertex_joints)

        # operator.report({'INFO'}, f"{f.tell()}")
        model['vertex_weight'] = []
        for _ in range(vertex_count):
            vertex_joint_weights = [readfloat(f) for _ in range(4)]
            model['vertex_weight'].append(vertex_joint_weights)

        # with open("C:\\Users\\Shirin\\AppData\\Roaming\\Blender Foundation\\Blender\\3.6\\scripts\\addons\\IDVMI_Tools\\neox_tools\\weights.txt", "w") as www:
        #     www.write(f"{model['vertex_joint_weight']}")
    
    # footer
    bone_tail_size = table_offset - f.tell()
    model['bone_tail'] = f.read(bone_tail_size)
    
    f.seek(table_offset)

    model['lod_data_table'] = f.read(16)

    # model['block_count'] = readuint16(f)
    # model['block_infos'] = readuint32(f)
    # model['pair_count'] = readuint16(f)
    # model['lod'] = readuint32(f)
    # model['block_idx'] = readuint32(f)

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
            for _ in range(bone_count):                
                model['bounding_info'].append(tuple(readfloat(f) for _ in range(7)))

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

    # footer
    bone_tail_size = table_offset - f.tell()
    model['bone_tail'] = f.read(bone_tail_size)
    
    f.seek(table_offset)

    model['lod_data_table'] = f.read(16)

    return model