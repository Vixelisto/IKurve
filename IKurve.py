import bpy
from bpy.types import Operator
from bpy.props import IntProperty, BoolProperty


bl_info = {
    "name": "IKurve",
    "author": "Vixelisto",
    "version": (1, 0),
    "blender": (2, 8, 0),
    "location": "Context Menu (Right-Click) in Object Mode",
    "description": "Creates an IK armature with bones along the selected curve",
    "warning": "",
    "wiki_url": "",
    "category": "Animation",
}


class OBJECT_OT_CreateBonesAlongCurve(Operator):
    bl_idname = "object.create_bones_along_curve"
    bl_label = "Rig along Curve"
    bl_description = "Creates an armature with bones along the selected curve"
    bl_options = {'REGISTER', 'UNDO'}

    bone_count: IntProperty(
        name="Bones",
        description="Sets the number of bones to create along the curve",
        default=10,
        min=1,
    )

    equal_length: BoolProperty(
        name="Even bones length",
        description="If enabled, all bones will have the same length",
        default=False,
    )

    ik_rig: BoolProperty(
        name="IK rig",
        description="If enabled, adds an IK constraint to the last bone and sets up an IK target at the end",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'CURVE'

    def execute(self, context):
        curve_obj = context.active_object

        if not curve_obj or curve_obj.type != 'CURVE':
            self.report({'ERROR'}, "Please select an active curve")
            return {'CANCELLED'}

        num_bones = self.bone_count
        equal_length = self.equal_length

        # Get the evaluated curve to access sampled points
        depsgraph = context.evaluated_depsgraph_get()
        eval_curve = curve_obj.evaluated_get(depsgraph)

        # Convert the evaluated curve to a new mesh
        mesh = bpy.data.meshes.new_from_object(eval_curve)

        # Get the vertex positions from the mesh
        verts = mesh.vertices

        max_bones = len(verts) - 1  # Maximum possible bones based on curve resolution

        # Clamp bone_count to max_bones
        if num_bones > max_bones:
            num_bones = max_bones
            self.bone_count = max_bones  # Update property to reflect change
            self.report({'WARNING'}, f"Max {max_bones} Bones for this curve resolution.")

        # Ensure there are enough points to create the bones
        if len(verts) < num_bones + 1:
            self.report({'ERROR'}, "Curve resolution is insufficient for the bone count.")
            bpy.data.meshes.remove(mesh)
            return {'CANCELLED'}

        # Calculate cumulative lengths along the curve
        total_length = 0.0
        cumulative_lengths = [0.0]
        for i in range(1, len(verts)):
            seg_length = (verts[i].co - verts[i - 1].co).length
            total_length += seg_length
            cumulative_lengths.append(total_length)

        points = []
        if equal_length:
            # Equal length for each bone
            bone_length = total_length / num_bones
            # Positions along the curve where bones should start
            bone_positions = [bone_length * i for i in range(num_bones + 1)]
        else:
            # Positions based on vertex indices
            bone_positions = [cumulative_lengths[int(i * (len(cumulative_lengths) - 1) / num_bones)] for i in range(num_bones + 1)]

        # Interpolate positions along the curve
        for pos in bone_positions:
            for i in range(1, len(cumulative_lengths)):
                if cumulative_lengths[i - 1] <= pos <= cumulative_lengths[i]:
                    segment_length = cumulative_lengths[i] - cumulative_lengths[i - 1]
                    if segment_length == 0:
                        t = 0
                    else:
                        t = (pos - cumulative_lengths[i - 1]) / segment_length
                    point = verts[i - 1].co.lerp(verts[i].co, t)
                    points.append(curve_obj.matrix_world @ point)
                    break
            else:
                # Si aucun segment correspondant n'est trouvé, ajouter le dernier point
                point = verts[-1].co.copy()
                points.append(curve_obj.matrix_world @ point)

        # Clean up the temporary mesh
        bpy.data.meshes.remove(mesh)

        # If a previously created armature exists, delete it
        armature_name = curve_obj.get("bac_armature_name")
        if armature_name:
            old_armature = bpy.data.objects.get(armature_name)
            if old_armature:
                # Delete armature object
                bpy.data.objects.remove(old_armature, do_unlink=True)
                # Delete armature data
                if old_armature.data:
                    bpy.data.armatures.remove(old_armature.data, do_unlink=True)

        # Create a new armature
        armature_data = bpy.data.armatures.new("Armature")
        armature_obj = bpy.data.objects.new("Armature", armature_data)
        context.collection.objects.link(armature_obj)

        # Temporarily set the armature as the active object to edit bones
        prev_active = context.view_layer.objects.active
        context.view_layer.objects.active = armature_obj
        bpy.ops.object.mode_set(mode='EDIT', toggle=False)

        # Create bones along the points
        armature = armature_obj.data
        prev_bone = None
        bone_names = []
        for i in range(num_bones):
            bone = armature.edit_bones.new(f"Bone.{i+1}")
            bone.head = points[i]
            bone.tail = points[i + 1]
            bone_names.append(bone.name)
            if prev_bone:
                bone.parent = prev_bone
                bone.use_connect = True
            prev_bone = bone

        if self.ik_rig:
            # Extrude a new bone from the last bone
            last_bone = armature.edit_bones[bone_names[-1]]
            ik_target_bone = armature.edit_bones.new("IK_Target")
            ik_target_bone_name = ik_target_bone.name
            ik_target_bone.head = last_bone.tail
            # Extend the tail in the same direction as the last bone
            direction = last_bone.tail - last_bone.head
            if direction.length == 0:
                direction = bpy.Vector((0, 0, 1))
            ik_target_bone.tail = last_bone.tail + direction.normalized() * (total_length / num_bones)
            ik_target_bone.parent = None
            ik_target_bone.use_connect = False
            #bpy.data.objects["Armature"].pose.bones["Bone.5"].constraints["IK" ].subtarget

        # Return to Object mode
        bpy.ops.object.mode_set(mode='OBJECT')

        if self.ik_rig:
            # Add IK constraint to the last bone
            last_bone_pose = armature_obj.pose.bones[bone_names[-1]]
            ik_constraint = last_bone_pose.constraints.new('IK')
            ik_constraint.chain_count = num_bones
            ik_constraint.target = armature_obj
            ik_constraint.subtarget = ik_target_bone_name

            # Optionally, you can add a pole target
            # For simplicity, we won't add a pole target here

        # Restore previous active object
        context.view_layer.objects.active = prev_active

        # Store the armature name in the curve
        curve_obj["bac_armature_name"] = armature_obj.name

        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "bone_count")
        layout.prop(self, "equal_length")
        layout.prop(self, "ik_rig")


def menu_func(self, context):
    if context.active_object and context.active_object.type == 'CURVE':
        self.layout.operator_context = 'INVOKE_DEFAULT'
        self.layout.operator(OBJECT_OT_CreateBonesAlongCurve.bl_idname, icon='BONE_DATA')


def register():
    bpy.utils.register_class(OBJECT_OT_CreateBonesAlongCurve)
    bpy.types.VIEW3D_MT_object_context_menu.append(menu_func)


def unregister():
    bpy.utils.unregister_class(OBJECT_OT_CreateBonesAlongCurve)
    bpy.types.VIEW3D_MT_object_context_menu.remove(menu_func)


if __name__ == "__main__":
    register()
