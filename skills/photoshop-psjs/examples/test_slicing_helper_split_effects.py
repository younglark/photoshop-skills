#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for 1.4.5: effects redrawn along the new inner edge after a split, blend overlays, split-fix.

    python test_slicing_helper_split_effects.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slicing_helper as h  # noqa: E402

W = 80


def _ring(outer=(10, 10, 70, 70), inner=(20, 20, 60, 60), color=(40, 180, 210, 255)):
    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle(outer, fill=color)
    d.rectangle(inner, fill=(0, 0, 0, 0))
    return im


def _content():
    a = np.zeros((W, W, 4), np.uint8)
    yy, xx = np.mgrid[0:W, 0:W]
    a[..., 0] = 120 + (xx % 30) * 3
    a[..., 1] = 60 + (yy % 20) * 4
    a[..., 2] = 40
    a[..., 3] = 0
    a[18:63, 18:63, 3] = 255  # content footprint, slightly under the ring
    return Image.fromarray(a, "RGBA")


def _shadow_of(alpha_img, dy=4, op=0.6, blur=1.5):
    al = alpha_img.getchannel("A")
    sh = Image.new("L", al.size, 0)
    sh.paste(al, (0, dy))
    sh = sh.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * op))
    out = Image.new("RGBA", al.size, (0, 0, 0, 0))
    out.putalpha(sh)
    return out


def _rim_overlay():
    """An effect-only overlay along the inner edge (inside the hole), meant for linear burn."""
    a = np.zeros((W, W, 4), np.uint8)
    a[30:61, 20:24] = (170, 120, 90, 200)  # left inner edge only (the top edge is left to the shadow)
    return Image.fromarray(a, "RGBA")


def _design():
    """Photoshop-like merged render: owner shadow cast from the solid silhouette, content, ring, burn overlay."""
    content, ring, rim = _content(), _ring(), _rim_overlay()
    silhouette = Image.alpha_composite(content, ring)
    merged = Image.alpha_composite(_shadow_of(silhouette), content)
    merged = Image.alpha_composite(merged, ring)
    merged = h.blend_arrays_img(merged, rim, "linear_burn")
    # naive split as exported: the shadow now comes from the ring alone (falls into the hole) and the
    # burn overlay is blended with transparency (normal)
    frame_naive = Image.alpha_composite(Image.alpha_composite(_shadow_of(ring), ring), rim)
    return merged, content, ring, rim, frame_naive


