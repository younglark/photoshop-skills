#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for repeated-element reuse: evidence, flatten, build-layout placement, validate (synthetic data).

    python test_slicing_helper_reuse.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slicing_helper as h  # noqa: E402


def _box(l, t, r, b):
    return {"left": l, "top": t, "right": r, "bottom": b}


def _row_img(color=(40, 120, 200, 255), size=(30, 10)):
    return Image.new("RGBA", size, color)


class TestReuseCandidates(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "crops").mkdir()

    def _rec(self, lid, name, im, **kw):
        im.save(self.dir / "crops" / f"{lid}.png")
        rec = {"layer_id": lid, "name": name, "path": f"list/{name}", "kind": "pixel",
               "crop": f"crops/{lid}.png", "nine_slice": None}
        rec.update(kw)
        return rec

    def test_identical_rows_hint_reuse(self):
        recs = [self._rec(i, f"row {i}", _row_img()) for i in (1, 2, 3)]
        out = h.compute_reuse_candidates(recs, self.dir, self.dir / "reuse_compare")
        self.assertEqual(out["count"], 1)
        g = out["groups"][0]
        self.assertEqual(g["hint"], "reuse")
        self.assertEqual(g["layer_ids"], [1, 2, 3])
        self.assertEqual(g["evidence"]["same_name_pattern"], "row #")
        self.assertEqual(g["evidence"]["ui_role_prior"]["role"], "list_row_bg")
        self.assertIsNone(g["compare_image"])

    def test_same_pattern_different_color_needs_decision(self):
        recs = [self._rec(1, "row 1", _row_img()), self._rec(2, "row 2", _row_img()),
                self._rec(3, "row 3", _row_img((200, 60, 40, 255)))]
        out = h.compute_reuse_candidates(recs, self.dir, self.dir / "reuse_compare")
        g = out["groups"][0]
        self.assertEqual(g["hint"], "needs_decision")
        self.assertEqual(g["pixel_clusters"], [[1, 2], [3]])
        self.assertTrue((self.dir / g["compare_image"]).exists())
        self.assertGreater(g["max_mad"], h.REUSE_MAD_TOL)

    def test_smart_object_evidence_and_unrelated(self):
        recs = [self._rec(1, "icon a", _row_img(size=(12, 12)), smart_object_id="abc"),
                self._rec(2, "badge", _row_img((0, 0, 0, 255), (12, 12)), smart_object_id="abc"),
                self._rec(3, "other", _row_img((9, 9, 9, 255), (50, 20)))]
        out = h.compute_reuse_candidates(recs, self.dir, self.dir / "reuse_compare")
        self.assertEqual(out["count"], 1)
        g = out["groups"][0]
        self.assertEqual(g["evidence"]["same_smart_object"], "abc")
        self.assertEqual(g["hint"], "needs_decision")

    def test_pixel_identical_without_logic_is_reuse_hint(self):
        recs = [self._rec(1, "alpha", _row_img()), self._rec(2, "beta", _row_img())]
        g = h.compute_reuse_candidates(recs, self.dir, self.dir / "rc")["groups"][0]
        self.assertEqual(g["hint"], "reuse")
        self.assertIsNone(g["evidence"]["same_name_pattern"])


class TestUiPriors(unittest.TestCase):
    def test_role_priors(self):
        self.assertEqual(h.ui_role_prior("row 3", "list/row 3")["role"], "list_row_bg")
        self.assertEqual(h.ui_role_prior("头像框")["role"], "avatar_frame")
        self.assertEqual(h.ui_role_prior("avatar")["prior"], "dynamic_placeholder")
        self.assertEqual(h.ui_role_prior("medal_1")["prior"], "small_fixed_set")
        self.assertIsNone(h.ui_role_prior("layer 7"))

    def test_avatar_groups_are_placeholders_and_merge_flagged(self):
        d = Path(tempfile.mkdtemp())
        (d / "crops").mkdir()
        recs = []
        for lid, color in ((1, (10, 10, 10, 255)), (2, (200, 200, 0, 255))):
            Image.new("RGBA", (16, 16), color).save(d / "crops" / f"{lid}.png")
            recs.append({"layer_id": lid, "name": f"avatar {lid}", "path": "row/avatar", "kind": "smartobject",
                         "crop": f"crops/{lid}.png", "smart_object_id": f"so{lid}"})
        Image.new("RGBA", (16, 16), (1, 1, 1, 255)).save(d / "crops" / "3.png")  # opaque centre: merged
        recs.append({"layer_id": 3, "name": "avatar frame", "path": "row/avatar frame", "kind": "pixel",
                     "crop": "crops/3.png"})
        ring = Image.new("RGBA", (16, 16), (1, 1, 1, 255))
        ring.paste((0, 0, 0, 0), (3, 3, 13, 13))  # see-through centre: frame only
        ring.save(d / "crops" / "4.png")
        recs.append({"layer_id": 4, "name": "avatar frame b", "path": "x/avatar frame b", "kind": "pixel",
                     "crop": "crops/4.png"})
        out = h.compute_reuse_candidates(recs, d, d / "rc")
        g = [g for g in out["groups"] if 1 in g["layer_ids"]][0]
        self.assertEqual(g["hint"], "dynamic_placeholder")
        self.assertEqual(g["evidence"]["ui_role_prior"]["role"], "avatar")
        suspects = {s["layer_id"] for s in out["avatar_merge_suspects"]}
        self.assertEqual(suspects, {1, 2, 3})


