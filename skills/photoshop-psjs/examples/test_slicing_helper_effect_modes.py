#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for baked vs engine-drawn group effects and the exported preview (synthetic data only).

    python test_slicing_helper_effect_modes.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slicing_helper as h  # noqa: E402


class _Enum:
    def __init__(self, name):
        self.name = name


class DropShadow:  # class name is what effect_params reports
    enabled = True
    blend_mode = _Enum("MULTIPLY")
    color = {b"Rd  ": 255.0, b"Grn ": 0.0, b"Bl  ": 128.0}
    opacity = 60.0
    angle = 90.0
    use_global_light = False
    distance = 4.0
    size = 8.0
    choke = 10.0
    noise = 0.0


class Stroke:
    enabled = True
    blend_mode = _Enum("NORMAL")
    color = {"Rd  ": 0, "Grn ": 0, "Bl  ": 0}
    opacity = 100.0
    size = 3.0
    position = _Enum("OUTSIDE")
    fill_type = _Enum("SOLID_COLOR")


class WeirdEffect:
    @property
    def enabled(self):
        return True

    @property
    def color(self):
        raise RuntimeError("unreadable")

    @property
    def opacity(self):
        return "n/a"


class TestEffectParams(unittest.TestCase):
    def test_drop_shadow(self):
        p = h.effect_params(DropShadow())
        self.assertEqual(p["type"], "DropShadow")
        self.assertEqual(p["color"], "#ff0080")
        self.assertAlmostEqual(p["opacity"], 0.6)
        self.assertEqual(p["blend_mode"], "multiply")
        self.assertEqual(p["distance"], 4.0)
        self.assertEqual(p["size"], 8.0)
        self.assertEqual(p["spread"], 10.0)
        self.assertNotIn("choke", p)

    def test_stroke_position(self):
        p = h.effect_params(Stroke())
        self.assertEqual(p["position"], "outside")
        self.assertEqual(p["color"], "#000000")
        self.assertEqual(p["size"], 3.0)

    def test_unknown_effect_never_raises(self):
        p = h.effect_params(WeirdEffect())
        self.assertEqual(p["type"], "WeirdEffect")
        self.assertTrue(p.get("unknown"))
        self.assertNotIn("color", p)
        self.assertNotIn("opacity", p)

    def test_color_float_and_hex(self):
        self.assertEqual(h.color_to_hex({"redFloat": 1.0, "greenFloat": 0.5, "blueFloat": 0.0}), "#ff8000")
        self.assertIsNone(h.color_to_hex({"H   ": 10}))


class TestPsdToolsEffectClasses(unittest.TestCase):
    """Uses the installed psd-tools effect classes on a hand-built descriptor (no PSD file)."""

    def test_real_drop_shadow(self):
        try:
            from psd_tools.api.effects import DropShadow as PtDropShadow
            from psd_tools.psd.descriptor import Bool, Descriptor, Double, Enumerated, UnitFloat
            from psd_tools.terminology import Key, Unit
        except Exception as exc:  # psd-tools absent or API moved
            self.skipTest(f"psd-tools effect classes unavailable: {exc}")
        c = Descriptor(classID=b"RGBC")
        c[Key.Red], c[Key.Green], c[Key.Blue] = Double(200), Double(10), Double(0)
        d = Descriptor(classID=b"DrSh")
        d[Key.Enabled] = Bool(True)
        d[Key.Color] = c
        d[Key.Opacity] = UnitFloat(unit=Unit.Percent, value=55.0)
        d[Key.Mode] = Enumerated(typeID=b"BlnM", enum=b"Mltp")
        d[Key.UseGlobalAngle] = Bool(False)
        d[Key.LocalLightingAngle] = UnitFloat(unit=Unit.Angle, value=120.0)
        d[Key.Distance] = UnitFloat(unit=Unit.Pixels, value=5.0)
        d[Key.Blur] = UnitFloat(unit=Unit.Pixels, value=7.0)
        d[Key.ChokeMatte] = UnitFloat(unit=Unit.Pixels, value=12.0)
        p = h.effect_params(PtDropShadow(d, None))
        self.assertEqual(p["color"], "#c80a00")
        self.assertAlmostEqual(p["opacity"], 0.55)
        self.assertEqual((p["angle"], p["distance"], p["size"], p["spread"]), (120.0, 5.0, 7.0, 12.0))
        self.assertEqual(p["blend_mode"], "multiply")


def _logical(l, t, r, b):
    return {"left": l, "top": t, "right": r, "bottom": b}


