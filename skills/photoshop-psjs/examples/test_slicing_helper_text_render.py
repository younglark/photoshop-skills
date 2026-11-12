#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for dynamic_text attribute reading and the preview text renderer (synthetic data only).

    python test_slicing_helper_text_render.py

Rendering tests need a TTF on the machine (DejaVu or any font under the usual
system folders, or set TEST_FONT=/path/to/font.ttf); the HarfBuzz / FreeType
tests also need ``pip install uharfbuzz freetype-py``. Missing pieces are skipped.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slicing_helper as h  # noqa: E402
import text_render as T  # noqa: E402

SCALE = 1.345  # text layer transform scale used throughout


def _u(v, unit="pointsUnit"):
    return {"_unit": unit, "_value": v}


def _style(size_pt, *, leading_pt=None, kern="manual", color=(140, 67, 66), tracking=0):
    st = {
        "fontPostScriptName": "Sample-Font",
        "size": _u(size_pt),
        "impliedFontSize": _u(size_pt * SCALE),
        "autoLeading": leading_pt is None,
        "tracking": tracking,
        "baselineShift": _u(0),
        "autoKern": {"_enum": "autoKern", "_value": kern},
        "color": {"_obj": "RGBColor", "red": color[0], "grain": color[1], "blue": color[2]},
    }
    if leading_pt is not None:
        st["leading"] = _u(leading_pt)
        st["impliedLeading"] = _u(leading_pt * SCALE)
    return st


def _desc(*, fx_visible=True, stroke_enabled=True, two_runs=True):
    runs = [{"from": 0, "to": 6, "textStyle": _style(30.0, leading_pt=33.0)}]
    if two_runs:
        runs.append({"from": 6, "to": 10, "textStyle": _style(42.0, leading_pt=33.0)})
    return {
        "opacity": 255,
        "fillOpacity": 255,
        "layerFXVisible": fx_visible,
        "globalAngle": 120,
        "layerEffects": {
            "frameFX": {"enabled": stroke_enabled, "present": True, "style": {"_value": "outsetFrame"},
                        "paintType": {"_value": "solidColor"}, "mode": {"_value": "normal"},
                        "opacity": _u(100, "percentUnit"), "size": _u(3, "pixelsUnit"),
                        "color": {"red": 20, "grain": 100, "blue": 20}},
            "dropShadowMulti": [{"enabled": False, "present": True, "useGlobalAngle": True,
                                 "localLightingAngle": _u(90, "angleUnit"), "distance": _u(5, "pixelsUnit"),
                                 "blur": _u(0, "pixelsUnit"), "chokeMatte": _u(0, "pixelsUnit"),
                                 "opacity": _u(100, "percentUnit"), "color": {"red": 0, "grain": 0, "blue": 0}}],
        },
        "textKey": {
            "textKey": "Label\r123" if two_runs else "Label",
            "transform": {"xx": SCALE, "xy": 0, "yx": 0, "yy": SCALE, "tx": 0, "ty": 0},
            "textClickPoint": {"horizontal": _u(50, "percentUnit"), "vertical": _u(50, "percentUnit")},
            "antiAlias": {"_enum": "antiAliasType", "_value": "antiAliasPlatformGray"},
            "warp": {"warpStyle": {"_enum": "warpStyle", "_value": "warpNone"}},
            "textShape": [{"char": {"_enum": "char", "_value": "paint"}}],
            "textStyleRange": runs,
            "paragraphStyleRange": [{"from": 0, "to": 10, "paragraphStyle": {"align": {"_value": "center"}}}],
            "kerningRange": [],
        },
    }


class FakeShaper:
    """Every glyph advances 0.5 em; records the kern flag it was asked for."""

    ascender = 0.8

    def __init__(self):
        self.kern_flags = []

    def shape(self, s, size, kern):
        self.kern_flags.append(kern)
        return [(ch, 0.5 * size, 0.0, 0.0) for ch in s]


def find_test_font():
    env = os.environ.get("TEST_FONT")
    if env and Path(env).exists():
        return Path(env)
    for p in T._SYSTEM_FONT_FALLBACKS:
        if Path(p).exists():
            return Path(p)
    for root in ("/usr/share/fonts", "/Library/Fonts", "C:/Windows/Fonts"):
        if Path(root).is_dir():
            for f in sorted(Path(root).rglob("*.ttf")):
                return f
    return None


