# Copyright (C) 2026 Clonephaze
#
# SPDX-License-Identifier: GPL-3.0-or-later

import blf
import bmesh
import bpy
import gpu
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.geometry import tessellate_polygon

from ..utilities.color_utilities import GRADIENT_EPSILON, pick_color_from_face
from ..utilities.palette_utilities import _linear_to_srgb, _srgb_to_linear, find_palette_match
from .base_operators import _ROOT_PKG, BaseOperator

_DEFAULT_TOLERANCE = 1.0  # Palette match tolerance (delta E) when preferences are unavailable
_KIND_LABELS = {"FACE": "Face", "EDGE": "Edge", "VERTEX": "Vertex"}

# Blender 5.0 and later treat colors from draw handlers as scene linear and color manage them,
# so an sRGB color (what the picker reads and what the UI shows) has to be linearized to look right.
_DRAW_IS_LINEAR = bpy.app.version >= (5, 0, 0)


def _draw_color(rgb, alpha=1.0):
    """An sRGB color as the RGBA tuple to hand to a shader."""
    if _DRAW_IS_LINEAR:
        rgb = tuple(_srgb_to_linear(max(0.0, min(1.0, c))) for c in rgb[:3])
    return (*rgb[:3], alpha)


_HIGHLIGHT = _draw_color((1.0, 0.83, 0.0))[:3]  # Amber, as it first appeared before the color fix
_CIRCLE_RADIUS = 22
_CIRCLE_OFFSET = (36, 36)

# Key icons that show a word, wider than an icon-only label in Blender 4.3 and later
_WIDE_KEYS = {"EVENT_ESC"}
# Mouse icons narrower than an icon-only label in Blender 4.3 and later
_NARROW_KEYS = {"MOUSE_LMB", "MOUSE_RMB"}


# MARK: — Status bar ——————————————————————————————————————————————————

def _key_hint(layout, text, icons):
    row = layout.row(align=True)
    for icon in icons:
        sub = row.row(align=True)
        if bpy.app.version >= (4, 3, 0):
            if icon in _WIDE_KEYS:
                sub.ui_units_x = 1.4
            elif icon in _NARROW_KEYS:
                sub.ui_units_x = 0.75
        sub.label(text="", icon=icon)
    row.label(text=text)


def _draw_status(header, context):
    layout = header.layout
    _key_hint(layout, "Pick", ("MOUSE_LMB",))
    _key_hint(layout, "Cancel", ("EVENT_ESC", "MOUSE_RMB"))
    layout.separator_spacer()
    layout.template_status_info()


# MARK: — Sampling ————————————————————————————————————————————————————

def _region_under_mouse(area, event):
    """The 3D viewport region under the mouse, or ``None`` over the sidebar, header or outside."""
    window = None
    for region in area.regions:
        inside = (region.x <= event.mouse_x < region.x + region.width
                  and region.y <= event.mouse_y < region.y + region.height)
        if not inside:
            continue
        if region.type == "WINDOW":
            window = region
        elif region.type in {"UI", "TOOLS", "HEADER", "TOOL_HEADER", "NAVIGATION_BAR", "FOOTER"}:
            if region.width > 1 and region.height > 1:
                return None
    return window


def _corner_colors_mesh(mesh, attribute, polygon):
    if attribute.domain == "POINT":
        indices = [mesh.loops[li].vertex_index for li in polygon.loop_indices]
    else:
        indices = list(polygon.loop_indices)
    return [tuple(attribute.data[i].color_srgb) for i in indices]


def _srgb(c):
    return tuple(_linear_to_srgb(max(0.0, min(1.0, v))) for v in c[:3]) + (c[3],)


def _corner_colors_bmesh(bm, attribute, face):
    """Edit Mode colors: bmesh float layers are scene linear, byte layers are already sRGB."""
    name = attribute.name
    if attribute.domain == "POINT":
        layers = bm.verts.layers
        elements = face.verts
    else:
        layers = bm.loops.layers
        elements = face.loops
    layer = layers.float_color.get(name)
    if layer is not None:
        return [_srgb(tuple(e[layer])) for e in elements]
    layer = layers.color.get(name)
    if layer is None:
        return None
    return [tuple(e[layer]) for e in elements]


