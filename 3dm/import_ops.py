import io
import json
import os
import re
import math
import numpy as np
import bpy

from .constant_buffer_armature import import_constant_buffer_pose_armature
from .data_importer import BlenderDataImporter
from ..export_mod.datastructures import (
    IndividualVertexBuffer,
    InputLayout,
    VertexBufferGroup,
    IndexBuffer,
)
from ..export_mod.data.byte_buffer import (
    AbstractSemantic, Semantic, BufferSemantic, BufferLayout, NumpyBuffer,
)
from ..export_mod.data.dxgi_format import DXGIFormat, DXGIType


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

_RESOURCE_RE = re.compile(
    r'^(\d{6})-(ib|vb\d+)=([0-9a-f]{8})-vs=([0-9a-f]{16})-ps=([0-9a-f]{16})\.(txt|buf)$',
    re.IGNORECASE,
)

_STANDARD_MODEL_FMT = """stride: 80
topology: trianglelist
format: DXGI_FORMAT_R16_UINT
element[0]:
  SemanticName: POSITION
  SemanticIndex: 0
  Format: R32G32B32_FLOAT
  InputSlot: 0
  AlignedByteOffset: 0
  InputSlotClass: per-vertex
  InstanceDataStepRate: 0
element[1]:
  SemanticName: NORMAL
  SemanticIndex: 0
  Format: R32G32B32_FLOAT
  InputSlot: 0
  AlignedByteOffset: 12
  InputSlotClass: per-vertex
  InstanceDataStepRate: 0
element[2]:
  SemanticName: TEXCOORD
  SemanticIndex: 0
  Format: R32G32_FLOAT
  InputSlot: 0
  AlignedByteOffset: 64
  InputSlotClass: per-vertex
  InstanceDataStepRate: 0
element[3]:
  SemanticName: COLOR
  SemanticIndex: 0
  Format: R8G8B8A8_UNORM
  InputSlot: 0
  AlignedByteOffset: 60
  InputSlotClass: per-vertex
  InstanceDataStepRate: 0
element[4]:
  SemanticName: TEXCOORD
  SemanticIndex: 1
  Format: R32G32_FLOAT
  InputSlot: 0
  AlignedByteOffset: 72
  InputSlotClass: per-vertex
  InstanceDataStepRate: 0
element[5]:
  SemanticName: TEXCOORD
  SemanticIndex: 2
  Format: R32G32_FLOAT
  InputSlot: 0
  AlignedByteOffset: 72
  InputSlotClass: per-vertex
  InstanceDataStepRate: 0
element[6]:
  SemanticName: TEXCOORD
  SemanticIndex: 3
  Format: R32G32_FLOAT
  InputSlot: 0
  AlignedByteOffset: 72
  InputSlotClass: per-vertex
  InstanceDataStepRate: 0
element[7]:
  SemanticName: TEXCOORD
  SemanticIndex: 4
  Format: R32G32_FLOAT
  InputSlot: 0
  AlignedByteOffset: 72
  InputSlotClass: per-vertex
  InstanceDataStepRate: 0
element[8]:
  SemanticName: TEXCOORD
  SemanticIndex: 5
  Format: R32G32_FLOAT
  InputSlot: 0
  AlignedByteOffset: 72
  InputSlotClass: per-vertex
  InstanceDataStepRate: 0
element[9]:
  SemanticName: TEXCOORD
  SemanticIndex: 6
  Format: R32G32_FLOAT
  InputSlot: 0
  AlignedByteOffset: 72
  InputSlotClass: per-vertex
  InstanceDataStepRate: 0
element[10]:
  SemanticName: TEXCOORD
  SemanticIndex: 7
  Format: R32G32_FLOAT
  InputSlot: 0
  AlignedByteOffset: 72
  InputSlotClass: per-vertex
  InstanceDataStepRate: 0
element[11]:
  SemanticName: TANGENT
  SemanticIndex: 0
  Format: R32G32B32_FLOAT
  InputSlot: 0
  AlignedByteOffset: 24
  InputSlotClass: per-vertex
  InstanceDataStepRate: 0
element[12]:
  SemanticName: BLENDINDICES
  SemanticIndex: 0
  Format: R16G16B16A16_UINT
  InputSlot: 0
  AlignedByteOffset: 52
  InputSlotClass: per-vertex
  InstanceDataStepRate: 0
element[13]:
  SemanticName: BLENDWEIGHT
  SemanticIndex: 0
  Format: R32G32B32A32_FLOAT
  InputSlot: 0
  AlignedByteOffset: 36
  InputSlotClass: per-vertex
  InstanceDataStepRate: 0
"""

