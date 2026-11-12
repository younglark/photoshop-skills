#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for answers, confirmed layout data and complete states (1.4.15, synthetic data):

A. a slice whose layer was hidden at open and is covered by an unanswered blocking decision is not exported
   (exporter: skipped_pending_decision); a PNG older than the answer it depends on is exported_before_decision;
B. build-layout takes kind and nine-slice margins from the confirmed manifest (no image -> nine_slice promotion,
   approved margins kept, detection only fills empty margins); validate nine_slice_changed_after_confirm;
C. paragraph (box) text starts at the click point (box_bounds_pt gives only the size);
D. the exporter header names the Python text-mode CRLF pitfall;
E. annotation labels look for a CJK-capable font and report a warning without one;
F. cumulative / ordinal groups: build-layout expands level_N states, validate previews whole levels and reports
   state_activation_incomplete for an explicit activation that leaves a lower member off.

    python test_slicing_helper_confirmed_layout.py
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import slicing_helper as h  # noqa: E402
import text_render as T  # noqa: E402
from test_slicing_helper_text_render import FAKE_FONT, InkShaper, _box_attrs  # noqa: E402

SHIPPED = HERE / "export_slices.psjs"


def _box(l, t, r, b):
    return {"left": l, "top": t, "right": r, "bottom": b}


def _types(issues):
    return sorted(i["type"] for i in issues)


def _layer(lid, parent=1, visible=True, bounds=(0, 0, 60, 20), kind="pixel"):
    return {"id": lid, "name": f"<layer {lid}>", "parent_id": parent, "kind": kind, "visible": visible,
            "effective_visible": visible, "opacity": 100, "bounds": _box(*bounds), "group": kind == "group"}


LAYERS = [_layer(1, parent=None, kind="group"), _layer(40), _layer(41, visible=False)]
STATE_41 = {"id": "state_41", "type": "hidden_layer_state", "layer_ids": [41],
            "question": "Is hidden <layer 41> the disabled state of the button?",
            "options": ["state", "leftover"], "default": "state"}


def _cell(size=(60, 20), seed=0):
    w, hh = size
    a = np.zeros((hh, w, 4), dtype=np.uint8)
    a[..., 0] = (np.arange(w) * 7 + seed) % 256
    a[..., 1] = 120
    a[..., 2] = (np.arange(hh)[:, None] * 13) % 256
    a[..., 3] = 255
    return Image.fromarray(a, "RGBA")


def _panel(w=120, hh=80, edge=20):
    """A panel with distinct corners/edges and a flat centre (detect_nine_slice finds margins)."""
    a = np.zeros((hh, w, 4), dtype=np.uint8)
    a[..., 3] = 255
    a[..., 0] = 200
    for y in range(hh):
        for x in range(w):
            if x < edge or x >= w - edge or y < edge or y >= hh - edge:
                a[y, x, 1] = (x * 5 + y * 3) % 256
    return Image.fromarray(a, "RGBA")


def _confirm(path, note=None, decision=None, relock=False):
    with contextlib.redirect_stdout(io.StringIO()):
        return h.cmd_confirm(SimpleNamespace(manifest=str(path), note=note, relock=relock, decision=decision or []))


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **kw)


# ---------------------------------------------------------------------------
# A. exports before the user's answer
# ---------------------------------------------------------------------------

def _node(name, lid, state="disabled", image=True, **kw):
    n = {"name": name, "layer_id": lid, "select_ids": [lid], "component_id": "btn", "part_role": "base",
         "state": state, "default_state": "normal", "image": f"{name}.png" if image else None, "kind": "image"}
    n.update(kw)
    return n


def _answered(value="state", at="2026-11-13T00:23:38+0800"):
    d = dict(STATE_41, decision=value, decided_by="user", decided_at=at)
    return d, {"id": "state_41", "decision": value, "decided_at": at}


