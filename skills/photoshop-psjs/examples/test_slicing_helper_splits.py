#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for 1.4.4: avatar/frame split (try first, ask after) and frame-like elements split by layer.

    python test_slicing_helper_splits.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slicing_helper as h  # noqa: E402


def _rec(lid, name, kind="pixel", clipping=False, visible=True, parents=(), clip_base=None, is_group=False):
    return {"id": lid, "name": name, "kind": kind, "clipping": clipping, "visible": visible,
            "parents": list(parents), "clip_base": clip_base, "is_group": is_group, "bbox": [0, 0, 10, 10]}


class TestClassifyInner(unittest.TestCase):
    def test_clipped_photo_is_avatar_rest_is_frame(self):
        recs = [
            _rec(1, "group", kind="group", is_group=True),
            _rec(2, "rect", kind="shape", parents=[1]),
            _rec(3, "photo", kind="pixel", clipping=True, parents=[1], clip_base=2),
            _rec(4, "ring", kind="shape", parents=[1]),
            _rec(5, "old photo", kind="pixel", clipping=True, visible=False, parents=[1], clip_base=2),
        ]
        cls = h.classify_inner_layers(recs)
        self.assertEqual({x["id"] for x in cls["avatar"]}, {2, 3})
        self.assertEqual({x["id"] for x in cls["frame"]}, {4})

    def test_hidden_group_leaves_ignored_and_nothing_separable(self):
        recs = [_rec(1, "g", kind="group", is_group=True, visible=False), _rec(2, "photo", parents=[1]),
                _rec(3, "ring", kind="shape")]
        cls = h.classify_inner_layers(recs)
        self.assertIsNone(cls["avatar"])

    def test_frame_blend_risks(self):
        recs = [
            _rec(1, "group", kind="group", is_group=True),
            _rec(2, "rect", kind="shape", parents=[1]),
            _rec(3, "photo", kind="pixel", clipping=True, parents=[1], clip_base=2),
            _rec(4, "ring", kind="shape", parents=[1]),
            _rec(5, "rim shadow", kind="shape", parents=[1]),
        ]
        for r in recs:
            r.update(blend_mode="normal", fill_opacity=255, effects=[])
        cls = h.classify_inner_layers(recs)
        self.assertEqual(h.frame_blend_risks(recs, cls), [])
        recs[4].update(blend_mode="linear_burn", fill_opacity=0,
                       effects=[{"type": "InnerShadow", "blend_mode": "multiply"}])
        recs[0]["effects"] = [{"type": "InnerShadow", "blend_mode": "multiply"}]
        risks = {r["id"]: r["why"] for r in h.frame_blend_risks(recs, h.classify_inner_layers(recs))}
        self.assertEqual(set(risks), {1, 5})
        self.assertTrue(any("multiply" in w for w in risks[5]))

    def test_inner_records_from_psd_like_tree(self):
        class L:
            def __init__(self, lid, name, kind, clipping=False, children=None):
                self.layer_id, self.name, self.kind, self.clipping = lid, name, kind, clipping
                self.visible, self.bbox, self._c = True, (0, 0, 4, 4), children
                self.effects, self.blend_mode = [], "BlendMode.NORMAL"

            def is_group(self):
                return self._c is not None

            def __iter__(self):
                return iter(self._c or [])
        root = [L(1, "g", "group", children=[L(2, "rect", "shape"), L(3, "photo", "pixel", clipping=True), L(4, "ring", "shape")])]
        recs = h._inner_layer_records(root)
        by = {r["id"]: r for r in recs}
        self.assertEqual(by[3]["clip_base"], 2)
        self.assertEqual(by[3]["parents"], [1])
        self.assertIsNone(by[4]["clip_base"])


