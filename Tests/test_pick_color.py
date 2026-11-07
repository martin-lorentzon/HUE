# Copyright (C) 2026 Clonephaze
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the color picker's mesh sampling (operators/pick_color.sample_hit).

Runs inside Blender via::

    blender -b --factory-startup -P Tests/conftest.py --python-exit-code 1
"""

import sys
import unittest
from pathlib import Path

_tests_dir = Path(__file__).resolve().parent
_repo_root = _tests_dir.parent
_addon_root = _repo_root / "HUE"
for p in (_tests_dir, _repo_root, _addon_root):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

import bpy
from mathutils import Matrix, Vector

from HUE.operators.pick_color import sample_hit

RED = (1.0, 0.0, 0.0, 1.0)
GREEN = (0.0, 1.0, 0.0, 1.0)
BLUE = (0.0, 0.0, 1.0, 1.0)
YELLOW = (1.0, 1.0, 0.0, 1.0)

# Unit quad, counter-clockwise: vertices 0..3 in face order
QUAD = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]


def _make_object(domain, data_type, colors, name="PickTest"):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(QUAD, [], [(0, 1, 2, 3)])
    mesh.update()
    attribute = mesh.color_attributes.new(name="Col", type=data_type, domain=domain)
    mesh.color_attributes.active_color = attribute
    for element, color in zip(attribute.data, colors):
        element.color_srgb = color
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _sample(obj, point):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    return sample_hit(depsgraph, obj, 0, Vector(point), Matrix.Identity(4))


class TestSampleHit(unittest.TestCase):
    def setUp(self):
        self.objects = []

    def tearDown(self):
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        for obj in self.objects:
            mesh = obj.data
            bpy.data.objects.remove(obj)
            bpy.data.meshes.remove(mesh)

    def make(self, *args, **kwargs):
        obj = _make_object(*args, **kwargs)
        self.objects.append(obj)
        return obj

    def assertColor(self, actual, expected, places=2):
        for a, e in zip(actual, expected):
            self.assertAlmostEqual(a, e, places=places)

    def test_solid_face_corner_domain(self):
        obj = self.make("CORNER", "FLOAT_COLOR", [RED] * 4)
        hit = _sample(obj, (0.5, 0.5, 0))
        self.assertEqual(hit["kind"], "FACE")
        self.assertColor(hit["color"], RED)

    def test_solid_face_point_domain(self):
        obj = self.make("POINT", "FLOAT_COLOR", [BLUE] * 4)
        hit = _sample(obj, (0.5, 0.5, 0))
        self.assertEqual(hit["kind"], "FACE")
        self.assertColor(hit["color"], BLUE)

    def test_gradient_with_solid_edge(self):
        obj = self.make("CORNER", "FLOAT_COLOR", [RED, RED, GREEN, BLUE])
        hit = _sample(obj, (0.5, 0.1, 0))
        self.assertEqual((hit["kind"], hit["corner"]), ("EDGE", 0))
        self.assertColor(hit["color"], RED)

    def test_gradient_falls_back_to_nearest_vertex(self):
        obj = self.make("CORNER", "FLOAT_COLOR", [RED, GREEN, BLUE, YELLOW])
        hit = _sample(obj, (0.9, 0.1, 0))
        self.assertEqual((hit["kind"], hit["corner"]), ("VERTEX", 1))
        self.assertColor(hit["color"], GREEN)
        hit = _sample(obj, (0.1, 0.9, 0))
        self.assertEqual(hit["corner"], 3)
        self.assertColor(hit["color"], YELLOW)

    def test_point_domain_gradient(self):
        obj = self.make("POINT", "FLOAT_COLOR", [RED, GREEN, BLUE, YELLOW])
        hit = _sample(obj, (0.1, 0.1, 0))
        self.assertEqual((hit["kind"], hit["corner"]), ("VERTEX", 0))
        self.assertColor(hit["color"], RED)

    def test_byte_color_attribute(self):
        obj = self.make("CORNER", "BYTE_COLOR", [RED, GREEN, BLUE, YELLOW])
        hit = _sample(obj, (0.9, 0.1, 0))
        self.assertEqual(hit["kind"], "VERTEX")
        self.assertColor(hit["color"], GREEN, places=1)

    def test_world_points_follow_matrix(self):
        obj = self.make("CORNER", "FLOAT_COLOR", [RED] * 4)
        matrix = Matrix.Translation((10, 0, 0))
        depsgraph = bpy.context.evaluated_depsgraph_get()
        hit = sample_hit(depsgraph, obj, 0, Vector((10.5, 0.5, 0)), matrix)
        self.assertEqual(hit["kind"], "FACE")
        self.assertAlmostEqual(hit["world"][0][0], 10.0)

    def _edit_mode_cases(self, data_type, domain):
        obj = self.make(domain, data_type, [RED, GREEN, BLUE, YELLOW])
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        object_hit = _sample(obj, (0.9, 0.1, 0))
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.context.view_layer.update()
        try:
            edit_hit = _sample(obj, (0.9, 0.1, 0))
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
        self.assertIsInstance(edit_hit, dict)
        self.assertEqual(edit_hit["kind"], object_hit["kind"])
        self.assertEqual(edit_hit["corner"], object_hit["corner"])
        self.assertColor(edit_hit["color"], object_hit["color"], places=2)

    def test_edit_mode_float_corner(self):
        self._edit_mode_cases("FLOAT_COLOR", "CORNER")

    def test_edit_mode_float_point(self):
        self._edit_mode_cases("FLOAT_COLOR", "POINT")

    def test_edit_mode_byte_corner(self):
        self._edit_mode_cases("BYTE_COLOR", "CORNER")

    def test_edit_mode_byte_point(self):
        self._edit_mode_cases("BYTE_COLOR", "POINT")

    def test_no_color_attribute(self):
        mesh = bpy.data.meshes.new("NoColor")
        mesh.from_pydata(QUAD, [], [(0, 1, 2, 3)])
        obj = bpy.data.objects.new("NoColor", mesh)
        bpy.context.scene.collection.objects.link(obj)
        self.objects.append(obj)
        self.assertIsInstance(_sample(obj, (0.5, 0.5, 0)), str)


def _cast(obj, x, y):
    """Ray cast straight down at (x, y) and sample the hit, like the modal operator does."""
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    hit, location, _normal, face_index, hit_obj, matrix = bpy.context.scene.ray_cast(
        depsgraph, Vector((x, y, 5.0)), Vector((0, 0, -1)))
    if not hit:
        return None
    return sample_hit(depsgraph, hit_obj, face_index, location, matrix)


class TestSampleHitWithModifiers(TestSampleHit):
    """Same objects, but the ray hits the evaluated mesh of mirror / bevel modifiers."""

    def mirrored(self, colors):
        obj = self.make("CORNER", "FLOAT_COLOR", colors)
        modifier = obj.modifiers.new("Mirror", "MIRROR")
        modifier.use_axis[0] = True
        return obj

    def test_mirror_solid_copy(self):
        obj = self.mirrored([RED] * 4)
        hit = _cast(obj, -0.5, 0.5)
        self.assertEqual(hit["kind"], "FACE")
        self.assertColor(hit["color"], RED)

    def test_mirror_gradient_copy_uses_mirrored_vertex(self):
        obj = self.mirrored([RED, GREEN, BLUE, YELLOW])
        hit = _cast(obj, -0.9, 0.1)
        self.assertEqual(hit["kind"], "VERTEX")
        self.assertColor(hit["color"], GREEN)
        # The highlight is on the mirrored side
        self.assertLess(min(p[0] for p in hit["world"]), -0.5)

    def test_mirror_original_side_still_works(self):
        obj = self.mirrored([RED, GREEN, BLUE, YELLOW])
        hit = _cast(obj, 0.9, 0.9)
        self.assertColor(hit["color"], BLUE)

    def test_mirror_in_edit_mode(self):
        obj = self.mirrored([RED, GREEN, BLUE, YELLOW])
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            hit = _cast(obj, -0.9, 0.1)
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")
        self.assertIsInstance(hit, dict)
        self.assertColor(hit["color"], GREEN)

    def test_bevel_cube(self):
        mesh = bpy.data.meshes.new("BevelCube")
        mesh.from_pydata(
            [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], [],
            [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
        mesh.update()
        attribute = mesh.color_attributes.new(name="Col", type="FLOAT_COLOR", domain="CORNER")
        mesh.color_attributes.active_color = attribute
        for element in attribute.data:
            element.color_srgb = RED
        obj = bpy.data.objects.new("BevelCube", mesh)
        bpy.context.scene.collection.objects.link(obj)
        self.objects.append(obj)
        bevel = obj.modifiers.new("Bevel", "BEVEL")
        bevel.width = 0.2
        bevel.segments = 2

        self.assertGreater(len(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.polygons), 6)
        # Center of the top, and a spot on the beveled edge near the corner: all red
        for x, y in ((0.0, 0.0), (0.9, 0.0), (0.88, 0.88)):
            hit = _cast(obj, x, y)
            self.assertIsInstance(hit, dict, (x, y))
            self.assertColor(hit["color"], RED)

    def test_attribute_removed_by_modifiers(self):
        obj = self.make("CORNER", "FLOAT_COLOR", [RED] * 4)
        modifier = obj.modifiers.new("Nodes", "NODES")
        group = bpy.data.node_groups.new("Empty", "GeometryNodeTree")
        group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        group.nodes.new("NodeGroupInput")
        group.nodes.new("NodeGroupOutput")
        modifier.node_group = group
        hit = _cast(obj, 0.5, 0.5)
        # Nothing to hit (empty geometry) is also acceptable; it must not raise
        self.assertTrue(hit is None or isinstance(hit, str))
        bpy.data.node_groups.remove(group)


if __name__ == "__main__":
    unittest.main()
