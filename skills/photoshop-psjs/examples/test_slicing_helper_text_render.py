#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for dynamic_text attribute reading and the preview text renderer (synthetic data only).

    python test_slicing_helper_text_render.py

Rendering tests need ``pip install uharfbuzz freetype-py`` (Skia test: skia-python)
and a TTF on the machine (DejaVu or any font under the usual system folders, or
set TEST_FONT=/path/to/font.ttf); without them they are skipped. The
"missing packages is an error" tests simulate the import failure and always run.
"""
from __future__ import annotations

import contextlib
import io
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


def FAKE_FONT(_name, _fonts_dir):
    return Path("sample-font.ttf"), "test"


def find_test_font():
    env = os.environ.get("TEST_FONT")
    if env and Path(env).exists():
        return Path(env)
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf", "C:/Windows/Fonts/arial.ttf"):
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
        T.layout_glyphs(spec, lambda _p: shaper, resolve_font=FAKE_FONT)
        self.assertTrue(shaper.kern_flags and not any(shaper.kern_flags))
        self.assertTrue(T.kern_enabled({"kerning": "metrics"})[0])
        self.assertFalse(T.kern_enabled({"kerning": "manual", "manual_kerning_ranges": 2})[0])

    def test_runs_and_mixed_size_leading(self):
        attrs = T.text_attrs_from_descriptor(_desc())
        for k in (0.86, 0.0):
            spec = self._spec(attrs, mixed_leading_k=k)
            glyphs, _fonts, _asc = T.layout_glyphs(spec, lambda _p: FakeShaper(), resolve_font=FAKE_FONT)
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
        glyphs, _f, _a = T.layout_glyphs(spec, lambda _p: FakeShaper(), resolve_font=FAKE_FONT)
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


@contextlib.contextmanager
def missing_packages(hb=True, ft=False, skia=True):
    """Simulate failed imports of uharfbuzz / freetype-py / skia-python."""
    saved = (T._hb, T._HB_ERR, T._ft, T._FT_ERR, T._SKIA, T._SKIA_ERR)
    try:
        if hb:
            T._hb, T._HB_ERR = None, "uharfbuzz not importable: simulated"
        if ft:
            T._ft, T._FT_ERR = None, "freetype-py not importable: simulated"
        if skia:
            T._SKIA, T._SKIA_ERR = None, "skia-python not importable: simulated"
        yield
    finally:
        T._hb, T._HB_ERR, T._ft, T._FT_ERR, T._SKIA, T._SKIA_ERR = saved


HAVE_FT = T._hb is not None and T._ft is not None


class RendererSelection(unittest.TestCase):
    def test_missing_packages_raise_with_install_command(self):
        with missing_packages(hb=True, ft=True, skia=True):
            for name in ("auto", "freetype"):
                with self.assertRaises(T.TextRendererUnavailable) as cm:
                    T.select_renderer(name)
                self.assertIn("pip install uharfbuzz freetype-py", str(cm.exception))
                self.assertEqual(cm.exception.install, T.INSTALL_FREETYPE)
            with self.assertRaises(T.TextRendererUnavailable) as cm:
                T.select_renderer("skia")
            self.assertIn("pip install uharfbuzz skia-python", str(cm.exception))

    def test_freetype_missing_is_not_replaced_when_requested(self):
        with missing_packages(hb=False, ft=True, skia=False):
            with self.assertRaises(T.TextRendererUnavailable):
                T.select_renderer("freetype")

    def test_no_pillow_renderer(self):
        self.assertEqual(T.RENDERERS, ("auto", "freetype", "skia"))
        for name in ("pillow", "gdi"):
            with self.assertRaises(ValueError):
                T.select_renderer(name)
        self.assertFalse(hasattr(T, "PillowRaster"))

    @unittest.skipUnless(HAVE_FT, "uharfbuzz / freetype-py not installed")
    def test_auto_prefers_freetype(self):
        self.assertEqual(T.select_renderer("auto"), "freetype")


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

    @unittest.skipIf(T._hb is None or T._ft is None, "uharfbuzz / freetype-py not installed")
    def test_missing_font_is_an_error(self):
        desc = _desc(two_runs=False)
        desc["textKey"]["textStyleRange"][0]["textStyle"]["fontPostScriptName"] = "Not-Installed-Font"
        with self.assertRaises(T.TextRenderError) as cm:
            self._render("freetype", desc)
        self.assertIn("Not-Installed-Font", str(cm.exception))

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
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.slices = self.tmp / "slices"
        self.slices.mkdir()
        self.fonts = self.tmp / "fonts"
        self.fonts.mkdir()
        font = find_test_font()
        if font:
            shutil.copy(font, self.fonts / "Sample-Font.ttf")
        T._FONT_INDEX.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _validate(self, attrs, renderer="auto"):
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
        lpath = self.tmp / "layout.json"
        lpath.write_text(json.dumps(layout), encoding="utf-8")
        rpath = self.tmp / "full.png"
        Image.new("RGBA", (200, 80), (30, 30, 30, 255)).save(rpath)
        out = self.tmp / "validate"
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = h.cmd_validate(SimpleNamespace(layout=str(lpath), slices_dir=str(self.slices), full_render=str(rpath),
                                                out_dir=str(out), fonts_dir=str(self.fonts), group_effects=None,
                                                reuse_tol_mae=4.0, reuse_tol_pct=1.0, text_renderer=renderer,
                                                mixed_leading_k=None, snap_glyph_origin=None, text_legacy_rules=False))
        report = json.loads((out / "validate_report.json").read_text(encoding="utf-8"))
        return rc, report, out, err.getvalue()

    def test_validate_fails_before_rendering_without_packages(self):
        with missing_packages(hb=True, ft=True, skia=True):
            rc, report, out, err = self._validate(T.text_attrs_from_descriptor(_desc(two_runs=False)))
        self.assertNotEqual(rc, 0)
        self.assertFalse(report["ok"])
        self.assertEqual(report["error"]["type"], "text_renderer_unavailable")
        self.assertEqual(report["error"]["install"], "pip install uharfbuzz freetype-py")
        self.assertIn("pip install uharfbuzz freetype-py", err)
        self.assertFalse((out / "recomposite.png").exists(), "no result images on failure")
        self.assertNotIn("diff_text_rendered", report)

    def test_validate_skia_request_fails_without_skia(self):
        with missing_packages(hb=False, ft=False, skia=True):
            rc, report, _out, err = self._validate(T.text_attrs_from_descriptor(_desc(two_runs=False)), "skia")
        self.assertNotEqual(rc, 0)
        self.assertIn("pip install uharfbuzz skia-python", err)
        self.assertEqual(report["text_renderer"]["requested"], "skia")

    @unittest.skipUnless(HAVE_FT, "uharfbuzz / freetype-py not installed")
    def test_validate_fails_when_a_text_cannot_render(self):
        attrs = T.text_attrs_from_descriptor(_desc(two_runs=False))
        attrs["runs"][0]["font"] = attrs["font"] = "Not-Installed-Font"
        rc, report, out, err = self._validate(attrs)
        self.assertNotEqual(rc, 0)
        self.assertEqual(report["error"]["type"], "text_render_failed")
        self.assertIn("main_text_label", report["error"]["message"])
        self.assertIn("Not-Installed-Font", err)
        self.assertFalse((out / "recomposite.png").exists())

    @unittest.skipUnless(HAVE_FT and find_test_font(), "uharfbuzz / freetype-py or a TTF missing")
    def test_validate_records_text_renderer(self):
        rc, report, _out, _err = self._validate(T.text_attrs_from_descriptor(_desc(two_runs=False)))
        tr = report["text_renderer"]
        self.assertEqual((tr["requested"], tr["used"]), ("auto", "freetype"))
        self.assertNotIn("fallback_reason", tr)
        self.assertAlmostEqual(tr["options"]["mixed_leading_k"], 0.0)
        self.assertEqual(tr["options"]["aa_rules"]["antiAliasPlatformGray"], {"gamma": 1.4})
        self.assertNotIn("profile", tr)
        self.assertNotIn("auto_text_rules", tr["options"])
        self.assertEqual(tr["legacy_v1_text_attrs"], 0)
        info = report["dynamic_text_render_info"][0]
        self.assertEqual((info["name"], info["renderer"]), ("main_text_label", "freetype"))
        self.assertEqual(report["dynamic_text_rendered"], 1)

    @unittest.skipUnless(HAVE_FT and find_test_font(), "uharfbuzz / freetype-py or a TTF missing")
    def test_validate_renders_and_flags_legacy_v1_attrs(self):
        v1 = {"content": "Label", "font": "Sample-Font", "size": 30.0 * SCALE, "leading": None,
              "auto_leading": True, "alignment": "center", "color": "#ffffff",
              "transform": {"xx": SCALE, "xy": 0, "yx": 0, "yy": SCALE},
              "click_point": {"horizontal": 50, "vertical": 50}}
        rc, report, _out, err = self._validate(v1)
        self.assertEqual(report["dynamic_text_rendered"], 1)
        self.assertEqual(report["text_renderer"]["legacy_v1_text_attrs"], 1)
        self.assertEqual(report["text_renderer"]["legacy_v1_nodes"], ["main_text_label"])
        self.assertIn("legacy v1", err)


class Rules14(unittest.TestCase):
    """1.4.0 rules checked against Photoshop gold images (references/slicing.md 10.6)."""

    def test_default_mixed_leading_is_zero_and_legacy_keeps_086(self):
        self.assertEqual(T.DEFAULT_OPTIONS["mixed_leading_k"], 0.0)
        self.assertEqual(T.LEGACY_1_3_OPTIONS["mixed_leading_k"], 0.86)
        prev = [{"size_pt": 30.0, "auto_leading": True, "leading_pt": None}]
        cur = [{"size_pt": 42.0, "auto_leading": True, "leading_pt": None}]
        self.assertAlmostEqual(T.line_advance(prev, cur, dict(T.DEFAULT_OPTIONS)), 42.0 * 1.2)
        self.assertAlmostEqual(T.line_advance(prev, cur, dict(T.DEFAULT_OPTIONS, **T.LEGACY_1_3_OPTIONS)),
                               42.0 * 1.2 + 0.86 * 12.0)

    def test_outer_glow_range_is_exported(self):
        desc = _desc(two_runs=False)
        desc["layerEffects"]["outerGlow"] = {"enabled": True, "present": True, "blur": _u(6, "pixelsUnit"),
                                             "chokeMatte": _u(0, "percentUnit"), "inputRange": _u(50, "percentUnit"),
                                             "opacity": _u(75, "percentUnit"), "color": {"red": 255, "grain": 230, "blue": 128}}
        attrs = T.text_attrs_from_descriptor(desc)
        glow = [e for e in attrs["layer_effects"] if e["type"] == "OuterGlow"][0]
        self.assertEqual((glow["size"], glow["range"]), (6.0, 50.0))

    def test_aa_rule_and_snap_axes(self):
        spec = T.text_spec(dict(T.text_attrs_from_descriptor(_desc(two_runs=False))), doc_size=(1000, 400))
        self.assertEqual(spec["anti_alias"], "antiAliasPlatformGray")
        self.assertEqual(T.aa_rule(spec), {"gamma": 1.4})
        self.assertEqual(T.snap_mode(spec), (True, True))
        spec["anti_alias"] = "antiAliasSmooth"
        self.assertEqual(T.snap_mode(spec), (True, False))
        spec["anti_alias"] = "antiAliasSharp"
        self.assertEqual(T.aa_rule(spec), {})


class MixedLines(unittest.TestCase):
    def test_detects_mixed_size_lines(self):
        runs = [{"from": 0, "to": 6, "size_px": 40}, {"from": 6, "to": 9, "size_px": 56}]
        self.assertTrue(h.text_has_mixed_size_lines({"content": "Level\r280", "runs": runs}))
        self.assertFalse(h.text_has_mixed_size_lines({"content": "Level 280", "runs": runs}))
        same = [{"from": 0, "to": 3, "size_px": 40}, {"from": 3, "to": 9, "size_px": 40}]
        self.assertFalse(h.text_has_mixed_size_lines({"content": "Level\r280", "runs": same}))



class Rules141(unittest.TestCase):
    """1.4.1 auto rules: leadingType and non-uniform transform (references/slicing.md 10.7)."""

    def test_leading_type_from_descriptor_and_engine(self):
        desc = _desc()
        desc["textKey"]["paragraphStyleRange"][0]["paragraphStyle"]["leadingType"] = {
            "_enum": "leadingType", "_value": "leadingAbove"}
        attrs = T.text_attrs_from_descriptor(desc)
        self.assertEqual(attrs["leading_type"], "leadingAbove")
        self.assertEqual(attrs["paragraphs"][0]["leading_type"], "leadingAbove")
        self.assertEqual(T.leading_type_from_engine({
            "ParagraphRun": {"RunArray": [{"ParagraphSheet": {"Properties": {"LeadingType": 1}}}]}
        }), "leadingAbove")
        self.assertEqual(T.leading_type_from_engine({
            "'ParagraphRun'": {"'RunArray'": [{"'ParagraphSheet'": {"'Properties'": {"'LeadingType'": 0}}}]}
        }), "leadingBelow")
        self.assertIsNone(T.leading_type_from_engine({"StyleRun": {}}))

    def test_transform_is_uniform(self):
        self.assertTrue(T.transform_is_uniform(None))
        self.assertTrue(T.transform_is_uniform({"xx": 1.345, "yy": 1.345}))
        self.assertFalse(T.transform_is_uniform({"xx": 1.3488, "yy": 1.345}))
        self.assertFalse(T.transform_is_uniform({"xx": 1.001, "yy": 1.0}))
        self.assertTrue(T.transform_is_uniform({"xx": 1.345, "yy": 1.345 + 1e-9}))

    def test_auto_rules_select_k_and_gamma(self):
        base = T.text_attrs_from_descriptor(_desc())
        # default new-layer: uniform scale, no leadingType -> k 0, gamma kept
        opts = T.apply_auto_text_rules(base, {})
        self.assertEqual(opts["mixed_leading_k"], 0.0)
        self.assertEqual(opts["aa_rules"]["antiAliasPlatformGray"], {"gamma": 1.4})
        above = dict(base, leading_type="leadingAbove", paragraphs=[{**base["paragraphs"][0], "leading_type": "leadingAbove"}])
        opts_a = T.apply_auto_text_rules(above, {})
        self.assertAlmostEqual(opts_a["mixed_leading_k"], 0.86)
        nonu = dict(base, transform={"xx": 1.3488, "xy": 0, "yx": 0, "yy": 1.345})
        opts_n = T.apply_auto_text_rules(nonu, {})
        self.assertNotIn("gamma", opts_n["aa_rules"].get("antiAliasPlatformGray") or {})
        # explicit overrides win
        opts_o = T.apply_auto_text_rules(above, {"mixed_leading_k": 0.5})
        self.assertAlmostEqual(opts_o["mixed_leading_k"], 0.5)
        opts_g = T.apply_auto_text_rules(nonu, {"aa_rules": {"antiAliasPlatformGray": {"gamma": 1.4}}})
        self.assertEqual(opts_g["aa_rules"]["antiAliasPlatformGray"], {"gamma": 1.4})

    def test_leading_above_is_bidirectional(self):
        prev = [{"size_pt": 30.0, "auto_leading": False, "leading_pt": 33.0}]
        bigger = [{"size_pt": 42.0, "auto_leading": False, "leading_pt": 33.0}]
        smaller = [{"size_pt": 20.0, "auto_leading": False, "leading_pt": 33.0}]
        opts = dict(T.DEFAULT_OPTIONS, mixed_leading_k=0.86, leading_type="leadingAbove")
        self.assertAlmostEqual(T.line_advance(prev, bigger, opts, leading_type="leadingAbove"), 33.0 + 0.86 * 12.0)
        self.assertAlmostEqual(T.line_advance(prev, smaller, opts, leading_type="leadingAbove"), 33.0 + 0.86 * (20.0 - 30.0))
        # leadingBelow still only adds on a size increase
        self.assertAlmostEqual(T.line_advance(prev, smaller, dict(T.DEFAULT_OPTIONS, mixed_leading_k=0.86)), 33.0)

    def test_text_profile_option_is_removed(self):
        self.assertFalse(hasattr(T, "TEXT_PROFILES"))
        self.assertNotIn("auto_text_rules", T.DEFAULT_OPTIONS)
        parser = h.build_parser()
        base = ["validate", "--layout", "l.json", "--slices-dir", "s", "--full-render", "f.png", "--out-dir", "o"]
        args = parser.parse_args(base)
        self.assertFalse(hasattr(args, "text_profile") and args.text_profile)
        for extra in (["--text-profile", "older-file"], ["--text-profile", "ps2026"], ["--text-profile=auto"],
                      ["--text-profile"]):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
                parser.parse_args(base + extra)
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("--text-profile was removed in 1.4.2", err.getvalue())


@unittest.skipIf(find_test_font() is None or T._hb is None or T._ft is None, "uharfbuzz / freetype-py or a TTF missing")
class Rules14Rendering(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fonts = self.tmp / "fonts"
        self.fonts.mkdir()
        shutil.copy(find_test_font(), self.fonts / "Sample-Font.ttf")
        T._FONT_INDEX.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _render(self, desc, renderer="freetype", **opts):
        attrs = T.text_attrs_from_descriptor(desc)
        return T.render_text(attrs, renderer=renderer, fonts_dir=str(self.fonts), doc_size=(1000, 400), options=opts)

    @staticmethod
    def _alpha(im):
        return np.asarray(im)[..., 3].astype(float)

    def test_platform_gray_gamma_darkens_partial_pixels(self):
        desc = _desc(two_runs=False, fx_visible=False)
        im_rule, _xy, info = self._render(desc)
        im_plain, _xy2, _i = self._render(desc, aa_rules={})
        self.assertEqual(info["aa_rule"]["gamma"], 1.4)
        self.assertGreater(self._alpha(im_rule).sum(), self._alpha(im_plain).sum() * 1.02)

    def _faux(self, key):
        desc = _desc(two_runs=False, fx_visible=False)
        desc["textKey"]["textStyleRange"][0]["textStyle"][key] = True
        return desc

    def test_faux_bold_is_drawn_and_can_be_turned_off(self):
        plain, _xy, _i = self._render(_desc(two_runs=False, fx_visible=False))
        bold, _xy2, info = self._render(self._faux("syntheticBold"))
        off, _xy3, info_off = self._render(self._faux("syntheticBold"), faux_bold_k=0.0, faux_bold_adv_k=0.0)
        self.assertGreater(self._alpha(bold).sum(), self._alpha(plain).sum() * 1.05)
        cols = lambda im: np.where(self._alpha(im).max(axis=0) > 0)[0]
        self.assertGreater(cols(bold)[-1] - cols(bold)[0], cols(plain)[-1] - cols(plain)[0])
        self.assertTrue(any("embolden" in a for a in info["approximations"]))
        self.assertIn("faux bold not rendered", info_off["approximations"])
        self.assertAlmostEqual(self._alpha(off).sum(), self._alpha(plain).sum(), delta=1.0)

    def test_faux_italic_is_a_shear(self):
        plain, _xy, _i = self._render(_desc(two_runs=False, fx_visible=False))
        ital, _xy2, info = self._render(self._faux("syntheticItalic"))
        self.assertTrue(any("shear" in a for a in info["approximations"]))
        self.assertFalse(np.array_equal(np.asarray(plain), np.asarray(ital)))

    def test_fill_opacity_keeps_the_outside_stroke(self):
        desc = _desc(two_runs=False)
        desc["fillOpacity"] = 0
        disjoint, _xy, _i = self._render(desc)
        over, _xy2, _i2 = self._render(desc, stroke_composite="over")
        # Photoshop: fill opacity 0 leaves only the stroke ring; the 1.3 'over' composite kept the glyph body
        self.assertLess(self._alpha(disjoint).sum(), self._alpha(over).sum() * 0.9)
        self.assertGreater(self._alpha(disjoint).max(), 200)

    def test_hires_stroke_needs_scipy(self):
        saved = T._ndi
        T._ndi = None
        try:
            with self.assertRaises(T.TextRendererUnavailable) as cm:
                self._render(_desc(two_runs=False))
            self.assertIn("pip install scipy", str(cm.exception))
            im, _xy, _i = self._render(_desc(two_runs=False, fx_visible=False))  # no effect: no scipy needed
            self.assertIsNotNone(im)
        finally:
            T._ndi = saved

    def test_legacy_rules_still_render(self):
        for renderer in ("freetype",) + (("skia",) if T._skia() is not None else ()):
            for fill_opacity in (100, 0):
                desc = _desc(two_runs=False)
                desc["textKey"]["textStyleRange"][0]["textStyle"]["syntheticBold"] = True
                desc["fillOpacity"] = fill_opacity
                im, _xy, info = self._render(desc, renderer=renderer, **T.LEGACY_1_3_OPTIONS)
                self.assertEqual(info["effects_drawn"], ["Stroke"])
                self.assertGreater(self._alpha(im).max(), 200)

    @unittest.skipIf(T._skia() is None, "skia-python not installed")
    def test_skia_draws_faux_bold(self):
        plain, _xy, _i = self._render(_desc(two_runs=False, fx_visible=False), renderer="skia")
        bold, _xy2, _i2 = self._render(self._faux("syntheticBold"), renderer="skia")
        self.assertGreater(self._alpha(bold).sum(), self._alpha(plain).sum() * 1.05)


def find_cjk_font():
    env = os.environ.get("TEST_CJK_FONT")
    if env and Path(env).exists():
        return Path(env)
    for root in ("/usr/share/fonts", "C:/Windows/Fonts", "/Library/Fonts"):
        if Path(root).is_dir():
            for pat in ("NotoSansCJK*", "NotoSerifCJK*", "NotoSansSC*", "NotoSerifSC*"):
                for f in sorted(Path(root).rglob(pat)):
                    return f
    return None


class InkShaper(FakeShaper):
    """FakeShaper with an ink top of 0.7 em and a features argument."""

    def __init__(self):
        super().__init__()
        self.features = []

    def ink_top(self, _gid):
        return 0.7

    def shape(self, s, size, kern, features=None):
        self.features.append(features)
        return super().shape(s, size, kern)


def _box_attrs(content, width, height, size=30.0):
    desc = _desc(two_runs=False, fx_visible=False)
    tk = desc["textKey"]
    tk["textKey"] = content
    tk["transform"] = {"xx": 1, "xy": 0, "yx": 0, "yy": 1, "tx": 0, "ty": 0}
    tk["textShape"] = [{"char": {"_enum": "char", "_value": "box"},
                        "bounds": {"left": _u(0), "top": _u(0), "right": _u(width), "bottom": _u(height)}}]
    st = _style(size)
    st["impliedFontSize"] = _u(size)
    tk["textStyleRange"] = [{"from": 0, "to": len(content) + 1, "textStyle": st}]
    tk["paragraphStyleRange"] = [{"from": 0, "to": len(content) + 1, "paragraphStyle": {"align": {"_value": "left"}}}]
    return T.text_attrs_from_descriptor(desc)


class Rules143(unittest.TestCase):
    """1.4.3: whole-pixel shadow offset, no locl, paragraph (box) wrapping (references/slicing.md 10.8)."""

    def _lines(self, content, width, height=400, **opts):
        spec = T.text_spec(_box_attrs(content, width, height), doc_size=(1000, 400), options=opts)
        shaper = InkShaper()
        glyphs, _f, _a = T.layout_glyphs(spec, lambda _p: shaper, resolve_font=FAKE_FONT)
        lines = {}
        for g in glyphs:
            lines.setdefault(g["line"], []).append(g)
        return ["".join(g["gid"] for g in lines[k]) for k in sorted(lines)], glyphs, shaper

    def test_shadow_offset_rounds_half_away_with_float_edge(self):
        dx, dy = T._shadow_offset({"distance": 3, "angle": 120})
        self.assertLess(dx, 1.5)                      # 1.4999999999999993 in floating point
        self.assertEqual((T._round_half_away(dx), T._round_half_away(dy)), (2, 3))
        dx4, dy4 = T._shadow_offset({"distance": 4, "angle": 120})
        self.assertEqual((T._round_half_away(dx4), T._round_half_away(dy4)), (2, 3))
        for v, want in ((-1.5, -2), (-0.5, -1), (0.5, 1), (2.49, 2), (-2.121, -2), (0.0, 0)):
            self.assertEqual(T._round_half_away(v), want, v)
        self.assertEqual(T.DEFAULT_OPTIONS["shadow_offset_model"], "pixel")
        self.assertEqual(T.LEGACY_1_3_OPTIONS["shadow_offset_model"], "raster")

    def test_shift_px_moves_whole_pixels(self):
        a = np.zeros((5, 6))
        a[1, 1] = 1.0
        b = T._shift_px(a, 2, 3)
        self.assertEqual(b[4, 3], 1.0)
        self.assertEqual(b.sum(), 1.0)
        c = T._shift_px(a, -1, -1)
        self.assertEqual(c[0, 0], 1.0)
        self.assertEqual(T._shift_px(a, 9, 0).sum(), 0.0)

    def test_layout_turns_locl_off(self):
        _lines, _g, shaper = self._lines("ab 12", 1000)
        self.assertTrue(shaper.features and all(f == {"locl": False} for f in shaper.features))
        _lines, _g, shaper = self._lines("ab 12", 1000, font_locl=True)
        self.assertTrue(all(f is None for f in shaper.features))
        self.assertFalse(T.DEFAULT_OPTIONS["font_locl"])
        self.assertTrue(T.LEGACY_1_3_OPTIONS["font_locl"])

    @unittest.skipUnless(HAVE_FT and find_cjk_font(), "uharfbuzz / freetype-py or a Noto CJK font missing")
    def test_locl_off_keeps_cmap_digits(self):
        path = find_cjk_font()
        hbf = T._HBFont.get(path)
        cmap = T._ft.Face(str(path)).get_char_index(ord("1"))
        gid = hbf.shape("1", 32, True, features={"locl": False})[0][0]
        self.assertEqual(gid, cmap)

    def test_greedy_wrap_and_first_baseline(self):
        # every glyph advances 15 pt (0.5 em at 30 pt)
        lines, glyphs, _s = self._lines("aaa bbb ccc", 110)
        self.assertEqual(lines, ["aaa bbb ", "ccc"])
        self.assertAlmostEqual(glyphs[0]["y"], 0.7 * 30.0, places=6)    # box top + tallest ink
        self.assertAlmostEqual(glyphs[0]["x"], 0.0, places=6)
        second = [g for g in glyphs if g["line"] == 1]
        self.assertAlmostEqual(second[0]["y"] - glyphs[0]["y"], 1.2 * 30.0, places=6)
        self.assertAlmostEqual(second[0]["x"], 0.0, places=6)
        lines, _g, _s = self._lines("aaa bbb ccc", 100)
        self.assertEqual(lines, ["aaa ", "bbb ", "ccc"])
        lines, _g, _s = self._lines("aaa bbb ccc", 110, box_wrap=False)
        self.assertEqual(lines, ["aaa bbb ccc"])

    def test_lines_below_the_box_are_hidden(self):
        # baselines 21, 57, 93: a line is drawn only while its baseline is inside the box
        lines, _g, _s = self._lines("aaa bbb ccc", 60, height=60)
        self.assertEqual(lines, ["aaa ", "bbb "])
        lines, _g, _s = self._lines("aaa bbb ccc", 60, height=56)
        self.assertEqual(lines, ["aaa "])
        lines, _g, _s = self._lines("aaa bbb ccc", 60, height=100)
        self.assertEqual(lines, ["aaa ", "bbb ", "ccc"])

    def test_cjk_line_end_may_overrun_by_an_eighth_em(self):
        # five glyphs = 75 pt; allowance 0.125 x 30 = 3.75 pt
        self.assertEqual(self._lines("一二三四五", 73)[0], ["一二三四五"])
        self.assertEqual(self._lines("一二三四五", 70)[0], ["一二三四", "五"])
        self.assertEqual(self._lines("一二三四五", 73, box_cjk_overflow_em=0)[0], ["一二三四", "五"])
        # Latin gets no allowance; a word wider than the box breaks at the last glyph that fits
        self.assertEqual(self._lines("abcde", 73)[0], ["abcd", "e"])
        # no line may start with closing punctuation
        self.assertEqual(self._lines("一二三，", 50)[0], ["一二", "三，"])

    @unittest.skipUnless(HAVE_FT and find_test_font(), "uharfbuzz / freetype-py or a TTF missing")
    def test_rendered_shadow_is_the_fill_moved_by_whole_pixels(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            shutil.copy(find_test_font(), tmp / "Sample-Font.ttf")
            T._FONT_INDEX.clear()
            base = _desc(two_runs=False, fx_visible=True, stroke_enabled=False)
            base["textKey"]["transform"] = {"xx": 1, "xy": 0, "yx": 0, "yy": 1, "tx": 0, "ty": 0}
            fx = base["layerEffects"]["dropShadowMulti"][0]
            fx.update({"enabled": True, "useGlobalAngle": False, "localLightingAngle": _u(120, "angleUnit"),
                       "distance": _u(3, "pixelsUnit")})
            plain = json.loads(json.dumps(base))
            plain["layerEffects"]["dropShadowMulti"][0]["enabled"] = False

            def canvas(desc):
                im, (x0, y0), _i = T.render_text(T.text_attrs_from_descriptor(desc), renderer="freetype",
                                                 fonts_dir=str(tmp), doc_size=(400, 200))
                c = np.zeros((200, 400))
                a = np.asarray(im)[..., 3] / 255.0
                c[y0:y0 + a.shape[0], x0:x0 + a.shape[1]] = a
                return c

            fill, shadowed = canvas(plain), canvas(base)
            moved = T._shift_px(fill, 2, 3)
            only = (fill == 0) & (moved > 0)
            self.assertGreater(only.sum(), 20)
            self.assertLess(np.abs(shadowed[only] - moved[only]).max(), 2.5 / 255)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_box_wrap_info(self):
        spec = T.text_spec(_box_attrs("aaa", 100, 100), doc_size=(1000, 400))
        self.assertEqual(spec["shape"], "box")
        self.assertTrue(spec["options"]["box_wrap"])
        self.assertAlmostEqual(spec["options"]["box_cjk_overflow_em"], 0.125)


if __name__ == "__main__":
    unittest.main(verbosity=2)
