"""Turns a Blender mesh object into the compact form Atelier sends to Studio."""

import base64
from array import array


def to_roblox(x, y, z):
    """Blender (Z up, model faces -Y) to Roblox (Y up, faces -Z), the same mapping as Roblox's recommended FBX
    export (Forward +Z, Up Y): a mesh faces the same way in Live Link as after Import 3D."""
    return (-x, z, y)


def _b64(values):
    return base64.b64encode(values.tobytes()).decode("ascii")


def _cframe(matrix_world, scale):
    loc, rot, _ = matrix_world.decompose()
    m = rot.to_matrix()
    px, py, pz = to_roblox(loc.x * scale, loc.y * scale, loc.z * scale)
    # Change of basis P * R * P with P = (x, y, z) -> (-x, z, y) (P is its own inverse).
    r00, r01, r02 = m[0][0], -m[0][2], -m[0][1]
    r10, r11, r12 = -m[2][0], m[2][2], m[2][1]
    r20, r21, r22 = -m[1][0], m[1][2], m[1][1]
    return [px, py, pz, r00, r01, r02, r10, r11, r12, r20, r21, r22]


def pack_object(obj, depsgraph, scale):
    """Evaluated mesh (modifiers applied), triangulated, in Roblox axes and studs."""
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        _, _, obj_scale = obj.matrix_world.decompose()
        sx, sy, sz = obj_scale.x * scale, obj_scale.y * scale, obj_scale.z * scale

        positions = array("f")
        for v in mesh.vertices:
            positions.extend(to_roblox(v.co.x * sx, v.co.y * sy, v.co.z * sz))

        # Blender 4.1+ has corner_normals; older versions compute split normals onto the loops.
        if hasattr(mesh, "corner_normals"):
            corner_normals = [c.vector for c in mesh.corner_normals]
        else:
            mesh.calc_normals_split()
            corner_normals = [loop.normal for loop in mesh.loops]
        uv_layer = mesh.uv_layers.active
        uv_data = uv_layer.data if uv_layer else None

        indices = array("I")
        normals = array("f")
        uvs = array("f")
        for tri in mesh.loop_triangles:
            indices.extend(tri.vertices)
            for loop_index in tri.loops:
                n = corner_normals[loop_index]
                normals.extend(to_roblox(n.x, n.y, n.z))
                if uv_data is not None:
                    u, v = uv_data[loop_index].uv
                    uvs.extend((u, 1.0 - v))
                else:
                    uvs.extend((0.0, 0.0))

        return {
            "name": obj.name,
            "tris": len(mesh.loop_triangles),
            "verts": len(mesh.vertices),
            "positions": _b64(positions),
            "indices": _b64(indices),
            "normals": _b64(normals),
            "uvs": _b64(uvs),
            "cframe": _cframe(obj.matrix_world, scale),
        }
    finally:
        evaluated.to_mesh_clear()