def _layout(nodes, decisions=(), log=(), started=None):
    return {"nodes": nodes, "reuse_decisions": list(decisions),
            "manifest_lock": {"locked": True, "decision_log": list(log)},
            "export": {"layers_at_open": LAYERS, "started": started}}


class TestExportedBeforeDecision(unittest.TestCase):
    def test_parse_time_forms(self):
        a = h.parse_time("2026-11-13T00:23:38+0800")
        b = h.parse_time("2026-11-12T16:23:38Z")
        c = h.parse_time("2026-11-12T16:23:38.500+00:00")
        self.assertEqual(a, b)
        self.assertLess(a, c)
        self.assertIsNone(h.parse_time("not a time"))
        self.assertIsNone(h.parse_time(None))

    def test_png_older_than_answer(self):
        d, rec = _answered()
        nodes = [_node("btn_bg_disabled", 41, exported_at="2026-11-12T16:05:03.000Z")]
        issues = h.exported_before_decision_issues(_layout(nodes, [d], [rec]))
        self.assertEqual(_types(issues), ["exported_before_decision"])
        e = issues[0]["entries"][0]
        self.assertEqual((e["name"], e["decision_id"], e["reason"]), ("btn_bg_disabled", "state_41", "png_older_than_decision"))

    def test_png_after_answer_is_fine(self):
        d, rec = _answered()
        nodes = [_node("btn_bg_disabled", 41, exported_at="2026-11-12T16:30:00.000Z")]
        self.assertEqual(h.exported_before_decision_issues(_layout(nodes, [d], [rec])), [])

    def test_log_started_is_the_fallback_time(self):
        d, rec = _answered()
        nodes = [_node("btn_bg_disabled", 41)]
        self.assertEqual(_types(h.exported_before_decision_issues(
            _layout(nodes, [d], [rec], started="2026-11-12T16:04:56.072Z"))), ["exported_before_decision"])
        self.assertEqual(h.exported_before_decision_issues(_layout(nodes, [d], [rec])), [], "no time: not judged")

    def test_relock_with_the_same_answer_keeps_the_first_time(self):
        d, rec = _answered()
        later = dict(rec, decided_at="2026-11-13T09:00:00+0800")
        self.assertEqual(h.decision_answered_at(d, [rec, later]), rec["decided_at"])
        changed = [{"id": "state_41", "decision": "leftover", "decided_at": "2026-11-13T00:10:00+0800"}, rec]
        self.assertEqual(h.decision_answered_at(d, changed), rec["decided_at"])
        nodes = [_node("btn_bg_disabled", 41, exported_at="2026-11-12T16:30:00.000Z")]
        self.assertEqual(h.exported_before_decision_issues(_layout(nodes, [d], [rec, later])), [])

    def test_skipped_while_pending_then_answered(self):
        d, rec = _answered()
        nodes = [_node("btn_bg_disabled", 41, export_skipped="pending_decision")]
        issues = h.exported_before_decision_issues(_layout(nodes, [d], [rec]))
        self.assertEqual(issues[0]["entries"][0]["reason"], "skipped_while_pending")
        d2, rec2 = _answered("leftover")
        self.assertEqual(h.exported_before_decision_issues(_layout(nodes, [d2], [rec2])), [])

    def test_unanswered_is_left_to_pending(self):
        nodes = [_node("btn_bg_disabled", 41, exported_at="2026-11-12T16:05:03.000Z")]
        self.assertEqual(h.exported_before_decision_issues(_layout(nodes, [STATE_41])), [])

    def test_bounds_only_reuse_instance_is_not_judged(self):
        d, rec = _answered()
        nodes = [_node("star_slot1", 40, state=None, image=True, reuse_of="star", reuse_source="star_slot1"),
                 _node("star_slot4", 41, image=True, reuse_of="star", reuse_source="star_slot1", bounds_only=True)]
        nodes[1]["image"] = "star.png"
        lay = _layout(nodes, [d], [rec], started="2026-11-12T16:04:56.072Z")
        self.assertEqual(h.exported_before_decision_issues(lay), [])
        self.assertEqual(h.hidden_layer_export_issues(dict(lay, reuse_decisions=[])), [],
                         "a bounds-only instance draws the source PNG, not its hidden layer")

    def test_full_render_older_than_an_exclusion(self):
        d = {"id": "inc_40", "type": "layer_inclusion", "layer_ids": [40], "options": ["include", "exclude"],
             "decision": "exclude", "decided_by": "user", "decided_at": "2026-11-13T00:23:38+0800"}
        rec = {"id": "inc_40", "decision": "exclude", "decided_at": d["decided_at"]}
        issues = h.exported_before_decision_issues(_layout([], [d], [rec], started="2026-11-12T16:00:00Z"))
        self.assertEqual(issues[0]["entries"][0]["reason"], "full_render_older_than_decision")


