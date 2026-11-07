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
    clear_merge_snapshots,
    color_distance,
    discard_merge_snapshot,
    get_color_icon,
    get_or_create_default_palette,
    has_merge_snapshot,
    import_gpl_palette,
    merge_close_colors,
    merge_close_order,
    parse_gpl,
    restore_merged_colors,
    serialize_gpl,
    sort_order_hue,
    sort_order_hue_ramps,
    sort_order_lightness,
    sort_order_reverse,
    sort_order_saturation,
    _srgb_to_linear,
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


class TestSortHueRamps(unittest.TestCase):
    @staticmethod
    def _lin(hex_str):
        return tuple(_srgb_to_linear(int(hex_str[i:i + 2], 16) / 255.0) for i in (0, 2, 4))

    def test_permutation_and_deterministic(self):
        colors = [self._lin(h) for h in ("ff0000", "808080", "0000ff", "00ff00", "400000", "ffffff")]
        order = sort_order_hue_ramps(colors)
        self.assertEqual(sorted(order), list(range(len(colors))))
        self.assertEqual(order, sort_order_hue_ramps(colors))

    def test_ramp_dark_to_light_and_neutrals_last(self):
        hexes = ["ff8080", "400000", "ff0000", "ffffff", "000000", "808080", "0000ff"]
        colors = [self._lin(h) for h in hexes]
        sorted_hex = [hexes[i] for i in sort_order_hue_ramps(colors)]
        self.assertEqual(sorted_hex[:3], ["400000", "ff0000", "ff8080"])
        self.assertEqual(sorted_hex[3], "0000ff")
        self.assertEqual(sorted_hex[4:], ["000000", "808080", "ffffff"])

    def test_dark_tinted_color_keeps_hue_group(self):
        hexes = ["ff0000", "200010", "808080"]
        sorted_hex = [hexes[i] for i in sort_order_hue_ramps([self._lin(h) for h in hexes])]
        self.assertEqual(sorted_hex[-1], "808080")

    def test_other_methods(self):
        hexes = ["0000ff", "ff0000", "ffffff", "202020", "ff8080"]
        colors = [self._lin(h) for h in hexes]
        for fn in (sort_order_hue, sort_order_lightness, sort_order_saturation, sort_order_reverse):
            self.assertEqual(sorted(fn(colors)), list(range(len(colors))))
        by_light = [hexes[i] for i in sort_order_lightness(colors)]
        self.assertEqual((by_light[0], by_light[-1]), ("202020", "ffffff"))
        self.assertEqual([hexes[i] for i in sort_order_hue(colors)][:2], ["ff0000", "ff8080"])
        self.assertEqual([hexes[i] for i in sort_order_saturation(colors)][-2:], ["0000ff", "ff0000"])
        self.assertEqual(sort_order_reverse(colors), [4, 3, 2, 1, 0])


