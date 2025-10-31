import bpy
from ...neox_tools.utils.gim_crypt import decode_gim_file, encode_gim_file
from xml.etree import ElementTree as ET
from ...xxhash import xxhash

class IDVMI_OT_Bind_Gim(bpy.types.Operator):
    bl_idname = "idvmi_neox.bind_gim"
    bl_label = "Bind Gim to Socket"

    def execute(self, context):
        gim_path = bpy.path.abspath(context.scene.socket_default_gim_selector)
        socket_name = context.scene.default_socket_name.strip()
        gim_socket_path = context.scene.gim_path.replace("\\", "/").strip()

        root_element: ET.Element = decode_gim_file(gim_path)

        socket_objects = root_element.find("SocketObject")

        for element in socket_objects:
            if element.attrib["Name"] == socket_name:
                ET.SubElement(element, "Object", {"CastShadow": "true",
                                                         "Id": str(xxhash.xxh64(socket_name.encode()).hexdigest()).lower(),
                                                         "Inherit": "263",
                                                         "Loading": "4",
                                                         "Name": socket_name,
                                                         "Uri": gim_socket_path})
                break
        
        with open(gim_path, "wb") as gim_file:
            gim_file.write(encode_gim_file(root_element))

        self.report({'INFO'}, f"Exported: {gim_path}")
        return {'FINISHED'}