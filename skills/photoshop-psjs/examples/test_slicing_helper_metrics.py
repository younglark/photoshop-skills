#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the 1.4.17 official PSNR definition (synthetic data).

Official metrics (mae, psnr_db, pct_px_diff_gt16) stay on raw RGB when the reference is fully
opaque. When the reference has transparent pixels they compare premultiplied RGB plus alpha, so
transparent pixels that differ only in RGB are not a mismatch, while missing / extra visible
content still is.

    python test_slicing_helper_metrics.py
"""
from __future__ import annotations

import unittest

import numpy as np

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slicing_helper as h  # noqa: E402


def _rgba(hgt, w, rgb=(0, 0, 0), a=0):
    im = np.zeros((hgt, w, 4), dtype=np.uint8)
    im[..., :3] = rgb
    im[..., 3] = a
    return im


class TestOfficialPsnr(unittest.TestCase):
    def test_transparent_rgb_only_difference_is_perfect(self):
        # Photoshop often writes white in fully transparent pixels; a recomposite writes black.
        ref = _rgba(8, 8, (255, 255, 255), 0)
        rec = _rgba(8, 8, (0, 0, 0), 0)
        m = h.metrics(ref, rec)
        self.assertEqual(m["psnr_definition"], h.PSNR_DEFINITION_PREMULTIPLIED)
        self.assertEqual(m["psnr_db"], 99.0)
        self.assertEqual(m["mae"], 0.0)
        self.assertEqual(m["pct_px_diff_gt16"], 0.0)
        self.assertEqual(m["mean_alpha_diff"], 0.0)
        self.assertLess(m["psnr_db_raw_rgb"], 10.0)

    def test_eval_shaped_artifact_is_officially_perfect(self):
        # Opaque content identical; fully transparent area differs only in RGB (white vs black).
        ref = _rgba(10, 20, (255, 255, 255), 0)
        rec = _rgba(10, 20, (0, 0, 0), 0)
        ref[:, :8] = (40, 80, 120, 255)
        rec[:, :8] = (40, 80, 120, 255)
        m = h.metrics(ref, rec)
        self.assertEqual(m["psnr_definition"], h.PSNR_DEFINITION_PREMULTIPLIED)
        self.assertEqual(m["psnr_db"], 99.0)
        self.assertEqual(m["mae"], 0.0)
        self.assertEqual(m["pct_px_diff_gt16"], 0.0)
        self.assertEqual(m["mean_alpha_diff"], 0.0)
        self.assertLess(m["psnr_db_raw_rgb"], 10.0)

    def test_opaque_identical_is_unchanged(self):
        a = _rgba(6, 6, (10, 20, 30), 255)
        m = h.metrics(a, a.copy())
        self.assertEqual(m["psnr_definition"], h.PSNR_DEFINITION_OPAQUE)
        self.assertEqual(m["psnr_db"], 99.0)
        self.assertEqual(m["psnr_db_raw_rgb"], 99.0)
        self.assertEqual(m["mae"], 0.0)

    def test_opaque_mismatch_matches_legacy_raw_rgb(self):
        a = _rgba(6, 6, (10, 20, 30), 255)
        b = a.copy()
        b[0:3, 0:3] = (200, 20, 30, 255)
        m = h.metrics(a, b)
        self.assertEqual(m["psnr_definition"], h.PSNR_DEFINITION_OPAQUE)
        # recompute the pre-1.4.17 raw-RGB figure independently
        diff = np.abs(a[..., :3].astype(np.int16) - b[..., :3].astype(np.int16))
        mae = float(diff.mean())
        mse = float((diff.astype(np.float64) ** 2).mean())
        psnr = 10 * np.log10(255 ** 2 / mse)
        self.assertEqual(m["mae"], round(mae, 3))
        self.assertEqual(m["psnr_db"], round(float(psnr), 2))
        self.assertEqual(m["psnr_db"], m["psnr_db_raw_rgb"])
        self.assertLess(m["psnr_db"], 30.0)
        self.assertGreater(m["pct_px_diff_gt16"], 0.0)

    def test_missing_content_in_transparent_area_fails(self):
        ref = _rgba(10, 10, (255, 255, 255), 0)
        rec = ref.copy()
        rec[2:8, 2:8] = (200, 10, 10, 255)
        m = h.metrics(ref, rec)
        self.assertEqual(m["psnr_definition"], h.PSNR_DEFINITION_PREMULTIPLIED)
        self.assertLess(m["psnr_db"], 20.0)
        self.assertGreater(m["mae"], 10.0)
        self.assertGreater(m["mean_alpha_diff"], 0.0)

    def test_missing_reference_content_fails(self):
        rec = _rgba(10, 10, (0, 0, 0), 0)
        ref = rec.copy()
        ref[2:8, 2:8] = (200, 10, 10, 255)
        m = h.metrics(ref, rec)
        self.assertEqual(m["psnr_definition"], h.PSNR_DEFINITION_PREMULTIPLIED)
        self.assertLess(m["psnr_db"], 20.0)
        self.assertGreater(m["mae"], 10.0)

    def test_partial_alpha_color_error_still_counts(self):
        ref = _rgba(4, 4, (255, 0, 0), 128)
        rec = _rgba(4, 4, (0, 0, 255), 128)
        m = h.metrics(ref, rec)
        self.assertEqual(m["psnr_definition"], h.PSNR_DEFINITION_PREMULTIPLIED)
        self.assertLess(m["psnr_db"], 20.0)

    def test_mask_keeps_opaque_path_when_kept_pixels_are_opaque(self):
        a = _rgba(4, 4, (10, 20, 30), 255)
        b = a.copy()
        b[0, 0] = (0, 0, 0, 0)  # outside the compared region
        mask = np.zeros((4, 4), dtype=bool)
        mask[0, 0] = True
        m = h.metrics(a, a.copy(), mask)  # remaining pixels are opaque and identical
        self.assertEqual(m["psnr_definition"], h.PSNR_DEFINITION_OPAQUE)
        self.assertEqual(m["psnr_db"], 99.0)
        # with a transparent kept pixel, official path switches even if the opaque crop matches
        a[1, 1] = (255, 255, 255, 0)
        b[1, 1] = (0, 0, 0, 0)
        m2 = h.metrics(a, b, mask)
        self.assertEqual(m2["psnr_definition"], h.PSNR_DEFINITION_PREMULTIPLIED)
        self.assertEqual(m2["psnr_db"], 99.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
