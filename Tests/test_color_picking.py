# Copyright (C) 2026 Clonephaze
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the color picker helpers (pick_color_from_face, find_palette_match).

Runs inside Blender via::

    blender -b --factory-startup -P Tests/conftest.py --python-exit-code 1
"""

import sys
import unittest
from pathlib import Path

_tests_dir = Path(__file__).resolve().parent
_addon_root = _tests_dir.parent / "HUE"
for p in (_tests_dir, _addon_root):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from utilities.color_utilities import colors_equal, distance_to_segment, pick_color_from_face
from utilities.palette_utilities import find_palette_match

RED = (1.0, 0.0, 0.0, 1.0)
GREEN = (0.0, 1.0, 0.0, 1.0)
BLUE = (0.0, 0.0, 1.0, 1.0)

# Unit square, counter-clockwise: corner 0 at the origin
SQUARE = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]


class TestColorsEqual(unittest.TestCase):
    def test_identical(self):
        self.assertTrue(colors_equal([RED, RED, RED]))

    def test_within_epsilon(self):
        self.assertTrue(colors_equal([(0.5, 0.5, 0.5, 1.0), (0.5005, 0.5, 0.5, 1.0)], 0.001))

    def test_different(self):
        self.assertFalse(colors_equal([RED, GREEN]))

    def test_alpha_counts(self):
        self.assertFalse(colors_equal([(1, 0, 0, 1.0), (1, 0, 0, 0.5)]))


class TestDistanceToSegment(unittest.TestCase):
    def test_perpendicular(self):
        self.assertAlmostEqual(distance_to_segment((0.5, 1.0, 0.0), (0, 0, 0), (1, 0, 0)), 1.0)

    def test_clamped_to_endpoint(self):
        self.assertAlmostEqual(distance_to_segment((3.0, 0.0, 0.0), (0, 0, 0), (1, 0, 0)), 2.0)

    def test_degenerate_segment(self):
        self.assertAlmostEqual(distance_to_segment((0.0, 2.0, 0.0), (0, 0, 0), (0, 0, 0)), 2.0)


class TestPickColorFromFace(unittest.TestCase):
    def test_solid_face(self):
        color, kind, index = pick_color_from_face([RED] * 4, SQUARE, (0.5, 0.5, 0.0))
        self.assertEqual((color, kind, index), (RED, "FACE", None))

    def test_gradient_face_solid_nearest_edge(self):
        # Corners 0 and 1 are red (bottom edge), the top corners differ
        colors = [RED, RED, GREEN, BLUE]
        color, kind, index = pick_color_from_face(colors, SQUARE, (0.5, 0.1, 0.0))
        self.assertEqual((color, kind, index), (RED, "EDGE", 0))

    def test_gradient_face_gradient_edge_uses_nearest_vertex(self):
        colors = [RED, GREEN, BLUE, (1.0, 1.0, 0.0, 1.0)]
        color, kind, index = pick_color_from_face(colors, SQUARE, (0.9, 0.1, 0.0))
        self.assertEqual((kind, index), ("VERTEX", 1))
        self.assertEqual(color, GREEN)

    def test_nearest_vertex_follows_the_cursor(self):
        colors = [RED, GREEN, BLUE, (1.0, 1.0, 0.0, 1.0)]
        _, _, index = pick_color_from_face(colors, SQUARE, (0.1, 0.9, 0.0))
        self.assertEqual(index, 3)

    def test_nearest_edge_follows_the_cursor(self):
        # Left edge (corners 3 -> 0) is blue; the other edges are gradients
        colors = [BLUE, GREEN, RED, BLUE]
        color, kind, index = pick_color_from_face(colors, SQUARE, (0.1, 0.5, 0.0))
        self.assertEqual((color, kind, index), (BLUE, "EDGE", 3))

    def test_triangle(self):
        tri = SQUARE[:3]
        color, kind, _ = pick_color_from_face([RED, RED, RED], tri, (0.6, 0.3, 0.0))
        self.assertEqual((color, kind), (RED, "FACE"))


class TestFindPaletteMatch(unittest.TestCase):
    PALETTE = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]

    def test_exact(self):
        self.assertEqual(find_palette_match(self.PALETTE, (0.0, 1.0, 0.0), 1.0), 1)

    def test_within_tolerance(self):
        self.assertEqual(find_palette_match(self.PALETTE, (0.999, 0.001, 0.0), 1.0), 0)

    def test_outside_tolerance(self):
        self.assertIsNone(find_palette_match(self.PALETTE, (0.5, 0.5, 0.5), 1.0))

    def test_empty_palette(self):
        self.assertIsNone(find_palette_match([], (1.0, 0.0, 0.0), 1.0))

    def test_nearest_of_several(self):
        palette = [(0.50, 0.50, 0.50), (0.52, 0.50, 0.50), (0.70, 0.70, 0.70)]
        self.assertEqual(find_palette_match(palette, (0.521, 0.50, 0.50), 20.0), 1)

    def test_alpha_is_ignored(self):
        self.assertEqual(find_palette_match(self.PALETTE, (1.0, 0.0, 0.0, 0.2), 1.0), 0)


if __name__ == "__main__":
    unittest.main()