_CONSTANT_BUFFER_TXT_RES = (
    re.compile(r'^[0-9a-f]{8}-stride=\d+\.txt$', re.IGNORECASE),
    re.compile(
        r'^\d{6}\.\d+-\[.+\]-vs-cb\d+=[0-9a-f]{8}\.txt$',
        re.IGNORECASE,
    ),
    re.compile(
        r'^\d{6}-vs-cb\d+=[0-9a-f]{8}-vs=[0-9a-f]{16}-ps=[0-9a-f]{16}\.txt$',
        re.IGNORECASE,
    ),
)

_VB_HASH_RE = re.compile(r'vb\d=([0-9a-f]{8})', re.IGNORECASE)


def _parse_resource(path):
    """Return dict(draw_call, resource_type, resource_hash) or raise ValueError."""
    name = os.path.basename(path)
    m = _RESOURCE_RE.match(name)
    if not m:
        raise ValueError(
            f"'{name}' does not match the 3DM resource format "
            f"(<000000>-<ib|vbN>=<hash8>-vs=<hash16>-ps=<hash16>.<txt|buf>)"
        )
    return {
        'draw_call':     m.group(1),
        'resource_type': m.group(2).lower(),
        'resource_hash': m.group(3).lower(),
        'extension':     m.group(6).lower(),
    }


def _scan_directory(directory, extension=None):
    """Return dict: draw_call -> {resource_type: (hash, abs_path)}."""
    grouped = {}
    for fname in os.listdir(directory):
        m = _RESOURCE_RE.match(fname)
        if not m:
            continue
        if extension is not None and m.group(6).lower() != extension:
            continue
        dc    = m.group(1)
        rtype = m.group(2).lower()
        rhash = m.group(3).lower()
        grouped.setdefault(dc, {})[rtype] = (rhash, os.path.join(directory, fname))
    return grouped


def _get_draw_call_hashes(scan, draw_call):
    """Return (ib_hash, vb0_hash) for a draw call, or (None, None)."""
    resources = scan.get(draw_call, {})
    ib_entry  = resources.get('ib')
    vb0_entry = resources.get('vb0')
    return (
        ib_entry[0]  if ib_entry  else None,
        vb0_entry[0] if vb0_entry else None,
    )


def _find_related_draw_calls(scan, ib_hash, vb0_hash):
    """Return sorted list of (draw_call, vb0_path, ib_path) sharing ib_hash and vb0_hash."""
    results = []
    for dc, resources in sorted(scan.items()):
        ib_entry  = resources.get('ib')
        vb0_entry = resources.get('vb0')
        if (ib_entry and vb0_entry
                and ib_entry[0]  == ib_hash
                and vb0_entry[0] == vb0_hash):
            results.append((dc, vb0_entry[1], ib_entry[1]))
    return results


def _vb0_stem(vb_path):
    """Return the vb0 filename without extension, used as the Blender object name."""
    return os.path.splitext(os.path.basename(vb_path))[0]


def _resource_extension(import_mode):
    return "buf" if import_mode == 'BUF' else "txt"


def _resource_filter(import_mode):
    ext = _resource_extension(import_mode)
    return f"*-ib=*.{ext};*-vb*.{ext}"


def _resource_kind(import_mode):
    return "*.buf" if import_mode == 'BUF' else "*.txt"


def _import_all_related_enabled(context, import_mode):
    if import_mode == 'BUF':
        return context.scene.migoto_import_all_related_buf
    return context.scene.migoto_import_all_related_txt


# ---------------------------------------------------------------------------
# Buffer parsing
# ---------------------------------------------------------------------------

_SEMANTIC_MAP = {
    'POSITION':     Semantic.Position,
    'NORMAL':       Semantic.Normal,
    'TANGENT':      Semantic.Tangent,
    'BINORMAL':     Semantic.Binormal,
    'TEXCOORD':     Semantic.TexCoord,
    'COLOR':        Semantic.Color,
    'BLENDINDICES': Semantic.Blendindices,
    'BLENDWEIGHT':  Semantic.Blendweight,
    'SHAPEKEY':     Semantic.ShapeKey,
}