class DescriptorParsing(unittest.TestCase):
    def setUp(self):
        self.attrs = T.text_attrs_from_descriptor(_desc())

    def test_size_and_leading_use_transform_scale(self):
        a = self.attrs
        self.assertEqual(a["text_attrs_version"], 2)
        self.assertAlmostEqual(a["size"], 30.0 * SCALE, places=4)
        self.assertAlmostEqual(a["size_pt"], 30.0, places=4)
        self.assertAlmostEqual(a["leading"], 33.0, places=4)          # text-space points (unchanged meaning)
        self.assertAlmostEqual(a["leading_px"], 33.0 * SCALE, places=4)
        self.assertAlmostEqual(a["scale"]["y"], SCALE, places=6)

    def test_style_runs_are_kept(self):
        runs = self.attrs["runs"]
        self.assertEqual(len(runs), 2)
        self.assertAlmostEqual(runs[0]["size_px"], 30.0 * SCALE, places=4)
        self.assertAlmostEqual(runs[1]["size_px"], 42.0 * SCALE, places=4)
        self.assertEqual((runs[1]["from"], runs[1]["to"]), (6, 10))

    def test_kerning_and_anti_alias(self):
        self.assertEqual(self.attrs["kerning"], "manual")
        self.assertEqual(self.attrs["manual_kerning_ranges"], 0)
        self.assertEqual(self.attrs["anti_alias"], "antiAliasPlatformGray")
        self.assertIsNone(self.attrs["warp"])

    def test_effects_listed_with_visibility(self):
        a = T.text_attrs_from_descriptor(_desc(fx_visible=False))
        self.assertFalse(a["effects_visible"])
        self.assertIsNone(a["stroke"], "legacy stroke field must honour the hidden master switch")
        types = sorted(e["type"] for e in a["layer_effects"])
        self.assertEqual(types, ["DropShadow", "Stroke"])
        shadow = [e for e in a["layer_effects"] if e["type"] == "DropShadow"][0]
        self.assertEqual(shadow["angle"], 120)  # global angle resolved
        visible = T.text_attrs_from_descriptor(_desc())
        self.assertEqual(visible["stroke"]["size"], 3)


