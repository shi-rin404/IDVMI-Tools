import collections
import json
import os
import re

from pathlib import Path
from typing import Callable
import bpy
from bpy.props import (
    StringProperty,
)
from bpy.types import Context, Mesh, Object, Operator
from bpy_extras.io_utils import ExportHelper
from .data.byte_buffer import (
    Semantic,
    BufferLayout,
)
from .data.dxgi_format import DXGIType
from .datahandling import (
    Fatal,
    custom_attributes_float,
    custom_attributes_int,
    keys_to_ints,
    mesh_triangulate,
)
from .datastructures import (
    HashableVertex,
    IndexBuffer,
    InputLayout,
    VertexBufferGroup,
)

from .ini_maker import ini_maker


class Export3DMigoto(Operator, ExportHelper):
    """Export a mesh for re-injection into a game with 3DMigoto"""

    bl_idname = "idvmi_tools.export_mod_migoto"
    bl_label = "Export Mod"

    filename_ext = ".vb0"
    filter_glob: StringProperty(
        default="*.vb*",
        options={"HIDDEN"},
    )

    def invoke(self, context, event):
        # return ExportHelper.invoke(self, context, event)
        return self.execute(context)

    def execute(self, context):
        try:
            clean_ini = context.scene.clean_ini
            if clean_ini:
                if not context.scene.namespace_textbox.strip():
                    self.report({"ERROR"}, "Specify a namespace name or disable 'Clean INI' option!")
                else:
                    namespace = context.scene.namespace_textbox.strip()

            # file_path = Path(self.filepath)
            # vb_path = file_path.parent / (file_path.stem + ".vb")
            # ib_path = file_path.parent / (file_path.stem + ".ib")
            # fmt_path = file_path.parent / (file_path.stem + ".fmt")
            # ini_path = file_path.parent / (file_path.stem + "_generated.ini")

            obj = context.object

            export_path = bpy.path.abspath(context.scene.export_selector)
            obj_migoto_info = re.search(r"(\d{6})-vb0=([a-f0-9]{8}).*?.txt", obj.name)
            vb0_draw_call = obj_migoto_info.group(1)
            vb0_hash = obj_migoto_info.group(2)

            if not vb0_draw_call or not vb0_hash:
                self.report({"ERROR"}, "The selected object name is not in 'DRAW_CALL-HASH*.txt' format!")

            if not os.path.isdir(os.path.join(export_path, "Meshes")):
                os.mkdir(os.path.join(export_path, "Meshes"))

            vb_path = os.path.join(export_path, "Meshes", f"{vb0_draw_call}_{vb0_hash}.vb")
            ib_path = os.path.join(export_path, "Meshes", f"{vb0_draw_call}_{vb0_hash}.ib")
            fmt_path = os.path.join(export_path, "Meshes", f"{vb0_draw_call}_{vb0_hash}.fmt")
            ini_path = os.path.join(export_path, "mod.ini")
            
            self.flip_normal = obj.get("3DMigoto:FlipNormal", False)
            self.flip_tangent = obj.get("3DMigoto:FlipTangent", False)
            self.flip_winding = obj.get("3DMigoto:FlipWinding", False)
            self.flip_mesh = obj.get("3DMigoto:FlipMesh", False)
            # FIXME: ExportHelper will check for overwriting vb_path, but not ib_path
            export_3dmigoto(self, context, vb_path, ib_path, fmt_path, ini_path)
            vb_path = vb_path + "0"
            if not clean_ini:
                ini_maker(self, vb0_draw_call, vb0_hash, vb_path, ib_path, export_path, ini_path, bpy.path.abspath(context.scene.frame_dump_selector), context, obj)
            else:
                ini_maker(self, vb0_draw_call, vb0_hash, vb_path, ib_path, export_path, ini_path, bpy.path.abspath(context.scene.frame_dump_selector), context, obj, namespace, clean_ini)
        except Fatal as e:
            self.report({"ERROR"}, str(e))
        return {"FINISHED"}