def _sample(context, area, region, rv3d, event):
    """Ray cast under the mouse and choose the color to pick. Returns a dict or an error string."""
    coord = (event.mouse_x - region.x, event.mouse_y - region.y)
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    depsgraph = context.evaluated_depsgraph_get()
    hit, location, _normal, face_index, obj, matrix = context.scene.ray_cast(depsgraph, origin, direction)
    if not hit or obj is None or obj.type != "MESH":
        return None
    return sample_hit(depsgraph, obj, face_index, location, matrix)


def sample_hit(depsgraph, obj, face_index, location, matrix):
    """Choose the color to pick for a ray hit. Returns a dict or an error string.

    The ray hits the evaluated mesh, so with modifiers (mirror, bevel, subdivision...)
    colors are read from the evaluated mesh too: what you see is what you pick.
    """
    obj = obj.original
    original = obj.data
    active = original.color_attributes.active_color
    if active is None:
        return "No color attribute on this mesh"

    local_hit = matrix.inverted() @ location
    modifiers_in_edit_mode = any(m.show_viewport and m.show_in_editmode for m in obj.modifiers)

    if obj.mode == "EDIT" and not modifiers_in_edit_mode:
        # No modifiers: the edit mesh is the displayed mesh and its face indices match the ray hit
        bm = bmesh.from_edit_mesh(original)
        bm.faces.ensure_lookup_table()
        if face_index >= len(bm.faces):
            return "No mesh under the cursor"
        face = bm.faces[face_index]
        colors = _corner_colors_bmesh(bm, active, face)
        coords = [tuple(v.co) for v in face.verts]
    else:
        evaluated = obj.evaluated_get(depsgraph)
        # In Edit Mode the evaluated mesh is a wrapper without polygons, so convert it
        mesh = evaluated.to_mesh() if obj.mode == "EDIT" else evaluated.data
        try:
            attribute = mesh.color_attributes.get(active.name)
            if attribute is None:
                return "The modifiers remove the color attribute"
            if face_index >= len(mesh.polygons):
                return "No mesh under the cursor"
            polygon = mesh.polygons[face_index]
            colors = _corner_colors_mesh(mesh, attribute, polygon)
            coords = [tuple(mesh.vertices[mesh.loops[li].vertex_index].co) for li in polygon.loop_indices]
        finally:
            if obj.mode == "EDIT":
                evaluated.to_mesh_clear()
    if not colors:
        return "No color attribute on this mesh"

    color, kind, corner = pick_color_from_face(colors, coords, tuple(local_hit), GRADIENT_EPSILON)
    world = [tuple(matrix @ Vector(co)) for co in coords]
    return {"color": color, "kind": kind, "corner": corner, "world": world}


# MARK: — Drawing —————————————————————————————————————————————————————

def _polyline_shader():
    return gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")


def _draw_polyline(points, color, width, loop=False):
    shader = _polyline_shader()
    shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    shader.uniform_float("lineWidth", width)
    shader.uniform_float("color", color)
    pts = list(points) + ([points[0]] if loop else [])
    batch_for_shader(shader, "LINE_STRIP", {"pos": pts}).draw(shader)


def _draw_highlight(op):
    """Highlight the face, edge or vertex that the color comes from (POST_VIEW)."""
    hit = op.hit
    if not hit or bpy.context.area.as_pointer() != op.area_pointer:
        return
    world = hit["world"]
    kind, corner = hit["kind"], hit["corner"]

    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")
    flat = gpu.shader.from_builtin("UNIFORM_COLOR")

    if kind == "FACE":
        triangles = tessellate_polygon([world])
        batch = batch_for_shader(flat, "TRIS", {"pos": world}, indices=triangles)
        flat.uniform_float("color", (*_HIGHLIGHT, 0.25))
        batch.draw(flat)
        _draw_polyline(world, (*_HIGHLIGHT, 1.0), 2.5, loop=True)
    elif kind == "EDGE":
        a, b = world[corner], world[(corner + 1) % len(world)]
        _draw_polyline([a, b], (*_HIGHLIGHT, 1.0), 4.0)
    else:
        _draw_polyline(world, (*_HIGHLIGHT, 0.6), 1.5, loop=True)
        gpu.state.point_size_set(12.0)
        if bpy.app.version >= (4, 5, 0):
            point_shader = gpu.shader.from_builtin("POINT_UNIFORM_COLOR")
        else:
            point_shader = flat
        point_shader.uniform_float("color", (*_HIGHLIGHT, 1.0))
        batch_for_shader(point_shader, "POINTS", {"pos": [world[corner]]}).draw(point_shader)

    gpu.state.depth_test_set("NONE")
    gpu.state.blend_set("NONE")


