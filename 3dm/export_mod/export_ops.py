import collections
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
    mesh_triangulate,
)
from .datastructures import (
    HashableVertex,
    IndexBuffer,
    InputLayout,
    VertexBufferGroup,
)

from .ini_maker import ini_maker_combined


_MIGOTO_OBJECT_RE = re.compile(r"(\d{6})-vb0=([a-f0-9]{8})(?:-|\.|$)", re.IGNORECASE)


def _parse_migoto_object_name(obj: Object):
    obj_migoto_info = _MIGOTO_OBJECT_RE.search(obj.name)
    if obj_migoto_info is None:
        return None
    return obj_migoto_info.group(1), obj_migoto_info.group(2).lower()


def _iter_related_export_objects(context, vb0_hash):
    if not context.scene.export_all_relative_meshes:
        return [context.object]

    related = []
    seen_draw_calls = set()

    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.hide_get() or not obj.visible_get():
            continue

        obj_info = _parse_migoto_object_name(obj)
        if obj_info is None:
            continue

        draw_call, obj_vb0_hash = obj_info
        if obj_vb0_hash != vb0_hash:
            continue
        if draw_call in seen_draw_calls:
            raise Fatal(f"Multiple visible meshes use draw call {draw_call}; hide duplicates before exporting all relative meshes")

        seen_draw_calls.add(draw_call)
        related.append((draw_call, obj_vb0_hash, obj))

    related.sort(key=lambda item: item[0])
    return [obj for _, _, obj in related]


class Export3DMigoto(Operator, ExportHelper):
    """Export a mesh for re-injection into a game with 3DMigoto"""

    bl_idname = "idvmi_migoto.export_mod_migoto"
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
                    return {"CANCELLED"}
                else:
                    namespace = context.scene.namespace_textbox.strip().replace(" ","")

            obj = context.object
            if obj is None:
                self.report({"ERROR"}, "Select a 3DMigoto mesh object to export!")
                return {"CANCELLED"}

            export_path = bpy.path.abspath(context.scene.migoto_export_selector)
            obj_migoto_info = _parse_migoto_object_name(obj)
            if obj_migoto_info is None:
                self.report({"ERROR"}, "The selected object name is not in '<draw_call>-vb0=<hash>' format!")
                return {"CANCELLED"}
            vb0_draw_call, vb0_hash = obj_migoto_info

            if not os.path.isdir(os.path.join(export_path, "Meshes")):
                os.mkdir(os.path.join(export_path, "Meshes"))

            ini_path = os.path.join(export_path, "mod.ini")

            export_objs = _iter_related_export_objects(context, vb0_hash)
            if not export_objs:
                self.report({"ERROR"}, "No visible relative 3DMigoto mesh objects found to export!")
                return {"CANCELLED"}

            combined_vb_path = os.path.join(export_path, "Meshes", f"vb0_{vb0_hash}.vb")
            combined_ib_path = os.path.join(export_path, "Meshes", f"vb0_{vb0_hash}.ib")
            combined_fmt_path = os.path.join(export_path, "Meshes", f"vb0_{vb0_hash}.fmt")

            save_fmt_file = context.scene.migoto_save_fmt_file
            combined_vb, combined_ib, combined_strides, ini_entries = export_3dmigoto_combined(
                self,
                context,
                export_objs,
                combined_vb_path,
                combined_ib_path,
                combined_fmt_path,
                save_fmt_file,
            )

            ini_maker_combined(
                self,
                ini_entries,
                vb0_hash,
                combined_vb_path + "0",
                combined_ib_path,
                combined_strides,
                export_path,
                ini_path,
                bpy.path.abspath(context.scene.frame_dump_selector),
                context,
                namespace if clean_ini else "",
                clean_ini,
            )
        except Fatal as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
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


