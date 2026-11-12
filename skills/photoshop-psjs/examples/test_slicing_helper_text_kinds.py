#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for 1.4.8 text kinds: static_text vs art_text, and no nine_slice on text.

    python test_slicing_helper_text_kinds.py
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slicing_helper as h  # noqa: E402


def _rules():
    return h.NamingRules(h.DEFAULT_RULES)


def _node(text="Home", name="label", effects=None, params=None, **extra):
    node = {
        "name": name,
        "is_text": True,
        "text": text,
        "effects": list(effects or []),
        "effect_params": list(params or []),
    }
    node.update(extra)
    return node


def _stroke(fill_type="color", blend="normal"):
    return {"type": "Stroke", "enabled": True, "fill_type": fill_type, "blend_mode": blend, "size": 3,
            "color": "#112233", "opacity": 1, "position": "outside"}


def _shadow(blend="multiply"):
    return {"type": "DropShadow", "enabled": True, "blend_mode": blend, "size": 4, "distance": 2,
            "angle": 90, "color": "#000000", "opacity": 0.5, "spread": 0}


class ClassifyText(unittest.TestCase):
    def test_stroke_only_fixed_label_is_static_text(self):
        kind = h.classify_text(_node("Home", effects=["Stroke"], params=[_stroke()]), _rules())
        self.assertEqual(kind, "static_text")
        self.assertEqual(h.text_bake_reasons(_node("Home", effects=["Stroke"], params=[_stroke()])), [])

    def test_drop_shadow_only_is_static_text(self):
        self.assertEqual(
            h.classify_text(_node("Ranking", effects=["DropShadow"], params=[_shadow()]), _rules()),
            "static_text",
        )

    def test_plain_fill_and_stroke_plus_shadow_are_static_text(self):
        self.assertEqual(h.classify_text(_node("OK", name="title"), _rules()), "static_text")
        both = _node("Total", effects=["Stroke", "DropShadow"], params=[_stroke(), _shadow()])
        self.assertEqual(h.classify_text(both, _rules()), "static_text")

    def test_title_name_does_not_force_art_text(self):
        # art_text_name_keywords includes "title"; that must not bake a stroke-only title.
        node = _node("Ranking", name="topbar_title", effects=["Stroke"], params=[_stroke()])
        self.assertEqual(h.classify_text(node, _rules()), "static_text")

    def test_gradient_overlay_is_art_text(self):
        node = _node("Title", effects=["GradientOverlay"],
                     params=[{"type": "GradientOverlay", "enabled": True, "blend_mode": "normal"}])
        self.assertEqual(h.classify_text(node, _rules()), "art_text")
        self.assertIn("GradientOverlay", h.text_bake_reasons(node))

    def test_bevel_is_art_text(self):
        node = _node("Title", effects=["BevelEmboss"],
                     params=[{"type": "BevelEmboss", "enabled": True}])
        self.assertEqual(h.classify_text(node, _rules()), "art_text")
        self.assertIn("BevelEmboss", h.text_bake_reasons(node))

    def test_unreplayable_effects_are_art_text(self):
        for typ in ("InnerGlow", "Satin", "PatternOverlay", "OuterGlow", "ColorOverlay", "InnerShadow"):
            node = _node("Label", effects=[typ], params=[{"type": typ, "enabled": True}])
            self.assertEqual(h.classify_text(node, _rules()), "art_text", typ)
            self.assertIn(typ, h.text_bake_reasons(node), typ)

    def test_warp_and_gradient_stroke_and_rasterized_are_art_text(self):
        self.assertEqual(h.classify_text(_node("Hi", warp="warpArc"), _rules()), "art_text")
        grad_stroke = _node("Hi", effects=["Stroke"], params=[_stroke(fill_type="gradient")])
        self.assertEqual(h.classify_text(grad_stroke, _rules()), "art_text")
        raster = _node("Hi", is_text=False, kind="pixel")
        self.assertEqual(h.classify_text(raster, _rules()), "art_text")
        self.assertIn("rasterized_or_non_text", h.text_bake_reasons(raster))

    def test_value_text_stays_dynamic_even_with_stroke(self):
        node = _node("12850", name="score", effects=["Stroke"], params=[_stroke()])
        self.assertEqual(h.classify_text(node, _rules()), "dynamic_text")

    def test_disabled_or_hidden_effects_do_not_bake(self):
        hidden = _node("Home", effects=["BevelEmboss"],
                       params=[{"type": "BevelEmboss", "enabled": False}], effects_visible=True)
        self.assertEqual(h.classify_text(hidden, _rules()), "static_text")
        master = _node("Home", effects=["BevelEmboss"],
                       params=[{"type": "BevelEmboss", "enabled": True}], effects_visible=False)
        self.assertEqual(h.classify_text(master, _rules()), "static_text")

    def test_pair_consistency_raises_stroke_sibling_to_art_text(self):
        entries = [
            {"kind": "static_text", "text": {"content": "Daily"}, "source_path": "tabs/left", "notes": []},
            {"kind": "art_text", "text": {"content": "Daily"}, "source_path": "tabs/right", "notes": []},
        ]
        h.apply_pair_consistency(entries, _rules())
        self.assertEqual(entries[0]["kind"], "art_text")
        self.assertTrue(any("art_text" in n for n in entries[0]["notes"]))


