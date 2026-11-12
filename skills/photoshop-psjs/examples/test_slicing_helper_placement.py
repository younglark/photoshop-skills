#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for placement checks (1.4.11): an entry whose layer measures at another box than its manifest box
(layer_bounds_mismatch), borrowed non-default states placed at their manifest box, and images whose recorded
position is off by a pixel or two (placement_offset). Synthetic data.

    python test_slicing_helper_placement.py
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


def _box(l, t, r, b):
    return {"left": l, "top": t, "right": r, "bottom": b}


def _cell(size=(30, 12), seed=0):
    """Opaque cell with a horizontal gradient and dark edges: a 1 px shift is visible."""
    w, hh = size
    a = np.zeros((hh, w, 4), dtype=np.uint8)
    a[..., 0] = (np.arange(w) * 7 + seed) % 256
    a[..., 1] = 120
    a[..., 2] = (np.arange(hh)[:, None] * 13) % 256
    a[..., 3] = 255
    a[:, :2, :3] = 10
    a[:, -2:, :3] = 10
    return Image.fromarray(a, "RGBA")


class TestLayerBoundsConflict(unittest.TestCase):
    def test_tolerance(self):
        self.assertEqual(h.layer_bounds_conflict([0, 0, 30, 12], _box(1, 0, 31, 12)), 0)
        self.assertEqual(h.layer_bounds_conflict([0, 0, 30, 12], _box(30, 0, 60, 12)), 30)
        self.assertEqual(h.layer_bounds_conflict(None, _box(30, 0, 60, 12)), 0)


def _manifest(parts):
    return {
        "schema": h.SCHEMA_MANIFEST, "status": "confirmed",
        "source": {"artboard": {"id": 1, "name": "<artboard>", "bounds": [0, 0, 90, 20], "size": [90, 20]}},
        "components": [{"id": "nav", "type": "nav", "shared": parts}],
        "reuse_decisions": [],
    }


