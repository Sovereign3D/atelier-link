"""Spawns one of Roblox's official R15 bodies with the official size box for one UGC item type.

Bodies are Roblox's own rig files (github.com/Roblox/avatar, Legacy/rigs). They are not shipped with this add-on:
the first time a body is used it is downloaded from Roblox and kept in the add-on's user folder. The size limits
come from data/specs.json, generated from the Studio plugin's numbers so both sides agree.
1 Blender unit = 1 stud; bodies face -Y with their feet on the 3D cursor.
"""

import json
import os
import tempfile
import urllib.request

import bpy

DATA = os.path.join(os.path.dirname(__file__), "data")
SKIN_LINEAR = (0.8228, 0.4793, 0.2874)  # Roblox skin 234,184,146 in Blender's linear colour
BOX_LINEAR = (0.0513, 0.5527, 0.1878)
OFFICIAL_BASE = "https://raw.githubusercontent.com/Roblox/avatar/main/Legacy/rigs/"

BODIES = {
    "Blocky": ("R15 Blocky", "R15_Block.fbx"),
    "Rthro": ("R15 Rthro", "R15_Rthro_Normal.fbx"),
    "RthroSlender": ("R15 Rthro Slender", "R15_Rthro_Slender.fbx"),
    "Woman": ("R15 Woman", "R15_Woman.fbx"),
    "Man": ("R15 Man", "R15_Man.fbx"),
}

BODY_ATTACHMENT = {
    "Hat": "Hat_Att",
    "Hair": "Hair_Att",
    "Face": "FaceFront_Att",
    "Eyebrow": "FaceFront_Att",
    "Eyelash": "FaceFront_Att",
    "Neck": "Neck_Att",
    "ShoulderNeck": "Neck_Att",
    "ShoulderCollar": "RightCollar_Att",
    "ShoulderArm": "RightShoulder_Att",
    "Front": "BodyFront_Att",
    "Back": "BodyBack_Att",
    "Waist": "WaistCenter_Att",
}

KINDS = list(BODY_ATTACHMENT)
SCALES = ("Classic", "Normal", "Slender")
SPACING = 6.0


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as fh:
        return json.load(fh)


def to_blender(x, y, z):
    """Roblox (Y up, faces -Z) -> Blender (Z up, faces -Y); inverse of the packer's mapping."""
    return (-x, z, y)


def official_url(body):
    return OFFICIAL_BASE + BODIES[body][1]


def _body_dir():
    override = os.environ.get("ATELIER_BODY_DIR")
    if override:
        return override
    try:
        return bpy.utils.extension_path_user(__package__, path="bodies", create=True)
    except (AttributeError, ValueError):
        path = os.path.join(bpy.utils.user_resource("CONFIG"), "atelier_bodies")
        os.makedirs(path, exist_ok=True)
        return path


def _fetch(url, path):
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = resp.read()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".part")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def body_file(body, fetch=_fetch):
    """Local path of an official body file, downloading it from Roblox the first time."""
    path = os.path.join(_body_dir(), BODIES[body][1])
    if not os.path.exists(path):
        fetch(official_url(body), path)
    return path


def _material(name, colour, alpha=1.0):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (*colour, 1.0)
            bsdf.inputs["Alpha"].default_value = alpha
            bsdf.inputs["Roughness"].default_value = 0.8
        if alpha < 1.0 and hasattr(mat, "surface_render_method"):
            mat.surface_render_method = "BLENDED"
    mat.diffuse_color = (*colour, alpha)
    return mat


def _box_mesh(name, size):
    sx, sy, sz = size
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    verts = [(x, y, z) for x in (-hx, hx) for y in (-hy, hy) for z in (-hz, hz)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def _next_origin(cursor):
    taken = [c for c in bpy.data.collections if c.name.startswith("Atelier Kit")]
    return (cursor[0] + SPACING * len(taken), cursor[1], cursor[2])


def _base(name):
    return name.split(".")[0]


def _import_body(path, col):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=path)
    imported = [o for o in bpy.data.objects if o not in before]
    for obj in imported:
        for owner in list(obj.users_collection):
            owner.objects.unlink(obj)
        col.objects.link(obj)
    return imported


def _geo_points(imported):
    return [o.matrix_world @ v.co for o in imported
            if o.type == "MESH" and _base(o.name).endswith("_Geo") for v in o.data.vertices]


def _studs_and_floor(imported, origin):
    """The rig files are authored in centimetres (scale 0.01): bring them to studs, feet on the origin."""
    roots = [o for o in imported if o.parent is None]
    bpy.context.view_layer.update()
    pts = _geo_points(imported)
    factor = 100.0 if max(p.z for p in pts) - min(p.z for p in pts) < 0.5 else 1.0
    for root in roots:
        root.scale = root.scale * factor
        root.location = root.location * factor
    bpy.context.view_layer.update()
    pts = _geo_points(imported)
    shift = (
        origin[0] - (min(p.x for p in pts) + max(p.x for p in pts)) / 2,
        origin[1] - (min(p.y for p in pts) + max(p.y for p in pts)) / 2,
        origin[2] - min(p.z for p in pts),
    )
    for root in roots:
        root.location = (root.location.x + shift[0], root.location.y + shift[1], root.location.z + shift[2])
    bpy.context.view_layer.update()


def spawn(kind, scale, body):
    specs = _load("specs.json")
    box = specs["boxes"][scale][kind]
    label = specs["labels"].get(kind, kind)
    path = body_file(body)

    origin = _next_origin(tuple(bpy.context.scene.cursor.location))
    col = bpy.data.collections.new(f"Atelier Kit · {label} · {scale} · {BODIES[body][0]}")
    bpy.context.scene.collection.children.link(col)

    imported = _import_body(path, col)
    _studs_and_floor(imported, origin)

    skin = _material("Atelier Skin", SKIN_LINEAR)
    for obj in imported:
        name = _base(obj.name)
        if obj.type == "MESH" and name.endswith("_Geo"):
            obj.data.materials.clear()
            obj.data.materials.append(skin)
        elif obj.type == "MESH" and (name.endswith("_Att") or "Cage" in name):
            obj.hide_render = True
            obj.display_type = "WIRE"

    att = next(o for o in imported if _base(o.name) == BODY_ATTACHMENT[kind])
    anchor = att.matrix_world.translation
    off = box["offset"]
    dx, dy, dz = to_blender(off[0], off[1], off[2])
    sx, sy, sz = box["size"]
    bounds = bpy.data.objects.new("Bounds", _box_mesh(f"Bounds {label}", (sx, sz, sy)))
    bounds.location = (anchor.x + dx, anchor.y + dy, anchor.z + dz)
    bounds.data.materials.append(_material("Atelier Bounds", BOX_LINEAR, 0.25))
    bounds.display_type = "SOLID"
    bounds.show_wire = True
    bounds.color = (*BOX_LINEAR, 0.25)
    bounds.hide_render = True
    bounds["atelier_kind"] = kind
    bounds["atelier_scale"] = scale
    col.objects.link(bounds)
    return col
