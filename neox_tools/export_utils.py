from mathutils import Vector
import struct

def writeuint8(data):
    return struct.pack('B', data)

def writeuint16(data):
    return struct.pack('H', data)

def writeuint32(data):
    return struct.pack('I', data)

def writefloat(data):
    return struct.pack('f', data)