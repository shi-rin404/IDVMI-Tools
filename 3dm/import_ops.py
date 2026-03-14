import os
import re
import math
import numpy as np
import bpy

from .data_importer import BlenderDataImporter
from ..export_mod.datastructures import VertexBufferGroup, IndexBuffer
from ..export_mod.data.byte_buffer import (
    AbstractSemantic, Semantic, BufferSemantic, BufferLayout, NumpyBuffer,
)
from ..export_mod.data.dxgi_format import DXGIFormat, DXGIType


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------

_RESOURCE_RE = re.compile(
    r'^(\d{6})-(ib|vb\d+)=([0-9a-f]{8})-vs=([0-9a-f]{16})-ps=([0-9a-f]{16})\.txt$',
    re.IGNORECASE,
)


def _parse_resource(path):
    """Return dict(draw_call, resource_type, resource_hash) or raise ValueError."""
    name = os.path.basename(path)
    m = _RESOURCE_RE.match(name)
    if not m:
        raise ValueError(
            f"'{name}' does not match the 3DM resource format "
            f"(<000000>-<ib|vbN>=<hash8>-vs=<hash16>-ps=<hash16>.txt)"
        )
    return {
        'draw_call':     m.group(1),
        'resource_type': m.group(2).lower(),
        'resource_hash': m.group(3).lower(),
    }


def _scan_directory(directory):
    """Return dict: draw_call -> {resource_type: (hash, abs_path)}."""
    grouped = {}
    for fname in os.listdir(directory):
        m = _RESOURCE_RE.match(fname)
        if not m:
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


def _parse_vertex_buffer(vb_path: str) -> NumpyBuffer:
    vbg = VertexBufferGroup([vb_path])
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


def _parse_index_buffer(ib_path: str) -> NumpyBuffer:
    with open(ib_path, 'r') as f:
        ib = IndexBuffer(f)

    if not ib.faces:
        raise ValueError("Index buffer contains no faces")

    index_abstract = AbstractSemantic(Semantic.Index)
    index_sem      = BufferSemantic(index_abstract, DXGIFormat.R32G32B32_UINT)
    index_layout   = BufferLayout([index_sem])

    index_arr = np.zeros(len(ib.faces), dtype=index_layout.get_numpy_type())
    index_arr['INDEX'] = np.array(ib.faces, dtype=np.uint32)

    return NumpyBuffer(index_layout, index_arr)


# ---------------------------------------------------------------------------
# Mesh building
# ---------------------------------------------------------------------------

def _build_mesh(vb_path: str, ib_path: str, name: str):
    """Parse buffers and create a Blender object. Returns (mesh_data, obj).
    Raises on any failure — does NOT call operator.report."""
    vertex_buffer = _parse_vertex_buffer(vb_path)
    index_buffer  = _parse_index_buffer(ib_path)

    mesh_data = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh_data)
    obj.rotation_euler[0] = math.radians(90)
    bpy.context.collection.objects.link(obj)

    try:
        BlenderDataImporter().set_data(
            obj, mesh_data, index_buffer, vertex_buffer, {}, {},
        )
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

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        selected  = bpy.path.abspath(self.filepath)
        directory = os.path.dirname(selected)
        scan      = _scan_directory(directory)

        if context.scene.migoto_import_all_related:
            return self._execute_batch(selected, scan)
        return self._execute_single(selected, scan)

    # ------------------------------------------------------------------
    def _execute_single(self, selected, scan):
        vb_path, ib_path = self._resolve_pair(selected, scan)
        if vb_path is None:
            return {'FINISHED'}   # error reported or VB dialog opened
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

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

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

        return _run_import(self, vb_path, self.ib_path)
