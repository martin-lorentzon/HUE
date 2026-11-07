# Copyright (C) 2026 Clonephaze
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for utilities/palette_utilities.py.

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

import bpy

from utilities.palette_utilities import (
    DEFAULT_PALETTE_NAME,
    _linear_to_srgb,
    cleanup_previews,
    get_color_icon,
    get_or_create_default_palette,
    import_gpl_palette,
    parse_gpl,
    serialize_gpl,
)


class TestLinearToSrgb(unittest.TestCase):
    """Pure function — no Blender data needed."""

    def test_zero(self):
        self.assertAlmostEqual(_linear_to_srgb(0.0), 0.0)

    def test_one(self):
        self.assertAlmostEqual(_linear_to_srgb(1.0), 1.0)

    def test_low_linear_value(self):
        # Below 0.0031308 → linear region: c * 12.92
        self.assertAlmostEqual(_linear_to_srgb(0.001), 0.001 * 12.92, places=6)

    def test_mid_value(self):
        # 0.5 linear ≈ 0.735 sRGB (standard formula)
        result = _linear_to_srgb(0.5)
        expected = 1.055 * (0.5 ** (1.0 / 2.4)) - 0.055
        self.assertAlmostEqual(result, expected, places=6)

    def test_monotonic(self):
        """sRGB conversion should be monotonically increasing."""
        prev = _linear_to_srgb(0.0)
        for i in range(1, 101):
            val = i / 100.0
            curr = _linear_to_srgb(val)
            self.assertGreater(curr, prev)
            prev = curr


class TestGetOrCreateDefaultPalette(unittest.TestCase):
    def setUp(self):
        # Remove existing palette if present
        existing = bpy.data.palettes.get(DEFAULT_PALETTE_NAME)
        if existing:
            bpy.data.palettes.remove(existing)

    def tearDown(self):
        existing = bpy.data.palettes.get(DEFAULT_PALETTE_NAME)
        if existing:
            bpy.data.palettes.remove(existing)

    def test_creates_palette(self):
        palette = get_or_create_default_palette()
        self.assertIsNotNone(palette)
        self.assertEqual(palette.name, DEFAULT_PALETTE_NAME)

    def test_palette_has_colors(self):
        palette = get_or_create_default_palette()
        self.assertGreater(len(palette.colors), 0)

    def test_returns_same_palette_on_second_call(self):
        p1 = get_or_create_default_palette()
        p2 = get_or_create_default_palette()
        self.assertEqual(p1.name, p2.name)
        # Should not have duplicated colors
        self.assertEqual(len(p1.colors), len(p2.colors))

    def test_default_colors_are_valid_rgb(self):
        palette = get_or_create_default_palette()
        for pc in palette.colors:
            for ch in range(3):
                self.assertGreaterEqual(pc.color[ch], 0.0)
                self.assertLessEqual(pc.color[ch], 1.0)


class TestGetColorIcon(unittest.TestCase):
    def tearDown(self):
        cleanup_previews()

    def test_returns_int_icon_id(self):
        icon_id = get_color_icon(1.0, 0.0, 0.0)
        self.assertIsInstance(icon_id, int)

    def test_same_color_returns_same_id(self):
        id1 = get_color_icon(0.5, 0.5, 0.5)
        id2 = get_color_icon(0.5, 0.5, 0.5)
        self.assertEqual(id1, id2)

    def test_clamps_out_of_range(self):
        # Should not crash with out-of-range linear values
        icon_id = get_color_icon(2.0, -0.5, 0.5)
        self.assertIsInstance(icon_id, int)


class TestGpl(unittest.TestCase):
    SAMPLE = (
        "GIMP Palette\n"
        "Name: Test Pal\n"
        "Columns: 4\n"
        "# comment\n"
        "255   0   0\tRed\n"
        "128 128 128 Gray\n"
        "bad line\n"
        "300 0 0\n"
        "\n"
        "  0   0 255\n"
    )

    def test_parse_name_colors_and_skipped(self):
        name, colors, skipped = parse_gpl(self.SAMPLE)
        self.assertEqual(name, "Test Pal")
        self.assertEqual(len(colors), 3)
        self.assertEqual(skipped, 2)

    def test_parse_converts_srgb_to_linear(self):
        _, colors, _ = parse_gpl(self.SAMPLE)
        self.assertAlmostEqual(colors[0][0], 1.0)
        self.assertAlmostEqual(colors[1][0], 0.2158, places=3)

    def test_parse_rejects_non_gpl(self):
        with self.assertRaises(ValueError):
            parse_gpl("not a palette\n1 2 3\n")

    def test_parse_without_name(self):
        name, colors, _ = parse_gpl("GIMP Palette\n1 2 3\n")
        self.assertIsNone(name)
        self.assertEqual(len(colors), 1)

    def test_round_trip(self):
        _, colors, _ = parse_gpl(self.SAMPLE)
        text = serialize_gpl("Test Pal", colors)
        name, colors2, skipped = parse_gpl(text)
        self.assertEqual(name, "Test Pal")
        self.assertEqual(skipped, 0)
        for a, b in zip(colors, colors2):
            for ch in range(3):
                self.assertAlmostEqual(a[ch], b[ch], places=3)

    def test_serialize_clamps_and_names_untitled(self):
        text = serialize_gpl("X", [(2.0, -1.0, 0.0)])
        self.assertIn("255   0   0\tUntitled", text)

    def test_import_creates_palette(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "p.gpl"
            path.write_text(self.SAMPLE, encoding="utf-8")
            palette, skipped = import_gpl_palette(str(path))
        try:
            self.assertEqual(len(palette.colors), 3)
            self.assertEqual(skipped, 2)
        finally:
            bpy.data.palettes.remove(palette)


if __name__ == "__main__":
    unittest.main()