class TestEffectBounds(unittest.TestCase):
    def test_drop_shadow_down(self):
        eb = h.estimate_effect_bounds(_logical(10, 10, 30, 30), [{"type": "DropShadow", "angle": 90, "distance": 4, "size": 5}])
        self.assertEqual(eb, _logical(5, 9, 35, 39))

    def test_stroke_and_inner(self):
        eb = h.estimate_effect_bounds(_logical(0, 0, 10, 10), [
            {"type": "Stroke", "size": 2, "position": "outside"},
            {"type": "ColorOverlay"},
        ])
        self.assertEqual(eb, _logical(-2, -2, 12, 12))
        self.assertEqual(h.estimate_effect_bounds(_logical(0, 0, 10, 10), [{"type": "Stroke", "size": 2, "position": "inside"}]), _logical(0, 0, 10, 10))


GROUPS = [{"layer_id": 10, "effects": ["DropShadow"], "effect_params": [{"type": "DropShadow"}],
           "child_ids": [11, 12, 20], "ancestor_ids": [5]}]


class TestSilentLoss(unittest.TestCase):
    def _item(self, **kw):
        base = {"name": "<item>_img", "kind": "image", "layer_id": 11, "select_ids": [11, 12]}
        base.update(kw)
        return base

    def test_children_without_mode_is_flagged(self):
        probs = h.find_silent_effect_loss([self._item()], GROUPS)
        self.assertEqual(len(probs), 1)
        self.assertEqual(probs[0]["type"], "group_effect_lost")
        self.assertEqual(probs[0]["effect_group_id"], 10)

    def test_baked_on_children_still_flagged(self):
        self.assertEqual(len(h.find_silent_effect_loss([self._item(effect_mode="baked")], GROUPS)), 1)

    def test_engine_without_params_flagged(self):
        self.assertEqual(len(h.find_silent_effect_loss([self._item(effect_mode="engine")], GROUPS)), 1)

    def test_ok_cases(self):
        ok = [
            self._item(layer_id=10, select_ids=[10], effect_mode="baked"),
            self._item(layer_id=5, select_ids=[5]),
            self._item(effect_mode="engine", engine_effects=[{"type": "DropShadow"}]),
            self._item(kind="dynamic_text"),
            self._item(layer_id=99, select_ids=[99]),
        ]
        self.assertEqual(h.find_silent_effect_loss(ok, GROUPS), [])

    def test_groups_from_tree(self):
        leaf = {"layer_id": 20, "name": "a", "children": [], "effects": []}
        grp = {"layer_id": 10, "name": "<item>", "is_group": True, "effects": ["DropShadow"],
               "effect_params": [{"type": "DropShadow"}], "children": [leaf], "bbox": [0, 0, 5, 5]}
        root = {"layer_id": 5, "name": "root", "is_group": True, "effects": [], "children": [grp]}
        groups = h.effect_groups_from_tree(root)
        self.assertEqual(groups[0]["layer_id"], 10)
        self.assertEqual(groups[0]["child_ids"], [20])
        self.assertEqual(groups[0]["ancestor_ids"], [5])


