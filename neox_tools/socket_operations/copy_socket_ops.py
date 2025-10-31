import bpy, os
from ..utils.gim_crypt import decode_gim_file, encode_gim_file
from xml.etree import ElementTree as ET

class IDVMI_OT_Copy_Socket(bpy.types.Operator):
    bl_idname = "idvmi_neox.copy_socket"
    bl_label = "Copy Socket"

    def execute(self, context):
        source_gim_path = bpy.path.abspath(context.scene.socket_source_gim_selector)
        target_gim_path = bpy.path.abspath(context.scene.socket_default_gim_selector)        
        socket_name = context.scene.default_socket_name.strip()

        source_gim_root:ET.Element = decode_gim_file(source_gim_path)

        source_socket_objects = source_gim_root.find("SocketObject")
        source_aiming_socket = None

        for source_socket in source_socket_objects:
            if source_socket.attrib["Name"] == socket_name:
                source_aiming_socket = source_socket
                break
        
        if source_aiming_socket == None:
            self.report({'ERROR'}, f"Socket couldn't find in {os.path.basename(source_gim_path)}")
            return {'CANCELLED'}
        
        target_gim_root:ET.Element = decode_gim_file(target_gim_path)

        target_socket_objects = target_gim_root.find("SocketObject")

        # Get highest socket ID of target gim
        MAX = 0

        for target_socket in target_socket_objects:
            _id = target_socket.tag.rsplit("_", 1)
            if len(_id) > 1:
                if _id[1].isnumeric():
                    _id_converted = int(_id[1])
                    if _id_converted > MAX: MAX = _id_converted                    

        ET.SubElement(target_socket_objects, f"Socket_{MAX+1}", source_aiming_socket.attrib)

        with open(target_gim_path, "wb") as target_gim_file:
            target_gim_file.write(encode_gim_file(target_gim_root))

        self.report({'INFO'}, f"Copied from {os.path.basename(source_gim_path)} to {os.path.basename(target_gim_path)}")
        return {'FINISHED'}