class TestHoleAndRisks(unittest.TestCase):
    def test_hole_mask(self):
        _, frac = h.alpha_hole_mask(_ring())
        self.assertGreater(frac, 0.3)
        solid = Image.new("RGBA", (W, W), (0, 0, 0, 0))
        ImageDraw.Draw(solid).rectangle((10, 10, 70, 70), fill=(1, 2, 3, 255))
        self.assertEqual(h.alpha_hole_mask(solid)[1], 0.0)
        open_ring = _ring()
        ImageDraw.Draw(open_ring).rectangle((35, 60, 45, 70), fill=(0, 0, 0, 0))  # gap to the outside
        self.assertEqual(h.alpha_hole_mask(open_ring)[1], 0.0)

    def test_outer_effect_risks_only_for_rings_and_edge_effects(self):
        fx = [{"type": "DropShadow", "blend_mode": "normal"}, {"type": "ColorOverlay", "blend_mode": "normal"}]
        risks = h.outer_effect_risks(fx, _ring())
        self.assertEqual([r["type"] for r in risks], ["DropShadow"])
        self.assertIn("hole", risks[0]["mechanism"])
        solid = Image.new("RGBA", (W, W), (0, 0, 0, 255))
        self.assertEqual(h.outer_effect_risks(fx, solid), [])
        self.assertEqual(h.outer_effect_risks([{"type": "ColorOverlay"}], _ring()), [])
        self.assertEqual(len(h.outer_effect_risks(["Stroke"], None)), 1)

    def _recs(self):
        def r(lid, name, kind="shape", parents=(1,), **kw):
            d = {"id": lid, "name": name, "kind": kind, "visible": True, "clipping": False, "is_group": False,
                 "bbox": [0, 0, 10, 10], "parents": list(parents), "clip_base": None, "blend_mode": "normal",
                 "fill_opacity": 255, "effects": []}
            d.update(kw)
            return d
        return [
            r(1, "group", kind="group", parents=(), is_group=True,
              effects=[{"type": "InnerShadow", "blend_mode": "lighten"}]),
            r(2, "base"), r(3, "photo", kind="pixel", clipping=True, clip_base=2),
            r(4, "ring"),
            r(5, "burn", blend_mode="linear_burn", fill_opacity=0, effects=[{"type": "InnerShadow", "blend_mode": "multiply"}]),
            r(6, "glow", fill_opacity=0, effects=[{"type": "InnerGlow", "blend_mode": "normal"}]),
            r(7, "dodge", blend_mode="color_dodge"),
            r(8, "levels", kind="levels"),
        ]

    def test_inner_edge_effect_risks_and_fix_suggestion(self):
        recs = self._recs()
        cls = h.classify_inner_layers(recs)
        risks = {x["id"]: x for x in h.inner_edge_effect_risks(recs, cls)}
        self.assertEqual(risks[1]["fix"], "frame_from_merged")
        self.assertEqual(risks[5]["fix"], "overlay_part")
        self.assertIn("degrades to normal", risks[5]["mechanism"])
        self.assertEqual(risks[6]["fix"], "overlay_part")
        self.assertEqual(risks[7]["fix"], "needs_user")
        self.assertEqual(risks[8]["fix"], "needs_user")
        self.assertNotIn(4, risks)
        rec = {"method": "smart_object_contents", "edge_effect_risks": list(risks.values()),
               "outer_effect_risks": h.outer_effect_risks(["DropShadow"], _ring())}
        fix = h.effect_fix_suggestion(rec)
        self.assertEqual(fix["parts"][0]["split_fix"], "two_part_approx")
        self.assertEqual(len(fix["parts"]), 1)
        burn = [p for p in fix["optional_overlays"] if p.get("blend_mode") == "linear_burn"][0]
        self.assertIn("reverse-subtract", burn["engine"])
        self.assertEqual({x["id"] for x in fix["needs_user"]}, {7, 8})
        self.assertIn("frame_plain", fix["photoshop_exports"])
        self.assertIsNone(h.effect_fix_suggestion({"outer_effect_risks": [], "edge_effect_risks": []}))


class TestBlend(unittest.TestCase):
    def test_formulas_on_opaque_backdrop(self):
        dst = np.full((1, 1, 4), 255.0); dst[..., :3] = [200, 100, 50]
        src = np.array([[[128, 255, 0, 255]]], float)
        mul = h.blend_arrays(dst, src, "multiply")[0, 0, :3]
        np.testing.assert_allclose(mul, [200 * 128 / 255, 100, 0], atol=0.01)
        burn = h.blend_arrays(dst, src, "linear_burn")[0, 0, :3]
        np.testing.assert_allclose(burn, [200 + 128 - 255, 100, 0], atol=0.01)
        half = src.copy(); half[..., 3] = 127.5
        burn_half = h.blend_arrays(dst, half, "linear_burn")[0, 0, :3]
        # reverse-subtract form: dst - a * (1 - src)
        np.testing.assert_allclose(burn_half, [200 - 0.5 * 127, 100, 50 - 0.5 * 50], atol=0.01)
        scr = h.blend_arrays(dst, src, "screen")[0, 0, :3]
        np.testing.assert_allclose(scr, [255 - (55 * 127 / 255), 255, 50], atol=0.01)
        with self.assertRaises(ValueError):
            h.blend_arrays(dst, src, "color_dodge")

    def test_normal_equals_alpha_composite(self):
        a, b = _content(), _ring(color=(10, 200, 30, 140))
        ref = h.premultiplied(Image.alpha_composite(a, b))
        got = h.premultiplied(h.blend_arrays_img(a, b, "normal"))
        self.assertLessEqual(float(np.abs(ref - got).max()), 1.0)

    def test_blend_onto_offset(self):
        canvas = Image.new("RGBA", (10, 10), (200, 200, 200, 255))
        im = Image.new("RGBA", (4, 4), (128, 128, 128, 255))
        out = np.asarray(h.blend_onto(canvas, im, (3, 3), "multiply"))
        self.assertEqual(tuple(out[4, 4, :3]), (100, 100, 100))
        self.assertEqual(tuple(out[0, 0, :3]), (200, 200, 200))