_disc_shader = None


def _get_disc_shader():
    """Shader for an anti-aliased disc with a ring, drawn on a quad (circles from triangles are jagged)."""
    global _disc_shader
    if _disc_shader is None:
        interface = gpu.types.GPUStageInterfaceInfo("hue_pick_disc_interface")
        interface.smooth("VEC2", "uv")
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant("MAT4", "ModelViewProjectionMatrix")
        info.push_constant("VEC2", "center")
        info.push_constant("FLOAT", "outerRadius")
        info.push_constant("FLOAT", "innerRadius")
        info.push_constant("VEC4", "fillColor")
        info.push_constant("VEC4", "ringColor")
        info.vertex_in(0, "VEC2", "pos")
        info.vertex_out(interface)
        info.fragment_out(0, "VEC4", "fragColor")
        info.vertex_source(
            "void main() {"
            "  uv = pos;"
            "  gl_Position = ModelViewProjectionMatrix * vec4(center + pos * (outerRadius + 1.0), 0.0, 1.0);"
            "}")
        info.fragment_source(
            "void main() {"
            "  float d = length(uv) * (outerRadius + 1.0);"  # distance from the center in pixels
            "  float outer = 1.0 - smoothstep(outerRadius - 0.75, outerRadius + 0.75, d);"
            "  float inner = 1.0 - smoothstep(innerRadius - 0.75, innerRadius + 0.75, d);"
            "  vec4 color = mix(ringColor, fillColor, inner);"
            "  fragColor = vec4(color.rgb, color.a * outer);"
            "}")
        _disc_shader = gpu.shader.create_from_info(info)
    return _disc_shader


def _draw_disc(center, radius, fill, ring=None, ring_width=3.0):
    """Anti-aliased disc of *radius* pixels with an optional ring around it."""
    shader = _get_disc_shader()
    outer = radius + (ring_width if ring else 0.0)
    shader.uniform_float("ModelViewProjectionMatrix",
                         gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix())
    shader.uniform_float("center", center)
    shader.uniform_float("outerRadius", outer)
    shader.uniform_float("innerRadius", radius)
    shader.uniform_float("fillColor", fill)
    shader.uniform_float("ringColor", ring or fill)
    batch_for_shader(shader, "TRI_FAN", {"pos": ((-1, -1), (1, -1), (1, 1), (-1, 1))}).draw(shader)


def _draw_cursor_swatch(op):
    """A circle in the hovered color next to the pointer (POST_PIXEL)."""
    hit = op.hit
    if not hit or bpy.context.area.as_pointer() != op.area_pointer:
        return
    region = bpy.context.region
    cx = op.mouse[0] - region.x + _CIRCLE_OFFSET[0]
    cy = op.mouse[1] - region.y + _CIRCLE_OFFSET[1]
    r, g, b = hit["color"][:3]

    # Flip the offset near the right/top edge so the swatch stays inside the region
    if cx + _CIRCLE_RADIUS + 20 > region.width:
        cx -= 2 * _CIRCLE_OFFSET[0]
    if cy + _CIRCLE_RADIUS + 20 > region.height:
        cy -= 2 * _CIRCLE_OFFSET[1]

    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    ring = (0.0, 0.0, 0.0, 1.0) if luminance > 0.5 else (1.0, 1.0, 1.0, 1.0)

    gpu.state.blend_set("ALPHA")
    _draw_disc((cx, cy), _CIRCLE_RADIUS, _draw_color((r, g, b)), ring)

    if op.match_index is not None:
        font = 0
        blf.size(font, 14)
        text = f"#{op.match_index + 1}"
        width, _height = blf.dimensions(font, text)
        blf.position(font, cx - width / 2, cy - _CIRCLE_RADIUS - 20, 0)
        blf.color(font, 1.0, 1.0, 1.0, 1.0)
        blf.shadow(font, 3, 0.0, 0.0, 0.0, 1.0)
        blf.enable(font, blf.SHADOW)
        blf.draw(font, text)
        blf.disable(font, blf.SHADOW)
    gpu.state.blend_set("NONE")


# MARK: — Operator ————————————————————————————————————————————————————

_running = []  # Pickers that are currently modal, so unregister can shut them down


def cancel_running():
    """End every running picker (add-on unregister / reload) and restore the UI."""
    for op in list(_running):
        op._finish(bpy.context)

