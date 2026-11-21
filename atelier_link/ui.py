import secrets

import bpy
from bpy.app.handlers import persistent

from . import kit
from .tracker import TRACKER

_KIND_LABELS = {
    "Hat": "Hat", "Hair": "Hair", "Face": "Face", "Eyebrow": "Eyebrow", "Eyelash": "Eyelash", "Neck": "Neck",
    "ShoulderNeck": "Shoulder (neck)", "ShoulderCollar": "Shoulder (collar)", "ShoulderArm": "Shoulder (arm)",
    "Front": "Front", "Back": "Back", "Waist": "Waist",
}


class AtelierLinkSettings(bpy.types.PropertyGroup):
    port: bpy.props.IntProperty(name="Port", default=34873, min=1024, max=65535)
    auto_sync: bpy.props.BoolProperty(name="Auto Sync", default=True)
    source: bpy.props.EnumProperty(
        name="Send",
        items=[
            ("SELECTED", "Selected", "Send the selected mesh objects"),
            ("COLLECTION", "Collection", "Send every mesh in one collection"),
        ],
        default="SELECTED",
    )
    collection: bpy.props.StringProperty(name="Collection", default="")
    texture_size: bpy.props.EnumProperty(
        name="Texture",
        items=[("512", "512", "Preview texture 512 x 512"), ("1024", "1024", "Preview texture 1024 x 1024")],
        default="512",
    )
    scale: bpy.props.FloatProperty(name="Scale", default=1.0, min=0.0001, soft_max=1000.0,
                                   description="Studs per Blender unit")
    kit_kind: bpy.props.EnumProperty(
        name="Item",
        items=[(k, _KIND_LABELS[k], f"Official size box for a {_KIND_LABELS[k].lower()} item") for k in kit.KINDS],
        default="Hat",
    )
    kit_scale: bpy.props.EnumProperty(
        name="Scale",
        items=[(s, s, f"{s} body scale size limits") for s in kit.SCALES],
        default="Classic",
    )
    kit_rig: bpy.props.EnumProperty(
        name="Body",
        items=[(k, v[0], f"Roblox's official {v[0]} body") for k, v in kit.BODIES.items()],
        default="Blocky",
    )


class AtelierLinkPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    pair_code: bpy.props.StringProperty(name="Pair code", default="")

    def draw(self, context):
        self.layout.label(text=f"Pair code: {self.pair_code or 'made on first Start Link'}")


def _prefs():
    return bpy.context.preferences.addons[__package__].preferences


def _pair_code():
    prefs = _prefs()
    if not prefs.pair_code:
        prefs.pair_code = f"{secrets.randbelow(1000000):06d}"
        bpy.context.preferences.is_dirty = True
    return prefs.pair_code


class ATELIER_OT_new_code(bpy.types.Operator):
    bl_idname = "atelier.new_pair_code"
    bl_label = "New Code"
    bl_description = "Make a new pair code. Studio will need the new code once"

    def execute(self, context):
        _prefs().pair_code = f"{secrets.randbelow(1000000):06d}"
        bpy.context.preferences.is_dirty = True
        if TRACKER.running:
            TRACKER.start(context.scene.atelier_link, _prefs().pair_code)
        return {"FINISHED"}


class ATELIER_OT_start(bpy.types.Operator):
    bl_idname = "atelier.start_link"
    bl_label = "Start Link"
    bl_description = "Let the Atelier plugin in Roblox Studio read your meshes"

    def execute(self, context):
        try:
            TRACKER.start(context.scene.atelier_link, _pair_code())
        except OSError as err:
            self.report({"ERROR"}, f"Port {context.scene.atelier_link.port} is busy ({err.strerror})")
            return {"CANCELLED"}
        return {"FINISHED"}


class ATELIER_OT_stop(bpy.types.Operator):
    bl_idname = "atelier.stop_link"
    bl_label = "Stop Link"

    def execute(self, context):
        TRACKER.stop()
        return {"FINISHED"}


