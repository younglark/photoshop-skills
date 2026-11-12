#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for 1.4.10: two-part split fitted on solid-content probe renders, a stable anti-aliased hole
edge, and the content check (synthetic data; "Photoshop renders" are produced with blend formulas).

    python test_slicing_helper_split_probes.py
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

W = 96
PROBES = [(0, 0, 0), (255, 255, 255), (128, 128, 128), (220, 40, 40), (40, 180, 60), (30, 80, 200)]


def _aa_ring(color=(40, 180, 210)):
    """Rounded ring with an anti-aliased inner edge (drawn at 4x, downsampled)."""
    s = 4
    im = Image.new("L", (W * s, W * s), 0)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((12 * s, 12 * s, 84 * s, 84 * s), radius=14 * s, fill=255)
    d.rounded_rectangle((22 * s, 22 * s, 74 * s, 74 * s), radius=9 * s, fill=0)
    a = np.asarray(im.resize((W, W), Image.LANCZOS)).astype(float)
    out = np.zeros((W, W, 4))
    out[..., :3] = color
    out[..., 3] = a
    return out


def _content_alpha():
    a = np.zeros((W, W))
    a[18:78, 18:78] = 255  # runs under the ring
    return a


def _photo():
    yy, xx = np.mgrid[0:W, 0:W]
    c = np.zeros((W, W, 4))
    c[..., 0] = 120 + (xx % 30) * 3
    c[..., 1] = 60 + (yy % 20) * 4
    c[..., 2] = 40 + (xx * yy) % 50
    c[..., 3] = _content_alpha()
    return c


def _solid(rgb):
    c = np.zeros((W, W, 4))
    c[..., :3] = rgb
    c[..., 3] = _content_alpha()
    return c


