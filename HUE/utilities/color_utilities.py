# Copyright (C) 2024 Kai Fardreamer <tojynick@protonmail.com>
# Copyright (C) 2026 Clonephaze
#
# SPDX-License-Identifier: GPL-3.0-or-later

import colorsys
import random
from contextlib import contextmanager

import bpy
import numpy as np


@contextmanager
def ensure_object_mode(obj):
    """Context manager that temporarily switches *obj* to Object mode if needed.

    Usage::

        with ensure_object_mode(obj):
            # work with mesh data ...
    """
    was_edit = (obj.mode == "EDIT")
    if was_edit:
        bpy.ops.object.mode_set(mode="OBJECT")
    try:
        yield
    finally:
        if was_edit:
            bpy.ops.object.mode_set(mode="EDIT")


def get_target_objects(context):
    """Return the objects tools should act on.

    In Edit Mode this is every object being edited (even if it is not selected in
    Object Mode); otherwise it is the selected objects.
    """
    if context.mode == "EDIT_MESH":
        return list(context.objects_in_mode)
    return list(context.selected_objects)


def build_vertex_loop_map(obj):
    """Return a dict mapping vertex index to a list of loop indices in O(L)."""
    vert_to_loops = {}
    for loop in obj.data.loops:
        vert_to_loops.setdefault(loop.vertex_index, []).append(loop.index)
    return vert_to_loops


def get_random_color_by_RGBA():
    """Returns a tuple with 4 floats (RGBA), each float is a random between 0 and 1."""
    return (random.random(), random.random(), random.random(), random.random())


def get_random_color_by_hue():
    """
    Generates a random color in HSL space, with hue random 0-1,
    saturation 1, lightness 0.5. Converts to RGB and returns RGBA
    with a random alpha.
    """
    hue = random.random()
    r, g, b = colorsys.hls_to_rgb(hue, 0.5, 1)
    return (r, g, b, random.random())


def get_random_color(mode="RGBA", palette=None):
    """Returns a random RGBA tuple. Mode selects the generation algorithm."""
    match mode:
        case "RGBA":
            return get_random_color_by_RGBA()
        case "Hue":
            return get_random_color_by_hue()
        case "Palette":
            return get_color_from_palette(palette)


def get_masked_color(old_color, new_color, mask=(True, True, True, True)):
    """Applies new_color to old_color using the per-channel mask."""
    result_color = [old_color[0], old_color[1], old_color[2], old_color[3]]

    if mask[0]:
        result_color[0] = new_color[0]
    if mask[1]:
        result_color[1] = new_color[1]
    if mask[2]:
        result_color[2] = new_color[2]
    if mask[3]:
        result_color[3] = new_color[3]

    return result_color


def get_active_color_attribute(obj):
    """
    Gets the active color attribute of *obj*.
    Creates a default FLOAT_COLOR/CORNER attribute if none exists.
    """
    color_attribute = obj.data.color_attributes.active_color

    if color_attribute is None:
        color_attribute = obj.data.color_attributes.new(
            name="Color", type="FLOAT_COLOR", domain="CORNER"
        )

    return color_attribute


def get_color_from_palette(palette):
    """Randomly selects a color from the given palette list."""
    return random.choice(palette)


def _color_distance(c1, c2):
    """Euclidean distance between two colors in RGB space (ignores alpha)."""
    return sum((c1[i] - c2[i]) ** 2 for i in range(3)) ** 0.5


def get_distinct_random_colors(count, mode="RGBA", palette=None, min_distance=0.3, max_attempts=100):
    """Generate a list of visually distinct random colors.

    Uses rejection sampling to ensure minimum RGB distance between colors.
    Falls back to the best candidate found if *min_distance* can't be met.
    """
    colors = []
    for _ in range(count):
        best_color = None
        best_min_dist = -1
        for _ in range(max_attempts):
            candidate = get_random_color(mode, palette=palette)
            if not colors:
                best_color = candidate
                break
            min_dist = min(_color_distance(candidate, c) for c in colors)
            if min_dist >= min_distance:
                best_color = candidate
                break
            if min_dist > best_min_dist:
                best_color = candidate
                best_min_dist = min_dist
        colors.append(best_color)
    return colors


# ---- Numpy bulk I/O ----

def bulk_get_colors(color_attribute):
    """Fetch all sRGB colors as a (N, 4) float32 numpy array."""
    n = len(color_attribute.data)
    flat = np.empty(n * 4, dtype=np.float32)
    color_attribute.data.foreach_get("color_srgb", flat)
    return flat.reshape(n, 4)


def bulk_set_colors(color_attribute, colors):
    """Write a (N, 4) float32 numpy array back to color data."""
    color_attribute.data.foreach_set("color_srgb", colors.ravel())


def linear_to_srgb(colors):
    """Convert scene-linear (N, 4) colors to sRGB; alpha is left unchanged."""
    out = np.array(colors, dtype=np.float32, copy=True)
    rgb = np.clip(out[..., :3], 0.0, None)
    out[..., :3] = np.where(
        rgb <= 0.0031308,
        rgb * 12.92,
        1.055 * np.power(rgb, 1.0 / 2.4) - 0.055,
    )
    return out