class HUE_OT_pick_color(BaseOperator):
    """Pick a color from the mesh under the cursor. Uses the face color; on a gradient, the nearest solid edge, otherwise the nearest vertex"""

    bl_label = "Pick Color"
    bl_idname = "hue.pick_color"
    bl_options = {"BLOCKING"}

    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == "VIEW_3D"
                and any(obj.type == "MESH" for obj in context.scene.objects))

    def invoke(self, context, event):
        self.area = context.area
        self.area_pointer = context.area.as_pointer()
        self.workspace = context.workspace
        self.hit = None
        self.message = ""
        self.match_index = None
        self.mouse = (event.mouse_x, event.mouse_y)
        self._handles = []
        _running.append(self)

        space = bpy.types.SpaceView3D
        self._handles.append(space.draw_handler_add(_draw_highlight, (self,), "WINDOW", "POST_VIEW"))
        self._handles.append(space.draw_handler_add(_draw_cursor_swatch, (self,), "WINDOW", "POST_PIXEL"))
        context.window.cursor_modal_set("EYEDROPPER")
        # Hints last: nothing after this can fail
        self._show()
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if self not in _running:
            # Shut down by cancel_running()
            return {"CANCELLED"}
        try:
            if event.value == "PRESS" and event.type in {"RIGHTMOUSE", "ESC"}:
                self._finish(context)
                return {"CANCELLED"}

            if event.type == "LEFTMOUSE" and event.value == "PRESS":
                self._update(context, event)
                if self.hit is None:
                    return {"RUNNING_MODAL"}
                self._commit(context)
                self._finish(context)
                return {"FINISHED"}

            if event.type == "MOUSEMOVE":
                self._update(context, event)
                self.area.tag_redraw()
                return {"RUNNING_MODAL"}
        except Exception:
            # Blender cancels the operator without calling cancel()
            self._finish(context)
            raise
        # Let the user orbit, pan and zoom while picking
        return {"PASS_THROUGH"}

    def cancel(self, context):
        self._finish(context)

    # ---- helpers ----

    def _update(self, context, event):
        self.mouse = (event.mouse_x, event.mouse_y)
        self.hit = None
        self.match_index = None
        region = _region_under_mouse(self.area, event)
        if region is None:
            self.message = "Move over the viewport"
        else:
            rv3d = self.area.spaces.active.region_3d
            result = _sample(context, self.area, region, rv3d, event)
            if isinstance(result, str):
                self.message = result
            elif result is None:
                self.message = "No mesh under the cursor"
            else:
                self.hit = result
                self.match_index = self._find_match(context, result["color"])
                where = _KIND_LABELS[result["kind"]]
                if self.match_index is None:
                    self.message = f"{where}  |  Not in palette"
                else:
                    self.message = f"{where}  |  Palette color #{self.match_index + 1}"
        self._show()

    def _find_match(self, context, color):
        tool = context.scene.hue_simple_fill_tool
        palette = tool.preset_palette
        if not palette or len(palette.colors) == 0:
            return None
        try:
            tolerance = context.preferences.addons[_ROOT_PKG].preferences.pick_match_tolerance
        except KeyError:
            tolerance = _DEFAULT_TOLERANCE
        return find_palette_match([tuple(pc.color) for pc in palette.colors], color, tolerance)

    def _commit(self, context):
        tool = context.scene.hue_simple_fill_tool
        color = self.hit["color"]
        if self.match_index is not None:
            # Snap to the exact swatch value and select it in the grid
            exact = tool.preset_palette.colors[self.match_index].color
            tool.selected_color = (exact[0], exact[1], exact[2], color[3])
            tool.active_preset_index = self.match_index
            self.report({"INFO"}, f"Picked palette color #{self.match_index + 1}")
        else:
            tool.selected_color = tuple(color)
            self.report({"INFO"}, "Picked color (not in the palette)")

    def _show(self):
        self.area.header_text_set(f"Pick Color: {self.message}" if self.message else "Pick Color")
        self.workspace.status_text_set(_draw_status)

    def _finish(self, context):
        if self in _running:
            _running.remove(self)
        for handle in self._handles:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
        self._handles = []
        self.hit = None
        self.workspace.status_text_set(None)
        if context.window is not None:
            context.window.cursor_modal_restore()
        try:
            self.area.header_text_set(None)
            self.area.tag_redraw()
        except ReferenceError:
            pass  # The area is gone (window closed or file loaded)
