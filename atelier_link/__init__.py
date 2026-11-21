# SPDX-License-Identifier: GPL-3.0-or-later

# Read by Blender 2.93 to 4.1 (Install from file). Blender 4.2+ reads blender_manifest.toml instead.
bl_info = {
    "name": "Atelier Link",
    "author": "Sovereign3D",
    "version": (1, 0, 1),
    "blender": (2, 93, 0),
    "location": "3D View > Sidebar (N) > Atelier",
    "description": "Live preview of your meshes in Roblox Studio with the Atelier plugin",
    "category": "Import-Export",
}


def register():
    from . import ui
    ui.register()


def unregister():
    from . import ui
    ui.unregister()