def build_3dmigoto_buffers(operator: Operator, context: Context, obj=None):
    obj = obj or context.object
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

        flip_texcoord_v = bool(context.scene.flip_uv_y)
        try:
            flip_texcoord_v = flip_texcoord_v or obj["3DMigoto:" + uv_layer.name]["flip_v"]
        except KeyError:
            pass

        if flip_texcoord_v:
            flip_uv = lambda uv: (uv[0], 1.0 - uv[1])
        else:
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

    return vb, ib, strides


def write_3dmigoto_buffers(operator: Operator, vb, ib, strides, vb_path, ib_path, fmt_path, save_fmt_file=True):
    vb_path = Path(vb_path)
    ib_path = Path(ib_path)
    fmt_path = Path(fmt_path)

    vb.write(vb_path, strides, operator=operator)

    if ib is not None:
        with open(ib_path, "wb") as output:
            ib.write(output, operator=operator)

    if save_fmt_file:
        with open(fmt_path, "w") as output:
            write_fmt_file(output, vb, ib, strides)


def export_3dmigoto(operator: Operator, context: Context, vb_path, ib_path, fmt_path, ini_path, obj=None):
    vb, ib, strides = build_3dmigoto_buffers(operator, context, obj)
    save_fmt_file = getattr(context.scene, "migoto_save_fmt_file", False)
    write_3dmigoto_buffers(operator, vb, ib, strides, vb_path, ib_path, fmt_path, save_fmt_file)
    return vb, ib, strides


def export_3dmigoto_combined(operator: Operator, context: Context, export_objs, vb_path, ib_path, fmt_path, save_fmt_file=True):
    combined_vb = None
    combined_ib = None
    combined_strides = None
    ini_entries = []

    for export_obj in export_objs:
        export_obj_info = _parse_migoto_object_name(export_obj)
        if export_obj_info is None:
            continue

        draw_call, export_vb0_hash = export_obj_info

        operator.flip_normal = export_obj.get("3DMigoto:FlipNormal", False)
        operator.flip_tangent = export_obj.get("3DMigoto:FlipTangent", False)
        operator.flip_winding = export_obj.get("3DMigoto:FlipWinding", False)
        operator.flip_mesh = export_obj.get("3DMigoto:FlipMesh", False)

        vb, ib, strides = build_3dmigoto_buffers(operator, context, export_obj)
        if ib is None:
            raise Fatal("Combined export requires indexed meshes")

        vgmaps = [k for k in export_obj.keys() if k.startswith("3DMigoto:VGMap:")]
        if vgmaps:
            raise Fatal("Combined export does not support 3DMigoto vertex group maps yet")

        if combined_vb is None:
            combined_vb = VertexBufferGroup(layout=vb.layout, topology=vb.topology)
            combined_vb.flag_invalid_semantics()
            combined_ib = IndexBuffer(ib.format)
            combined_ib.topology = ib.topology
            combined_strides = strides
        else:
            if combined_vb.layout != vb.layout:
                raise Fatal("Cannot combine meshes with different vertex layouts")
            if combined_vb.topology != vb.topology:
                raise Fatal("Cannot combine meshes with different topologies")
            if combined_ib.format != ib.format:
                raise Fatal("Cannot combine meshes with different index buffer formats")
            if combined_strides != strides:
                raise Fatal("Cannot combine meshes with different vertex buffer strides")

        start_index = len(combined_ib)
        vertex_offset = len(combined_vb)
        index_count = len(ib)

        combined_vb.vertices.extend(vb.vertices)
        combined_vb.vertex_count = len(combined_vb.vertices)

        for face in ib.faces:
            combined_ib.append(tuple(index + vertex_offset for index in face))

        ini_entries.append({
            "draw_call": draw_call,
            "vb0_hash": export_vb0_hash,
            "start_index": start_index,
            "index_count": index_count,
            "obj": export_obj,
        })

    if combined_vb is None or combined_ib is None:
        raise Fatal("No meshes were exported")

    write_3dmigoto_buffers(operator, combined_vb, combined_ib, combined_strides, vb_path, ib_path, fmt_path, save_fmt_file)
    return combined_vb, combined_ib, combined_strides, ini_entries

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