def _manifest(rows):
    parts = []
    for i, (lid, reuse) in enumerate(rows, start=1):
        p = {"name": f"main_bg_row{i}", "role": "base", "kind": "image", "layer_id": lid, "hidden": False}
        if reuse:
            p["reuse_of"] = "main_bg_row"
        parts.append(p)
    return {
        "schema": h.SCHEMA_MANIFEST, "status": "confirmed",
        "source": {"artboard": {"id": 1, "name": "<artboard>", "bounds": [0, 0, 60, 60], "size": [60, 60]}},
        "components": [{"id": "main_list", "type": "list", "shared": parts}],
        "reuse_decisions": [{"id": "rd1", "instances": ["main_bg_row3"], "decision": None}],
    }


class TestReuseFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.slices = self.tmp / "slices"
        self.slices.mkdir()

    def test_flatten_assigns_one_source(self):
        m = _manifest([(11, True), (12, True), (13, False)])
        entries = {e["name"]: e for e in h.flatten_components(m)}
        self.assertEqual(entries["main_bg_row1"]["image_name"], "main_bg_row")
        self.assertTrue(entries["main_bg_row1"]["export_png"])
        self.assertFalse(entries["main_bg_row2"]["export_png"])
        self.assertEqual(entries["main_bg_row2"]["reuse_source"], "main_bg_row1")
        self.assertEqual(entries["main_bg_row3"]["image_name"], "main_bg_row3")
        self.assertTrue(entries["main_bg_row3"]["export_png"])

    def _run(self, row2_color):
        m = _manifest([(11, True), (12, True)])
        h.flatten_components(m)
        mpath = self.tmp / "manifest.json"
        mpath.write_text(json.dumps(m), encoding="utf-8")
        _row_img().save(self.slices / "main_bg_row.png")
        log = {"ok": True, "items": [
            {"name": "main_bg_row1", "ok": True, "out_size": [30, 10], "visible_bounds_from_trim_insets": True,
             "visible_bounds": _box(5, 5, 35, 15), "logical_bounds": _box(5, 5, 35, 15)},
            {"name": "main_bg_row2", "ok": True, "bounds_only": True,
             "visible_bounds": _box(4, 24, 36, 36), "logical_bounds": _box(5, 25, 35, 35)},
        ]}
        lpath = self.tmp / "export_log.json"
        lpath.write_text(json.dumps(log), encoding="utf-8")
        out = self.tmp / "layout.json"
        h.cmd_build_layout(SimpleNamespace(manifest=str(mpath), export_log=str(lpath), slices_dir=str(self.slices),
                                           out=str(out), group_effects=None))
        layout = json.loads(out.read_text(encoding="utf-8"))
        ref = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
        ref.paste(_row_img(), (5, 5))
        ref.paste(_row_img(row2_color), (5, 25))
        rpath = self.tmp / "full_render.png"
        ref.save(rpath)
        vdir = self.tmp / "validate"
        rc = h.cmd_validate(SimpleNamespace(layout=str(out), slices_dir=str(self.slices), full_render=str(rpath),
                                            out_dir=str(vdir), fonts_dir=None, group_effects=None,
                                            reuse_tol_mae=4.0, reuse_tol_pct=1.0))
        report = json.loads((vdir / "validate_report.json").read_text(encoding="utf-8"))
        return layout, rc, report

    def test_build_layout_places_instances(self):
        layout, rc, report = self._run((40, 120, 200, 255))
        nodes = {n["name"]: n for n in layout["nodes"]}
        self.assertEqual(nodes["main_bg_row1"]["image"], "main_bg_row.png")
        self.assertEqual(nodes["main_bg_row2"]["image"], "main_bg_row.png")
        self.assertEqual(nodes["main_bg_row2"]["visible_bounds"], _box(5, 25, 35, 35))
        self.assertEqual(nodes["main_bg_row2"]["reuse_of"], "main_bg_row")
        self.assertEqual(rc, 0, report["issues"])
        self.assertEqual(len(report["reuse_checked"]), 2)
        self.assertEqual(len(layout["reuse_decisions"]), 1)

    def test_validate_flags_reuse_mismatch(self):
        layout, rc, report = self._run((200, 60, 40, 255))
        bad = [i for i in report["issues"] if i["type"] == "reuse_mismatch"]
        self.assertEqual([b["name"] for b in bad], ["main_bg_row2"])
        self.assertTrue(Path(bad[0]["compare_image"]).exists())
        self.assertEqual(rc, 1)

    def test_preview_entries_carry_reuse(self):
        entries = h.layout_nodes_as_preview_entries([
            {"name": "a", "kind": "image", "visible_bounds": _box(0, 0, 2, 2), "reuse_of": "shared"}])
        self.assertEqual(entries[0]["reuse_of"], "shared")


if __name__ == "__main__":
    unittest.main(verbosity=2)