def _square(path, size=20, color=(0, 200, 255, 255)):
    im = Image.new("RGBA", (size, size), color)
    im.save(path)
    return im


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.slices = self.tmp / "slices"
        self.slices.mkdir()

    def _manifest(self, mode, effects):
        part = {"name": "main_img_item", "role": "icon", "kind": "image", "layer_id": 10, "select_ids": [11, 12],
                "hidden": False, "effect_mode": mode, "engine_effects": effects,
                "effect_reason": "simple shadow; engine draws it"}
        return {
            "schema": h.SCHEMA_MANIFEST,
            "status": "confirmed",
            "source": {"artboard": {"id": 1, "name": "<artboard>", "bounds": [0, 0, 80, 80], "size": [80, 80]}},
            "components": [{"id": "main_screen", "type": "screen", "shared": [part]}],
        }

    def _build(self, manifest):
        h.flatten_components(manifest)
        mpath = self.tmp / "manifest.json"
        mpath.write_text(json.dumps(manifest), encoding="utf-8")
        (self.tmp / "group_effects.json").write_text(json.dumps({"groups": GROUPS}), encoding="utf-8")
        log = {"ok": True, "items": [{"name": "main_img_item", "ok": True, "out_size": [20, 20],
                                      "visible_bounds_from_trim_insets": True,
                                      "visible_bounds": _logical(30, 30, 50, 50),
                                      "logical_bounds": _logical(30, 30, 50, 50)}]}
        lpath = self.tmp / "export_log.json"
        lpath.write_text(json.dumps(log), encoding="utf-8")
        out = self.tmp / "layout.json"
        h.cmd_build_layout(SimpleNamespace(manifest=str(mpath), export_log=str(lpath), slices_dir=str(self.slices),
                                           out=str(out), group_effects=None))
        return json.loads(out.read_text(encoding="utf-8"))

    def test_build_layout_passes_engine_fields(self):
        effects = [{"type": "DropShadow", "angle": 90, "distance": 4, "size": 6, "color": "#000000", "opacity": 0.5}]
        layout = self._build(self._manifest("engine", effects))
        node = layout["nodes"][0]
        self.assertEqual(node["effect_mode"], "engine")
        self.assertEqual(node["engine_effects"], effects)
        self.assertEqual(node["effect_reason"], "simple shadow; engine draws it")
        self.assertTrue(node["effect_bounds_estimated"])
        self.assertEqual(node["effect_bounds"], _logical(24, 28, 56, 60))
        self.assertEqual(node["logical_bounds"], node["visible_bounds"])
        part = layout["components"][0]["shared"][0]
        self.assertEqual(part["effect_mode"], "engine")
        self.assertIn("effect_bounds", part)
        self.assertEqual(layout["effect_issues"], [])

    def test_build_layout_flags_silent_loss(self):
        layout = self._build(self._manifest(None, []))
        self.assertEqual(len(layout["effect_issues"]), 1)

    def _ref(self, with_shadow, effects):
        canvas = Image.new("RGBA", (80, 80), (255, 255, 255, 255))
        im = Image.new("RGBA", (20, 20), (0, 200, 255, 255))
        node = {"name": "x", "effect_mode": "engine", "engine_effects": effects}
        if with_shadow:
            canvas, _, _ = h.render_engine_effects(canvas, node, im, 30, 30)
        return h.alpha_over(canvas, im, (30, 30))

    def _validate(self, layout, ref):
        _square(self.slices / "main_img_item.png")
        lpath = self.tmp / "layout.json"
        lpath.write_text(json.dumps(layout), encoding="utf-8")
        rpath = self.tmp / "full_render.png"
        ref.save(rpath)
        vdir = self.tmp / "validate"
        rc = h.cmd_validate(SimpleNamespace(layout=str(lpath), slices_dir=str(self.slices), full_render=str(rpath),
                                            out_dir=str(vdir), fonts_dir=None, group_effects=None))
        return rc, json.loads((vdir / "validate_report.json").read_text(encoding="utf-8")), vdir

    def test_validate_emulates_shadow_and_writes_exported_preview(self):
        effects = [{"type": "DropShadow", "angle": 90, "distance": 4, "size": 6, "spread": 0,
                    "color": "#000000", "opacity": 0.6}]
        layout = self._build(self._manifest("engine", effects))
        # The reference is drawn with a shadow on a white canvas; the recomposite is
        # transparent outside the slice, so compare the shadow region via emulation flags.
        rc, report, vdir = self._validate(layout, self._ref(True, effects))
        self.assertEqual(report["engine_effects_emulated"], [{"name": "main_img_item", "effects": ["DropShadow"]}])
        self.assertEqual(report["engine_effects_unemulated"], [])
        self.assertFalse([i for i in report["issues"] if i["type"] == "group_effect_lost"])
        rec = np.asarray(Image.open(vdir / "recomposite_text_masked.png").convert("RGBA"))
        self.assertGreater(int(rec[52, 40, 3]), 0, "shadow pixels below the part")
        self.assertEqual(int(rec[10, 10, 3]), 0)
        self.assertTrue(Path(report["exported_preview"]["annotated"]).exists())
        self.assertTrue(Path(report["exported_preview"]["side_by_side"]).exists())
        ann = Image.open(report["exported_preview"]["annotated"])
        self.assertEqual(ann.size, (80, 80 + 36))

    def test_validate_masks_unemulated_stroke(self):
        effects = [{"type": "Stroke", "size": 3, "position": "outside", "color": "#000000"}]
        layout = self._build(self._manifest("engine", effects))
        rc, report, _ = self._validate(layout, self._ref(False, effects))
        un = report["engine_effects_unemulated"]
        self.assertEqual(len(un), 1)
        self.assertEqual(un[0]["effects"], ["Stroke"])
        self.assertEqual(un[0]["area"], "ring")

    def test_validate_reports_silent_loss(self):
        layout = self._build(self._manifest(None, []))
        rc, report, _ = self._validate(layout, self._ref(False, []))
        self.assertEqual(rc, 1)
        self.assertTrue([i for i in report["issues"] if i["type"] == "group_effect_lost"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
