"""Watches the scene, packs changed meshes and publishes them to the link server."""

import base64
import hashlib
import secrets
import time

import bpy
import numpy as np

from . import packer
from .server import LinkServer

QUIET_SECONDS = 0.25


def _base_color_image(obj):
    for slot in obj.material_slots:
        mat = slot.material
        if not mat or not mat.use_nodes or not mat.node_tree:
            continue
        for node in mat.node_tree.nodes:
            if node.type != "BSDF_PRINCIPLED":
                continue
            socket = node.inputs.get("Base Color")
            if socket and socket.is_linked:
                source = socket.links[0].from_node
                if source.type == "TEX_IMAGE" and source.image:
                    return source.image
    return None


def texture_rgba(obj, size):
    """Base Color image of the object, resized to size x size, RGBA8, top row first."""
    image = _base_color_image(obj)
    if image is None:
        return None
    width, height = image.size
    if width == 0 or height == 0:
        return None
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(height, width, 4)
    rows = (np.arange(size) * height // size)
    cols = (np.arange(size) * width // size)
    resized = pixels[rows][:, cols]
    resized = resized[::-1]
    rgba = (np.clip(resized, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8).tobytes()
    return {
        "name": obj.name,
        "size": size,
        "hash": hashlib.sha1(rgba).hexdigest()[:16],
        "rgba": base64.b64encode(rgba).decode("ascii"),
    }


def collect(context, source, collection_name):
    if source == "COLLECTION":
        col = bpy.data.collections.get(collection_name)
        objects = col.all_objects if col else []
    else:
        objects = [o for o in context.view_layer.objects if o.select_get()]
        active = context.view_layer.objects.active
        if not any(o.type == "MESH" for o in objects) and active is not None:
            objects = [active]
    return sorted((o for o in objects if o.type == "MESH" and not _in_kit(o)), key=lambda o: o.name)


def _in_kit(obj):
    """Reference bodies and size boxes from the Spawn Kit stay in Blender."""
    return any(c.name.startswith("Atelier Kit") for c in obj.users_collection)


class Tracker:
    def __init__(self):
        self.server = None
        self.pair_code = ""
        self._cache = {}
        self._textures = {}
        self._dirty = set()
        self._textures_dirty = True
        self._last_change = 0.0
        self._timer_on = False
        self.last_error = ""

    @property
    def settings(self):
        # Read fresh every time: Ctrl+Z frees and reloads the scene, so a stored reference would point at freed
        # memory and crash Blender on the next update.
        return getattr(bpy.context.scene, "atelier_link", None)

    @property
    def running(self):
        return self.server is not None and self.server.running

    def start(self, settings, pair_code=None):
        self.stop()
        self.pair_code = pair_code or f"{secrets.randbelow(1000000):06d}"
        server = LinkServer(settings.port, self.pair_code)
        server.start()
        self.server = server
        self._cache.clear()
        self._textures.clear()
        self._textures_dirty = True
        self.push()

    def stop(self):
        if self.server:
            self.server.stop()
        self.server = None
        self.pair_code = ""

    def selection_changed(self, context, source, collection_name):
        names = {o.name for o in collect(context, source, collection_name)}
        return names != set(self._cache)

    def on_depsgraph(self, depsgraph):
        if not self.running:
            return
        settings = self.settings
        if settings and self.selection_changed(bpy.context, settings.source, settings.collection):
            self._dirty.add("")
        for update in depsgraph.updates:
            data = update.id
            if isinstance(data, bpy.types.Object) and data.type == "MESH":
                self._dirty.add(data.name)
            elif isinstance(data, (bpy.types.Image, bpy.types.Material)):
                self._textures_dirty = True
        if settings and settings.auto_sync and (self._dirty or self._textures_dirty):
            self._last_change = time.monotonic()
            if not self._timer_on:
                self._timer_on = True
                bpy.app.timers.register(self._on_timer, first_interval=QUIET_SECONDS)

    def after_undo(self):
        """Undo and redo replace every object: resend the whole selection."""
        self._cache.clear()
        self._textures.clear()
        self._textures_dirty = True
        if self.running and not self._timer_on:
            self._last_change = time.monotonic()
            self._timer_on = True
            bpy.app.timers.register(self._on_timer, first_interval=QUIET_SECONDS)

    def _on_timer(self):
        if not self.running:
            self._timer_on = False
            return None
        waited = time.monotonic() - self._last_change
        if waited < QUIET_SECONDS:
            return QUIET_SECONDS - waited
        self._timer_on = False
        self.push()
        return None

    def push(self, force=False):
        if not self.running:
            return
        settings = self.settings
        if settings is None:
            return
        context = bpy.context
        depsgraph = context.evaluated_depsgraph_get()
        objects = collect(context, settings.source, settings.collection)
        published = []
        textures = {}
        try:
            for obj in objects:
                name = obj.name
                if force or name in self._dirty or name not in self._cache:
                    data = packer.pack_object(obj, depsgraph, settings.scale)
                    data["texture"] = None
                    self._cache[name] = data
                data = self._cache[name]
                if force or self._textures_dirty or name not in self._textures:
                    texture = texture_rgba(obj, int(settings.texture_size))
                    if texture is None:
                        self._textures.pop(name, None)
                    elif self._textures.get(name, {}).get("hash") != texture["hash"]:
                        self._textures[name] = texture
                        textures[name] = texture
                texture_hash = self._textures.get(name, {}).get("hash")
                if data["texture"] != texture_hash:
                    data = dict(data, texture=texture_hash)
                    self._cache[name] = data
                published.append(data)
            self.last_error = ""
        except Exception as err:
            self.last_error = str(err)
            return
        finally:
            self._dirty.clear()
            self._textures_dirty = False
        for name in list(self._cache):
            if name not in {o.name for o in objects}:
                del self._cache[name]
                self._textures.pop(name, None)
        self.server.publish(published, textures)


TRACKER = Tracker()
