# Copyright (C) 2024 Kai Fardreamer <tojynick@protonmail.com>
# Copyright (C) 2026 Clonephaze
#
# SPDX-License-Identifier: GPL-3.0-or-later

# bl_info is only read when installed as a legacy add-on,
# extensions use blender_manifest.toml instead (keep the two in sync)
bl_info = {
    "name": "HUE",
    "description": "A set of tools to make vertex painting easier",
    "author": "Fardreamer Labs, Martin Lorentzon",
    "version": (1, 2, 2),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > HUE",
    "doc_url": "https://github.com/Fardreamer-Labs/HUE",
    "tracker_url": "https://github.com/Fardreamer-Labs/HUE/issues",
    "support": "COMMUNITY",
    "category": "Paint",
}


# ——————————————————————————————————————————————————————————————————————
# MARK: IMPORTS
# ——————————————————————————————————————————————————————————————————————


# fmt: off
import sys

# Drop every cached submodule of this package and import again on reload
if "bpy" in locals():
    # Also clear the package's submodule attributes, or "from . import x" reuses stale ones
    _pkg = sys.modules[__name__]
    for _name in [n for n in sys.modules if n.startswith(__name__ + ".")]:
        del sys.modules[_name]
        _attr = _name[len(__name__) + 1:].split(".")[0]
        if hasattr(_pkg, _attr):
            delattr(_pkg, _attr)

import bpy

from . import operators, preferences, property_groups, ui
from .utilities.palette_utilities import register_handlers, unregister_handlers
# fmt: on


# ——————————————————————————————————————————————————————————————————————
# MARK: REGISTRATION
# ——————————————————————————————————————————————————————————————————————


packages = [property_groups, operators, ui]


def register():
    preferences.register()

    for package in packages:
        package.register()

    register_handlers()

    # Startup defaults — own handler + timer so they survive file loads
    bpy.app.handlers.load_post.append(preferences._apply_startup_defaults)
    bpy.app.timers.register(preferences._apply_startup_defaults, first_interval=0)


def unregister():
    if preferences._apply_startup_defaults in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(preferences._apply_startup_defaults)

    unregister_handlers()

    for package in reversed(packages):
        package.unregister()

    preferences.unregister()