def blender_vertex_to_3dmigoto_vertex(
    mesh: Mesh,
    obj: Object,
    blender_loop_vertex,
    layout,
    texcoords,
    blender_vertex,
    translate_normal,
    translate_tangent,
    export_outline=None,
):
    if blender_loop_vertex is not None:
        blender_vertex = mesh.vertices[blender_loop_vertex.vertex_index]
    vertex = {}
    blp_normal = list(blender_loop_vertex.normal)

    # TODO: Warn if vertex is in too many vertex groups for this layout,
    # ignoring groups with weight=0.0
    vertex_groups = sorted(blender_vertex.groups, key=lambda x: x.weight, reverse=True)

    for elem in layout:
        if elem.InputSlotClass != "per-vertex" or elem.reused_offset:
            continue

        semantic_translations = layout.get_semantic_remap()
        translated_elem_name, translated_elem_index = semantic_translations.get(
            elem.name, (elem.name, elem.SemanticIndex)
        )

        # Some games don't follow the official DirectX UPPERCASE semantic naming convention:
        translated_elem_name = translated_elem_name.upper()

        if translated_elem_name == "POSITION":
            if "POSITION.w" in custom_attributes_float(mesh):
                vertex[elem.name] = list(blender_vertex.undeformed_co) + [
                    custom_attributes_float(mesh)["POSITION.w"]
                    .data[blender_vertex.index]
                    .value
                ]
            else:
                vertex[elem.name] = elem.pad(list(blender_vertex.undeformed_co), 1.0)
        elif translated_elem_name.startswith("COLOR"):
            if elem.name in mesh.vertex_colors:
                vertex[elem.name] = elem.clip(
                    list(
                        mesh.vertex_colors[elem.name]
                        .data[blender_loop_vertex.index]
                        .color
                    )
                )
            else:
                vertex[elem.name] = list(
                    mesh.vertex_colors[elem.name + ".RGB"]
                    .data[blender_loop_vertex.index]
                    .color
                )[:3] + [
                    mesh.vertex_colors[elem.name + ".A"]
                    .data[blender_loop_vertex.index]
                    .color[0]
                ]
        elif translated_elem_name == "NORMAL":
            if "NORMAL.w" in custom_attributes_float(mesh):
                vertex[elem.name] = list(
                    map(translate_normal, blender_loop_vertex.normal)
                ) + [
                    custom_attributes_float(mesh)["NORMAL.w"]
                    .data[blender_vertex.index]
                    .value
                ]
            elif blender_loop_vertex:
                vertex[elem.name] = elem.pad(
                    list(map(translate_normal, blender_loop_vertex.normal)), 0.0
                )
            else:
                # XXX: point list topology, these normals are probably going to be pretty poor, but at least it's something to export
                vertex[elem.name] = elem.pad(
                    list(map(translate_normal, blender_vertex.normal)), 0.0
                )
        elif translated_elem_name.startswith("TANGENT"):
            if export_outline:
                # Genshin optimized outlines
                vertex[elem.name] = elem.pad(
                    list(
                        map(
                            translate_tangent,
                            export_outline.get(
                                blender_loop_vertex.vertex_index, blp_normal
                            ),
                        )
                    ),
                    blender_loop_vertex.bitangent_sign,
                )
            # DOAXVV has +1/-1 in the 4th component. Not positive what this is,
            # but guessing maybe the bitangent sign? Not even sure it is used...
            # FIXME: Other games
            elif blender_loop_vertex:
                vertex[elem.name] = elem.pad(
                    list(map(translate_tangent, blender_loop_vertex.tangent)),
                    blender_loop_vertex.bitangent_sign,
                )
            else:
                # XXX Blender doesn't save tangents outside of loops, so unless
                # we save these somewhere custom when importing they are
                # effectively lost. We could potentially calculate a tangent
                # from blender_vertex.normal, but there is probably little
                # point given that normal will also likely be garbage since it
                # wasn't imported from the mesh.
                pass
        elif translated_elem_name.startswith("BINORMAL"):
            # Some DOA6 meshes (skirts) use BINORMAL, but I'm not certain it is
            # actually the binormal. These meshes are weird though, since they
            # use 4 dimensional positions and normals, so they aren't something
            # we can really deal with at all. Therefore, the below is untested,
            # FIXME: So find a mesh where this is actually the binormal,
            # uncomment the below code and test.
            # normal = blender_loop_vertex.normal
            # tangent = blender_loop_vertex.tangent
            # binormal = numpy.cross(normal, tangent)
            # XXX: Does the binormal need to be normalised to a unit vector?
            # binormal = binormal / numpy.linalg.norm(binormal)
            # vertex[elem.name] = elem.pad(list(map(translate_binormal, binormal)), 0.0)
            pass
        elif translated_elem_name.startswith("BLENDINDICES"):
            i = translated_elem_index * 4
            vertex[elem.name] = elem.pad([x.group for x in vertex_groups[i : i + 4]], 0)
        elif translated_elem_name.startswith("BLENDWEIGHT"):
            # TODO: Warn if vertex is in too many vertex groups for this layout
            i = translated_elem_index * 4
            vertex[elem.name] = elem.pad(
                [x.weight for x in vertex_groups[i : i + 4]], 0.0
            )
        elif translated_elem_name.startswith("TEXCOORD") and elem.is_float():
            uvs = []
            for uv_name in ("%s.xy" % elem.remapped_name, "%s.zw" % elem.remapped_name):
                if uv_name in texcoords:
                    uvs += list(texcoords[uv_name][blender_loop_vertex.index])
            # Handle 1D + 3D TEXCOORDs. Order is important - 1D TEXCOORDs won't
            # match anything in above loop so only .x below, 3D TEXCOORDS will
            # have processed .xy part above, and .z part below
            for uv_name in ("%s.x" % elem.remapped_name, "%s.z" % elem.remapped_name):
                if uv_name in texcoords:
                    uvs += [texcoords[uv_name][blender_loop_vertex.index].x]
            vertex[elem.name] = uvs
        else:
            # Unhandled semantics are saved in vertex layers
            data = []
            for component in "xyzw":
                layer_name = "%s.%s" % (elem.name, component)
                if layer_name in custom_attributes_int(mesh):
                    data.append(
                        custom_attributes_int(mesh)[layer_name]
                        .data[blender_vertex.index]
                        .value
                    )
                elif layer_name in custom_attributes_float(mesh):
                    data.append(
                        custom_attributes_float(mesh)[layer_name]
                        .data[blender_vertex.index]
                        .value
                    )
            if data:
                # print('Retrieved unhandled semantic %s %s from vertex layer' % (elem.name, elem.Format), data)
                vertex[elem.name] = data

        if elem.name not in vertex:
            print("NOTICE: Unhandled vertex element: %s" % elem.name)
        # else:
        #    print('%s: %s' % (elem.name, repr(vertex[elem.name])))

    return vertex


