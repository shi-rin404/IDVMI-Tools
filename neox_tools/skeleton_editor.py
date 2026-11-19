"""Build a rest-pose hybrid without modifying either source armature."""

import bpy


def _missing_bones(dominant, source):
    target_bones = dominant.data.bones
    fallback = dominant.matrix_world.inverted() @ source.matrix_world
    records = []
    unanchored = 0
    for bone in source.data.bones:
        if bone.name in target_bones:
            continue
        anchor = bone.parent
        while anchor is not None and anchor.name not in target_bones:
            anchor = anchor.parent
        if anchor is None:
            transform = fallback
            unanchored += 1
        else:
            # Keep the local delta, including direction; do not infer body scale
            # from imported bone display lengths. Use the same anchor per branch.
            transform = target_bones[anchor.name].matrix_local @ anchor.matrix_local.inverted()
        records.append((bone, transform.copy()))
    return records, unanchored


class IDVMI_OT_Create_Hybrid_Skeleton(bpy.types.Operator):
    bl_idname = "idvmi_neox.create_hybrid_skeleton"
    bl_label = "Create Hybrid Skeleton"
    bl_description = "Create a new skeleton from two armatures; active bones take priority"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        selected = context.selected_objects
        valid = (context.mode == 'OBJECT' and len(selected) == 2
                 and all(obj.type == 'ARMATURE' for obj in selected)
                 and context.view_layer.objects.active in selected)
        if not valid:
            cls.poll_message_set("Select exactly two armatures in Object Mode; make the dominant one active")
        return valid

    def execute(self, context):
        if not self.poll(context):
            self.report({'ERROR'}, "Select exactly two armatures in Object Mode")
            return {'CANCELLED'}
        dominant = context.view_layer.objects.active
        selected = list(context.selected_objects)
        source = next(obj for obj in selected if obj != dominant)
        result = None
        data = None
        try:
            records, unanchored = _missing_bones(dominant, source)
            data = dominant.data.copy()
            data.name = dominant.data.name + "_Hybrid"
            data.animation_data_clear()
            # A fresh object avoids copying actions, constraints and pose offsets.
            result = bpy.data.objects.new(dominant.name + "_Hybrid", data)
            context.collection.objects.link(result)
            result.matrix_world = dominant.matrix_world.copy()
            result.show_in_front = dominant.show_in_front
            result.display_type = dominant.display_type
            for obj in selected:
                obj.select_set(False)
            result.select_set(True)
            context.view_layer.objects.active = result
            bpy.ops.object.mode_set(mode='EDIT')
            for bone, transform in records:
                new = data.edit_bones.new(bone.name)
                new.head = transform @ bone.head_local
                new.tail = transform @ bone.tail_local
                new.align_roll(transform.to_3x3() @ bone.matrix_local.to_3x3().col[2])
                for prop in (
                    'use_deform', 'use_inherit_rotation', 'use_local_location',
                    'inherit_scale', 'use_relative_parent', 'envelope_distance',
                    'envelope_weight', 'head_radius', 'tail_radius',
                    'use_envelope_multiply', 'bbone_x', 'bbone_z',
                ):
                    setattr(new, prop, getattr(bone, prop))
                if hasattr(bone, 'layers'):
                    new.layers = bone.layers[:]
                for key in bone.keys():
                    new[key] = bone[key]
            # Resolve parents only after every missing bone exists.
            for bone, _ in records:
                new = data.edit_bones[bone.name]
                if bone.parent is not None:
                    new.parent = data.edit_bones[bone.parent.name]
                    # Connecting mismatched endpoints would move the new bone.
                    new.use_connect = (bone.use_connect and
                                       (new.head - new.parent.tail).length < 1e-6)
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception as exc:
            if result is not None:
                if result.mode != 'OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
                bpy.data.objects.remove(result, do_unlink=True)
            if data is not None and data.users == 0:
                bpy.data.armatures.remove(data)
            for obj in selected:
                obj.select_set(True)
            context.view_layer.objects.active = dominant
            self.report({'ERROR'}, "Could not create hybrid skeleton: " + str(exc))
            return {'CANCELLED'}
        message = f"Created {result.name}: added {len(records)} bones; originals preserved"
        if unanchored:
            self.report({'WARNING'}, message + f". {unanchored} bones had no common ancestor; kept world placement")
        else:
            self.report({'INFO'}, message)
        return {'FINISHED'}