def _vertex_buffer_group_to_numpy(vbg: VertexBufferGroup) -> NumpyBuffer:
    valid_names = vbg.get_valid_semantics()

    buf_semantics = []
    field_mapping: dict[str, tuple[str, BufferSemantic]] = {}

    for elem in vbg.layout:
        if elem.name not in valid_names:
            continue
        sem_enum = _SEMANTIC_MAP.get(elem.SemanticName.upper())
        if sem_enum is None:
            continue
        abstract = AbstractSemantic(sem_enum, elem.SemanticIndex)
        dxgi_fmt = DXGIFormat(elem.Format)
        if dxgi_fmt is None:
            raise ValueError(f"Unsupported DXGI format: {elem.Format}")
        buf_sem = BufferSemantic(abstract, dxgi_fmt)
        buf_semantics.append(buf_sem)
        field_mapping[elem.name] = (abstract.get_name(), buf_sem)

    if not buf_semantics:
        raise ValueError("No supported semantics found in vertex buffer")

    vertex_layout = BufferLayout(buf_semantics)
    vertex_arr    = np.zeros(vbg.vertex_count, dtype=vertex_layout.get_numpy_type())

    for vb_name, (numpy_field, buf_sem) in field_mapping.items():
        col_arr   = np.array([v[vb_name] for v in vbg.vertices], dtype=np.float64)
        dxgi_type = buf_sem.format.dxgi_type
        if dxgi_type == DXGIType.UNORM8:
            vertex_arr[numpy_field] = np.round(col_arr * 255).astype(np.uint8)
        elif dxgi_type == DXGIType.UNORM16:
            vertex_arr[numpy_field] = np.round(col_arr * 65535).astype(np.uint16)
        elif dxgi_type == DXGIType.SNORM8:
            vertex_arr[numpy_field] = np.round(col_arr * 127).astype(np.int8)
        elif dxgi_type == DXGIType.SNORM16:
            vertex_arr[numpy_field] = np.round(col_arr * 32767).astype(np.int16)
        else:
            vertex_arr[numpy_field] = col_arr.astype(buf_sem.format.numpy_base_type)

    return NumpyBuffer(vertex_layout, vertex_arr)


def _parse_vertex_buffer(vb_path: str) -> tuple[NumpyBuffer, VertexBufferGroup]:
    vbg = VertexBufferGroup([vb_path])
    return _vertex_buffer_group_to_numpy(vbg), vbg


def _parse_vertex_buffer_bin(vb_path: str, fmt_text: str) -> tuple[NumpyBuffer, VertexBufferGroup]:
    layout = InputLayout()
    idx = _get_vb_index(vb_path)
    vb = IndividualVertexBuffer(idx, io.StringIO(fmt_text), layout, False)
    with open(vb_path, "rb") as f:
        vb.parse_vb_bin(f)

    if not vb.vertices:
        raise ValueError("Vertex buffer contains no vertices")

    vbg = VertexBufferGroup(layout=layout)
    vbg.vbs.append(vb)
    vbg.slots[idx] = vb
    vbg.first = vb.first
    vbg.vertex_count = vb.vertex_count
    vbg.topology = vb.topology
    vbg.flag_invalid_semantics()
    vbg.merge_vbs(vbg.vbs)
    return _vertex_buffer_group_to_numpy(vbg), vbg


def _parse_index_buffer(ib_path: str) -> tuple[NumpyBuffer, IndexBuffer]:
    with open(ib_path, 'r') as f:
        ib = IndexBuffer(f)

    return _index_buffer_to_numpy(ib), ib


def _parse_index_buffer_bin(ib_path: str, fmt_text: str) -> tuple[NumpyBuffer, IndexBuffer]:
    ib = IndexBuffer(io.StringIO(fmt_text), load_indices=False)
    with open(ib_path, "rb") as f:
        ib.parse_ib_bin(f)

    return _index_buffer_to_numpy(ib), ib