def _hole_effects(ring):
    """Frame-owned effects that fall on the content: a black inner glow (normal) and a teal
    multiply inner shadow along the top inner edge (content-dependent blend)."""
    hole = (ring[..., 3] < 255) & (_content_alpha() > 0)
    dist = np.asarray(Image.fromarray((hole * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(5))).astype(float)
    glow = np.zeros((W, W, 4))
    glow[..., 3] = np.where(hole, (255 - dist) * 0.5, 0)
    shadow = np.zeros((W, W, 4))
    shadow[..., :3] = (78, 171, 205)
    band = np.zeros((W, W))
    band[22:27, 22:74] = np.linspace(200, 40, 5)[:, None]
    shadow[..., 3] = band * hole
    return glow, shadow


def _render(content):
    """Stand-in for the Photoshop merged render of one content."""
    ring = _aa_ring()
    glow, shadow = _hole_effects(ring)
    out = h.blend_arrays(content, ring, "normal")
    out = h.blend_arrays(out, glow, "normal")
    out = h.blend_arrays(out, shadow, "multiply")
    return out


def _img(a):
    return h._to_rgba_img(a)


class TestProbeFit(unittest.TestCase):
    def setUp(self):
        self.photo = _photo()
        self.merged = _img(_render(self.photo))
        self.content = _img(self.photo)
        self.probes = [(rgb, _img(_render(_solid(rgb)))) for rgb in PROBES]

    def test_probe_fit_matches_placeholder_and_held_out_swaps(self):
        res = h.compose_two_part_frame_from_probes(self.merged, self.content, self.probes)
        self.assertGreaterEqual(res["match"]["psnr"], 35)
        self.assertIsNotNone(res["swap_min_psnr"])
        self.assertGreaterEqual(res["swap_min_psnr"], 35)
        self.assertTrue(res["swap_is_real_render"])
        self.assertFalse(res["simulated_swap"])
        self.assertEqual(len(res["swap_loo"]), len(PROBES))
        self.assertTrue(res["pass"])
        self.assertEqual(res["zone_partial_content_px"], 0)

    def test_probe_fit_beats_per_pixel_simulation_on_a_real_swap(self):
        """The simulated path fits the placeholder too; checked on a held-out real render it is worse."""
        ring = _aa_ring()
        glow, shadow = _hole_effects(ring)
        res_sim = h.compose_two_part_frame(self.merged, self.content, _img(ring),
                                           [(_img(glow), "normal"), (_img(shadow), "multiply")])
        res_pr = h.compose_two_part_frame_from_probes(self.merged, self.content, self.probes[:5])
        other = _photo()
        other[..., :3] = 255 - other[..., :3]
        truth = _img(_render(other))
        db = {}
        for k, r in (("sim", res_sim), ("probe", res_pr)):
            got = _img(h.blend_arrays(other, np.asarray(r["frame"], float), "normal"))
            db[k] = h.split_match(got, truth)["psnr"]
        self.assertGreaterEqual(db["probe"], 35)
        self.assertGreaterEqual(db["probe"], db["sim"] - 0.5)

    def test_fewer_than_three_probes_has_no_swap_check(self):
        res = h.compose_two_part_frame_from_probes(self.merged, self.content, self.probes[:2])
        self.assertIsNone(res["swap_min_psnr"])
        self.assertFalse(res["pass"])

    def test_probe_canvas_mismatch_raises(self):
        small = Image.new("RGBA", (10, 10))
        with self.assertRaises(ValueError):
            h.compose_two_part_frame_from_probes(self.merged, self.content, [((0, 0, 0), small), ((255, 255, 255), small)])

    def test_aa_edge_is_smooth(self):
        """Low-alpha edge pixels get a stable colour: close to the true premultiplied frame."""
        res = h.compose_two_part_frame_from_probes(self.merged, self.content, self.probes)
        f = np.asarray(res["frame"], float)
        # true src-over layer of ring+glow over black/white differs only by the multiply shadow; compare the ring edge
        ring = _aa_ring()
        edge = (ring[..., 3] > 0) & (ring[..., 3] < 255) & (_content_alpha() > 0)
        edge[22:27] = False  # skip the multiply band (content-dependent)
        pm = lambda a: a[..., :3] * a[..., 3:4] / 255.0
        glow, _ = _hole_effects(ring)
        truth = h.blend_arrays(ring, glow, "normal")
        err = np.abs(pm(f) - pm(truth))[edge].max()
        self.assertLess(err, 3.0)
        self.assertIsNotNone(res["aa_band_noise"])


class TestContentCheck(unittest.TestCase):
    def test_rim_in_clear_area_is_flagged(self):
        photo = _photo()
        merged = _img(_render(photo))
        rimmed = photo.copy()
        rimmed[40:56, 30:34, :3] = (131, 174, 228)  # frame-coloured stripe where the frame is clear
        probes = [(rgb, _img(_render(_solid(rgb)))) for rgb in PROBES[:3]]
        res = h.compose_two_part_frame_from_probes(merged, _img(rimmed), probes)
        self.assertGreater(res["content_check"]["px"], 0)
        self.assertTrue(res["content_check"]["rim_suspect"])
        self.assertFalse(res["pass"])
        ok = h.compose_two_part_frame_from_probes(merged, _img(photo), probes)
        self.assertFalse(ok["content_check"]["rim_suspect"])

    def test_no_clear_area_reports_none(self):
        full = np.zeros((8, 8, 4))
        full[..., 3] = 255
        cc = h.content_vs_merged_where_frame_clear(_img(full), _img(full), _img(full))
        self.assertEqual(cc["px"], 0)
        self.assertFalse(cc["rim_suspect"])


class TestSmoothedLSQ(unittest.TestCase):
    def test_smooth_mask_reduces_alternating_edge_solutions(self):
        rng = np.random.default_rng(3)
        H = Wd = 24
        zone = np.zeros((H, Wd), bool)
        zone[4:20, 4:20] = True
        band = np.zeros_like(zone)
        band[4:6, 4:20] = True
        C = np.array([40.0, 180.0, 210.0])
        alpha = np.zeros((H, Wd))
        alpha[4:6, 4:20] = [[0.6], [0.2]]
        cols = {"k": (0, 0, 0), "w": (255, 255, 255), "g": (128, 128, 128), "r": (220, 40, 40)}
        bases, targets = {}, {}
        for n, rgb in cols.items():
            B = np.zeros((H, Wd, 4))
            B[..., :3] = rgb
            B[..., 3] = 255
            T = B.copy()
            T[..., :3] = (1 - alpha[..., None]) * B[..., :3] + alpha[..., None] * C
            T[..., :3] += rng.normal(0, 8, (H, Wd, 3)) * band[..., None]  # approximate targets
            bases[n], targets[n] = B, np.clip(T, 0, 255)
        per_px = h.fit_src_over_layer(bases, targets, zone)
        pooled = h.fit_src_over_layer(bases, targets, zone, smooth_mask=band, smooth_radius=1)
        self.assertLess(h.aa_band_noise(pooled, band), h.aa_band_noise(per_px, band))
        # outside the band nothing changes
        np.testing.assert_allclose(per_px[zone & ~band], pooled[zone & ~band])

    def test_two_part_reports_band_smoothing(self):
        from test_slicing_helper_split_effects import _two_part_fixture
        merged, content, ring, rim = _two_part_fixture()
        res = h.compose_two_part_frame(merged, content, ring, [(rim, "linear_burn")])
        self.assertIn("aa_band_px", res)
        self.assertTrue(res["aa_band_smoothed"])
        self.assertIn("content_check", res)


class TestProbeCli(unittest.TestCase):
    def test_parse_probe_spec(self):
        self.assertEqual(h._parse_probe_spec("0,0,0=a.png"), ((0, 0, 0), "a.png"))
        self.assertEqual(h._parse_probe_spec("#ff8000=b.png#3,4"), ((255, 128, 0), "b.png#3,4"))
        for bad in ("a.png", "1,2=a.png", "300,0,0=a.png", "x,y,z=a.png"):
            with self.assertRaises(SystemExit):
                h._parse_probe_spec(bad)

    def _args(self, td, probes):
        return SimpleNamespace(merged=str(td / "m.png"), content=str(td / "c.png"), frame=str(td / "f.png"),
                               overlay=None, overlay_blend=None, canvas=None, threshold=None,
                               out_dir=str(td / "out"), emit_overlays=False, swap_content=None, probe=probes)

    def test_cmd_split_fix_probe_mode(self):
        photo = _photo()
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            _img(_render(photo)).save(td / "m.png")
            _img(photo).save(td / "c.png")
            _img(_aa_ring()).save(td / "f.png")
            specs = []
            for i, rgb in enumerate(PROBES[:4]):
                _img(_render(_solid(rgb))).save(td / f"p{i}.png")
                specs.append("%d,%d,%d=%s" % (rgb + (td / f"p{i}.png",)))
            self.assertEqual(h.cmd_split_fix(self._args(td, specs)), 0)
            rep = json.loads((td / "out" / "split_fix.json").read_text(encoding="utf-8"))
            self.assertEqual(rep["approx"], "probe_fit_src_over")
            self.assertTrue(rep["swap_is_real_render"])
            self.assertFalse(rep["simulated_swap"])
            self.assertTrue(rep["pass"])
            self.assertEqual(rep["parts"][0]["blend_mode"], "normal")
            self.assertTrue((td / "out" / "frame.png").exists())
            # two probes: no held-out check -> not a pass
            self.assertEqual(h.cmd_split_fix(self._args(td, specs[:2])), 2)
            rep2 = json.loads((td / "out" / "split_fix.json").read_text(encoding="utf-8"))
            self.assertIsNone(rep2["swap_min_psnr"])
            self.assertIn("Fewer than 3 probes", rep2["note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
