# Copyright (C) 2026 Clonephaze
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.props import EnumProperty, FloatProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ..utilities.palette_utilities import (
    discard_merge_snapshot, import_gpl_palette, merge_close_colors, palette_columns, restore_merged_colors, serialize_gpl,
    SORT_METHOD_ITEMS, sort_palette,
)
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


class HUE_OT_sort_palette(BaseOperator):
    """Sort this tool's palette into hue ramps (each hue dark to light, neutrals last)"""

    bl_label = "Sort Palette"
    bl_idname = "hue.sort_palette"
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(items=_TARGET_ITEMS, options={'HIDDEN', 'SKIP_SAVE'})
    method: EnumProperty(items=SORT_METHOD_ITEMS, default="HUE_RAMPS", options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        tool, attr = _get_tool_and_attr(context, self.target)
        palette = getattr(tool, attr)
        if not palette or len(palette.colors) == 0:
            self.report({"ERROR"}, "Palette is empty, nothing to sort")
            return {"CANCELLED"}
        order = sort_palette(palette, self.method)
        if self.target == "FILL":
            old_index = tool.active_preset_index
            if old_index in order:
                tool.active_preset_index = order.index(old_index)
        self.report({"INFO"}, f"Sorted {len(order)} colors")
        return {"FINISHED"}


class HUE_OT_merge_close_colors(BaseOperator):
    """Merge colors in this tool's palette that look nearly the same (each group becomes its average color)"""

    bl_label = "Merge Close Colors"
    bl_idname = "hue.merge_close_colors"
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(items=_TARGET_ITEMS, options={'HIDDEN', 'SKIP_SAVE'})
    threshold: FloatProperty(
        name="Threshold",
        description="Colors closer than this perceptual distance (CIE delta E) are merged. About 2 is barely visible",
        default=5.0, min=0.0, max=50.0,
    )

    def invoke(self, context, event):
        # A fresh merge works on the colors you see; only the redo panel (which skips
        # invoke) re-merges from the colors the previous run started with.
        tool, attr = _get_tool_and_attr(context, self.target)
        discard_merge_snapshot(getattr(tool, attr))
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        tool, attr = _get_tool_and_attr(context, self.target)
        palette = getattr(tool, attr)
        if not palette or len(palette.colors) == 0:
            self.report({"ERROR"}, "Palette is empty, nothing to merge")
            return {"CANCELLED"}
        old_index = tool.active_preset_index if self.target == "FILL" else 0
        removed, new_index = merge_close_colors(palette, self.threshold, old_index)
        if self.target == "FILL":
            tool.active_preset_index = new_index
        self.report({"INFO"}, f"Merged {removed} close color(s)" if removed else "No close colors found")
        return {"FINISHED"}


class HUE_OT_restore_merged_colors(BaseOperator):
    """Bring back the colors this tool's palette had before Merge Close Colors"""

    bl_label = "Restore Merged Colors"
    bl_idname = "hue.restore_merged_colors"
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(items=_TARGET_ITEMS, options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        tool, attr = _get_tool_and_attr(context, self.target)
        palette = getattr(tool, attr)
        current = tool.active_preset_index if self.target == "FILL" else None
        active = restore_merged_colors(palette, current) if palette else None
        if active is None:
            self.report({"ERROR"}, "Nothing to restore")
            return {"CANCELLED"}
        if self.target == "FILL":
            tool.active_preset_index = active
        self.report({"INFO"}, "Restored colors from before the merge")
        return {"FINISHED"}


class _SortMenuBase(bpy.types.Menu):
    bl_label = "Sort / Layout"
    target = "FILL"

    def draw(self, context):
        layout = self.layout
        for method, label, _desc in SORT_METHOD_ITEMS:
            op = layout.operator("hue.sort_palette", text=label)
            op.target = self.target
            op.method = method
        tool, attr = _get_tool_and_attr(context, self.target)
        palette = getattr(tool, attr)
        if palette:
            layout.separator()
            layout.prop(palette, "hue_columns")


class HUE_MT_sort_palette_fill(_SortMenuBase):
    bl_idname = "HUE_MT_sort_palette_fill"
    target = "FILL"


class HUE_MT_sort_palette_random(_SortMenuBase):
    bl_idname = "HUE_MT_sort_palette_random"
    target = "RANDOM"


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
        text = serialize_gpl(palette.name, [tuple(pc.color) for pc in palette.colors],
                              palette_columns(palette))
        try:
            with open(self.filepath, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except OSError as e:
            self.report({"ERROR"}, f"Could not write file: {e}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Exported {len(palette.colors)} colors to {self.filepath}")
        return {"FINISHED"}