class ExportAndNineSlice(unittest.TestCase):
    def test_assign_reuse_exports_no_png_for_static_or_dynamic(self):
        entries = [
            {"name": "a", "kind": "static_text"},
            {"name": "b", "kind": "dynamic_text"},
            {"name": "c", "kind": "art_text"},
            {"name": "d", "kind": "image"},
        ]
        h.assign_reuse(entries)
        self.assertFalse(entries[0]["export_png"])
        self.assertFalse(entries[1]["export_png"])
        self.assertTrue(entries[2]["export_png"])
        self.assertTrue(entries[3]["export_png"])

    def test_silent_effect_loss_skips_static_text(self):
        groups = [{"layer_id": 10, "effects": ["DropShadow"], "effect_params": [{"type": "DropShadow"}],
                   "child_ids": [11], "ancestor_ids": []}]
        item = {"name": "lab", "kind": "static_text", "layer_id": 11, "select_ids": [11]}
        self.assertEqual(h.find_silent_effect_loss([item], groups), [])

    def _nine_png(self, path):
        # 64x64 panel with a 10px distinct frame so detect_nine_slice would fire.
        im = Image.new("RGBA", (64, 64), (20, 20, 40, 255))
        for x in range(10, 54):
            for y in range(10, 54):
                im.putpixel((x, y), (80, 160, 200, 255))
        im.save(path)
        detected = h.detect_nine_slice(Image.open(path))
        self.assertIsNotNone(detected, "fixture must look like a nine-slice so the skip is meaningful")
        return detected

    def _build(self, tmp, entries, items, pngs=()):
        slices = tmp / "slices"
        slices.mkdir()
        for name, im in pngs:
            im.save(slices / name)
        manifest = {
            "schema": h.SCHEMA_MANIFEST,
            "status": "confirmed",
            "source": {"artboard": {"id": 1, "name": "board", "bounds": [0, 0, 200, 200], "size": [200, 200]}},
            "entries": entries,
            "components": [],
        }
        mpath = tmp / "manifest.json"
        mpath.write_text(json.dumps(manifest), encoding="utf-8")
        lpath = tmp / "export_log.json"
        lpath.write_text(json.dumps({"ok": True, "items": items}), encoding="utf-8")
        out = tmp / "layout.json"
        h.cmd_build_layout(SimpleNamespace(
            manifest=str(mpath), export_log=str(lpath), slices_dir=str(slices),
            out=str(out), group_effects=None,
        ))
        return json.loads(out.read_text(encoding="utf-8")), slices

    def test_build_layout_static_text_is_attribute_only_without_nine_slice(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            text = {"content": "Home", "font": "Sample", "size": 20, "color": "#ffffff",
                    "text_attrs_version": 2, "runs": [], "stroke": {"size": 3, "color": "#000000", "enabled": True},
                    "layer_effects": [_stroke(), _shadow()], "effects_visible": True}
            entries = [{
                "name": "main_img_home", "kind": "static_text", "layer_id": 5, "select_ids": [5],
                "nine_slice": {"left": 7, "top": 0, "right": 293, "bottom": 0},
                "text": text, "logical_bounds": [10, 10, 80, 40],
            }]
            items = [{"name": "main_img_home", "ok": True, "text": text,
                      "visible_bounds": {"left": 10, "top": 10, "right": 80, "bottom": 40},
                      "logical_bounds": {"left": 10, "top": 10, "right": 80, "bottom": 40}}]
            layout, _slices = self._build(tmp, entries, items)
            node = layout["nodes"][0]
            self.assertEqual(node["kind"], "static_text")
            self.assertIsNone(node["image"])
            self.assertIsNone(node["nine_slice"])
            self.assertEqual(node["text"]["content"], "Home")
            self.assertTrue(any(e.get("type") == "DropShadow" for e in node["text"]["layer_effects"]))

    def test_build_layout_art_text_keeps_png_but_drops_nine_slice(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # Place a nine-slice-looking PNG; build-layout must still null nine_slice.
            entries = [{
                "name": "main_img_title", "kind": "art_text", "layer_id": 8, "select_ids": [8],
                "nine_slice": {"left": 7, "top": 2, "right": 293, "bottom": 2},
                "text": {"content": "Ranking"}, "logical_bounds": [0, 0, 64, 64],
            }]
            items = [{"name": "main_img_title", "ok": True, "out_size": [64, 64],
                      "visible_bounds_from_trim_insets": True,
                      "visible_bounds": {"left": 0, "top": 0, "right": 64, "bottom": 64},
                      "logical_bounds": {"left": 0, "top": 0, "right": 64, "bottom": 64}}]
            # Build after writing the png via _build's pngs arg.
            slices_im = Image.new("RGBA", (64, 64), (20, 20, 40, 255))
            for x in range(10, 54):
                for y in range(10, 54):
                    slices_im.putpixel((x, y), (80, 160, 200, 255))
            self.assertIsNotNone(h.detect_nine_slice(slices_im))
            layout, _ = self._build(tmp, entries, items, pngs=[("main_img_title.png", slices_im)])
            node = layout["nodes"][0]
            self.assertEqual(node["kind"], "art_text")
            self.assertEqual(node["image"], "main_img_title.png")
            self.assertIsNone(node["nine_slice"])

    def test_validate_flags_text_with_nine_slice(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            slices = tmp / "slices"
            slices.mkdir()
            Image.new("RGBA", (64, 64), (10, 20, 30, 255)).save(slices / "main_img_title.png")
            layout = {
                "schema": h.SCHEMA_LAYOUT,
                "source": {"artboard": {"id": 1, "name": "board", "bounds": [0, 0, 80, 80], "size": [80, 80]}},
                "nodes": [{
                    "id": "main_img_title", "name": "main_img_title", "kind": "art_text",
                    "image": "main_img_title.png",
                    "nine_slice": {"left": 7, "top": 0, "right": 293, "bottom": 0},
                    "visible_bounds": {"left": 0, "top": 0, "right": 64, "bottom": 64},
                    "logical_bounds": {"left": 0, "top": 0, "right": 64, "bottom": 64},
                    "text": {"content": "Ranking"}, "default": True,
                }],
                "components": [],
            }
            lpath = tmp / "layout.json"
            lpath.write_text(json.dumps(layout), encoding="utf-8")
            rpath = tmp / "full.png"
            Image.new("RGBA", (80, 80), (0, 0, 0, 255)).save(rpath)
            out = tmp / "validate"
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                rc = h.cmd_validate(SimpleNamespace(
                    layout=str(lpath), slices_dir=str(slices), full_render=str(rpath),
                    out_dir=str(out), fonts_dir=None, group_effects=None,
                    reuse_tol_mae=4.0, reuse_tol_pct=1.0, text_renderer="auto",
                    mixed_leading_k=None, snap_glyph_origin=None, text_legacy_rules=False,
                ))
            report = json.loads((out / "validate_report.json").read_text(encoding="utf-8"))
            kinds = [i["type"] for i in report["issues"]]
            self.assertIn("text_has_nine_slice", kinds)
            issue = next(i for i in report["issues"] if i["type"] == "text_has_nine_slice")
            self.assertEqual(issue["name"], "main_img_title")
            self.assertEqual(issue["kind"], "art_text")
            self.assertNotEqual(rc, 0)


class StaticTextPreview(unittest.TestCase):
    """static_text uses the same HarfBuzz+FreeType path, including drop shadow."""

    def setUp(self):
        try:
            from test_slicing_helper_text_render import find_test_font, HAVE_FT
        except Exception:
            self.font = None
            self.have_ft = False
            return
        self.have_ft = HAVE_FT
        self.font = find_test_font()

    def test_recomposite_draws_static_text_drop_shadow(self):
        if not self.have_ft or not self.font:
            self.skipTest("uharfbuzz / freetype-py or a TTF missing")
        import text_render as T
        font_name = Path(self.font).stem
        # Point the renderer at a folder that contains the test font under a guessable name.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            fonts = tmp / "fonts"
            fonts.mkdir()
            import shutil
            dest = fonts / (Path(self.font).name)
            shutil.copy(self.font, dest)
            T._FONT_INDEX.clear()
            attrs = {
                "text_attrs_version": 2,
                "content": "Hi",
                "font": Path(self.font).stem,
                "size": 24,
                "size_pt": 24,
                "color": "#ffffff",
                "alignment": "left",
                "transform": {"xx": 1, "xy": 0, "yx": 0, "yy": 1, "tx": 0, "ty": 0},
                "scale": {"x": 1, "y": 1},
                "runs": [{"from": 0, "to": 2, "font": Path(self.font).stem, "size_pt": 24, "size_px": 24,
                          "color": "#ffffff", "tracking": 0, "auto_leading": True, "leading_pt": None,
                          "leading_px": None, "faux_bold": False, "faux_italic": False,
                          "horizontal_scale": 100, "vertical_scale": 100, "auto_kern": "metrics"}],
                "paragraphs": [{"from": 0, "to": 2, "align": "left", "auto_leading_pct": 120}],
                "click_point": {"horizontal": 10, "vertical": 40},
                "effects_visible": True,
                "layer_effects": [_shadow()],
                "stroke": None,
            }
            layout = {
                "schema": h.SCHEMA_LAYOUT,
                "source": {"doc_size": [200, 80], "artboard": {"name": "board", "bounds": [0, 0, 200, 80],
                                                              "size": [200, 80]}},
                "nodes": [{"id": "lab", "name": "lab", "kind": "static_text", "text": attrs,
                           "visible_bounds": {"left": 10, "top": 10, "right": 120, "bottom": 60},
                           "logical_bounds": {"left": 10, "top": 10, "right": 120, "bottom": 60},
                           "default": True}],
            }
            canvas, placed = h.recomposite(layout, tmp / "slices", (200, 80), render_text=True,
                                           fonts_dir=str(fonts), text_opts={"renderer": "freetype"})
            self.assertTrue(placed and placed[0].get("rendered"))
            self.assertIn("DropShadow", placed[0].get("effects_drawn") or [])
            arr = np.asarray(canvas)
            self.assertGreater(int(arr[..., 3].max()), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
