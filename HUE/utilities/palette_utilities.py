# Copyright (C) 2026 Clonephaze
#
# SPDX-License-Identifier: GPL-3.0-or-later

from pathlib import Path

import bpy
import bpy.utils.previews

_preset_previews = {}

SWATCH_COLS = 8
_SWATCH_SIZE = 32

DEFAULT_PALETTE_NAME = "Default_Palette"
_DEFAULT_COLORS = [
    (1.0, 0.0, 0.0),
    (1.0, 0.5, 0.0),
    (1.0, 1.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 1.0, 1.0),
    (0.0, 0.0, 1.0),
    (0.5, 0.0, 1.0),
    (1.0, 0.0, 0.5),
    (1.0, 1.0, 1.0),
    (0.75, 0.75, 0.75),
    (0.25, 0.25, 0.25),
    (0.0, 0.0, 0.0),
]


def _linear_to_srgb(c):
    """Convert a single linear-space float to sRGB."""
    if c <= 0.0031308:
        return c * 12.92
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


def _srgb_to_linear(c):
    """Convert a single sRGB float to linear-space."""
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


# MARK: — GIMP palette (.gpl) I/O —————————————————————————————————————

GPL_HEADER = "GIMP Palette"


def parse_gpl(text):
    """Parse GIMP palette text.

    Returns ``(name, colors, skipped)`` where *colors* is a list of linear
    ``(r, g, b)`` float tuples, *name* is the header ``Name:`` (or ``None``)
    and *skipped* counts malformed color lines. Raises ``ValueError`` when
    the text is not a GIMP palette.
    """
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != GPL_HEADER:
        raise ValueError("Not a GIMP palette file")

    name = None
    colors = []
    skipped = 0
    for line in lines[1:]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lower = line.lower()
        if lower.startswith("name:"):
            name = line[5:].strip() or None
            continue
        if lower.startswith("columns:"):
            continue
        parts = line.split()
        try:
            rgb = [int(parts[i]) for i in range(3)]
        except (ValueError, IndexError):
            skipped += 1
            continue
        if any(v < 0 or v > 255 for v in rgb):
            skipped += 1
            continue
        colors.append(tuple(_srgb_to_linear(v / 255.0) for v in rgb))
    return name, colors, skipped


def serialize_gpl(name, colors):
    """Serialize linear ``(r, g, b)`` colors to GIMP palette text."""
    out = [GPL_HEADER, f"Name: {name}", "Columns: 8", "#"]
    for color in colors:
        r, g, b = (
            max(0, min(255, round(_linear_to_srgb(max(0.0, min(1.0, c))) * 255)))
            for c in color[:3]
        )
        out.append(f"{r:3d} {g:3d} {b:3d}\tUntitled")
    return "\n".join(out) + "\n"


def import_gpl_palette(filepath):
    """Create a new palette datablock from a .gpl file.

    Returns ``(palette, skipped)``. Raises ``ValueError`` for invalid files
    and ``OSError`` if the file cannot be read.
    """
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    name, colors, skipped = parse_gpl(text)
    if not colors:
        raise ValueError("Palette file contains no valid colors")
    palette = bpy.data.palettes.new(name or Path(filepath).stem)
    for color in colors:
        palette.colors.new().color = color
    return palette, skipped


def get_color_icon(r, g, b):
    """Get or create a colored swatch icon, fully in-memory."""
    if "main" not in _preset_previews:
        _preset_previews["main"] = bpy.utils.previews.new()
    pcoll = _preset_previews["main"]

    ri, gi, bi = int(r * 255), int(g * 255), int(b * 255)
    key = f"hue_{ri:03d}_{gi:03d}_{bi:03d}"

    if key not in pcoll:
        icon = pcoll.new(key)
        icon.icon_size = (_SWATCH_SIZE, _SWATCH_SIZE)
        pixel = [r, g, b, 1.0]
        icon.icon_pixels_float = pixel * (_SWATCH_SIZE * _SWATCH_SIZE)

    return pcoll[key].icon_id


def get_or_create_default_palette():
    """Get the shared default palette, creating it if it doesn't exist."""
    palette = bpy.data.palettes.get(DEFAULT_PALETTE_NAME)
    if not palette:
        palette = bpy.data.palettes.new(DEFAULT_PALETTE_NAME)
        # Try to read colors from preferences
        prefs_colors = None
        try:
            from ..preferences import get_default_palette_colors
            prefs_colors = get_default_palette_colors()
        except (ImportError, KeyError):
            pass
        colors = prefs_colors if prefs_colors else _DEFAULT_COLORS
        for color in colors:
            pc = palette.colors.new()
            pc.color = color
    return palette


def ensure_palette_assigned(tool, attr_name):
    """If the palette property on *tool* is unset, assign the shared default.

    Safe to call from draw() — uses a deferred timer so it never
    writes to ID data inside a restricted context.
    """
    if getattr(tool, attr_name):
        return

    def _deferred():
        if not getattr(tool, attr_name):
            setattr(tool, attr_name, get_or_create_default_palette())
        return None  # Don't repeat

    bpy.app.timers.register(_deferred, first_interval=0)


def cleanup_previews():
    """Remove preview collections. Called from ui unregister."""
    for pcoll in _preset_previews.values():
        bpy.utils.previews.remove(pcoll)
    _preset_previews.clear()


@bpy.app.handlers.persistent
def _assign_default_palettes(_=None):
    """Assign default palette to tools that don't have one set.

    Registered as a load_post handler and called once from register().
    """
    scene = bpy.context.scene
    palette = get_or_create_default_palette()

    fill_tool = getattr(scene, "hue_simple_fill_tool", None)
    if fill_tool and not fill_tool.preset_palette:
        fill_tool.preset_palette = palette

    random_tool = getattr(scene, "hue_random_color_tool", None)
    if random_tool and not random_tool.random_palette:
        random_tool.random_palette = palette


def register_handlers():
    """Register load_post handler and schedule initial assignment."""
    bpy.app.handlers.load_post.append(_assign_default_palettes)
    bpy.app.timers.register(_assign_default_palettes, first_interval=0)


def unregister_handlers():
    """Remove load_post handler."""
    if _assign_default_palettes in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_assign_default_palettes)