def _index_buffer_to_numpy(ib: IndexBuffer) -> NumpyBuffer:
    if not ib.faces:
        raise ValueError("Index buffer contains no faces")

    index_abstract = AbstractSemantic(Semantic.Index)
    index_sem      = BufferSemantic(index_abstract, DXGIFormat.R32G32B32_UINT)
    index_layout   = BufferLayout([index_sem])

    index_arr = np.zeros(len(ib.faces), dtype=index_layout.get_numpy_type())
    index_arr['INDEX'] = np.array(ib.faces, dtype=np.uint32)

    return NumpyBuffer(index_layout, index_arr)


def _get_vb_index(vb_path):
    match = VertexBufferGroup.vb_idx_pattern.search(vb_path)
    if match is None:
        return 0
    return int(match.group(1))


# ---------------------------------------------------------------------------
# Mesh building
# ---------------------------------------------------------------------------

def _build_mesh(vb_path: str, ib_path: str, name: str):
    """Parse buffers and create a Blender object. Returns (mesh_data, obj).
    Raises on any failure — does NOT call operator.report."""
    vertex_buffer, vertex_metadata = _parse_vertex_buffer(vb_path)
    index_buffer, index_metadata = _parse_index_buffer(ib_path)

    return _build_mesh_from_buffers(
        vertex_buffer,
        index_buffer,
        name,
        vertex_metadata,
        index_metadata,
    )


def _build_mesh_buf(vb_path: str, ib_path: str, fmt_text: str, name: str):
    vertex_buffer, vertex_metadata = _parse_vertex_buffer_bin(vb_path, fmt_text)
    index_buffer, index_metadata = _parse_index_buffer_bin(ib_path, fmt_text)

    return _build_mesh_from_buffers(
        vertex_buffer,
        index_buffer,
        name,
        vertex_metadata,
        index_metadata,
    )


def _set_3dmigoto_custom_properties(obj, vertex_metadata, index_metadata):
    obj["3DMigoto:VBLayout"] = vertex_metadata.layout.serialise()
    obj["3DMigoto:Topology"] = vertex_metadata.topology
    obj["3DMigoto:FirstVertex"] = vertex_metadata.first
    obj["3DMigoto:VertexCount"] = vertex_metadata.vertex_count

    for idx, vb in vertex_metadata.slots.items():
        obj[f"3DMigoto:VB{idx}Stride"] = vb.stride

    obj["3DMigoto:IBFormat"] = index_metadata.format
    obj["3DMigoto:FirstIndex"] = index_metadata.first
    obj["3DMigoto:IndexCount"] = index_metadata.index_count


def _mirror_uv_y(mesh_data, obj, enabled):
    for uv_layer in mesh_data.uv_layers:
        obj["3DMigoto:" + uv_layer.name] = {"flip_v": enabled}
        if not enabled:
            continue

        for loop_uv in uv_layer.data:
            loop_uv.uv.y = 1.0 - loop_uv.uv.y


def _build_mesh_from_buffers(
    vertex_buffer,
    index_buffer,
    name: str,
    vertex_metadata,
    index_metadata,
):
    mesh_data = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh_data)
    obj.rotation_euler[0] = math.radians(90)
    bpy.context.collection.objects.link(obj)

    try:
        BlenderDataImporter().set_data(
            obj, mesh_data, index_buffer, vertex_buffer, {}, {},
        )
        _mirror_uv_y(mesh_data, obj, bpy.context.scene.flip_uv_y)
        _set_3dmigoto_custom_properties(obj, vertex_metadata, index_metadata)
    except Exception:
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh_data)
        raise

    return mesh_data, obj


def _run_import(operator, vb_path: str, ib_path: str):
    """Single-mesh import with operator error reporting."""
    name = _vb0_stem(vb_path)
    try:
        mesh_data, _ = _build_mesh(vb_path, ib_path, name)
    except Exception as e:
        operator.report({'ERROR'}, f"Import failed: {e}")
        return {'CANCELLED'}

    operator.report(
        {'INFO'},
        f"Imported '{name}': {len(mesh_data.vertices)} verts, "
        f"{len(mesh_data.polygons)} faces",
    )
    return {'FINISHED'}


def _run_import_buf(operator, pairs, use_standard_model_format):
    if not use_standard_model_format:
        _ask_for_fmt_file(pairs)
        return {'FINISHED'}

    try:
        return _run_import_buf_with_fmt(operator, pairs, _STANDARD_MODEL_FMT)
    except Exception as e:
        operator.report(
            {'WARNING'},
            f"Standard model format failed: {e}. Select a .fmt file.",
        )
        _ask_for_fmt_file(pairs)
        return {'FINISHED'}