def export_3dmigoto(
    operator: Operator, context: Context, vb_path, ib_path, fmt_path, ini_path
):
    obj = context.object
    if obj is None:
        raise Fatal("No object selected")

    strides = {
        x[11:-6]: obj[x]
        for x in obj.keys()
        if x.startswith("3DMigoto:VB") and x.endswith("Stride")
    }
    layout = InputLayout(obj["3DMigoto:VBLayout"])
    topology = "trianglelist"
    if "3DMigoto:Topology" in obj:
        topology = obj["3DMigoto:Topology"]
        if topology == "trianglestrip":
            operator.report(
                {"WARNING"},
                "trianglestrip topology not supported for export, and has been converted to trianglelist. Override draw call topology using a [CustomShader] section with topology=triangle_list",
            )
            topology = "trianglelist"
    if hasattr(context, "evaluated_depsgraph_get"):  # 2.80
        mesh = obj.evaluated_get(context.evaluated_depsgraph_get()).to_mesh()
    else:  # 2.79
        mesh = obj.to_mesh(context.scene, True, "PREVIEW", calc_tessface=False)
    mesh_triangulate(mesh)

    try:
        ib_format = obj["3DMigoto:IBFormat"]
    except KeyError:
        ib = None
    else:
        ib = IndexBuffer(ib_format)

    # Calculates tangents and makes loop normals valid (still with our
    # custom normal data from import time):
    try:
        mesh.calc_tangents()
    except RuntimeError as e:
        operator.report(
            {"WARNING"},
            "Tangent calculation failed, the exported mesh may have bad normals/tangents/lighting. Original {}".format(
                str(e)
            ),
        )

    texcoord_layers = {}
    for uv_layer in mesh.uv_layers:
        texcoords = {}

        try:
            flip_texcoord_v = obj["3DMigoto:" + uv_layer.name]["flip_v"]
            if flip_texcoord_v:
                flip_uv = lambda uv: (uv[0], 1.0 - uv[1])
            else:
                flip_uv = lambda uv: uv
        except KeyError:
            flip_uv = lambda uv: uv

        for loop in mesh.loops:
            uv = flip_uv(uv_layer.data[loop.index].uv)
            texcoords[loop.index] = uv
        texcoord_layers[uv_layer.name] = texcoords

    translate_normal = normal_export_translation(
        layout, Semantic.Normal, operator.flip_normal
    )
    translate_tangent = normal_export_translation(
        layout, Semantic.Tangent, operator.flip_tangent
    )

    # Blender's vertices have unique positions, but may have multiple
    # normals, tangents, UV coordinates, etc - these are stored in the
    # loops. To export back to DX we need these combined together such that
    # a vertex is a unique set of all attributes, but we don't want to
    # completely blow this out - we still want to reuse identical vertices
    # via the index buffer. There might be a convenience function in
    # Blender to do this, but it's easy enough to do this ourselves
    indexed_vertices = collections.OrderedDict()
    vb = VertexBufferGroup(layout=layout, topology=topology)
    vb.flag_invalid_semantics()
    if vb.topology == "trianglelist":
        for poly in mesh.polygons:
            face = []
            for blender_lvertex in mesh.loops[
                poly.loop_start : poly.loop_start + poly.loop_total
            ]:
                vertex = blender_vertex_to_3dmigoto_vertex(
                    mesh,
                    obj,
                    blender_lvertex,
                    layout,
                    texcoord_layers,
                    None,
                    translate_normal,
                    translate_tangent,
                )
                if ib is not None:
                    face.append(
                        indexed_vertices.setdefault(
                            HashableVertex(vertex), len(indexed_vertices)
                        )
                    )
                else:
                    if operator.flip_winding:
                        raise Fatal(
                            "Flipping winding order without index buffer not implemented"
                        )
                    vb.append(vertex)
            if ib is not None:
                if operator.flip_winding:
                    face.reverse()
                ib.append(face)

        if ib is not None:
            for vertex in indexed_vertices:
                vb.append(vertex)
    elif vb.topology == "pointlist":
        for index, blender_vertex in enumerate(mesh.vertices):
            vb.append(
                blender_vertex_to_3dmigoto_vertex(
                    mesh,
                    obj,
                    None,
                    layout,
                    texcoord_layers,
                    blender_vertex,
                    translate_normal,
                    translate_tangent,
                )
            )
            if ib is not None:
                ib.append((index,))
    else:
        raise Fatal('topology "%s" is not supported for export' % vb.topology)

    vgmaps = {
        k[15:]: keys_to_ints(v)
        for k, v in obj.items()
        if k.startswith("3DMigoto:VGMap:")
    }

    if "" not in vgmaps:
        vb.write(vb_path, strides, operator=operator)

    for suffix, vgmap in vgmaps.items():
        ib_path = vb_path
        if suffix:
            ib_path = f"{vb_path.parent / vb_path.stem}-{suffix}{vb_path.suffix}"
        vgmap_path = (ib_path.parent / ib_path.stem) + ".vgmap"
        print("Exporting %s..." % ib_path)
        vb.remap_blendindices(obj, vgmap)
        vb.write(ib_path, strides, operator=operator)
        vb.revert_blendindices_remap()
        sorted_vgmap = collections.OrderedDict(
            sorted(vgmap.items(), key=lambda x: x[1])
        )
        json.dump(sorted_vgmap, open(vgmap_path, "w"), indent=2)

    if ib is not None:
        ib.write(open(ib_path, "wb"), operator=operator)

    # Write format reference file
    write_fmt_file(open(fmt_path, "w"), vb, ib, strides)

    # Not ready yet
    # if ini_path:
    #    write_ini_file(open(ini_path, 'w'), vb, vb_path, ib, ib_path, strides, obj, orig_topology)