# ---- Numpy mask helpers ----

def apply_mask_constant(colors, color, mask, indices=None):
    """Set constant *color* on rows of *colors*, honoring per-channel *mask*.

    *indices*: optional int array — when ``None``, all rows are updated.
    """
    for ch in range(4):
        if mask[ch]:
            if indices is not None:
                colors[indices, ch] = color[ch]
            else:
                colors[:, ch] = color[ch]


def apply_mask_array(colors, new_colors, mask, indices=None):
    """Set per-element *new_colors* on *colors*, honoring per-channel *mask*.

    *new_colors*: shape ``(len(indices), 4)`` or ``(N, 4)``.
    *indices*: optional int array — when ``None``, all rows are updated.
    """
    for ch in range(4):
        if mask[ch]:
            if indices is not None:
                colors[indices, ch] = new_colors[:, ch]
            else:
                colors[:, ch] = new_colors[:, ch]


# ---- Selection helpers ----

def get_selected_color_indices(obj, select_mode, domain):
    """Return indices into ``color_attribute.data`` for selected elements.

    For CORNER domain returns loop indices; for POINT returns vertex indices.
    Returns ``None`` when *select_mode* is ``None`` (object mode — all elements).
    """
    if select_mode is None:
        return None

    if domain == "POINT":
        return np.array(
            [v.index for v in obj.data.vertices if v.select], dtype=np.intp
        )

    # CORNER domain
    indices = set()
    if select_mode[0] or select_mode[1]:
        vert_to_loops = build_vertex_loop_map(obj)
    if select_mode[0]:
        for vert in obj.data.vertices:
            if vert.select:
                indices.update(vert_to_loops.get(vert.index, []))
    if select_mode[1]:
        for edge in obj.data.edges:
            if edge.select:
                for vi in edge.vertices:
                    indices.update(vert_to_loops.get(vi, []))
    if select_mode[2]:
        for poly in obj.data.polygons:
            if poly.select:
                indices.update(poly.loop_indices)
    return np.array(sorted(indices), dtype=np.intp) if indices else np.array([], dtype=np.intp)


def is_vertex_color_visible(context):
    """Return True if vertex colors are currently visible in the viewport.

    Returns True in Vertex Paint mode, when in Solid shading with VERTEX or
    ATTRIBUTE color type, or when not inside a 3D viewport at all.
    """
    if context.mode == 'VERTEX_PAINT':
        return True
    space = getattr(context, 'space_data', None)
    if space is None or not hasattr(space, 'shading'):
        return True  # Not in a 3D viewport — don't nag
    shading = space.shading
    return shading.type == 'SOLID' and shading.color_type in ('VERTEX', 'ATTRIBUTE')


# ---- Color picking (pure helpers, no bpy) ----

GRADIENT_EPSILON = 1.0 / 512.0


def colors_equal(colors, eps=GRADIENT_EPSILON):
    """True if every channel of *colors* varies by at most *eps*."""
    for ch in range(len(colors[0])):
        values = [c[ch] for c in colors]
        if max(values) - min(values) > eps:
            return False
    return True


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def distance_to_segment(point, a, b):
    """Distance from *point* to the segment *a*-*b* (3D tuples)."""
    ab = _sub(b, a)
    ap = _sub(point, a)
    length_sq = _dot(ab, ab)
    t = 0.0 if length_sq == 0.0 else max(0.0, min(1.0, _dot(ap, ab) / length_sq))
    closest = tuple(a[i] + ab[i] * t for i in range(3))
    return _dot(_sub(point, closest), _sub(point, closest)) ** 0.5


def pick_color_from_face(corner_colors, corner_coords, hit_point, eps=GRADIENT_EPSILON):
    """Choose the color to pick from one face.

    *corner_colors* and *corner_coords* are per-corner lists in face order and
    *hit_point* is in the same space as the coordinates. Returns
    ``(color, kind, index)``: a solid face gives ``"FACE"``; a gradient face
    falls back to the nearest edge if that edge is solid (``"EDGE"``, *index*
    is the corner the edge starts at); otherwise the nearest corner is used
    (``"VERTEX"``, *index* is that corner).
    """
    if colors_equal(corner_colors, eps):
        return tuple(corner_colors[0]), "FACE", None

    count = len(corner_coords)
    edge = min(range(count), key=lambda i: distance_to_segment(
        hit_point, corner_coords[i], corner_coords[(i + 1) % count]))
    c0, c1 = corner_colors[edge], corner_colors[(edge + 1) % count]
    if colors_equal((c0, c1), eps):
        return tuple(c0), "EDGE", edge

    vertex = min(range(count), key=lambda i: _dot(_sub(hit_point, corner_coords[i]),
                                                  _sub(hit_point, corner_coords[i])))
    return tuple(corner_colors[vertex]), "VERTEX", vertex
