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
COLUMNS_KEY = "hue_columns"


def palette_columns(palette):
    """Swatch columns to draw *palette* with (imported layout or the default)."""
    try:
        # Registered Palette.hue_columns (property_groups); the key lookup is the
        # fallback for when the property isn't registered (e.g. unit tests).
        if hasattr(palette, COLUMNS_KEY):
            cols = int(getattr(palette, COLUMNS_KEY))
        else:
            cols = int(palette.get(COLUMNS_KEY, SWATCH_COLS))
    except (TypeError, ValueError, AttributeError):
        return SWATCH_COLS
    return cols if cols > 0 else SWATCH_COLS


def parse_gpl_columns(text):
    """Return the ``Columns:`` value of GIMP palette text, or ``None``."""
    for line in text.splitlines():
        if line.strip().lower().startswith("columns:"):
            try:
                cols = int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
            return cols if 0 < cols <= 64 else None
    return None


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


def serialize_gpl(name, colors, columns=SWATCH_COLS):
    """Serialize linear ``(r, g, b)`` colors to GIMP palette text."""
    out = [GPL_HEADER, f"Name: {name}", f"Columns: {columns}", "#"]
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
    columns = parse_gpl_columns(text)
    if columns:
        if hasattr(palette, COLUMNS_KEY):
            setattr(palette, COLUMNS_KEY, columns)
        else:
            palette[COLUMNS_KEY] = columns
    return palette, skipped


# MARK: — Sorting ————————————————————————————————————————————————————

_NEUTRAL_SATURATION = 0.12
_HUE_BUCKETS = 12

SORT_METHOD_ITEMS = [
    ("HUE_RAMPS", "Hue Ramps", "Group by hue, each group dark to light, greys last"),
    ("HUE", "Hue", "Rainbow order (red to violet), greys last"),
    ("LIGHTNESS", "Lightness", "Dark to light"),
    ("SATURATION", "Saturation", "Muted to vivid"),
    ("REVERSE", "Reverse", "Flip the current order"),
]


def _luminance(color):
    r, g, b = color[:3]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _color_keys(color):
    """Return ``(hue 0-1, saturation, luminance)`` for a linear color."""
    import colorsys

    r, g, b = (_linear_to_srgb(max(0.0, min(1.0, c))) for c in color[:3])
    h, s, _v = colorsys.rgb_to_hsv(r, g, b)
    return h, s, _luminance(color)


def sort_order_hue_ramps(colors):
    """Return indices that order linear ``(r, g, b)`` colors into hue ramps.

    Colors are grouped by hue (12 buckets, reds wrap around 0), each group
    sorted dark to light. Greys (low saturation) come last, dark to light.
    """
    chromatic = []
    neutral = []
    for i, color in enumerate(colors):
        h, s, lum = _color_keys(color)
        if s < _NEUTRAL_SATURATION:
            neutral.append((lum, i))
        else:
            bucket = int(((h * 360.0 + 180.0 / _HUE_BUCKETS) % 360.0) // (360.0 / _HUE_BUCKETS))
            chromatic.append((bucket, lum, h, i))
    chromatic.sort()
    neutral.sort()
    return [c[3] for c in chromatic] + [n[1] for n in neutral]


def sort_order_hue(colors):
    """Indices in continuous hue order (red to violet), greys last."""
    chromatic = []
    neutral = []
    for i, color in enumerate(colors):
        h, s, lum = _color_keys(color)
        if s < _NEUTRAL_SATURATION:
            neutral.append((lum, i))
        else:
            chromatic.append((h, lum, i))
    chromatic.sort()
    neutral.sort()
    return [c[2] for c in chromatic] + [n[1] for n in neutral]


def sort_order_lightness(colors):
    """Indices ordered dark to light."""
    return sorted(range(len(colors)), key=lambda i: (_luminance(colors[i]), i))


def sort_order_saturation(colors):
    """Indices ordered muted to vivid (ties dark to light)."""
    keys = [_color_keys(c) for c in colors]
    return sorted(range(len(colors)), key=lambda i: (keys[i][1], keys[i][2], i))


def sort_order_reverse(colors):
    """Indices in reverse order."""
    return list(range(len(colors) - 1, -1, -1))


_SORT_FUNCTIONS = {
    "HUE_RAMPS": sort_order_hue_ramps,
    "HUE": sort_order_hue,
    "LIGHTNESS": sort_order_lightness,
    "SATURATION": sort_order_saturation,
    "REVERSE": sort_order_reverse,
}


def sort_palette(palette, method="HUE_RAMPS"):
    """Reorder *palette* colors in place using *method*.

    Returns the index list used (``order[new_index] == old_index``).
    """
    colors = [tuple(pc.color) for pc in palette.colors]
    order = _SORT_FUNCTIONS[method](colors)
    for pc, old_index in zip(palette.colors, order):
        pc.color = colors[old_index]
    return order


# MARK: — Merge close colors ——————————————————————————————————————————

def _linear_to_lab(color):
    """Convert a linear sRGB ``(r, g, b)`` color to CIE Lab (D65)."""
    r, g, b = (max(0.0, min(1.0, c)) for c in color[:3])
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t):
        return t ** (1.0 / 3.0) if t > 0.008856 else 7.787 * t + 16.0 / 116.0

    fx, fy, fz = f(x), f(y), f(z)
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