class SpecAndLayout(unittest.TestCase):
    def _spec(self, attrs, **opts):
        return T.text_spec(attrs, doc_size=(1000, 400), art_origin=(0, 0), options=opts)

    def test_hidden_or_disabled_effects_are_not_active(self):
        spec = self._spec(T.text_attrs_from_descriptor(_desc(fx_visible=False)))
        self.assertEqual(spec["effects"], [])
        self.assertTrue(all(e["reason"] == "layer effects hidden" for e in spec["effects_hidden"]))
        spec = self._spec(T.text_attrs_from_descriptor(_desc(fx_visible=True, stroke_enabled=False)))
        self.assertEqual(spec["effects"], [])
        self.assertEqual({e["reason"] for e in spec["effects_hidden"]}, {"effect disabled"})
        spec = self._spec(T.text_attrs_from_descriptor(_desc()))
        self.assertEqual([e["type"] for e in spec["effects"]], ["Stroke"])

    def test_no_kern_for_manual_without_pairs(self):
        spec = self._spec(T.text_attrs_from_descriptor(_desc()))
        self.assertFalse(spec["kern"])
        shaper = FakeShaper()
        T.layout_glyphs(spec, lambda _p: shaper)
        self.assertTrue(shaper.kern_flags and not any(shaper.kern_flags))
        self.assertTrue(T.kern_enabled({"kerning": "metrics"})[0])
        self.assertFalse(T.kern_enabled({"kerning": "manual", "manual_kerning_ranges": 2})[0])

    def test_runs_and_mixed_size_leading(self):
        attrs = T.text_attrs_from_descriptor(_desc())
        for k in (0.86, 0.0):
            spec = self._spec(attrs, mixed_leading_k=k)
            glyphs, _fonts, _asc = T.layout_glyphs(spec, lambda _p: FakeShaper())
            line0 = [g for g in glyphs if g["line"] == 0]
            line1 = [g for g in glyphs if g["line"] == 1]
            self.assertEqual({g["size_pt"] for g in line0}, {30.0})
            self.assertEqual({g["size_pt"] for g in line1}, {42.0})
            drop = line1[0]["y"] - line0[0]["y"]
            self.assertAlmostEqual(drop, 33.0 + k * (42.0 - 30.0), places=6)
            # in device pixels the drop is scaled by the transform
            y0 = T.to_device(spec, 0, line0[0]["y"])[1]
            y1 = T.to_device(spec, 0, line1[0]["y"])[1]
            self.assertAlmostEqual(y1 - y0, (33.0 + k * 12.0) * SCALE, places=6)

    def test_smaller_following_line_gets_no_extra_drop(self):
        prev = [{"size_pt": 40.0, "auto_leading": False, "leading_pt": 44.0}]
        cur = [{"size_pt": 20.0, "auto_leading": False, "leading_pt": 44.0}]
        self.assertAlmostEqual(T.line_advance(prev, cur, dict(T.DEFAULT_OPTIONS)), 44.0)
        auto = [{"size_pt": 20.0, "auto_leading": True, "leading_pt": None}]
        self.assertAlmostEqual(T.line_advance(prev, auto, dict(T.DEFAULT_OPTIONS)), 24.0)

    def test_tracking_and_center_alignment(self):
        desc = _desc(two_runs=False)
        desc["textKey"]["textStyleRange"][0]["textStyle"]["tracking"] = 100
        spec = self._spec(T.text_attrs_from_descriptor(desc))
        glyphs, _f, _a = T.layout_glyphs(spec, lambda _p: FakeShaper())
        step = glyphs[1]["x"] - glyphs[0]["x"]
        self.assertAlmostEqual(step, 0.5 * 30.0 + 100 / 1000.0 * 30.0, places=6)
        width = len(glyphs) * step
        self.assertAlmostEqual(glyphs[0]["x"], -width / 2.0, places=6)
        # click point 50% of a 1000x400 doc is the device anchor
        self.assertEqual(spec["anchor"], (500.0, 200.0))

    def test_legacy_v1_attrs_are_rescaled(self):
        v1 = {"content": "Label\r123", "font": "Sample-Font", "size": 30.0 * SCALE, "leading": 33.0,
              "auto_leading": False, "alignment": "center", "color": "#ffffff",
              "transform": {"xx": SCALE, "xy": 0, "yx": 0, "yy": SCALE},
              "click_point": {"horizontal": 50, "vertical": 50}}
        done = T.complete_text_attrs(v1)
        self.assertAlmostEqual(done["size_pt"], 30.0, places=6)
        self.assertAlmostEqual(done["leading_px"], 33.0 * SCALE, places=6)
        self.assertIn("content", done)
        spec = T.text_spec(v1, doc_size=(1000, 400))
        self.assertEqual(spec["version"], 1)
        self.assertAlmostEqual(spec["runs"][0]["size_pt"], 30.0, places=6)
        self.assertAlmostEqual(T.line_advance(spec["runs"], spec["runs"], spec["options"]), 33.0)


class RendererSelection(unittest.TestCase):
    def test_fallback_is_reported(self):
        saved = T._hb
        try:
            T._hb = None
            used, reason = T.select_renderer("auto")
            self.assertEqual(used, "pillow")
            self.assertIn("uharfbuzz", reason or "")
            used, reason = T.select_renderer("pillow")
            self.assertEqual((used, reason), ("pillow", None))
        finally:
            T._hb = saved
        with self.assertRaises(ValueError):
            T.select_renderer("gdi")


