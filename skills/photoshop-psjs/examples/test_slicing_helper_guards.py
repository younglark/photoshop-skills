#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the 1.4.9 guards: manifest lock, reuse cap, split threshold, thresholds from rules (synthetic data).

    python test_slicing_helper_guards.py
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


def _manifest():
    return {
        "schema": "psjs-slice-manifest/v2", "status": "pending_review",
        "source": {"file": "x.psb", "artboard": {"id": 1, "name": "a", "bounds": [0, 0, 60, 60], "size": [60, 60]}},
        "components": [{"id": "list", "type": "list", "shared": [
            {"name": "main_bg_row1", "kind": "image", "layer_id": 11, "reuse_of": "main_bg_row"},
            {"name": "main_bg_row2", "kind": "image", "layer_id": 12, "reuse_of": "main_bg_row"},
        ]}],
        "missing": [], "reuse_decisions": [],
    }


class TestManifestLock(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.m = self.tmp / "manifest.json"
        self.m.write_text(json.dumps(_manifest()), encoding="utf-8")

    def test_confirm_and_check(self):
        self.assertEqual(h.cmd_check_lock(SimpleNamespace(manifest=str(self.m))), 4)
        self.assertEqual(h.cmd_confirm(SimpleNamespace(manifest=str(self.m), note=None, relock=False)), 0)
        data = json.loads(self.m.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "confirmed")
        copy, sha = h.manifest_lock_paths(self.m)
        self.assertEqual(copy.read_bytes(), self.m.read_bytes())
        self.assertEqual(h.cmd_check_lock(SimpleNamespace(manifest=str(self.m))), 0)
        data["entries"] = [{"name": "added_after_confirm"}]
        self.m.write_text(json.dumps(data), encoding="utf-8")
        st = h.manifest_lock_state(self.m)
        self.assertTrue(st["locked"] and st["changed"])
        self.assertEqual(h.cmd_check_lock(SimpleNamespace(manifest=str(self.m))), 3)
        # confirm refuses to silently re-lock a changed manifest
        self.assertEqual(h.cmd_confirm(SimpleNamespace(manifest=str(self.m), note=None, relock=False)), 3)
        self.assertEqual(h.cmd_confirm(SimpleNamespace(manifest=str(self.m), note="user re-confirmed", relock=True)), 0)
        self.assertFalse(h.manifest_lock_state(self.m)["changed"])

    def test_build_layout_refuses_changed_manifest(self):
        h.flatten_components(json.loads(self.m.read_text(encoding="utf-8")))
        h.cmd_confirm(SimpleNamespace(manifest=str(self.m), note=None, relock=False))
        data = json.loads(self.m.read_text(encoding="utf-8"))
        data["notes"] = ["edited in phase 2"]
        self.m.write_text(json.dumps(data), encoding="utf-8")
        log = self.tmp / "export_log.json"
        log.write_text(json.dumps({"ok": True, "items": []}), encoding="utf-8")
        out = self.tmp / "layout.json"
        rc = h.cmd_build_layout(SimpleNamespace(manifest=str(self.m), export_log=str(log), slices_dir=str(self.tmp),
                                                out=str(out), group_effects=None))
        self.assertEqual(rc, 3)
        self.assertFalse(out.exists())

    def test_lock_issues(self):
        self.assertEqual([i["type"] for i in h.lock_issues(None)], ["manifest_not_locked"])
        self.assertEqual([i["type"] for i in h.lock_issues({"locked": False})], ["manifest_not_locked"])
        h.cmd_confirm(SimpleNamespace(manifest=str(self.m), note=None, relock=False))
        st = h.manifest_lock_state(self.m)
        self.assertEqual(h.lock_issues(st), [])
        self.m.write_text(self.m.read_text(encoding="utf-8") + " ", encoding="utf-8")
        self.assertEqual([i["type"] for i in h.lock_issues(st)], ["manifest_changed_after_confirm"])


class TestThresholds(unittest.TestCase):
    def test_precedence(self):
        tmp = Path(tempfile.mkdtemp())
        rules = tmp / "rules.json"
        rules.write_text(json.dumps({"thresholds": {"split_match_psnr": 30, "reuse_max_mae": 6, "bogus": 1}}), encoding="utf-8")
        t = h.resolve_thresholds({"split_match_psnr": 33}, h.thresholds_from_rules_file(rules), {"reuse_max_mae": None})
        self.assertEqual(t["split_match_psnr"], 30.0)
        self.assertEqual(t["reuse_max_mae"], 6.0)
        self.assertEqual(t["reuse_max_pct_gt16"], h.DEFAULT_THRESHOLDS["reuse_max_pct_gt16"])
        self.assertNotIn("bogus", t)
        self.assertEqual(h.resolve_thresholds(), h.DEFAULT_THRESHOLDS)

    def test_user_decision(self):
        dec = [{"type": "reuse", "instances": ["a2"], "decision": None},
               {"type": "reuse", "instances": ["a3"], "decision": "reuse", "decided_by": "user"},
               {"type": "reuse", "instances": ["a4"], "decision": "reuse", "default": "reuse"},
               {"type": "split_avatar", "instances": ["av1"], "decision": "wait_for_user"}]
        self.assertIsNone(h.user_decision_for("a2", dec, {"reuse"}))
        self.assertIsNotNone(h.user_decision_for("a3", dec, {"reuse"}, accept=h.REUSE_ACCEPT_DECISIONS))
        self.assertIsNone(h.user_decision_for("a3", dec, {"reuse"}, accept={"separate"}))
        self.assertIsNone(h.user_decision_for("av1", dec, {"split_avatar"}))
        # filled with the proposed default but not recorded as the user's answer -> does not count
        self.assertIsNone(h.user_decision_for("a4", dec, {"reuse"}, accept=h.REUSE_ACCEPT_DECISIONS))

    def test_confirm_records_user_decision(self):
        tmp = Path(tempfile.mkdtemp())
        m = tmp / "manifest.json"
        data = _manifest()
        data["reuse_decisions"] = [{"id": "rd_av", "type": "split_avatar", "instances": ["av1"],
                                    "default": "split_best_effort", "decision": "split_best_effort"}]
        m.write_text(json.dumps(data), encoding="utf-8")
        args = SimpleNamespace(manifest=str(m), note=None, relock=False, decision=[])
        self.assertEqual(h.cmd_confirm(args), 0)
        dec = json.loads(m.read_text(encoding="utf-8"))["reuse_decisions"]
        self.assertIsNone(h.user_decision_for("av1", dec, {"split_avatar"}))
        args = SimpleNamespace(manifest=str(m), note="user chose", relock=True, decision=["rd_av=split_best_effort"])
        self.assertEqual(h.cmd_confirm(args), 0)
        dec = json.loads(m.read_text(encoding="utf-8"))["reuse_decisions"]
        self.assertEqual(h.user_decision_for("av1", dec, {"split_avatar"})["decided_by"], "user")


class TestReuseCap(unittest.TestCase):
    def _layout(self, accepted=False, decisions=None):
        return {"nodes": [
            {"name": "row1", "kind": "image", "image": "row.png", "reuse_of": "row", "visible_bounds": _box(0, 0, 20, 10)},
            {"name": "row2", "kind": "image", "image": "row.png", "reuse_of": "row", "reuse_accepted": accepted,
             "visible_bounds": _box(0, 20, 20, 30)},
        ], "reuse_decisions": decisions or []}

    def _imgs(self, tmp, corner_only=False):
        sl = tmp / "slices"
        sl.mkdir(exist_ok=True)
        im = Image.new("RGBA", (20, 10), (50, 100, 150, 255))
        if corner_only:
            for x in range(4):
                im.putpixel((x, 0), (0, 0, 0, 0))
        im.save(sl / "row.png")
        ref = np.zeros((40, 20, 4), np.uint8); ref[..., 3] = 255
        ref[0:10] = (50, 100, 150, 255)
        ref[20:30] = (50, 100, 150, 255)
        rec = ref.copy()
        if corner_only:
            rec[20, 0:4] = (255, 0, 0, 255)  # differs only where the shared image is transparent
        else:
            rec[20:30, 0:10] = (200, 30, 30, 255)
        return sl, ref, rec

    def test_agent_flag_does_not_bypass(self):
        tmp = Path(tempfile.mkdtemp())
        sl, ref, rec = self._imgs(tmp)
        bad, ok, checked = h.reuse_region_checks(self._layout(accepted=True), ref, rec, None, tmp, slices_dir=sl)
        self.assertEqual([b["name"] for b in bad], ["row2"])
        self.assertTrue(bad[0]["reuse_accepted_by_agent_only"])
        self.assertEqual(ok, [])

    def test_user_decision_moves_to_accepted(self):
        tmp = Path(tempfile.mkdtemp())
        sl, ref, rec = self._imgs(tmp)
        dec = [{"id": "rd1", "type": "reuse", "instances": ["row2"], "decision": "reuse", "decided_by": "user"}]
        bad, ok, _ = h.reuse_region_checks(self._layout(decisions=dec), ref, rec, None, tmp, slices_dir=sl)
        self.assertEqual(bad, [])
        self.assertEqual([(a["name"], a["decision_id"]) for a in ok], [("row2", "rd1")])

    def test_opaque_region_only(self):
        tmp = Path(tempfile.mkdtemp())
        sl, ref, rec = self._imgs(tmp, corner_only=True)
        bad, _, checked = h.reuse_region_checks(self._layout(), ref, rec, None, tmp, slices_dir=sl)
        self.assertEqual(bad, [])
        self.assertEqual(checked[1]["region"], "opaque_alpha>=128")

    def test_error_under_upper_node_not_counted(self):
        tmp = Path(tempfile.mkdtemp())
        sl, ref, rec = self._imgs(tmp)
        lay = self._layout()
        # a text node painted above row2 covers exactly the differing pixels: that error is the text's, not row2's
        lay["nodes"].append({"name": "label", "kind": "dynamic_text", "visible_bounds": _box(0, 20, 10, 30)})
        bad, _, checked = h.reuse_region_checks(lay, ref, rec, None, tmp, slices_dir=sl)
        self.assertEqual(bad, [])
        self.assertIn("not_covered_above", checked[1]["region"])

    def test_cap_override(self):
        tmp = Path(tempfile.mkdtemp())
        sl, ref, rec = self._imgs(tmp)
        bad, _, _ = h.reuse_region_checks(self._layout(), ref, rec, None, tmp, tol_mae=255, tol_pct=100, slices_dir=sl)
        self.assertEqual(bad, [])


class TestSplitThreshold(unittest.TestCase):
    def _layout(self, decisions=None):
        return {"nodes": [
            {"name": "av1", "kind": "image", "component_id": "row1", "part_role": "avatar", "placeholder": True,
             "split_method": "smart_object_contents", "visible_bounds": _box(10, 10, 30, 30)},
            {"name": "fr1", "kind": "image", "component_id": "row1", "part_role": "frame",
             "split_method": "smart_object_contents", "visible_bounds": _box(8, 8, 32, 32)},
        ], "reuse_decisions": decisions or []}

    def _arrays(self, rim):
        ref = np.full((40, 40, 4), 200, np.uint8); ref[..., 3] = 255
        rec = ref.copy()
        if rim:
            rec[10:13, 10:30, :3] = 40  # dark inner rim along the hole
        return ref, rec

    def test_measured_below(self):
        tmp = Path(tempfile.mkdtemp())
        ref, rec = self._arrays(True)
        bad, ok, checked = h.split_region_checks(self._layout(), ref, rec, None, tmp)
        self.assertEqual([b["type"] for b in bad], ["split_below_threshold"])
        self.assertLess(bad[0]["psnr_db"], 35)
        self.assertTrue(Path(bad[0]["compare_image"]).exists())
        ref, rec = self._arrays(False)
        bad, ok, checked = h.split_region_checks(self._layout(), ref, rec, None, tmp)
        self.assertEqual(bad, [])
        self.assertEqual(len(checked), 1)

    def test_user_decision_and_threshold_override(self):
        tmp = Path(tempfile.mkdtemp())
        ref, rec = self._arrays(True)
        dec = [{"id": "rd_av", "type": "split_avatar", "instances": ["av1"], "decision": "split_best_effort",
                "decided_by": "user"}]
        bad, ok, _ = h.split_region_checks(self._layout(dec), ref, rec, None, tmp)
        self.assertEqual(bad, [])
        self.assertEqual(ok[0]["decision_id"], "rd_av")
        bad, _, _ = h.split_region_checks(self._layout(), ref, rec, None, tmp, threshold=5)
        self.assertEqual(bad, [])

    def test_declared_evidence(self):
        base = {"name": "fr1", "part_role": "frame", "split_method": "smart_object_contents",
                "split": {"hide_inner": [1]}}
        low = dict(base, split_evidence={"status": "candidate_unverified", "verified_psnr": 25.4,
                                         "effects_checked": "best effort accepted"})
        self.assertIn("split_below_threshold", [i["type"] for i in h.check_avatar_splits([low], [])])
        unv = dict(base, split_evidence={"status": "candidate_unverified", "match": {"psnr": 22.0}})
        self.assertIn("split_unverified", [i["type"] for i in h.check_avatar_splits([unv], [])])
        good = dict(base, split_evidence={"status": "candidate_unverified", "split_fix": {"match": {"psnr": 41.9},
                                                                                      "swap_min_psnr": 39.9}})
        self.assertEqual([i["type"] for i in h.check_avatar_splits([good], [])], [])
        agent_only = [{"type": "split_avatar", "instances": ["fr1"], "decision": "split_best_effort",
                       "default": "split_best_effort"}]
        self.assertIn("split_below_threshold", [i["type"] for i in h.check_avatar_splits([low], agent_only)])
        dec = [{"type": "split_avatar", "instances": ["fr1"], "decision": "split_best_effort", "decided_by": "user",
                "attempts": [{"method": "smart_object_contents", "result": "x", "reason": "y"}], "compare_image": "c.png"}]
        self.assertEqual([i["type"] for i in h.check_avatar_splits([low], dec)], [])
        self.assertEqual([i["type"] for i in h.check_avatar_splits([low], [], thresholds={"split_match_psnr": 20})], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
