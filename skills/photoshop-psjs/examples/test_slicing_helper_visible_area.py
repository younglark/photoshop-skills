#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the 1.4.16 checks (synthetic data):

A. a slice draws only what the document shows: a layer clipped by an ancestor group's mask (or a clipping base) and
   exported whole is slice_exceeds_visible_area (pixels vs the full render, or the exporter's mask_clip record);
   the user's unmasked_asset answer accepts it; prepare / flatten see group masks in the layer tree;
B. a visible layer with no entry needs an entry or a blocking layer_inclusion decision: validate splits
   visible_layer_unassigned by decision coverage, and the review gate (flatten / preview / confirm) lists such
   layers before the user reviews; confirm refuses to lock while any remain;
C. the unsliced region of the render (a backdrop) is reported apart: psnr_full stays official,
   psnr_full_over_backdrop lays the recomposite over the uniform colour the render shows there;
D. font paths compare with separators / case normalised (Windows).

    python test_slicing_helper_visible_area.py
"""
from __future__ import annotations

import contextlib
import io
import json
import ntpath
import posixpath
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import slicing_helper as h  # noqa: E402

SHIPPED = HERE / "export_slices.psjs"
GRAY = (92, 101, 108, 255)


def _box(l, t, r, b):
    return {"left": l, "top": t, "right": r, "bottom": b}


def _types(issues):
    return sorted(i["type"] for i in issues)


def _solid(w, hh, rgba):
    return Image.new("RGBA", (w, hh), rgba)


def _textured(w, hh, seed=0):
    a = np.zeros((hh, w, 4), dtype=np.uint8)
    a[..., 0] = (np.arange(w) * 7 + seed) % 200 + 30
    a[..., 1] = 40
    a[..., 2] = (np.arange(hh)[:, None] * 13) % 200 + 30
    a[..., 3] = 255
    return Image.fromarray(a, "RGBA")


def _node(name, box, image=True, **kw):
    n = {"name": name, "kind": "image", "layer_id": kw.pop("layer_id", 10), "visible_bounds": _box(*box),
         "logical_bounds": _box(*box), "image": f"{name}.png" if image else None, "default": True}
    n.update(kw)
    return n


# ---------------------------------------------------------------------------
# A. slice_exceeds_visible_area
# ---------------------------------------------------------------------------

class TestVisibleArea(unittest.TestCase):
    def _check(self, slice_img, box, ref, nodes_extra=(), decisions=(), log=(), mask_clip=None, slices=None):
        tmp = Path(tempfile.mkdtemp())
        slice_img.save(tmp / "bar.png")
        for name, img in (slices or {}).items():
            img.save(tmp / f"{name}.png")
        node = _node("bar", box)
        if mask_clip:
            node["mask_clip"] = mask_clip
        layout = {"nodes": [node, *nodes_extra], "reuse_decisions": list(decisions),
                  "manifest_lock": {"decision_log": list(log)}}
        return h.visible_area_checks(layout, np.asarray(ref.convert("RGBA")), None, tmp)

    def _scene(self, visible_w=None, visible_h=None):
        """A 60x20 textured bar at (10, 10); the render shows only its left visible_w columns / top visible_h rows."""
        bar = _textured(60, 20)
        ref = _solid(100, 50, GRAY)
        shown = bar.crop((0, 0, visible_w or 60, visible_h or 20))
        ref.paste(shown, (10, 10))
        return bar, ref

    def test_group_mask_strip_is_reported(self):
        bar, ref = self._scene(visible_w=8)
        issues, checked, ok = self._check(bar, (10, 10, 70, 30), ref)
        self.assertEqual(_types(issues), ["slice_exceeds_visible_area"])
        i = issues[0]
        self.assertEqual(i["visible_box"], [10, 10, 18, 30])
        self.assertEqual(i["overflow_px"], {"left": 0, "top": 0, "right": 52, "bottom": 0})
        self.assertEqual(i["source"], "pixels_vs_full_render")
        self.assertEqual(len(checked), 1)
        self.assertEqual(ok, [])

    def test_horizontal_strip_is_found_too(self):
        bar, ref = self._scene(visible_h=3)
        issues, _, _ = self._check(bar, (10, 10, 70, 30), ref)
        self.assertEqual(issues[0]["visible_box"], [10, 10, 70, 13])
        self.assertEqual(issues[0]["overflow_px"]["bottom"], 17)

    def test_fully_visible_slice_is_fine(self):
        bar, ref = self._scene()
        issues, checked, _ = self._check(bar, (10, 10, 70, 30), ref)
        self.assertEqual(issues, [])
        self.assertEqual(checked[0]["outside_unmatched_px"], 0)

    def test_small_overflow_below_both_floors_is_not_reported(self):
        bar, ref = self._scene(visible_w=59)  # one column = 20 px < 64
        issues, _, _ = self._check(bar, (10, 10, 70, 30), ref)
        self.assertEqual(issues, [])

    def test_pixels_under_a_node_above_belong_to_that_node(self):
        bar, ref = self._scene(visible_w=8)
        cover = _solid(52, 20, (200, 0, 0, 255))
        ref.paste(cover, (18, 10))
        above = _node("cover", (18, 10, 70, 30), layer_id=11)
        issues, _, _ = self._check(bar, (10, 10, 70, 30), ref, nodes_extra=[above], slices={"cover": cover})
        self.assertEqual(issues, [])

    def test_exporter_mask_record_without_pixels(self):
        mc = {"clipped": True, "cropped": False, "skipped": "tmp_not_aligned_with_source",
              "clip_bounds": _box(10, 10, 18, 30), "mask_layer_ids": [5]}
        layout = {"nodes": [_node("bar", (10, 10, 70, 30), mask_clip=mc)], "reuse_decisions": [],
                  "manifest_lock": {"decision_log": []}}
        issues, _, _ = h.visible_area_checks(layout, np.zeros((50, 100, 4), dtype=np.uint8), None, None)
        self.assertEqual(issues[0]["source"], "export_log_mask_clip")
        self.assertEqual(issues[0]["overflow_px"]["right"], 52)
        self.assertEqual(issues[0]["mask_layer_ids"], [5])

    def test_user_chose_the_unmasked_asset(self):
        bar, ref = self._scene(visible_w=8)
        d = {"id": "full_bar", "type": "unmasked_asset", "layer_ids": [10], "question": "<q>",
             "options": ["unmasked", "visible_only"], "default": "visible_only"}
        self.assertTrue(h.decision_is_blocking(d))
        issues, _, ok = self._check(bar, (10, 10, 70, 30), ref,
                                    decisions=[dict(d, decision="unmasked", decided_by="user")],
                                    log=[{"id": "full_bar", "decision": "unmasked"}])
        self.assertEqual(issues, [])
        self.assertEqual([a["decision_id"] for a in ok], ["full_bar"])
        # unanswered (or answered by hand without confirm): still an issue
        issues, _, ok = self._check(bar, (10, 10, 70, 30), ref,
                                    decisions=[dict(d, decision="unmasked", decided_by="user")])
        self.assertEqual(_types(issues), ["slice_exceeds_visible_area"])
        self.assertEqual(ok, [])

    def test_tree_mask_flags_entries_before_export(self):
        tree = {"name": "<artboard>", "layer_id": 1, "kind": "artboard", "is_artboard": True, "is_group": True,
                "visible": True, "bbox": [0, 0, 100, 50], "children": [
                    {"name": "<bar group>", "layer_id": 5, "kind": "group", "is_group": True, "visible": True,
                     "bbox": [10, 10, 70, 30],
                     "mask": {"bbox": [10, 0, 18, 50], "enabled": True, "clips_outside": True, "vector": False},
                     "children": [{"name": "<bar>", "layer_id": 10, "kind": "pixel", "visible": True,
                                   "bbox": [10, 10, 70, 30], "children": []}]}]}
        entries = [{"name": "bar", "kind": "image", "layer_id": 10}]
        got = h.mask_clipped_entries(entries, tree)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["visible_bbox"], [10, 10, 18, 30])
        self.assertEqual(got[0]["mask_layer_ids"], [5])
        tree["children"][0]["mask"]["clips_outside"] = False   # white outside the mask: nothing hidden
        self.assertEqual(h.mask_clipped_entries(entries, tree), [])
        tree["children"][0]["mask"].update(clips_outside=True, enabled=False)
        self.assertEqual(h.mask_clipped_entries(entries, tree), [])

    def test_exporter_crops_to_the_mask_area(self):
        text = SHIPPED.read_text(encoding="utf-8")
        self.assertRegex(text, r'const EXPORTER_RELEASE = "1\.\d+\.\d+";')
        self.assertIn("async function maskClipFor(src, selectIds)", text)
        self.assertIn('maskClip.skipped = "user_decision_unmasked";', text)
        self.assertIn("const left = cropOrigin.left + beforeTrim.left - afterTrim.left;", text)
        self.assertIn('"unmasked_asset"', text)


# ---------------------------------------------------------------------------
# A + C end to end through build-layout and validate
# ---------------------------------------------------------------------------

def _confirm(path, note=None, decision=None):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
        rc = h.cmd_confirm(SimpleNamespace(manifest=str(path), note=note, relock=False, decision=decision or []))
    return rc, buf.getvalue()


def _lao(lid, parent=1, bounds=(0, 0, 100, 50), kind="pixel", group=False):
    return {"id": lid, "name": f"<layer {lid}>", "parent_id": parent, "kind": kind, "visible": True,
            "effective_visible": True, "opacity": 100, "bounds": _box(*bounds), "group": group}


class TestEndToEnd(unittest.TestCase):
    def _run(self, decisions=(), answers=(), mask_clip=None, visible_w=None):
        tmp = Path(tempfile.mkdtemp())
        slices = tmp / "slices"
        slices.mkdir()
        part = {"name": "bar", "role": "base", "kind": "image", "layer_id": 10, "logical_bounds": [10, 10, 70, 30]}
        m = {"schema": h.SCHEMA_MANIFEST, "status": "pending_review",
             "source": {"artboard": {"id": 1, "name": "<artboard>", "bounds": [0, 0, 100, 50], "size": [100, 50]}},
             "components": [{"id": "bar", "type": "panel", "shared": [part]}], "reuse_decisions": list(decisions)}
        h.flatten_components(m)
        mp = tmp / "manifest.json"
        mp.write_text(json.dumps(m), encoding="utf-8")
        rc, _ = _confirm(mp, note="清单确认")
        self.assertEqual(rc, 0)
        if answers:
            _confirm(mp, decision=list(answers))
        bar = _textured(60, 20)
        bar.save(slices / "bar.png")
        item = {"name": "bar", "ok": True, "out_size": [60, 20], "visible_bounds_from_trim_insets": True,
                "visible_bounds": _box(10, 10, 70, 30), "logical_bounds": _box(10, 10, 70, 30)}
        if mask_clip:
            item["mask_clip"] = mask_clip
        log = {"ok": True, "exporter": {"release": h.SKILL_VERSION, "sha256": h.shipped_exporter_sha256(SHIPPED)},
               "layers_at_open": [_lao(1, None, kind="artboard", group=True), _lao(2), _lao(10, bounds=(10, 10, 70, 30))],
               "items": [item], "full_render": {"ok": True, "hidden_at_render": []}}
        lp = tmp / "export_log.json"
        lp.write_text(json.dumps(log), encoding="utf-8")
        out = tmp / "layout.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            h.cmd_build_layout(SimpleNamespace(manifest=str(mp), export_log=str(lp), slices_dir=str(slices),
                                               out=str(out), group_effects=None))
        ref = _solid(100, 50, GRAY)
        ref.paste(bar.crop((0, 0, visible_w or 60, 20)), (10, 10))
        rp = tmp / "full_render.png"
        ref.save(rp)
        vdir = tmp / "validate"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = h.cmd_validate(SimpleNamespace(layout=str(out), slices_dir=str(slices), full_render=str(rp),
                                                out_dir=str(vdir), fonts_dir=None, group_effects=None))
        layout = json.loads(out.read_text(encoding="utf-8"))
        return rc, json.loads((vdir / "validate_report.json").read_text(encoding="utf-8")), layout, vdir

    def test_backdrop_metrics_and_unassigned_backdrop_still_fails(self):
        rc, rep, _, vdir = self._run()
        self.assertEqual(_types(rep["issues"]), ["visible_layer_unassigned"])
        self.assertEqual(rep["issues"][0]["layers"][0]["reason"], "no_decision")
        self.assertEqual(rep["issues"][0]["suggested_decision"]["type"], "layer_inclusion")
        self.assertLess(rep["psnr_full"], 30)
        self.assertEqual(rep["psnr_full_over_backdrop"], 99.0)
        bd = rep["backdrop"]
        self.assertTrue(bd["used"])
        self.assertEqual(bd["color"], "#5c656c")
        self.assertEqual(bd["layers"], [{"id": 2, "name": "<layer 2>", "kind": "pixel", "status": "no_decision"}])
        self.assertTrue((vdir / "recomposite_over_backdrop.png").is_file())
        self.assertEqual(rc, 1)

    def test_backdrop_excluded_by_the_user(self):
        inc = {"id": "inc_2", "type": "layer_inclusion", "layer_ids": [2], "question": "<q>",
               "options": ["include", "exclude"], "default": "include"}
        rc, rep, _, _ = self._run(decisions=[inc], answers=["inc_2=exclude"])
        self.assertEqual(rep["issues"], [])
        self.assertEqual(rep["backdrop"]["layers"][0]["status"], "excluded_by_user")
        self.assertEqual(rep["psnr_full_over_backdrop"], 99.0)
        self.assertTrue(rep["ok"])

    def test_backdrop_pending_decision_is_reported_once(self):
        inc = {"id": "inc_2", "type": "layer_inclusion", "layer_ids": [2], "question": "<q>",
               "options": ["include", "exclude"], "default": "include"}
        rc, rep, _, _ = self._run(decisions=[inc])
        self.assertEqual(_types(rep["issues"]), ["pending_user_decision"])
        self.assertEqual(rep["backdrop"]["layers"][0]["status"], "pending_decision")

    def test_backdrop_answered_include_needs_an_entry(self):
        inc = {"id": "inc_2", "type": "layer_inclusion", "layer_ids": [2], "question": "<q>",
               "options": ["include", "exclude"], "default": "include"}
        rc, rep, _, _ = self._run(decisions=[inc], answers=["inc_2=include"])
        self.assertEqual(_types(rep["issues"]), ["visible_layer_unassigned"])
        self.assertEqual(rep["issues"][0]["layers"][0]["reason"], "user_answer_keeps_layer_needs_entry")

    def test_masked_bar_exported_whole_fails(self):
        inc = {"id": "inc_2", "type": "layer_inclusion", "layer_ids": [2], "question": "<q>",
               "options": ["include", "exclude"], "default": "include"}
        mc = {"clipped": True, "cropped": False, "skipped": "tmp_not_aligned_with_source",
              "clip_bounds": _box(10, 10, 18, 30), "layer_bounds": _box(10, 10, 70, 30), "mask_layer_ids": [5],
              "sources": [{"id": 5, "role": "ancestor", "user_mask": True}]}
        rc, rep, layout, _ = self._run(decisions=[inc], answers=["inc_2=exclude"], mask_clip=mc, visible_w=8)
        self.assertEqual(_types(rep["issues"]), ["slice_exceeds_visible_area"])
        node = layout["nodes"][0]
        self.assertTrue(node["mask_clipped"])
        self.assertEqual(node["mask_clip"]["clip_bounds"], _box(10.0, 10.0, 18.0, 30.0))
        self.assertEqual(rep["visible_area_checked"], 0)  # the exporter's record decided it

    def test_pixels_alone_catch_an_old_log(self):
        inc = {"id": "inc_2", "type": "layer_inclusion", "layer_ids": [2], "question": "<q>",
               "options": ["include", "exclude"], "default": "include"}
        rc, rep, _, _ = self._run(decisions=[inc], answers=["inc_2=exclude"], visible_w=8)
        self.assertEqual(_types(rep["issues"]), ["slice_exceeds_visible_area"])
        self.assertEqual(rep["issues"][0]["visible_box"], [10, 10, 18, 30])


class TestBackdropUnit(unittest.TestCase):
    def test_uniform_and_not(self):
        ref = np.zeros((10, 10, 4), dtype=np.uint8)
        ref[...] = GRAY
        alpha = np.zeros((10, 10), dtype=np.uint8)
        alpha[:5] = 255
        bd = h.backdrop_for_metrics({}, ref, alpha)
        self.assertTrue(bd["used"])
        self.assertEqual(bd["unsliced_px"], 50)
        ref[5:, :5] = (0, 0, 0, 255)
        bd = h.backdrop_for_metrics({}, ref, alpha)
        self.assertFalse(bd["used"])
        self.assertEqual(bd["reason"], "unsliced_region_not_uniform")
        self.assertEqual(h.backdrop_for_metrics({}, ref, np.full((10, 10), 255, np.uint8))["reason"],
                         "no_unsliced_region")

    def test_ignored_pixels_do_not_break_uniformity(self):
        ref = np.zeros((10, 10, 4), dtype=np.uint8)
        ref[...] = GRAY
        ref[0, 0] = (255, 255, 255, 255)   # e.g. attribute text drawn there
        alpha = np.zeros((10, 10), dtype=np.uint8)
        ignore = np.zeros((10, 10), dtype=bool)
        ignore[:3, :3] = True
        ref[:3, :3] = (255, 255, 255, 255)
        self.assertFalse(h.backdrop_for_metrics({}, ref, alpha)["used"])
        self.assertTrue(h.backdrop_for_metrics({}, ref, alpha, ignore=ignore)["used"])


# ---------------------------------------------------------------------------
# B. review gate for visible layers without entry or decision
# ---------------------------------------------------------------------------

def _tree(extra_children=()):
    return {"name": "<artboard>", "path": "<artboard>", "layer_id": 1, "kind": "artboard", "is_artboard": True,
            "is_group": True, "visible": True, "bbox": [0, 0, 100, 50], "children": [
                {"name": "<fill>", "path": "<artboard>/<fill>", "layer_id": 2, "kind": "solidcolorfill",
                 "visible": True, "opacity": 255, "bbox": [0, 0, 400, 200], "children": []},
                {"name": "<bar>", "path": "<artboard>/<bar>", "layer_id": 10, "kind": "pixel", "visible": True,
                 "opacity": 255, "bbox": [10, 10, 70, 30], "children": []},
                {"name": "<old>", "path": "<artboard>/<old>", "layer_id": 3, "kind": "group", "is_group": True,
                 "visible": False, "bbox": [0, 0, 10, 10], "children": [
                     {"name": "<in hidden group>", "layer_id": 4, "kind": "pixel", "visible": True,
                      "bbox": [0, 0, 10, 10], "children": []}]},
                {"name": "<levels>", "layer_id": 6, "kind": "levels", "is_adjustment": True, "visible": True,
                 "bbox": [0, 0, 100, 50], "children": []},
                {"name": "<off board>", "layer_id": 7, "kind": "pixel", "visible": True,
                 "bbox": [500, 500, 510, 510], "children": []},
                *extra_children]}


def _manifest(decisions=()):
    part = {"name": "bar", "role": "base", "kind": "image", "layer_id": 10, "logical_bounds": [10, 10, 70, 30]}
    m = {"schema": h.SCHEMA_MANIFEST, "status": "pending_review",
         "source": {"artboard": {"id": 1, "name": "<artboard>", "bounds": [0, 0, 100, 50], "size": [100, 50]}},
         "layer_tree": _tree(), "components": [{"id": "bar", "type": "panel", "shared": [part]}],
         "reuse_decisions": list(decisions)}
    return m


class TestReviewGate(unittest.TestCase):
    def test_tree_lists_only_visible_pixel_layers_without_entry_or_decision(self):
        m = _manifest()
        entries = h.flatten_components(m)
        got = h.tree_unassigned_visible_layers(m["layer_tree"], entries, [])
        self.assertEqual([l["id"] for l in got], [2])
        inc = {"id": "inc_2", "type": "layer_inclusion", "layer_ids": [2]}
        self.assertEqual(h.tree_unassigned_visible_layers(m["layer_tree"], entries, [inc]), [])
        # a non-blocking question (e.g. a reuse decision) is not an answer to "is this layer in the design"
        self.assertEqual(len(h.tree_unassigned_visible_layers(m["layer_tree"], entries,
                                                             [{"id": "r", "type": "reuse", "layer_ids": [2]}])), 1)

    def test_flatten_and_preview_list_them(self):
        tmp = Path(tempfile.mkdtemp())
        mp = tmp / "manifest.json"
        mp.write_text(json.dumps(_manifest()), encoding="utf-8")
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            h.cmd_flatten(SimpleNamespace(manifest=str(mp), out=None, group_effects=None))
        summary = json.loads(buf.getvalue())
        gate = summary["visible_layers_without_entry_or_decision"]
        self.assertEqual([l["id"] for l in gate["layers"]], [2])
        self.assertEqual(gate["suggested_decision"]["layer_ids"], [2])
        self.assertIn("confirm will refuse", err.getvalue())
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            h.cmd_preview(SimpleNamespace(manifest=str(mp), out=str(tmp / "p.png"), ref=None, ref_is_document=False,
                                          label_font=None))
        self.assertIn("visible_layers_without_entry_or_decision", buf.getvalue())

    def test_confirm_refuses_until_a_decision_is_written(self):
        tmp = Path(tempfile.mkdtemp())
        mp = tmp / "manifest.json"
        m = _manifest()
        h.flatten_components(m)
        mp.write_text(json.dumps(m), encoding="utf-8")
        rc, out = _confirm(mp, note="清单确认")
        self.assertEqual(rc, 5)
        self.assertEqual(json.loads(out)["error"], "visible_layer_without_entry_or_decision")
        self.assertFalse(h.manifest_lock_paths(mp)[1].exists())
        m["reuse_decisions"] = [dict(json.loads(out)["suggested_decision"])]
        mp.write_text(json.dumps(m), encoding="utf-8")
        rc, out = _confirm(mp, note="清单确认")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["blocking_not_user_decided"], ["inc_2"])

    def test_manifest_without_tree_is_not_gated(self):
        m = _manifest()
        del m["layer_tree"]
        self.assertIsNone(h.review_gate_unassigned(m))


# ---------------------------------------------------------------------------
# D. font path comparison
# ---------------------------------------------------------------------------

class TestSamePath(unittest.TestCase):
    def test_windows_separators_and_case(self):
        self.assertTrue(h.same_file_path("C:/Windows/Fonts/msyh.ttc", "C:\\Windows\\Fonts\\MSYH.ttc", flavor=ntpath))
        self.assertFalse(h.same_file_path("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", flavor=ntpath))

    def test_posix_keeps_case(self):
        self.assertTrue(h.same_file_path("/usr/share/fonts/a/../b.ttf", "/usr/share/fonts/b.ttf", flavor=posixpath))
        self.assertFalse(h.same_file_path("/fonts/A.ttf", "/fonts/a.ttf", flavor=posixpath))

    def test_configured_path_string_is_kept(self):
        tmp = Path(tempfile.mkdtemp())
        font = None
        for p in h.CJK_LABEL_FONTS:
            if Path(p).is_file() and h._font_has_cjk(p):
                font = p
                break
        if font is None:
            self.skipTest("no CJK font on this machine")
        try:
            info = h.resolve_label_font(font, force=True)
            self.assertEqual(info["path"], font)   # not re-spelled through Path()
        finally:
            h.LABEL_FONT["resolved"] = False


if __name__ == "__main__":
    unittest.main(verbosity=2)