class TestComposeEffectFix(unittest.TestCase):
    def test_naive_split_dark_rim_and_fix(self):
        merged, content, ring, rim, frame_naive = _design()
        naive = Image.alpha_composite(content, frame_naive)
        naive_db = h.split_match(naive, merged)["psnr"]
        self.assertLess(naive_db, 30)
        # the naive rim (just inside the hole, top edge) is darker than the design
        lum = lambda im: np.asarray(im).astype(float)[20:25, 30:50, :3].mean()
        self.assertLess(lum(naive), lum(merged) - 10)
        res = h.compose_effect_fix(merged, content, ring, [(rim, "linear_burn")])
        self.assertGreaterEqual(res["match"]["psnr"], 40)
        normal = h.compose_effect_fix(merged, content, ring, [(rim, "normal")])
        self.assertLess(normal["match"]["psnr"], res["match"]["psnr"])
        # the frame is content-independent: no content pixels inside the content zone
        fa = np.asarray(res["frame"])[..., 3]
        self.assertEqual(int(fa[30:50, 30:50].max()), 0)

    def test_cmd_split_fix(self):
        merged, content, ring, rim = _two_part_fixture()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            for n, im in (("m", merged), ("c", content), ("f", ring), ("o", rim)):
                im.save(td / f"{n}.png")
            # offset form for the overlay: crop it to its bbox and give #x,y
            bb = rim.getbbox(); rim.crop(bb).save(td / "o_crop.png")
            args = SimpleNamespace(merged=str(td / "m.png"), content=str(td / "c.png"), frame=str(td / "f.png"),
                                   overlay=[f"{td / 'o_crop.png'}#{bb[0]},{bb[1]}"], overlay_blend=["linear_burn"],
                                   canvas=f"{W},{W}", threshold=40.0, out_dir=str(td / "out"),
                                   emit_overlays=True, swap_content=None)
            self.assertEqual(h.cmd_split_fix(args), 0)
            rep = json.loads((td / "out" / "split_fix.json").read_text(encoding="utf-8"))
            self.assertTrue(rep["pass"])
            self.assertEqual(rep["mode"], "emit_overlays")
            self.assertEqual(rep["parts"][1]["blend_mode"], "linear_burn")
            self.assertTrue((td / "out" / "compare.png").exists())
            args.overlay_blend = ["color_dodge"]
            with self.assertRaises(SystemExit):
                h.cmd_split_fix(args)


