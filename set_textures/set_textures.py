import bpy, re, os, json
from ..extract_frame_dump.extract_frame_dump import _ensure_dir_ok

# ---------- OP: Set Textures ----------
class IDVMI_OT_set_textures(bpy.types.Operator):
    bl_idname = "idvmi_tools.set_textures"
    bl_label = "Set Textures"

    def execute(self, context):
        frame_dump = _ensure_dir_ok(context.scene.frame_dump_selector, must_exist=True, must_be_writable=True)
        setTextures(frame_dump)
        self.report({'INFO'}, f"Textures set")
        return {'FINISHED'}
    
def setTextures(frame_dump):
    files = os.listdir(frame_dump)

    for obj in bpy.data.objects:
        if obj.type != 'MESH' or obj.hide_get() or not obj.visible_get():
            continue

        object_name = re.search(r"(\d{6})-vb\d+=[a-f0-9]{8}.*?.txt", obj.name)
        
        if object_name:
            draw_call = object_name.group(1)
        else:
            continue

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

        texture_path = None

        if "TextureUsage.json" in files:
            with open(os.path.join(frame_dump, "TextureUsage.json"), "r") as f:
                texture_usage = json.load(f)
            
            if draw_call in texture_usage:
                t0_hash = texture_usage[draw_call]["t0"]
            else:
                raise Exception("Wrong TextureUsage.json")

            for file in files:
                if t0_hash in file:
                    texture_path = file
                    break
        else:
            for file in files:
                if f"{draw_call}-ps-t0=" in file or re.search(fr"{draw_call}\.\d+-\[.*?\]-ps-t0=", file):
                    texture_path = file
                    break

        if not texture_path:
            continue

        texture.image = bpy.data.images.load(os.path.join(frame_dump, texture_path))
        
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
    return True