class TestExporterSkipsPending(unittest.TestCase):
    def test_exporter_text(self):
        text = SHIPPED.read_text(encoding="utf-8")
        self.assertIn('const EXPORTER_RELEASE = "%s";' % h.SKILL_VERSION, text)
        self.assertIn("skipped_pending_decision: []", text)
        self.assertIn("hiddenAtOpen.has(id) && pendingByLayer.has(Number(id))", text)
        self.assertIn("|| skipPending)", text)
        self.assertIn("item.exported_at = new Date().toISOString();", text)
        self.assertIn("Python text mode", text)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_exporter_parses(self):
        code = ("const src=require('fs').readFileSync(process.argv[1],'utf8');"
                "new Function('require','return (async()=>{'+src+'\\n})');")
        r = subprocess.run(["node", "-e", code, str(SHIPPED)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestSkippedSliceEndToEnd(unittest.TestCase):
    def _run(self, answers=(), skipped=True, exported_at=None):
        tmp = Path(tempfile.mkdtemp())
        slices = tmp / "slices"
        slices.mkdir()
        comp = {"id": "btn", "type": "button", "default_state": "normal",
                "states": {"normal": {"parts": [{"name": "btn_bg_normal", "role": "base", "kind": "image",
                                                 "layer_id": 40, "logical_bounds": [0, 0, 60, 20]}]},
                           "disabled": {"from_hidden": True,
                                        "parts": [{"name": "btn_bg_disabled", "role": "base", "kind": "image",
                                                   "layer_id": 41, "logical_bounds": [0, 0, 60, 20]}]}}}
        m = {"schema": h.SCHEMA_MANIFEST, "status": "pending_review",
             "source": {"artboard": {"id": 1, "name": "<artboard>", "bounds": [0, 0, 60, 20], "size": [60, 20]}},
             "components": [comp], "reuse_decisions": [dict(STATE_41)]}
        h.flatten_components(m)
        mp = tmp / "manifest.json"
        mp.write_text(json.dumps(m), encoding="utf-8")
        _confirm(mp, note="清单确认")
        if answers:
            _confirm(mp, decision=list(answers))
        img = _cell()
        items = []
        for e in m["entries"]:
            it = {"name": e["name"], "ok": True, "out_size": [60, 20], "visible_bounds_from_trim_insets": True,
                  "visible_bounds": _box(0, 0, 60, 20), "logical_bounds": _box(0, 0, 60, 20)}
            if e["layer_id"] == 41 and skipped:
                it["skipped_pending_decision"] = {"layer_ids": [41], "decision_ids": ["state_41"]}
            else:
                img.save(slices / f"{e['name']}.png")
                it["exported_at"] = exported_at or "2099-01-01T00:00:00.000Z"
            items.append(it)
        log = {"ok": True, "started": "2026-11-12T16:04:56.072Z",
               "exporter": {"release": h.SKILL_VERSION, "sha256": h.shipped_exporter_sha256(SHIPPED)},
               "layers_at_open": LAYERS, "items": items, "full_render": {"ok": True, "hidden_at_render": []},
               "skipped_pending_decision": [{"name": "btn_bg_disabled", "layer_ids": [41]}] if skipped else []}
        lp = tmp / "export_log.json"
        lp.write_text(json.dumps(log), encoding="utf-8")
        out = tmp / "layout.json"
        _quiet(h.cmd_build_layout, SimpleNamespace(manifest=str(mp), export_log=str(lp), slices_dir=str(slices),
                                                   out=str(out), group_effects=None))
        img.save(tmp / "full_render.png")
        rc = _quiet(h.cmd_validate, SimpleNamespace(layout=str(out), slices_dir=str(slices),
                                                    full_render=str(tmp / "full_render.png"),
                                                    out_dir=str(tmp / "validate"), fonts_dir=None, group_effects=None))
        layout = json.loads(out.read_text(encoding="utf-8"))
        rep = json.loads((tmp / "validate" / "validate_report.json").read_text(encoding="utf-8"))
        return rc, rep, layout

    def test_pending_skip_is_reported_once(self):
        rc, rep, layout = self._run()
        node = next(n for n in layout["nodes"] if n["name"] == "btn_bg_disabled")
        self.assertEqual(node["export_skipped"], "pending_decision")
        self.assertEqual(layout["export"]["skipped_pending_decision"][0]["name"], "btn_bg_disabled")
        self.assertEqual(_types(rep["issues"]), ["pending_user_decision"])

    def test_answered_after_skip_needs_a_new_export(self):
        rc, rep, _ = self._run(answers=["state_41=state"])
        self.assertEqual(_types(rep["issues"]), ["exported_before_decision"])
        self.assertEqual(rep["issues"][0]["entries"][0]["reason"], "skipped_while_pending")

    def test_reexported_after_answer_is_clean(self):
        rc, rep, _ = self._run(answers=["state_41=state"], skipped=False)
        self.assertEqual(rep["issues"], [])
        self.assertTrue(rep["ok"])

    def test_old_png_after_answer(self):
        rc, rep, _ = self._run(answers=["state_41=state"], skipped=False, exported_at="2001-01-01T00:00:00Z")
        self.assertEqual(_types(rep["issues"]), ["exported_before_decision"])


# ---------------------------------------------------------------------------
# B. nine-slice data comes from the confirmed manifest
# ---------------------------------------------------------------------------

class TestLayoutNineSlice(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.png = self.tmp / "panel.png"
        _panel().save(self.png)
        self.detected = h.detect_nine_slice(Image.open(self.png))
        self.assertIsNotNone(self.detected)

    def test_image_is_never_promoted(self):
        kind, nine, src, det, filled = h.layout_nine_slice({"kind": "image", "nine_slice": None}, self.png)
        self.assertEqual((kind, nine, src, filled), ("image", None, None, []))
        self.assertEqual(det, self.detected, "detection is kept only as a suggestion")

    def test_approved_margins_are_kept(self):
        approved = {"left": 59, "top": 40, "right": 58, "bottom": 40}
        kind, nine, src, _det, filled = h.layout_nine_slice({"kind": "nine_slice", "nine_slice": approved}, self.png)
        self.assertEqual((kind, nine, src, filled), ("nine_slice", approved, "manifest", []))

    def test_empty_margins_are_filled_and_recorded(self):
        kind, nine, src, _det, filled = h.layout_nine_slice({"kind": "nine_slice", "nine_slice": None}, self.png)
        self.assertEqual((kind, nine, src), ("nine_slice", self.detected, "auto_detected"))
        self.assertEqual(filled, ["left", "top", "right", "bottom"])
        part = {"left": 30, "top": None, "right": 30, "bottom": None}
        kind, nine, src, _det, filled = h.layout_nine_slice({"kind": "nine_slice", "nine_slice": part}, self.png)
        self.assertEqual((nine["left"], nine["right"]), (30, 30))
        self.assertEqual((nine["top"], nine["bottom"]), (self.detected["top"], self.detected["bottom"]))
        self.assertEqual((src, filled), ("manifest+auto_detected", ["top", "bottom"]))

    def test_text_has_no_nine(self):
        self.assertEqual(h.layout_nine_slice({"kind": "static_text"}, self.png)[1], None)

    def test_issues_against_confirmed_entries(self):
        confirmed = {"bg": {"name": "bg", "kind": "image", "nine_slice": None},
                     "header": {"name": "header", "kind": "nine_slice",
                                "nine_slice": {"left": 59, "top": 100, "right": 58, "bottom": 100}},
                     "panel": {"name": "panel", "kind": "nine_slice", "nine_slice": None}}
        layout = {"nodes": [
            {"name": "bg", "kind": "nine_slice", "nine_slice": {"left": 688, "top": 1835, "right": 377, "bottom": 271}},
            {"name": "header", "kind": "nine_slice", "nine_slice": {"left": 61, "top": 61, "right": 59, "bottom": 25}},
            {"name": "panel", "kind": "nine_slice", "nine_slice": {"left": 20, "top": 20, "right": 20, "bottom": 20},
             "nine_slice_source": "auto_detected"},
        ]}
        issues = h.nine_slice_issues(layout, confirmed)
        self.assertEqual(_types(issues), ["nine_slice_changed_after_confirm"])
        got = {e["name"]: e["reason"] for e in issues[0]["entries"]}
        self.assertEqual(got, {"bg": "kind_changed", "header": "margins_changed"})
        layout["nodes"][2].pop("nine_slice_source")
        self.assertIn("panel", {e["name"] for e in h.nine_slice_issues(layout, confirmed)[0]["entries"]})

    def test_margins_missing(self):
        issues = h.nine_slice_issues({"nodes": [{"name": "p", "kind": "nine_slice", "nine_slice": None}]}, {})
        self.assertEqual(_types(issues), ["nine_slice_margins_missing"])

    def test_build_layout_and_validate(self):
        tmp = self.tmp
        slices = tmp / "slices"
        slices.mkdir()
        parts = [{"name": "bg", "role": "bg", "kind": "image", "layer_id": 40, "logical_bounds": [0, 0, 120, 80]},
                 {"name": "frame", "role": "base", "kind": "nine_slice", "layer_id": 42,
                  "logical_bounds": [0, 0, 120, 80], "nine_slice": {"left": 33, "top": 0, "right": 33, "bottom": 0}}]
        m = {"schema": h.SCHEMA_MANIFEST, "status": "pending_review",
             "source": {"artboard": {"id": 1, "name": "<artboard>", "bounds": [0, 0, 120, 80], "size": [120, 80]}},
             "components": [{"id": "popup", "type": "frame", "shared": parts}], "reuse_decisions": []}
        h.flatten_components(m)
        mp = tmp / "manifest.json"
        mp.write_text(json.dumps(m), encoding="utf-8")
        _confirm(mp, note="清单确认")
        items = []
        for e in m["entries"]:
            _panel().save(slices / f"{e['name']}.png")
            items.append({"name": e["name"], "ok": True, "out_size": [120, 80], "visible_bounds_from_trim_insets": True,
                          "visible_bounds": _box(0, 0, 120, 80), "logical_bounds": _box(0, 0, 120, 80)})
        layers = [_layer(1, parent=None, kind="group"), _layer(40, bounds=(0, 0, 120, 80)),
                  _layer(42, bounds=(0, 0, 120, 80))]
        lp = tmp / "export_log.json"
        lp.write_text(json.dumps({"ok": True, "items": items, "layers_at_open": layers,
                                  "exporter": {"release": h.SKILL_VERSION, "sha256": h.shipped_exporter_sha256(SHIPPED)},
                                  "full_render": {"ok": True, "hidden_at_render": []}}), encoding="utf-8")
        out = tmp / "layout.json"
        _quiet(h.cmd_build_layout, SimpleNamespace(manifest=str(mp), export_log=str(lp), slices_dir=str(slices),
                                                   out=str(out), group_effects=None))
        layout = json.loads(out.read_text(encoding="utf-8"))
        by = {n["name"]: n for n in layout["nodes"]}
        self.assertEqual((by["bg"]["kind"], by["bg"]["nine_slice"]), ("image", None))
        self.assertEqual(by["bg"]["nine_slice_detected"], self.detected)
        self.assertEqual(by["frame"]["nine_slice"], {"left": 33, "top": 0, "right": 33, "bottom": 0})
        self.assertEqual(by["frame"]["nine_slice_source"], "manifest")
        _panel().save(tmp / "full_render.png")

        def validate(path):
            _quiet(h.cmd_validate, SimpleNamespace(layout=str(path), slices_dir=str(slices),
                                                   full_render=str(tmp / "full_render.png"),
                                                   out_dir=str(tmp / "validate"), fonts_dir=None, group_effects=None))
            return json.loads((tmp / "validate" / "validate_report.json").read_text(encoding="utf-8"))

        rep = validate(out)
        self.assertNotIn("nine_slice_changed_after_confirm", _types(rep["issues"]))
        # a 1.4.14-style layout (detection overwrote the confirmed data) is caught
        for n in layout["nodes"]:
            n["kind"], n["nine_slice"] = "nine_slice", dict(self.detected)
        bad = tmp / "layout_old.json"
        bad.write_text(json.dumps(layout), encoding="utf-8")
        rep = validate(bad)
        issue = next(i for i in rep["issues"] if i["type"] == "nine_slice_changed_after_confirm")
        self.assertEqual({e["name"]: e["reason"] for e in issue["entries"]},
                         {"bg": "kind_changed", "frame": "margins_changed"})


# ---------------------------------------------------------------------------
# C. paragraph (box) text starts at the click point
# ---------------------------------------------------------------------------

def _box_text(offset=(140.0, 1343.0), width=800.0, height=104.0, click=(140.0, 1223.0), align="center"):
    attrs = _box_attrs("Enjoying the game? Tap a star to rate us !", width, height, size=48.0)
    attrs["box_bounds_pt"] = {"left": offset[0], "top": offset[1], "right": offset[0] + width,
                              "bottom": offset[1] + height}
    attrs["click_point"] = {"horizontal": click[0] / 1080 * 100, "vertical": click[1] / 2340 * 100}
    for p in attrs.get("paragraphs") or []:
        p["align"] = align
    attrs["alignment"] = align
    return attrs


class TestParagraphBoxOrigin(unittest.TestCase):
    def _glyphs(self, attrs, bounds):
        spec = T.text_spec(attrs, doc_size=(1080, 2340), bounds=bounds)
        shaper = InkShaper()
        glyphs, _f, _a = T.layout_glyphs(spec, lambda _p: shaper, resolve_font=FAKE_FONT)
        pts = [T.to_device(spec, g["x"], g["y"]) for g in glyphs]
        return spec, pts

    def test_box_offset_is_not_added_to_the_click_point(self):
        spec, pts = self._glyphs(_box_text(), _box(171, 1223, 909, 1318))
        self.assertEqual(spec["box_origin"], "click_point")
        self.assertAlmostEqual(spec["anchor"][0], 140.0, places=3)
        self.assertAlmostEqual(spec["anchor"][1], 1223.0, places=3)
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        self.assertGreaterEqual(min(xs), 140.0 - 1e-6)
        self.assertLessEqual(max(xs), 940.0)
        self.assertTrue(1223 < min(ys) < 1318, ys[:3])   # first baseline inside the layer box, on the canvas
        self.assertLess(max(ys), 2340)

    def test_zero_offset_box_is_unchanged(self):
        spec_a, pts_a = self._glyphs(_box_text(offset=(0.0, 0.0)), _box(171, 1223, 909, 1318))
        spec_b, pts_b = self._glyphs(_box_text(), _box(171, 1223, 909, 1318))
        self.assertEqual(pts_a, pts_b)

    def test_box_missing_the_layer_bounds_is_placed_from_them(self):
        spec, pts = self._glyphs(_box_text(click=(900.0, 100.0)), _box(171, 1223, 909, 1318))
        self.assertEqual((spec["box_origin"], spec["anchor_from"]), ("bounds", "bounds_box"))
        self.assertAlmostEqual(spec["anchor"][0], 540.0 - 400.0, places=3)
        self.assertAlmostEqual(spec["anchor"][1], 1223.0, places=3)
        self.assertTrue(all(1223 <= p[1] <= 1318 + 48 for p in pts))

    def test_point_text_is_untouched(self):
        attrs = _box_text()
        attrs["shape"] = "point"
        spec = T.text_spec(attrs, doc_size=(1080, 2340), bounds=_box(171, 1223, 909, 1318))
        self.assertIsNone(spec["box_origin"])


# ---------------------------------------------------------------------------
# D / E. exporter copy note; CJK label font
# ---------------------------------------------------------------------------

def _cjk_font():
    for p in h.CJK_LABEL_FONTS:
        if Path(p).is_file() and h._font_has_cjk(p):
            return p
    return None


class TestLabelFont(unittest.TestCase):
    def tearDown(self):
        h.LABEL_FONT["resolved"] = False

    @unittest.skipUnless(_cjk_font(), "no CJK font on this machine")
    def test_configured_font_is_used(self):
        path = _cjk_font()
        info = h.resolve_label_font(path, force=True)
        self.assertTrue(h.same_file_path(info["path"], path), (info["path"], path))
        self.assertEqual((info["cjk"], info["warning"]), (True, None))
        rep = h.label_font_report(["弹窗_五星好评", "popup_bg"])
        self.assertTrue(rep["labels_need_cjk"])
        self.assertNotIn("warning", rep)

    def test_no_cjk_font_warns_but_still_draws(self):
        with mock.patch.object(h, "CJK_LABEL_FONTS", ()), mock.patch.object(h, "_font_dirs", lambda: []), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            info = h.resolve_label_font("/nonexistent/font.ttf", force=True)
            self.assertFalse(info["cjk"])
            self.assertIn("CJK", info["warning"])
            self.assertIn("CJK", err.getvalue())
            rep = h.label_font_report(["弹窗"])
            self.assertTrue(rep["labels_need_cjk"])
            self.assertIn("warning", rep)
            tmp = Path(tempfile.mkdtemp())
            h.annotate_preview(_cell(), [{"name": "弹窗_bg", "kind": "image", "logical_bounds": [0, 0, 30, 10]}],
                               (0, 0), tmp / "p.png", "弹窗_五星好评")
            self.assertTrue((tmp / "p.png").is_file())

    def test_ascii_labels_do_not_need_cjk(self):
        self.assertFalse(h.label_font_report(["popup_bg", "rate"])["labels_need_cjk"])


# ---------------------------------------------------------------------------
# F. cumulative / ordinal groups
# ---------------------------------------------------------------------------

def _stars(n=5, lit=3, cumulative=True, explicit=None):
    slots = []
    for i in range(1, n + 1):
        slots.append({"id": f"slot{i}", "type": "rating_slot", "default_state": "filled" if i <= lit else "empty",
                      "shared": [{"name": f"star_empty_slot{i}", "role": "base", "layer_id": 100 + i}],
                      "states": {"filled": {"parts": [{"name": f"star_full_slot{i}", "role": "highlight",
                                                       "layer_id": 200 + i}]}}})
    group = {"id": "stars", "type": "rating", "children": slots}
    if cumulative:
        group["cumulative"] = True
    if explicit:
        group["states"] = explicit
    return group


class TestCumulativeStates(unittest.TestCase):
    def test_spec_and_levels(self):
        sp = h.cumulative_spec(_stars())
        self.assertEqual((sp["on_state"], sp["off_state"], sp["default_level"]), ("filled", "empty", 3))
        self.assertEqual(sp["levels"]["level_5"], {f"slot{i}": "filled" for i in range(1, 6)})
        self.assertEqual(sp["levels"]["level_4"]["slot4"], "filled")
        self.assertEqual(sp["levels"]["level_4"]["slot5"], "empty")
        self.assertIsNone(h.cumulative_spec(_stars(cumulative=False)), "not declared: nothing is guessed")

    def test_members_order_and_explicit_names(self):
        g = _stars()
        g["members"] = ["slot5", "slot4", "slot3", "slot2", "slot1"]
        g["cumulative_state"] = "filled"
        g["off_state"] = "dim"
        sp = h.cumulative_spec(g)
        self.assertEqual(sp["levels"]["level_1"], {"slot5": "filled", "slot4": "dim", "slot3": "dim",
                                                   "slot2": "dim", "slot1": "dim"})
        self.assertIsNone(sp["default_level"], "lit slots are not a prefix of this order")

    def test_previews_are_whole_levels(self):
        layout = {"components": [_stars()]}
        prev = {p["id"]: p["activation"] for p in h.collect_alternate_activations(layout)}
        self.assertNotIn("slot5_filled", prev)
        self.assertNotIn("stars_level_3", prev, "the default level is the default render")
        self.assertEqual(prev["stars_level_5"]["slot4"], "filled")
        self.assertEqual(sorted(prev), ["stars_level_0", "stars_level_1", "stars_level_2", "stars_level_4", "stars_level_5"])
        old = {p["id"] for p in h.collect_alternate_activations({"components": [_stars(cumulative=False)]})}
        self.assertIn("slot5_filled", old)

    def test_expanded_states_written(self):
        comps = [_stars()]
        self.assertEqual(h.expand_cumulative_states(comps), 1)
        self.assertEqual(comps[0]["expanded_states"]["level_5"]["activation"]["slot4"], "filled")
        self.assertEqual(comps[0]["cumulative_expanded"]["default_level"], 3)

    def test_incomplete_explicit_activation(self):
        bad = _stars(explicit={"rating5": {"activation": {"slot5": "filled"}}})
        issues = h.state_activation_issues({"components": [bad]})
        self.assertEqual(_types(issues), ["state_activation_incomplete"])
        self.assertEqual(issues[0]["entries"][0]["lower_members_off"], ["slot4"])
        good = _stars(explicit={"rating5": {"activation": {"slot4": "filled", "slot5": "filled"}}})
        self.assertEqual(h.state_activation_issues({"components": [good]}), [])
        undeclared = _stars(cumulative=False, explicit={"rating5": {"activation": {"slot5": "filled"}}})
        self.assertEqual(h.state_activation_issues({"components": [undeclared]}), [])

    def test_explicit_activation_is_previewed_in_full(self):
        g = _stars(explicit={"rating5": {"activation": {"slot4": "filled", "slot5": "filled"}}})
        prev = {p["id"]: p["activation"] for p in h.collect_alternate_activations({"components": [g]})}
        self.assertEqual(prev["stars_rating5"]["slot4"], "filled")
        nodes = [{"name": "full4", "component_id": "slot4", "state": "filled", "default": False},
                 {"name": "empty4", "component_id": "slot4", "state": None, "default": True},
                 {"name": "full5", "component_id": "slot5", "state": "filled", "default": False}]
        picked = [n["name"] for n in h.nodes_for_activation({"nodes": nodes}, prev["stars_level_4"])]
        self.assertEqual(picked, ["full4", "empty4"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