class ATELIER_OT_push(bpy.types.Operator):
    bl_idname = "atelier.push_now"
    bl_label = "Push Now"
    bl_description = "Send the current meshes to Studio"

    @classmethod
    def poll(cls, context):
        return TRACKER.running

    def execute(self, context):
        TRACKER.push(force=True)
        if TRACKER.last_error:
            self.report({"ERROR"}, TRACKER.last_error)
            return {"CANCELLED"}
        return {"FINISHED"}


class ATELIER_OT_spawn_kit(bpy.types.Operator):
    bl_idname = "atelier.spawn_kit"
    bl_label = "Spawn"
    bl_description = "Add a body with the official size box for this item at the 3D cursor"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.atelier_link
        try:
            col = kit.spawn(s.kit_kind, s.kit_scale, s.kit_rig)
        except OSError as err:
            self.report({"ERROR"}, f"Couldn't download the body from Roblox ({err}). Check your internet and try again.")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Added {col.name}")
        return {"FINISHED"}


class ATELIER_PT_kit(bpy.types.Panel):
    bl_label = "Spawn Kit"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Atelier"
    bl_order = 1

    def draw(self, context):
        s = context.scene.atelier_link
        col = self.layout.column(align=True)
        col.prop(s, "kit_kind")
        col.prop(s, "kit_scale")
        col.prop(s, "kit_rig")
        row = self.layout.row()
        row.scale_y = 1.4
        row.operator("atelier.spawn_kit", icon="ADD")
        self.layout.label(text="The green box is Roblox's max size.", icon="INFO")
        self.layout.label(text="Bodies are Roblox's official files,", icon="URL")
        self.layout.label(text="downloaded from Roblox on first use.")


class ATELIER_PT_link(bpy.types.Panel):
    bl_label = "Atelier Link"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Atelier"
    bl_order = 0

    def draw(self, context):
        settings = context.scene.atelier_link
        layout = self.layout
        if TRACKER.running:
            box = layout.box()
            box.label(text="Pair code", icon="LINKED")
            row = box.row()
            row.scale_y = 1.6
            row.label(text=" ".join(TRACKER.pair_code))
            row.operator("atelier.new_pair_code", text="", icon="FILE_REFRESH")
            if TRACKER.server and TRACKER.server.locked:
                box.label(text="Wrong codes were tried, link paused", icon="ERROR")
            count = len(TRACKER._cache)
            if count:
                box.label(text=f"Sending {count} mesh{'es' if count != 1 else ''}", icon="CHECKMARK")
            else:
                box.label(text="Select a mesh to send it", icon="INFO")
            layout.operator("atelier.stop_link", icon="CANCEL")
            layout.operator("atelier.push_now", icon="EXPORT")
            if TRACKER.last_error:
                layout.label(text=TRACKER.last_error, icon="ERROR")
        else:
            layout.operator("atelier.start_link", icon="PLAY")
        col = layout.column()
        col.enabled = not TRACKER.running
        col.prop(settings, "port")
        layout.prop(settings, "auto_sync")
        layout.prop(settings, "source", expand=True)
        if settings.source == "COLLECTION":
            layout.prop_search(settings, "collection", bpy.data, "collections", text="")
        layout.prop(settings, "texture_size", expand=True)
        layout.prop(settings, "scale")


@persistent
def _on_depsgraph(scene, depsgraph):
    TRACKER.on_depsgraph(depsgraph)


@persistent
def _on_undo(*_):
    TRACKER.after_undo()


@persistent
def _on_load(_):
    TRACKER.stop()


CLASSES = (AtelierLinkPreferences, ATELIER_OT_new_code, AtelierLinkSettings, ATELIER_OT_start, ATELIER_OT_stop, ATELIER_OT_push, ATELIER_OT_spawn_kit, ATELIER_PT_link, ATELIER_PT_kit)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.atelier_link = bpy.props.PointerProperty(type=AtelierLinkSettings)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    bpy.app.handlers.load_pre.append(_on_load)
    bpy.app.handlers.undo_post.append(_on_undo)
    bpy.app.handlers.redo_post.append(_on_undo)


def unregister():
    TRACKER.stop()
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    if _on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load)
    for handlers in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _on_undo in handlers:
            handlers.remove(_on_undo)
    del bpy.types.Scene.atelier_link
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