def _lab_distance_sq(la, lb):
    return sum((p - q) ** 2 for p, q in zip(la, lb))


def color_distance(a, b):
    """Perceptual distance (CIE76 delta E) between two linear colors."""
    return _lab_distance_sq(_linear_to_lab(a), _linear_to_lab(b)) ** 0.5


def merge_close_order(colors, threshold):
    """Decide which linear ``(r, g, b)`` colors survive a merge.

    Colors are visited in order; one within *threshold* (delta E) of an already
    kept color joins that color's group, so groups are anchored on their first
    member and palette order is kept.
    Returns ``(keep, mapping)``: ``keep`` lists surviving indices and
    ``mapping[old_index]`` is the new index of the color it ended up as.
    """
    labs = [_linear_to_lab(c) for c in colors]
    limit_sq = threshold * threshold
    keep = []
    mapping = []
    for i, lab in enumerate(labs):
        target = None
        for new_index, k in enumerate(keep):
            if _lab_distance_sq(lab, labs[k]) <= limit_sq:
                target = new_index
                break
        if target is None:
            keep.append(i)
            target = len(keep) - 1
        mapping.append(target)
    return keep, mapping


# Palette edits aren't covered by Edit Mode undo (it only tracks the mesh), so a
# merge keeps its own snapshot: Restore works in any mode and re-running with a
# new threshold (the F9 redo panel) starts again from the original colors.
_merge_snapshots = {}  # palette name -> {"original", "merged", "keep", "active"}


def clear_merge_snapshots():
    """Forget all saved merges (add-on unregister / reload)."""
    _merge_snapshots.clear()


def _palette_colors(palette):
    return [tuple(pc.color) for pc in palette.colors]


def _same_colors(a, b):
    return len(a) == len(b) and all(abs(x - y) < 1e-5 for ca, cb in zip(a, b) for x, y in zip(ca, cb))


def _set_palette_colors(palette, colors):
    while len(palette.colors) > len(colors):
        palette.colors.remove(palette.colors[len(palette.colors) - 1])
    while len(palette.colors) < len(colors):
        palette.colors.new()
    for pc, color in zip(palette.colors, colors):
        pc.color = color


def _valid_merge_snapshot(palette):
    """The saved merge for *palette* if it still holds the merged colors (read-only)."""
    snap = _merge_snapshots.get(palette.name)
    if snap and _same_colors(snap["merged"], _palette_colors(palette)):
        return snap
    return None


def has_merge_snapshot(palette):
    """True if *palette* still holds the result of a merge that can be restored."""
    return bool(palette) and _valid_merge_snapshot(palette) is not None


def discard_merge_snapshot(palette):
    """Make the palette's current colors the baseline for the next merge."""
    if palette:
        _merge_snapshots.pop(palette.name, None)


def merge_close_colors(palette, threshold, active_index=0):
    """Merge colors in *palette* that are within *threshold* of an earlier one.

    Each group collapses into one color at the position of its first member,
    set to the average of the group (in linear RGB). If the palette still holds
    the result of an earlier merge (a redo with a new threshold), the merge
    starts from the colors that merge started from; call
    :func:`discard_merge_snapshot` first to merge the current colors instead.
    Returns ``(removed_count, new_active_index)``.
    """
    for name in [n for n in _merge_snapshots if n not in bpy.data.palettes]:
        del _merge_snapshots[name]
    snap = _valid_merge_snapshot(palette)
    if snap:
        source, active_index = snap["original"], snap["active"]
    else:
        source = _palette_colors(palette)
    keep, mapping = merge_close_order(source, threshold)
    if 0 <= active_index < len(mapping):
        new_active = mapping[active_index]
    else:
        new_active = max(0, min(active_index, len(keep) - 1))
    if len(keep) == len(source):
        # Nothing merged: palette goes back to the source, nothing to restore.
        _set_palette_colors(palette, source)
        _merge_snapshots.pop(palette.name, None)
        return 0, new_active
    groups = [[] for _ in keep]
    for old_index, new_index in enumerate(mapping):
        groups[new_index].append(source[old_index])
    merged = [tuple(sum(c[i] for c in group) / len(group) for i in range(3)) for group in groups]
    _set_palette_colors(palette, merged)
    _merge_snapshots[palette.name] = {"original": list(source), "merged": _palette_colors(palette),
                                      "keep": keep, "active": active_index}
    return len(source) - len(keep), new_active


def restore_merged_colors(palette, active_index=None):
    """Undo the last merge on *palette*.

    Returns the index to make active (the original position of the color
    *active_index* was merged from, or the one active at merge time), or
    ``None`` if there is nothing to restore.
    """
    snap = _valid_merge_snapshot(palette)
    if not snap:
        return None
    _set_palette_colors(palette, snap["original"])
    del _merge_snapshots[palette.name]
    if active_index is not None and 0 <= active_index < len(snap["keep"]):
        return snap["keep"][active_index]
    return snap["active"]


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
    """Remove load_post handler and forget saved merges."""
    clear_merge_snapshots()
    if _assign_default_palettes in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_assign_default_palettes)