def _run_import_buf_with_fmt(operator, pairs, fmt_text):
    imported, failed = 0, []

    for vb_path, ib_path in pairs:
        name = _vb0_stem(vb_path)
        try:
            _build_mesh_buf(vb_path, ib_path, fmt_text, name)
            imported += 1
        except Exception as e:
            failed.append(f"{os.path.basename(vb_path)}: {e}")

    if failed and not imported:
        raise RuntimeError("; ".join(failed))

    for msg in failed:
        operator.report({'WARNING'}, f"Failed - {msg}")
    operator.report(
        {'INFO'},
        f"Imported {imported} binary mesh(es)"
        + (f" ({len(failed)} failed)" if failed else ""),
    )
    return {'FINISHED'}


def _ask_for_fmt_file(pairs):
    bpy.ops.idvmi_migoto.select_fmt_manual(
        'INVOKE_DEFAULT',
        pairs_json=json.dumps(pairs),
    )


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class IDVMI_OT_Import_3DM(bpy.types.Operator):
    """Select a 3DM vertex buffer or index buffer file to import"""
    bl_idname = "idvmi_migoto.import_3dm"
    bl_label  = "Import 3DM Mesh"

    filepath:    bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(
        default="*-ib=*.txt;*-vb*.txt",
        options={'HIDDEN'},
    )
    import_mode: bpy.props.EnumProperty(
        name="Import Type",
        items=[
            ('TXT', "Import *.txt", "Import text frame-analysis buffers"),
            ('BUF', "Import *.buf", "Import binary buffers using a .fmt layout"),
        ],
        default='TXT',
    )
    use_standard_model_format: bpy.props.BoolProperty(
        name="Use standard model format",
        description="Use the built-in default .fmt layout for .buf imports",
        default=False,
    )

    def invoke(self, context, event):
        self.import_mode = context.scene.migoto_mesh_import_mode
        self.use_standard_model_format = (
            context.scene.migoto_use_standard_model_format
        )
        self.filter_glob = _resource_filter(self.import_mode)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "import_mode")
        if self.import_mode == 'BUF':
            layout.prop(self, "use_standard_model_format")

    def execute(self, context):
        selected  = bpy.path.abspath(self.filepath)
        directory = os.path.dirname(selected)
        extension = _resource_extension(self.import_mode)
        scan      = _scan_directory(directory, extension)

        context.scene.migoto_mesh_import_mode = self.import_mode
        context.scene.migoto_use_standard_model_format = (
            self.use_standard_model_format
        )

        if not selected.lower().endswith("." + extension):
            self.report({'ERROR'}, f"Select a {_resource_kind(self.import_mode)} file")
            return {'CANCELLED'}

        if _import_all_related_enabled(context, self.import_mode):
            return self._execute_batch(selected, scan)
        return self._execute_single(selected, scan)

    # ------------------------------------------------------------------
    def _execute_single(self, selected, scan):
        vb_path, ib_path = self._resolve_pair(selected, scan)
        if vb_path is None:
            return {'FINISHED'}   # error reported or VB dialog opened
        if self.import_mode == 'BUF':
            return _run_import_buf(
                self,
                [[vb_path, ib_path]],
                self.use_standard_model_format,
            )
        return _run_import(self, vb_path, ib_path)

    def _execute_batch(self, selected, scan):
        try:
            info = _parse_resource(selected)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        draw_call = info['draw_call']
        ib_hash, vb0_hash = _get_draw_call_hashes(scan, draw_call)

        if ib_hash is None:
            self.report({'ERROR'}, f"No IB file found for draw call {draw_call}")
            return {'CANCELLED'}
        if vb0_hash is None:
            self.report({'ERROR'}, f"No vb0 file found for draw call {draw_call}")
            return {'CANCELLED'}

        related = _find_related_draw_calls(scan, ib_hash, vb0_hash)
        if not related:
            self.report({'ERROR'}, "No related draw calls found")
            return {'CANCELLED'}

        if self.import_mode == 'BUF':
            pairs = [[vb_path, ib_path] for _, vb_path, ib_path in related]
            return _run_import_buf(
                self,
                pairs,
                self.use_standard_model_format,
            )

        imported, failed = 0, []
        for dc, vb_path, ib_path in related:
            try:
                _build_mesh(vb_path, ib_path, name=_vb0_stem(vb_path))
                imported += 1
            except Exception as e:
                failed.append(f"{dc}: {e}")

        for msg in failed:
            self.report({'WARNING'}, f"Failed — {msg}")
        self.report(
            {'INFO'},
            f"Imported {imported} of {len(related)} related meshes"
            + (f" ({len(failed)} failed)" if failed else ""),
        )
        return {'FINISHED'}

    # ------------------------------------------------------------------
    def _resolve_pair(self, selected, scan):
        """Return (vb_path, ib_path) or (None, None) if further action is needed."""
        try:
            info = _parse_resource(selected)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return None, None

        draw_call     = info['draw_call']
        resource_type = info['resource_type']
        directory     = os.path.dirname(selected)
        resources     = scan.get(draw_call, {})

        if resource_type.startswith('vb'):
            ib_entry = resources.get('ib')
            if not ib_entry:
                self.report({'ERROR'}, f"No IB file found for draw call {draw_call}")
                return None, None
            return selected, ib_entry[1]

        # IB selected → find VBs
        ib_path  = selected
        vb_types = sorted(
            [(k, v) for k, v in resources.items() if k.startswith('vb')],
            key=lambda kv: int(kv[0][2:]),
        )
        if not vb_types:
            self.report({'ERROR'}, f"No VB file found for draw call {draw_call}")
            return None, None

        vb_slots = {int(k[2:]) for k, _ in vb_types}
        if vb_slots == {0}:
            return vb_types[0][1][1], ib_path

        # Multiple VB slots → open VB dialog, caller returns FINISHED
        bpy.ops.idvmi_migoto.select_vb_manual(
            'INVOKE_DEFAULT',
            ib_path=ib_path,
            directory=directory,
            import_mode=self.import_mode,
            use_standard_model_format=self.use_standard_model_format,
        )
        return None, None