class TestGeometryAndMatch(unittest.TestCase):
    def test_affine_from_quad_scale_and_offset(self):
        (a, b, c, d, e, f), err = h._affine_from_quad(200, 200, [58, 58, 183, 58, 183, 183, 58, 183])
        self.assertAlmostEqual(a, 0.625); self.assertAlmostEqual(e, 0.625)
        self.assertAlmostEqual(c, 58); self.assertAlmostEqual(f, 58)
        self.assertAlmostEqual(err, 0.0)
        _, err2 = h._affine_from_quad(200, 200, [0, 0, 200, 0, 220, 230, 0, 200])
        self.assertGreater(err2, 1.0)

    def test_map_inner_to_document(self):
        im = Image.new("RGBA", (200, 200), (255, 0, 0, 255))
        out, err = h.map_inner_to_document(im, [58, 58, 183, 58, 183, 183, 58, 183], (58, 58, 183, 183))
        self.assertEqual(out.size, (125, 125))
        a = np.asarray(out)[..., 3]
        self.assertGreater(a[5:-5, 5:-5].min(), 250)

    def test_over_and_match(self):
        bottom = Image.new("RGBA", (20, 20), (0, 0, 255, 255))
        top = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
        ImageDraw.Draw(top).rectangle([0, 0, 19, 3], fill=(255, 200, 0, 255))
        rec = h.over(top, bottom)
        self.assertEqual(rec.getpixel((5, 1))[:3], (255, 200, 0))
        self.assertEqual(rec.getpixel((5, 10))[:3], (0, 0, 255))
        self.assertEqual(h.split_match(rec, rec)["psnr"], 99.0)

    def test_estimate_avatar_mask_from_frame_instance(self):
        frame = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        ImageDraw.Draw(frame).ellipse([0, 0, 39, 39], outline=(230, 180, 40, 255), width=4)
        merged = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        ImageDraw.Draw(merged).ellipse([4, 4, 35, 35], fill=(40, 90, 220, 255))
        merged = h.over(frame, merged)
        avatar, rec, match = h.estimate_avatar_mask(merged, frame)
        self.assertGreater(match["psnr"], h.SPLIT_MATCH_PSNR)
        bbox = avatar.getbbox()
        self.assertTrue(bbox[0] >= 3 and bbox[2] <= 37)