@unittest.skipIf(find_test_font() is None, "no TTF font found (set TEST_FONT)")
class Rendering(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fonts = self.tmp / "fonts"
        self.fonts.mkdir()
        shutil.copy(find_test_font(), self.fonts / "Sample-Font.ttf")
        T._FONT_INDEX.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _render(self, renderer, desc, **opts):
        attrs = T.text_attrs_from_descriptor(desc)
        return T.render_text(attrs, renderer=renderer, fonts_dir=str(self.fonts), doc_size=(1000, 400),
                             options=opts)

    def _check_stroke_visibility(self, renderer):
        im_on, _xy, info_on = self._render(renderer, _desc(two_runs=False))
        im_off, _xy2, info_off = self._render(renderer, _desc(two_runs=False, fx_visible=False))
        self.assertEqual(info_on["effects_drawn"], ["Stroke"])
        self.assertEqual(info_off["effects_drawn"], [])
        self.assertTrue(info_off["effects_skipped_hidden"])
        a_on = np.asarray(im_on)[..., 3].astype(int).sum()
        a_off = np.asarray(im_off)[..., 3].astype(int).sum()
        self.assertGreater(a_on, a_off * 1.15, "visible stroke grows the coverage")
        green = np.asarray(im_on)
        self.assertTrue(((green[..., 1] > 80) & (green[..., 0] < 60) & (green[..., 3] > 200)).any())
        self.assertEqual(info_on["fonts"]["Sample-Font"]["match"], "file name")

    def test_pillow_fallback_renders_and_hides_effects(self):
        self._check_stroke_visibility("pillow")

    @unittest.skipIf(T._hb is None or T._ft is None, "uharfbuzz / freetype-py not installed")
    def test_freetype_renders_and_hides_effects(self):
        self._check_stroke_visibility("freetype")

    @unittest.skipIf(T._hb is None or T._ft is None, "uharfbuzz / freetype-py not installed")
    def test_freetype_two_runs_and_snap(self):
        im, (x0, y0), info = self._render("freetype", _desc(fx_visible=False))
        self.assertEqual(info["runs"], 2)
        a = np.asarray(im)[..., 3]
        rows = np.where(a.max(axis=1) > 0)[0]
        self.assertGreater(len(rows), 0)
        # two lines: ink spans more than one line height of the first run
        self.assertGreater(rows[-1] - rows[0], 30.0 * SCALE)
        im2, _xy, _i = self._render("freetype", _desc(fx_visible=False), snap_glyph_origin=False)
        self.assertEqual(im.size, im2.size)

    @unittest.skipIf(T._hb is None or T._skia() is None, "uharfbuzz / skia-python not installed")
    def test_skia_renders_and_hides_effects(self):
        self._check_stroke_visibility("skia")


class ValidateReport(unittest.TestCase):
    def test_validate_records_text_renderer(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            slices = tmp / "slices"
            slices.mkdir()
            attrs = T.text_attrs_from_descriptor(_desc(two_runs=False))
            layout = {
                "schema": h.SCHEMA_LAYOUT,
                "source": {"doc_size": [200, 80], "artboard": {"name": "board", "bounds": [0, 0, 200, 80],
                                                              "size": [200, 80]}},
                "nodes": [{"id": "main_text_label", "name": "main_text_label", "kind": "dynamic_text",
                           "visible_bounds": {"left": 40, "top": 10, "right": 160, "bottom": 60},
                           "logical_bounds": {"left": 40, "top": 10, "right": 160, "bottom": 60},
                           "text": attrs, "default": True}],
                "components": [],
            }
            lpath = tmp / "layout.json"
            lpath.write_text(json.dumps(layout), encoding="utf-8")
            rpath = tmp / "full.png"
            Image.new("RGBA", (200, 80), (30, 30, 30, 255)).save(rpath)
            fonts = tmp / "fonts"
            fonts.mkdir()
            font = find_test_font()
            if font:
                shutil.copy(font, fonts / "Sample-Font.ttf")
            out = tmp / "validate"
            h.cmd_validate(SimpleNamespace(layout=str(lpath), slices_dir=str(slices), full_render=str(rpath),
                                           out_dir=str(out), fonts_dir=str(fonts), group_effects=None,
                                           reuse_tol_mae=4.0, reuse_tol_pct=1.0, text_renderer="pillow",
                                           mixed_leading_k=None, snap_glyph_origin=None))
            report = json.loads((out / "validate_report.json").read_text(encoding="utf-8"))
            tr = report["text_renderer"]
            self.assertEqual(tr["requested"], "pillow")
            self.assertEqual(tr["used"], "pillow")
            self.assertIsNone(tr["fallback_reason"])
            self.assertAlmostEqual(tr["options"]["mixed_leading_k"], 0.86)
            self.assertEqual(tr["legacy_v1_text_attrs"], 0)
            info = report["dynamic_text_render_info"][0]
            self.assertEqual(info["name"], "main_text_label")
            self.assertEqual(info["renderer"], "pillow")
            self.assertEqual(report["dynamic_text_rendered"], 1 if font else report["dynamic_text_rendered"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