class TestCopiedLayerId(unittest.TestCase):
    """Three cells side by side; the second and third entries borrow the first cell's layer_id."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.slices = self.tmp / "slices"
        self.slices.mkdir()

    def _run(self, third_from_hidden):
        parts = [
            {"name": "cell_a", "role": "base", "kind": "image", "layer_id": 11, "reuse_of": "cell",
             "logical_bounds": [0, 0, 30, 12]},
            {"name": "cell_b", "role": "base", "kind": "image", "layer_id": 11, "reuse_of": "cell",
             "logical_bounds": [30, 0, 60, 12]},
            {"name": "cell_c", "role": "base", "kind": "image", "layer_id": 11, "reuse_of": "cell",
             "logical_bounds": [60, 0, 90, 12], "from_hidden": third_from_hidden},
        ]
        m = _manifest(parts)
        h.flatten_components(m)
        mpath = self.tmp / "manifest.json"
        mpath.write_text(json.dumps(m), encoding="utf-8")
        self.assertEqual(h.cmd_confirm(SimpleNamespace(manifest=str(mpath), note="test", relock=False)), 0)
        _cell().save(self.slices / "cell.png")
        measured = {"ok": True, "out_size": [30, 12], "visible_bounds_from_trim_insets": True,
                    "visible_bounds": _box(0, 0, 30, 12), "logical_bounds": _box(0, 0, 30, 12)}
        log = {"ok": True, "items": [dict(measured, name=n) for n in ("cell_a", "cell_b", "cell_c")]}
        lpath = self.tmp / "export_log.json"
        lpath.write_text(json.dumps(log), encoding="utf-8")
        out = self.tmp / "layout.json"
        h.cmd_build_layout(SimpleNamespace(manifest=str(mpath), export_log=str(lpath), slices_dir=str(self.slices),
                                           out=str(out), group_effects=None))
        layout = json.loads(out.read_text(encoding="utf-8"))
        ref = Image.new("RGBA", (90, 20), (0, 0, 0, 255))
        for x in (0, 30, 60):
            ref.paste(_cell(), (x, 0))
        rpath = self.tmp / "full_render.png"
        ref.save(rpath)
        vdir = self.tmp / "validate"
        rc = h.cmd_validate(SimpleNamespace(layout=str(out), slices_dir=str(self.slices), full_render=str(rpath),
                                            out_dir=str(vdir), fonts_dir=None, group_effects=None,
                                            reuse_tol_mae=4.0, reuse_tol_pct=1.0))
        report = json.loads((vdir / "validate_report.json").read_text(encoding="utf-8"))
        return {n["name"]: n for n in layout["nodes"]}, rc, report

    def test_default_instance_on_sibling_layer_is_flagged(self):
        nodes, rc, report = self._run(third_from_hidden=False)
        bad = sorted(i["name"] for i in report["issues"] if i["type"] == "layer_bounds_mismatch")
        self.assertEqual(bad, ["cell_b", "cell_c"])
        self.assertEqual(nodes["cell_b"]["layer_bounds_mismatch"]["manifest_logical_bounds"], _box(30, 0, 60, 12))
        self.assertEqual(rc, 1)

    def test_borrowed_non_default_state_goes_to_manifest_box(self):
        nodes, rc, report = self._run(third_from_hidden=True)
        self.assertFalse(nodes["cell_c"]["default"])
        self.assertEqual(nodes["cell_c"]["visible_bounds"], _box(60, 0, 90, 12))
        self.assertEqual(nodes["cell_c"]["layer_bounds_mismatch"]["placed_from"], "manifest_logical_bounds")
        bad = [i["name"] for i in report["issues"] if i["type"] == "layer_bounds_mismatch"]
        self.assertEqual(bad, ["cell_b"])


class TestPlacementOffset(unittest.TestCase):
    def setUp(self):
        self.slices = Path(tempfile.mkdtemp())

    def _check(self, img, placed_left, design_left, nine=None, placed_w=None, bg=(0, 0, 0)):
        img.save(self.slices / "a.png")
        w, hh = img.size
        ref = np.zeros((20, 60, 4), dtype=np.uint8)
        ref[..., :3] = bg
        ref[..., 3] = 255
        ref[4:4 + hh, design_left:design_left + w] = np.asarray(img)
        node = {"name": "a", "kind": "nine_slice" if nine else "image", "image": "a.png", "nine_slice": nine,
                "visible_bounds": _box(placed_left, 4, placed_left + (placed_w or w), 4 + hh)}
        return h.placement_offset_checks({"nodes": [node]}, ref, None, self.slices)

    def test_one_px_off_is_flagged_with_the_shift(self):
        bad, checked = self._check(_cell(), placed_left=10, design_left=11)
        self.assertEqual(len(checked), 1)
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["type"], "placement_offset")
        self.assertEqual(bad[0]["fits_at_shift"], [1, 0])
        self.assertLess(bad[0]["mae_at_shift"], 0.01)

    def test_exact_position_passes(self):
        bad, checked = self._check(_cell(), placed_left=10, design_left=10)
        self.assertEqual(bad, [])
        self.assertEqual(checked[0]["mae"], 0.0)

    def test_flat_colour_has_no_signal(self):
        flat = Image.new("RGBA", (30, 12), (90, 90, 90, 255))
        bad, _ = self._check(flat, placed_left=10, design_left=11, bg=(90, 90, 90))
        self.assertEqual(bad, [])  # same pixels at every shift: nothing to register on, no false alarm

    def test_native_size_nine_slice_is_checked_stretched_is_not(self):
        nine = {"left": 3, "top": 3, "right": 3, "bottom": 3}
        bad, checked = self._check(_cell(), placed_left=10, design_left=11, nine=nine)
        self.assertEqual([b["fits_at_shift"] for b in bad], [[1, 0]])
        bad, checked = self._check(_cell(), placed_left=10, design_left=11, nine=nine, placed_w=40)
        self.assertEqual((bad, checked), ([], []))

    def test_covered_pixels_and_blend_modes_are_skipped(self):
        img = _cell()
        img.save(self.slices / "a.png")
        ref = np.zeros((20, 60, 4), dtype=np.uint8)
        node = {"name": "a", "kind": "image", "image": "a.png", "blend_mode": "multiply",
                "visible_bounds": _box(10, 4, 40, 16)}
        self.assertEqual(h.placement_offset_checks({"nodes": [node]}, ref, None, self.slices), ([], []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