def normal_export_translation(
    layouts: list[BufferLayout], semantic: Semantic, flip: bool
) -> Callable:
    unorm = False
    for layout in layouts:
        # Ensure layout is iterable; if not, wrap it in a list
        if not hasattr(layout, '__iter__') or isinstance(layout, (str, bytes)):
            elements = [layout]
        else:
            elements = layout
        for elem in elements:
            if hasattr(elem, "semantic") and elem.semantic == semantic:
                if getattr(elem.format, "dxgi_type", None) in [DXGIType.UNORM8, DXGIType.UNORM16]:
                    unorm = True
                    break
        if unorm:
            break
    if unorm:
        # Scale normal range -1:+1 to UNORM range 0:+1
        if flip:
            return lambda x: -x / 2.0 + 0.5
        return lambda x: x / 2.0 + 0.5
    if flip:
        return lambda x: -x
    return lambda x: x

def write_fmt_file(f, vb: VertexBufferGroup, ib: IndexBuffer, strides: list[int]):
    for vbuf_idx, stride in strides.items():
        if vbuf_idx.isnumeric():
            f.write("vb%s stride: %i\n" % (vbuf_idx, stride))
        else:
            f.write("stride: %i\n" % stride)
    f.write("topology: %s\n" % vb.topology)
    if ib is not None:
        f.write("format: %s\n" % ib.format)
    f.write(vb.layout.to_string())


def register():
    """Register all classes"""
    pass


def unregister():
    """Unregister all classes"""
    pass