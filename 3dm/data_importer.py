import numpy
import bpy

from typing import List, Dict, Optional

from .export_mod.data.byte_buffer import AbstractSemantic, Semantic, BufferSemantic, NumpyBuffer
from .export_mod.data.dxgi_format import DXGIType


class BlenderDataImporter:

    def set_data(self,
                 obj: bpy.types.Object,
                 mesh: bpy.types.Mesh,
                 index_buffer: NumpyBuffer,
                 vertex_buffer: NumpyBuffer,
                 semantic_converters: Dict[AbstractSemantic, List[callable]],
                 format_converters: Dict[AbstractSemantic, List[callable]]):

        buffer_semantic = index_buffer.layout.get_element(AbstractSemantic(Semantic.Index))
        index_data = self.get_semantic_data(index_buffer, buffer_semantic, format_converters, semantic_converters)

        self.import_faces(mesh, index_data)

        vertex_ids = index_data.flatten()

        mesh.vertices.add(len(vertex_buffer.data))

        vg_indices = {}
        vg_weights = {}
        texcoords = {}
        shapekeys = {}
        normals = None

        for buffer_semantic in vertex_buffer.layout.semantics:
            semantic = buffer_semantic.abstract.enum

            # Skip tangents import, we'll recalc them on export
            if semantic in [Semantic.Tangent, Semantic.BitangentSign]:
                continue

            # Get converted data from vertex buffer
            data = self.get_semantic_data(vertex_buffer, buffer_semantic, format_converters, semantic_converters)

            # Any remaining normalized integers must be converted to floats before import
            if buffer_semantic.format.dxgi_type in [DXGIType.UNORM16, DXGIType.UNORM8, DXGIType.SNORM16, DXGIType.SNORM8]:
                data = buffer_semantic.format.type_decoder(data)

            if semantic == Semantic.ShapeKey:
                shapekeys[buffer_semantic.abstract.index] = data
            elif semantic == Semantic.Color:
                self.import_colors(mesh, buffer_semantic.get_name(), data, vertex_ids)
            elif semantic == Semantic.TexCoord:
                texcoords[buffer_semantic.abstract.index] = data
            elif semantic == Semantic.Normal:
                normals = data
            elif semantic == Semantic.Blendindices:
                vg_indices[buffer_semantic.abstract.index] = data
            elif semantic == Semantic.Blendweight:
                vg_weights[buffer_semantic.abstract.index] = data
            elif semantic == Semantic.Position:
                self.import_positions(mesh, data)
            else:
                continue

        self.import_texcoords(mesh, texcoords, vertex_ids)
        self.import_vertex_groups(obj, vg_indices, vg_weights)
        self.import_shapekeys(obj, shapekeys)

        # Create edges and other missing metadata
        mesh.validate(verbose=False, clean_customdata=False)
        mesh.update()

        self.import_normals(mesh, normals, vertex_ids)

    def get_semantic_data(self,
                          buffer: NumpyBuffer,
                          buffer_semantic: BufferSemantic,
                          format_converters: Dict[AbstractSemantic, List[callable]],
                          semantic_converters: Dict[AbstractSemantic, List[callable]]):

        data = buffer.get_field(buffer_semantic.get_name())

        for converter in format_converters.get(buffer_semantic.abstract, []):
            try:
                data = converter(data)
            except ValueError:
                if not data.flags.writeable:
                    data = converter(data.copy())

        for converter in semantic_converters.get(buffer_semantic.abstract, []):
            try:
                data = converter(data)
            except ValueError:
                if not data.flags.writeable:
                    data = converter(data.copy())

        return data

    def import_faces(self,
                     mesh: bpy.types.Mesh,
                     index_data: numpy.ndarray):

        mesh.loops.add(len(index_data) * 3)
        mesh.polygons.add(len(index_data))

        mesh.loops.foreach_set('vertex_index', index_data.flatten())

        mesh.polygons.foreach_set('loop_start', [x*3 for x in range(len(index_data))])
        mesh.polygons.foreach_set('loop_total', [3] * len(index_data))

    def import_positions(self,
                         mesh: bpy.types.Mesh,
                         position_data: numpy.ndarray):

        mesh.vertices.foreach_set('co', position_data.ravel())

    def import_vertex_groups(self,
                             obj: bpy.types.Object,
                             vg_indices: Dict[int, numpy.ndarray],
                             vg_weights: Dict[int, numpy.ndarray]):

        assert (len(vg_indices) == len(vg_weights))

        # Exclude the sentinel value 0xFFFF (65535) used for unused blend slots
        # before computing the highest real bone index, to avoid creating 65536 groups.
        num_vertex_groups = max([
            indices[indices != 65535].max()
            for indices in vg_indices.values()
        ])

        for i in range(num_vertex_groups + 1):
            obj.vertex_groups.new(name=str(i))

        for semantic_index in sorted(vg_indices.keys()):
            indices = vg_indices[semantic_index]
            weights = vg_weights[semantic_index]

            for vertex_id, (indexes, wts) in enumerate(zip(indices, weights)):
                for index, weight in zip(indexes, wts):
                    # Skip unused slots: zero weight or sentinel bone index
                    if weight == 0.0 or index == 65535:
                        continue
                    obj.vertex_groups[index].add((vertex_id,), weight, 'REPLACE')

    def import_colors(self,
                      mesh: bpy.types.Mesh,
                      color_name: str,
                      color_data: numpy.ndarray,
                      vertex_ids: numpy.ndarray):

        mesh.vertex_colors.new(name=color_name)
        color_layer = mesh.vertex_colors[color_name].data
        color_layer.foreach_set('color', color_data[vertex_ids].flatten())

    def import_normals(self,
                       mesh: bpy.types.Mesh,
                       normals: Optional[numpy.ndarray],
                       vertex_ids: numpy.ndarray):

        if normals is None:
            if hasattr(mesh, 'calc_normals'):
                mesh.calc_normals()
            return
        if bpy.app.version >= (4, 1):
            mesh.normals_split_custom_set_from_vertices(normals)
        else:
            mesh.create_normals_split()
            mesh.loops.foreach_set('normal', normals[vertex_ids].flatten().tolist())
            recalculated_normals = numpy.empty(len(mesh.loops)*3, dtype=numpy.float32)
            mesh.loops.foreach_get('normal', recalculated_normals)
            recalculated_normals = recalculated_normals.reshape((-1, 3))
            mesh.use_auto_smooth = True
            mesh.polygons.foreach_set('use_smooth', numpy.ones(len(mesh.polygons), dtype=numpy.int8))
            mesh.normals_split_custom_set(recalculated_normals.tolist())

    def import_texcoords(self,
                         mesh: bpy.types.Mesh,
                         texcoords: Dict[int, numpy.ndarray],
                         vertex_ids: numpy.ndarray):

        if not texcoords:
            return

        for (texcoord_id, data) in sorted(texcoords.items()):
            uv_name = f'TEXCOORD{texcoord_id and texcoord_id or ""}.xy'
            mesh.uv_layers.new(name=uv_name)

            uv_layer = mesh.uv_layers[uv_name].data
            uv_layer.foreach_set('uv', data[vertex_ids].flatten())

    def import_shapekeys(self,
                         obj: bpy.types.Object,
                         shapekeys: Dict[int, numpy.ndarray]):

        if not shapekeys:
            return

        # Add basis shapekey
        basis = obj.shape_key_add(name='Basis')
        basis.interpolation = 'KEY_LINEAR'

        obj.data.shape_keys.use_relative = True

        vert_count = len(obj.data.vertices)

        basis_co = numpy.empty(vert_count * 3, dtype=numpy.float32)
        basis.data.foreach_get('co', basis_co)
        basis_co = basis_co.reshape(-1, 3)

        for shapekey_id, offsets in shapekeys.items():
            shapekey = obj.shape_key_add(name=f'Deform {shapekey_id}')
            shapekey.interpolation = 'KEY_LINEAR'

            position_offsets = numpy.array(offsets, dtype=numpy.float32).reshape(-1, 3)
            position_offsets += basis_co

            shapekey.data.foreach_set('co', position_offsets.ravel())