class TestMergeCloseColors(unittest.TestCase):
    @staticmethod
    def _lin(hex_str):
        return tuple(_srgb_to_linear(int(hex_str[i:i + 2], 16) / 255.0) for i in (0, 2, 4))

    def test_distance(self):
        red, near, blue = self._lin("ff0000"), self._lin("fe0000"), self._lin("0000ff")
        self.assertAlmostEqual(color_distance(red, red), 0.0)
        self.assertLess(color_distance(red, near), 2.0)
        self.assertGreater(color_distance(red, blue), 50.0)

    def test_merges_near_duplicates_anchored_on_first(self):
        hexes = ["ff0000", "00ff00", "fe0101", "0000ff", "00fe00"]
        keep, mapping = merge_close_order([self._lin(h) for h in hexes], 5.0)
        self.assertEqual(keep, [0, 1, 3])
        self.assertEqual(mapping, [0, 1, 0, 2, 1])

    def test_distinct_and_zero_threshold_keep_all(self):
        hexes = ["ff0000", "00ff00", "0000ff"]
        colors = [self._lin(h) for h in hexes]
        self.assertEqual(merge_close_order(colors, 5.0)[0], [0, 1, 2])
        dup = colors + [colors[0]]
        self.assertEqual(merge_close_order(dup, 0.0)[0], [0, 1, 2])  # exact duplicate still merges
        near = colors + [self._lin("fe0000")]
        self.assertEqual(merge_close_order(near, 0.0)[0], [0, 1, 2, 3])

    def test_palette_wrapper(self):
        palette = bpy.data.palettes.new("merge_test")
        try:
            for h in ("ff0000", "fe0101", "00ff00", "ff0101"):
                palette.colors.new().color = self._lin(h)
            removed, new_active = merge_close_colors(palette, 5.0, active_index=3)
            self.assertEqual(removed, 2)
            self.assertEqual(new_active, 0)
            self.assertEqual(len(palette.colors), 2)
            reds = [self._lin(h) for h in ("ff0000", "fe0101", "ff0101")]
            for i in range(3):
                self.assertAlmostEqual(palette.colors[0].color[i], sum(c[i] for c in reds) / 3, places=5)
            self.assertAlmostEqual(palette.colors[1].color[1], 1.0, places=5)
        finally:
            bpy.data.palettes.remove(palette)

    def test_restore_and_remerge_from_original(self):
        palette = bpy.data.palettes.new("merge_restore_test")
        try:
            hexes = ("ff0000", "fe0101", "00ff00", "ff0101", "0000ff")
            originals = [self._lin(h) for h in hexes]
            for c in originals:
                palette.colors.new().color = c
            self.assertFalse(has_merge_snapshot(palette))
            removed, _ = merge_close_colors(palette, 5.0, active_index=4)
            self.assertEqual((removed, len(palette.colors)), (2, 3))
            self.assertTrue(has_merge_snapshot(palette))
            # Re-merging (F9 redo) starts from the original colors, even without undo.
            removed, new_active = merge_close_colors(palette, 0.5, active_index=0)
            self.assertEqual(len(palette.colors), 4)
            self.assertEqual(new_active, 3)  # active index remembered from the first merge
            self.assertEqual(restore_merged_colors(palette), 4)
            self.assertEqual(len(palette.colors), 5)
            for pc, c in zip(palette.colors, originals):
                for i in range(3):
                    self.assertAlmostEqual(pc.color[i], c[i], places=5)
            self.assertFalse(has_merge_snapshot(palette))
            self.assertIsNone(restore_merged_colors(palette))
        finally:
            bpy.data.palettes.remove(palette)

    def test_snapshot_dropped_after_manual_edit(self):
        palette = bpy.data.palettes.new("merge_stale_test")
        try:
            for h in ("ff0000", "fe0101", "00ff00"):
                palette.colors.new().color = self._lin(h)
            merge_close_colors(palette, 5.0)
            palette.colors.new().color = (0.3, 0.3, 0.3)
            self.assertFalse(has_merge_snapshot(palette))
            self.assertIsNone(restore_merged_colors(palette))
        finally:
            bpy.data.palettes.remove(palette)

    def test_no_op_merge_leaves_no_snapshot(self):
        palette = bpy.data.palettes.new("merge_noop_test")
        try:
            for h in ("ff0000", "fe0101", "00ff00"):
                palette.colors.new().color = self._lin(h)
            merge_close_colors(palette, 5.0)
            self.assertTrue(has_merge_snapshot(palette))
            self.assertEqual(merge_close_colors(palette, 0.0)[0], 0)  # redo finds nothing: back to original
            self.assertEqual(len(palette.colors), 3)
            self.assertFalse(has_merge_snapshot(palette))
        finally:
            bpy.data.palettes.remove(palette)

    def test_discard_makes_current_colors_the_baseline(self):
        palette = bpy.data.palettes.new("merge_discard_test")
        try:
            for h in ("ff0000", "ff0101", "00ff00", "0000ff"):
                palette.colors.new().color = self._lin(h)
            merge_close_colors(palette, 5.0)
            self.assertEqual(len(palette.colors), 3)
            discard_merge_snapshot(palette)
            merge_close_colors(palette, 0.0)  # fresh merge of what is shown: nothing changes
            self.assertEqual(len(palette.colors), 3)
        finally:
            bpy.data.palettes.remove(palette)

    def test_restore_maps_current_selection_and_snapshot_survives_revert(self):
        palette = bpy.data.palettes.new("merge_active_test")
        try:
            for h in ("ff0000", "00ff00", "fe0101", "0000ff"):
                palette.colors.new().color = self._lin(h)
            merge_close_colors(palette, 5.0, active_index=0)
            palette.colors.new().color = (0.3, 0.3, 0.3)
            self.assertFalse(has_merge_snapshot(palette))
            palette.colors.remove(palette.colors[3])  # edit reverted (e.g. redo): snapshot valid again
            self.assertTrue(has_merge_snapshot(palette))
            self.assertEqual(restore_merged_colors(palette, active_index=2), 3)  # blue was 4th originally
            self.assertEqual(len(palette.colors), 4)
        finally:
            bpy.data.palettes.remove(palette)

    def test_snapshots_pruned_and_cleared(self):
        from utilities import palette_utilities as pu
        palette = bpy.data.palettes.new("merge_prune_test")
        other = bpy.data.palettes.new("merge_prune_other")
        try:
            for pal in (palette, other):
                for h in ("ff0000", "fe0101"):
                    pal.colors.new().color = self._lin(h)
            merge_close_colors(palette, 5.0)
            bpy.data.palettes.remove(palette)
            palette = None
            merge_close_colors(other, 5.0)  # prunes the deleted palette's entry
            self.assertNotIn("merge_prune_test", pu._merge_snapshots)
            clear_merge_snapshots()
            self.assertFalse(has_merge_snapshot(other))
        finally:
            if palette:
                bpy.data.palettes.remove(palette)
            bpy.data.palettes.remove(other)


if __name__ == "__main__":
    unittest.main()