class IDVMI_OT_Select_VB_Manual(bpy.types.Operator):
    """Select which vertex buffer to pair with the previously chosen index buffer"""
    bl_idname = "idvmi_migoto.select_vb_manual"
    bl_label  = "Select Vertex Buffer"

    filepath:    bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(
        default="*-vb*.txt",
        options={'HIDDEN'},
    )
    directory: bpy.props.StringProperty(subtype="DIR_PATH")
    ib_path:   bpy.props.StringProperty(options={'HIDDEN'})
    import_mode: bpy.props.EnumProperty(
        name="Import Type",
        items=[
            ('TXT', "Import *.txt", "Import text frame-analysis buffers"),
            ('BUF', "Import *.buf", "Import binary buffers using a .fmt layout"),
        ],
        default='TXT',
    )
    use_standard_model_format: bpy.props.BoolProperty(
        name="Use standard model format",
        default=False,
    )

    def invoke(self, context, event):
        self.filter_glob = f"*-vb*.{_resource_extension(self.import_mode)}"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "import_mode")
        if self.import_mode == 'BUF':
            layout.prop(self, "use_standard_model_format")

    def execute(self, context):
        vb_path = bpy.path.abspath(self.filepath)

        try:
            info = _parse_resource(vb_path)
        except ValueError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        if not info['resource_type'].startswith('vb'):
            self.report(
                {'ERROR'},
                f"Expected a VB file, got resource type '{info['resource_type']}'",
            )
            return {'CANCELLED'}

        if info['extension'] != _resource_extension(self.import_mode):
            self.report({'ERROR'}, f"Expected a {_resource_kind(self.import_mode)} VB file")
            return {'CANCELLED'}

        if self.import_mode == 'BUF':
            return _run_import_buf(
                self,
                [[vb_path, self.ib_path]],
                self.use_standard_model_format,
            )

        return _run_import(self, vb_path, self.ib_path)