class TestAvatarSplitChecks(unittest.TestCase):
    def test_merged_default_and_no_attempts_flagged(self):
        d = [{"id": "rd", "type": "split_avatar", "instances": ["a"], "default": "accept_placeholder", "decision": None}]
        types = {i["type"] for i in h.check_avatar_splits([], d)}
        self.assertEqual(types, {"split_avatar_merged_default", "split_avatar_not_attempted"})

    def test_decision_after_attempts_is_clean(self):
        d = [{"id": "rd", "type": "split_avatar", "instances": ["a"], "default": "wait_for_user", "decision": None,
              "compare_image": "x.png", "attempts": [{"method": "smart_object_contents", "result": "no_separable_layers",
                                                      "reason": "single raster inside"}]}]
        self.assertEqual(h.check_avatar_splits([], d), [])

    def test_unsplit_suspect_flagged_split_one_clean(self):
        suspects = [{"layer_id": 10}]
        merged = [{"name": "m_img_avatar", "layer_id": 10, "select_ids": [10]}]
        self.assertEqual([i["type"] for i in h.check_avatar_splits(merged, [], suspects)], ["avatar_merged_unsplit"])
        # suspect inside an exported group is found through the tree
        tree = [{"layer_id": 5, "children": [{"layer_id": 10}]}]
        grp = [{"name": "m_card", "layer_id": 5, "select_ids": [5]}]
        self.assertEqual([i["type"] for i in h.check_avatar_splits(grp, [], suspects, tree)], ["avatar_merged_unsplit"])
        ok = [
            {"name": "m_img_avatar", "layer_id": 10, "select_ids": [10], "part_role": "avatar", "placeholder": True,
             "split_method": "smart_object_contents", "split": {"hide_inner": [{"id": 4}]}, "split_evidence": {"psnr": 40}},
            {"name": "m_img_avatar_frame", "layer_id": 10, "select_ids": [10], "part_role": "frame",
             "split_method": "smart_object_contents", "split": {"hide_inner": [{"id": 2}, {"id": 3}]}, "split_evidence": {"psnr": 40}},
        ]
        self.assertEqual(h.check_avatar_splits(ok, [], suspects), [])

    def test_incomplete_split_parts(self):
        bad = [{"name": "a", "part_role": "avatar", "split_method": "smart_object_contents"},
               {"name": "b", "split_method": "pixel_mask_estimate", "split_evidence": {"psnr": 30}},
               {"name": "c", "split_method": "magic", "split_evidence": {"x": 1}}]
        types = sorted(i["type"] for i in h.check_avatar_splits(bad, []))
        self.assertEqual(types, sorted(["split_evidence_missing", "split_avatar_not_placeholder", "split_spec_missing",
                                        "split_approximate_unmarked", "split_method_unknown"]))

    def test_flatten_carries_split_fields_and_reports(self):
        d = Path(tempfile.mkdtemp())
        part = {"name": "m_img_avatar", "role": "avatar", "layer_id": 10, "placeholder": True,
                "split_method": "smart_object_contents", "split": {"hide_inner": [{"id": 4, "name": "ring"}]},
                "split_evidence": {"psnr": 41.2}}
        manifest = {"schema": "psjs-slice-manifest/v2", "status": "pending_review",
                    "source": {"artboard": {"id": 1, "bounds": [0, 0, 100, 100], "size": [100, 100]}},
                    "components": [{"id": "row", "type": "list_item", "shared": [part]}],
                    "reuse_decisions": [{"id": "rd", "type": "split_avatar", "instances": ["x"], "default": "accept_placeholder"}]}
        (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (d / "reuse_candidates.json").write_text(json.dumps({"avatar_merge_suspects": [{"layer_id": 10}]}), encoding="utf-8")
        import io
        from contextlib import redirect_stdout, redirect_stderr
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(io.StringIO()):
            h.cmd_flatten(SimpleNamespace(manifest=str(d / "manifest.json"), out=None, group_effects=None))
        summary = json.loads(buf.getvalue())
        types = {i["type"] for i in summary["avatar_split_issues"]}
        self.assertIn("split_avatar_merged_default", types)
        self.assertNotIn("avatar_merged_unsplit", types)
        entry = json.loads((d / "manifest.json").read_text(encoding="utf-8"))["entries"][0]
        self.assertEqual(entry["split"]["hide_inner"][0]["id"], 4)
        self.assertTrue(entry["placeholder"])


def _leaf(lid, name, bbox, blend="normal"):
    return {"layer_id": lid, "name": name, "bbox": bbox, "blend_mode": blend, "visible": True, "children": []}


PANEL_TREE = [{"layer_id": 100, "name": "popup", "visible": True, "bbox": [0, 0, 970, 1042], "children": [
    _leaf(1, "frame", [0, 0, 970, 1042]),
    _leaf(2, "frame2", [21, 25, 951, 1008], "linear_burn"),
    _leaf(3, "inset", [86, 274, 886, 634]),
    _leaf(4, "shape 1", [277, 247, 695, 665], "color_burn"),
    _leaf(5, "title", [37, 38, 937, 238]),
]}]


class TestFrameSplit(unittest.TestCase):
    def test_classify_roles(self):
        roles = {c["layer_id"]: c["role"] for c in h.classify_frame_layers(PANEL_TREE[0]["children"])}
        self.assertEqual(roles, {1: "outer", 2: "outer", 3: "inset", 4: "decoration", 5: "header"})

    def test_baked_panel_flagged_with_suggestion(self):
        e = [{"name": "m_img_frame", "kind": "image", "layer_id": 1, "select_ids": [1, 2, 3, 4, 5], "nine_slice": None,
              "visible_bounds": {"left": 0, "top": 0, "right": 970, "bottom": 1042}}]
        iss = h.check_frame_bakes(e, PANEL_TREE)
        self.assertEqual([i["type"] for i in iss], ["frame_baked_whole"])
        roles = [p["role"] for p in iss[0]["suggested_parts"]]
        self.assertEqual(roles, ["frame", "header", "inset", "decoration"])
        deco = iss[0]["suggested_parts"][-1]
        self.assertEqual((deco["kind"], deco["select_ids"]), ("image", [4]))

    def test_bake_reason_small_or_split_parts_are_clean(self):
        base = {"name": "m_img_frame", "kind": "image", "select_ids": [1, 2, 3, 4, 5],
                "visible_bounds": {"left": 0, "top": 0, "right": 970, "bottom": 1042}}
        self.assertEqual(h.check_frame_bakes([dict(base, bake_reason="split recomposite 6 dB worse")], PANEL_TREE), [])
        self.assertEqual(h.check_frame_bakes([dict(base, name="m_icon_coin")], PANEL_TREE), [])
        parts = [{"name": "m_bg_frame", "kind": "nine_slice", "select_ids": [1, 2],
                  "nine_slice": {"left": 60, "top": 60, "right": 60, "bottom": 60}},
                 {"name": "m_img_deco", "kind": "image", "select_ids": [4]}]
        self.assertEqual(h.check_frame_bakes(parts, PANEL_TREE), [])

    def test_decoration_in_stretch_zone(self):
        e = [{"name": "m_bg_panel", "kind": "nine_slice", "select_ids": [1, 4],
              "nine_slice": {"left": 60, "top": 60, "right": 60, "bottom": 60}}]
        self.assertEqual([i["type"] for i in h.check_frame_bakes(e, PANEL_TREE)], ["decoration_in_stretch_zone"])

    def test_prepare_candidates(self):
        c = h.frame_split_candidates(PANEL_TREE)
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["layer_id"], 100)


if __name__ == "__main__":
    unittest.main()