class TestCheckSplitEffects(unittest.TestCase):
    TREE = {"layer_id": 100, "is_group": True, "children": [
        {"layer_id": 10, "name": "avatar so", "effects": ["DropShadow", "DropShadow"]},
        {"layer_id": 20, "name": "card", "is_group": True, "effects": ["OuterGlow"], "children": [
            {"layer_id": 21, "name": "photo"}, {"layer_id": 22, "name": "ring"}]},
    ]}

    def test_smart_object_owner(self):
        e = {"name": "a_frame", "role": "frame", "split_method": "smart_object_contents", "layer_id": 10,
             "select_ids": [10], "split": {"hide_inner": [1]}}
        iss = h.check_split_effects([e], self.TREE)
        self.assertEqual([i["type"] for i in iss], ["outer_effect_into_hole"])
        self.assertEqual(iss[0]["owner_layer_id"], 10)
        self.assertEqual(h.check_split_effects([dict(e, split_fix="frame_from_merged")], self.TREE), [])
        self.assertEqual(h.check_split_effects([dict(e, split_fix="two_part_approx")], self.TREE), [])
        # 1.4.9: a prose effects_checked note no longer counts; ownership must be recorded
        self.assertEqual([i["type"] for i in h.check_split_effects([dict(e, split_evidence={"effects_checked": True})], self.TREE)],
                         ["outer_effect_into_hole"])
        self.assertEqual(h.check_split_effects([dict(e, split={"hide_inner": [1], "outer_effects": "frame"})], self.TREE), [])

    def test_common_group_owner_and_blend_modes(self):
        fr = {"name": "c_frame", "part_role": "frame", "split_method": "mask_or_clip_layers", "layer_id": 22,
              "select_ids": [22], "component_id": "c"}
        av = {"name": "c_avatar", "part_role": "avatar", "split_method": "mask_or_clip_layers", "layer_id": 21,
              "select_ids": [21], "component_id": "c", "placeholder": True}
        iss = h.check_split_effects([fr, av], self.TREE)
        self.assertEqual([(i["type"], i["owner_layer_id"]) for i in iss], [("outer_effect_into_hole", 20)])
        ov = [{"name": "x", "blend_mode": "color_dodge"}, {"name": "y", "blend_mode": "linear_burn"},
              {"name": "z", "blend_mode": "pin_light"}, {"name": "w", "blend_mode": "weird_mode"}]
        types = [i["type"] for i in h.check_split_effects(ov, None)]
        self.assertEqual(types, ["blend_mode_content_dependent", "blend_mode_content_dependent", "blend_mode_not_replayable"])

    def test_recomposite_uses_blend_mode(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            Image.new("RGBA", (4, 4), (200, 200, 200, 255)).save(td / "base.png")
            Image.new("RGBA", (4, 4), (128, 128, 128, 255)).save(td / "mul.png")
            vb = {"left": 0, "top": 0, "right": 4, "bottom": 4}
            layout = {"nodes": [{"name": "base", "kind": "image", "image": "base.png", "visible_bounds": vb},
                                {"name": "mul", "kind": "image", "image": "mul.png", "visible_bounds": vb, "blend_mode": "multiply"}]}
            canvas, placed = h.recomposite(layout, td, (4, 4))
            self.assertEqual(tuple(np.asarray(canvas)[1, 1, :3]), (100, 100, 100))
            self.assertEqual(placed[1].get("blend_mode"), "multiply")



def _two_part_fixture():
    """A ring + a soft inner-edge linear-burn, closer to a real frame than the solid bar in _design()."""
    content, ring = _content(), _ring()
    rim = Image.fromarray(np.zeros((W, W, 4), np.uint8), "RGBA")
    a = np.zeros((W, W, 4), np.uint8)
    a[30:61, 20:23] = (170, 120, 90, 140)
    a[30:61, 23] = (170, 120, 90, 70)
    rim = Image.fromarray(a, "RGBA")
    silhouette = Image.alpha_composite(content, ring)
    merged = Image.alpha_composite(_shadow_of(silhouette), content)
    merged = Image.alpha_composite(merged, ring)
    merged = h.blend_arrays_img(merged, rim, "linear_burn")
    return merged, content, ring, rim


class TestTwoPartApprox(unittest.TestCase):
    def test_ls_beats_normal_bake_on_swap(self):
        merged, content, ring, rim = _two_part_fixture()
        extras = [("solid_white", Image.new("RGBA", (W, W), (255, 255, 255, 255))),
                  ("solid_black", Image.new("RGBA", (W, W), (0, 0, 0, 255))),
                  ("solid_red", Image.new("RGBA", (W, W), (200, 40, 40, 255)))]
        # force extras to share the content footprint
        ca = np.asarray(content)[..., 3]
        def masked(im):
            a = np.asarray(im.convert("RGBA")).copy()
            a[ca == 0] = 0
            a[..., 3] = ca
            return Image.fromarray(a, "RGBA")
        extras = [(n, masked(im)) for n, im in extras]
        res = h.compose_two_part_frame(merged, content, ring, [(rim, "linear_burn")], extras)
        self.assertGreaterEqual(res["match"]["psnr"], 35)
        self.assertGreaterEqual(res["swap_min_psnr"], 35)
        self.assertTrue(res["pass"])
        self.assertTrue(res["simulated_swap"])
        # baking the burn as normal into the frame is worse on a dark/bright swap
        zone = h.content_show_through_zone(content, ring)
        frame_m = h.frame_pixels_from_merged(merged, content, ring)
        baked = h.blend_arrays(frame_m, np.asarray(rim) * zone[..., None], "normal")
        # placeholder may be ok-ish, but a black content should drift
        black = masked(Image.new("RGBA", (W, W), (0, 0, 0, 255)))
        sim_black = h.simulate_overlay_stack(black, frame_m, [(rim, "linear_burn")], zone)
        rec_ls = h.blend_arrays(np.asarray(black, float), np.asarray(res["frame"], float), "normal")
        rec_n = h.blend_arrays(np.asarray(black, float), baked, "normal")
        db_ls = h.split_match(h._to_rgba_img(rec_ls), h._to_rgba_img(sim_black))["psnr"]
        db_n = h.split_match(h._to_rgba_img(rec_n), h._to_rgba_img(sim_black))["psnr"]
        self.assertGreater(db_ls, db_n)
        self.assertGreaterEqual(db_ls, 35)

    def test_cmd_split_fix_default_is_two_parts(self):
        merged, content, ring, rim = _two_part_fixture()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            for n, im in (("m", merged), ("c", content), ("f", ring), ("o", rim)):
                im.save(td / f"{n}.png")
            alt = content.copy()
            # a brighter stand-in for a swapped avatar
            a = np.asarray(alt)
            a = np.clip(a.astype(int) + 40, 0, 255).astype(np.uint8)
            Image.fromarray(a, "RGBA").save(td / "swap.png")
            args = SimpleNamespace(merged=str(td / "m.png"), content=str(td / "c.png"), frame=str(td / "f.png"),
                                   overlay=[str(td / "o.png")], overlay_blend=["linear_burn"],
                                   canvas=None, threshold=None, out_dir=str(td / "out"),
                                   emit_overlays=False, swap_content=[str(td / "swap.png")])
            self.assertEqual(h.cmd_split_fix(args), 0)
            self.assertTrue((td / "out" / "frame.png").exists())
            self.assertFalse((td / "out" / "overlay1_linear_burn.png").exists())
            rep = json.loads((td / "out" / "split_fix.json").read_text(encoding="utf-8"))
            self.assertEqual(rep["mode"], "two_part_approx")
            self.assertEqual(len(rep["parts"]), 1)
            self.assertEqual(rep["parts"][0]["blend_mode"], "normal")
            self.assertTrue(rep["simulated_swap"])
            self.assertGreaterEqual(rep["match"]["psnr"], 35)
            self.assertGreaterEqual(rep["swap_min_psnr"], 35)

    def test_extra_avatar_rounded_corners_do_not_darken_interior(self):
        """An external avatar with transparent rounded corners must not leak black RGB into the footprint."""
        # Circular placeholder, typical of a masked avatar slot.
        ph = Image.new("RGBA", (W, W), (0, 0, 0, 0))
        ImageDraw.Draw(ph).ellipse((16, 16, 63, 63), fill=(80, 90, 100, 255))
        fill = (220, 180, 140, 255)
        # Different size: a bright circle that does not fill its canvas. Transparent
        # corners (and the padded margin) store RGB black — the common PNG layout
        # that used to stain the placeholder rim when the whole image was stretched.
        src_s = 128
        avatar = Image.new("RGBA", (src_s, src_s), (0, 0, 0, 0))
        ImageDraw.Draw(avatar).ellipse((16, 16, src_s - 17, src_s - 17), fill=fill)
        out = h.representative_contents(ph, [("swap_round", avatar)])["swap_round"]
        interior = np.asarray(ph)[..., 3] > 0
        lum = 0.2126 * out[..., 0] + 0.7152 * out[..., 1] + 0.0722 * out[..., 2]
        # 2-px morphological erosion to split a rim band from the disk core.
        def erode(mask, n):
            m = mask.copy()
            for _ in range(n):
                m = m & np.roll(m, 1, 0) & np.roll(m, -1, 0) & np.roll(m, 1, 1) & np.roll(m, -1, 1)
            return m
        core, rim = erode(interior, 6), interior & ~erode(interior, 2)
        self.assertGreater(int(core.sum()), 50)
        self.assertGreater(int(rim.sum()), 20)
        core_med = float(np.median(lum[core]))
        # The fill luminance is ~186; a leaked black rim would drop this toward 0.
        self.assertGreaterEqual(core_med, 150)
        self.assertGreaterEqual(float(lum[rim].min()), core_med - 15)
        self.assertGreaterEqual(float(lum[interior].min()), 100)

        # Same-size source whose transparent corners fall inside a larger placeholder.
        same = Image.new("RGBA", (W, W), (0, 0, 0, 0))
        ImageDraw.Draw(same).ellipse((22, 22, 57, 57), fill=fill)
        out2 = h.representative_contents(ph, [("swap_same", same)])["swap_same"]
        lum2 = 0.2126 * out2[..., 0] + 0.7152 * out2[..., 1] + 0.0722 * out2[..., 2]
        self.assertGreaterEqual(float(np.median(lum2[core])), 150)
        self.assertGreaterEqual(float(lum2[rim].min()), float(np.median(lum2[core])) - 15)
        self.assertGreaterEqual(float(lum2[interior].min()), 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