class IDVMI_OT_Select_FMT_Manual(bpy.types.Operator):
    """Select the format file used to import binary 3DMigoto buffers"""
    bl_idname = "idvmi_migoto.select_fmt_manual"
    bl_label = "Select Format File"

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(
        default="*.fmt",
        options={'HIDDEN'},
    )
    pairs_json: bpy.props.StringProperty(options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        fmt_path = bpy.path.abspath(self.filepath)
        if not fmt_path.lower().endswith(".fmt"):
            self.report({'ERROR'}, "Select a .fmt file")
            return {'CANCELLED'}

        try:
            pairs = json.loads(self.pairs_json)
            with open(fmt_path, "r") as f:
                fmt_text = f.read()
            return _run_import_buf_with_fmt(self, pairs, fmt_text)
        except Exception as e:
            self.report({'ERROR'}, f"Binary 3DM import failed: {e}")
            return {'CANCELLED'}


class IDVMI_OT_Import_CB_Pose_Armature(bpy.types.Operator):
    """Import a 3DMigoto constant-buffer pose as an armature"""
    bl_idname = "idvmi_migoto.import_cb_pose_armature"
    bl_label = "Import Constant Buffer Pose"

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(
        default="*.txt;*.buf",
        options={'HIDDEN'},
    )
    start_index: bpy.props.IntProperty(
        name="Start Index",
        description="First constant-buffer float4 row/register to import",
        default=0,
        min=0,
    )
    end_index: bpy.props.IntProperty(
        name="End Index",
        description="Last constant-buffer float4 row/register to import",
        default=1019,
        min=0,
    )
    batch_import_related_meshes: bpy.props.BoolProperty(
        name="Batch Import Relates Meshes",
        description="Also apply the pose to scene meshes sharing a vbN hash with the selected mesh objects",
        default=False,
    )

    def invoke(self, context, event):
        self.start_index = context.scene.migoto_cb_pose_start_index
        self.end_index = context.scene.migoto_cb_pose_end_index
        self.batch_import_related_meshes = (
            context.scene.migoto_cb_pose_batch_import_related_meshes
        )
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "start_index")
        layout.prop(self, "end_index")
        layout.prop(self, "batch_import_related_meshes")

    def execute(self, context):
        filepath = bpy.path.abspath(self.filepath)
        ext = os.path.splitext(filepath)[1].lower()

        context.scene.migoto_cb_pose_start_index = self.start_index
        context.scene.migoto_cb_pose_end_index = self.end_index
        context.scene.migoto_cb_pose_batch_import_related_meshes = (
            self.batch_import_related_meshes
        )

        if ext not in {".txt", ".buf"}:
            self.report({'ERROR'}, "Select a .txt or .buf constant-buffer file")
            return {'CANCELLED'}

        if ext == ".txt" and not self._is_expected_txt_name(filepath):
            self.report(
                {'ERROR'},
                "TXT name must match one of the supported constant-buffer dump formats",
            )
            return {'CANCELLED'}

        targets = _get_cb_pose_target_objects(
            context,
            self.batch_import_related_meshes,
        )

        try:
            arm = import_constant_buffer_pose_armature(
                context,
                filepath,
                self.start_index,
                self.end_index,
                target_objs=targets,
            )
        except Exception as e:
            self.report({'ERROR'}, f"Constant-buffer pose import failed: {e}")
            return {'CANCELLED'}

        self.report(
            {'INFO'},
            f"Imported pose armature '{arm.name}' with {len(arm.data.bones)} bones"
            + (f" and applied it to {len(targets)} mesh(es)" if targets else ""),
        )
        return {'FINISHED'}

    @staticmethod
    def _is_expected_txt_name(filepath):
        filename = os.path.basename(filepath)
        return any(pattern.match(filename) for pattern in _CONSTANT_BUFFER_TXT_RES)


def _get_cb_pose_target_objects(context, include_related):
    selected = [
        obj for obj in context.selected_objects
        if obj.type == 'MESH'
    ]
    if not include_related or not selected:
        return selected

    selected_hashes = set()
    for obj in selected:
        selected_hashes.update(_extract_vb_hashes(obj.name))

    if not selected_hashes:
        return selected

    targets = []
    seen = set()
    for obj in context.scene.objects:
        if obj.type != 'MESH' or obj.name in seen:
            continue
        if selected_hashes.intersection(_extract_vb_hashes(obj.name)):
            targets.append(obj)
            seen.add(obj.name)

    return targets


def _extract_vb_hashes(name):
    return {match.group(1).lower() for match in _VB_HASH_RE.finditer(name)}
