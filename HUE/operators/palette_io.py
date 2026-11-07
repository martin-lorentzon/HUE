# Copyright (C) 2026 Clonephaze
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ..utilities.palette_utilities import import_gpl_palette, serialize_gpl
from .base_operators import BaseOperator

_TARGET_ITEMS = [
    ("FILL", "Fill", "Simple Fill tool palette"),
    ("RANDOM", "Random", "Random Color tool palette"),
]


def _get_tool_and_attr(context, target):
    scene = context.scene
    if target == "RANDOM":
        return scene.hue_random_color_tool, "random_palette"
    return scene.hue_simple_fill_tool, "preset_palette"


class HUE_OT_import_palette(BaseOperator, ImportHelper):
    """Import a GIMP palette (.gpl) as a new palette and assign it to this tool"""

    bl_label = "Import Palette"
    bl_idname = "hue.import_palette"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".gpl"
    filter_glob: StringProperty(default="*.gpl", options={'HIDDEN'})
    target: EnumProperty(items=_TARGET_ITEMS, options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        try:
            palette, skipped = import_gpl_palette(self.filepath)
        except (OSError, ValueError) as e:
            self.report({"ERROR"}, f"Could not import palette: {e}")
            return {"CANCELLED"}

        tool, attr = _get_tool_and_attr(context, self.target)
        setattr(tool, attr, palette)
        if self.target == "FILL":
            tool.active_preset_index = 0

        msg = f"Imported palette \"{palette.name}\" ({len(palette.colors)} colors)"
        if skipped:
            self.report({"WARNING"}, f"{msg}; skipped {skipped} invalid line(s)")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class HUE_OT_export_palette(BaseOperator, ExportHelper):
    """Export this tool's palette as a GIMP palette (.gpl)"""

    bl_label = "Export Palette"
    bl_idname = "hue.export_palette"
    bl_options = {'REGISTER'}

    filename_ext = ".gpl"
    filter_glob: StringProperty(default="*.gpl", options={'HIDDEN'})
    target: EnumProperty(items=_TARGET_ITEMS, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        tool, attr = _get_tool_and_attr(context, self.target)
        palette = getattr(tool, attr)
        if not palette or len(palette.colors) == 0:
            self.report({"ERROR"}, "Palette is empty, nothing to export")
            return {"CANCELLED"}
        self.filepath = bpy.path.clean_name(palette.name) + ".gpl"
        return super().invoke(context, event)

    def execute(self, context):
        tool, attr = _get_tool_and_attr(context, self.target)
        palette = getattr(tool, attr)
        if not palette or len(palette.colors) == 0:
            self.report({"ERROR"}, "Palette is empty, nothing to export")
            return {"CANCELLED"}
        text = serialize_gpl(palette.name, [tuple(pc.color) for pc in palette.colors])
        try:
            with open(self.filepath, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except OSError as e:
            self.report({"ERROR"}, f"Could not write file: {e}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Exported {len(palette.colors)} colors to {self.filepath}")
        return {"FINISHED"}
