import bpy, re, os, json
from ..extract_frame_dump.extract_frame_dump import _ensure_dir_ok

_DRAW_CALL_RE = re.compile(r"(\d{6})-vb\d+=[a-f0-9]{8}(?:-|\.|$)", re.IGNORECASE)

# ---------- OP: Set Textures ----------
class IDVMI_OT_set_textures(bpy.types.Operator):
    bl_idname = "idvmi_migoto.set_textures"
    bl_label = "Set Textures"

    def execute(self, context):
        try:
            frame_dump = _ensure_dir_ok(context.scene.frame_dump_selector, must_exist=True)
            matched_objects, textured_objects = setTextures(frame_dump)
        except Exception as e:
            self.report({'ERROR'}, f"Set Textures Error: {e}")
            return {'CANCELLED'}

        if matched_objects == 0:
            self.report({'WARNING'}, "No visible imported 3DM mesh objects matched '<draw>-vbN=<hash>'")
        elif textured_objects == 0:
            self.report({'WARNING'}, f"Matched {matched_objects} mesh object(s), but found no t0 textures")
        else:
            self.report({'INFO'}, f"Textures set on {textured_objects} of {matched_objects} mesh object(s)")
        return {'FINISHED'}


def _load_texture_usage(frame_dump, files):
    texture_usage_path = None
    if "TextureUsage.json" in files:
        texture_usage_path = os.path.join(frame_dump, "TextureUsage.json")
    else:
        character_usage_path = os.path.join(frame_dump, "Character", "TextureUsage.json")
        if os.path.isfile(character_usage_path):
            texture_usage_path = character_usage_path

    if not texture_usage_path:
        return None

    with open(texture_usage_path, "r") as f:
        return json.load(f)


def _find_t0_texture(frame_dump, files, draw_call, texture_usage):
    if texture_usage is not None:
        draw_usage = texture_usage.get(draw_call)
        if not draw_usage:
            return None
        t0_hash = draw_usage.get("t0")
        if not t0_hash:
            return None

        for file in files:
            if t0_hash in file:
                return file

        character_texture = os.path.join(frame_dump, "Character", f"ps-t0={t0_hash}.dds")
        if os.path.isfile(character_texture):
            return os.path.join("Character", f"ps-t0={t0_hash}.dds")

        return None

    for file in files:
        if f"{draw_call}-ps-t0=" in file or re.search(fr"{draw_call}\.\d+-\[.*?\]-ps-t0=", file):
            return file

    return None
    
def setTextures(frame_dump):
    files = os.listdir(frame_dump)
    texture_usage = _load_texture_usage(frame_dump, files)
    matched_objects = 0
    textured_objects = 0

    for obj in bpy.data.objects:
        if obj.type != 'MESH' or obj.hide_get() or not obj.visible_get():
            continue

        object_name = _DRAW_CALL_RE.search(obj.name)
        
        if object_name:
            draw_call = object_name.group(1)
        else:
            continue
        matched_objects += 1

        material_name = f"Diffuse-t0_{draw_call}"
        material = bpy.data.materials.get(material_name) or bpy.data.materials.new(material_name)
        material.use_nodes = True        

        nodes = material.node_tree.nodes
        links = material.node_tree.links
        bsdf = nodes.get("Principled BSDF")

        if not bsdf:
            bsdf = nodes.new("ShaderNodeBsdfPrincipled")
            bsdf.location = (200, 0)
            # Output yoksa ekle
            out = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None)
            if not out:
                out = nodes.new("ShaderNodeOutputMaterial")
                out.location = (400, 0)
            links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

        texture = None
        for n in nodes:
            if n.type == 'TEX_IMAGE':
                texture = n
                break
        if not texture:
            texture = nodes.new("ShaderNodeTexImage")
            texture.location = (-400, 0)

        if obj.data.materials:
            obj.data.materials[0] = material
        else:
            obj.data.materials.append(material)

        texture_path = _find_t0_texture(frame_dump, files, draw_call, texture_usage)

        if not texture_path:
            continue

        texture.image = bpy.data.images.load(os.path.join(frame_dump, texture_path))
        textured_objects += 1
        
        has_color_link = any(
            l.from_node == texture and l.from_socket.name == "Color" and l.to_node == bsdf and l.to_socket.name == "Base Color"
            for l in links
        )
        if not has_color_link:
            links.new(texture.outputs["Color"], bsdf.inputs["Base Color"])

        for link in list(links):
            if link.from_node == texture and link.from_socket.name == "Alpha" and link.to_node == bsdf and link.to_socket.name == "Alpha":
                links.remove(link)
        # bsdf.inputs["Alpha"].default_value = 1.0
        # material.blend_method = 'OPAQUE'
    return matched_objects, textured_objects
