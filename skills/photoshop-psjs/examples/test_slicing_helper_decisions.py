#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the 1.4.12 guards (synthetic data):

- an open question for the user is a structured decision; a plain confirm note does not answer it
  (pending_user_decision, confirm's not_user_decided, decision_log in the lock);
- a layer visible when the exporter opened the document but hidden for the full render without a recorded user
  exclusion (visible_layer_hidden), a visible pixel layer no entry covers (visible_layer_unassigned);
- an exporter that is not the shipped examples/export_slices.psjs (exporter_modified);
- multi-layer entries (select_ids) compared on their union box, not on the layer_id box (layer_bounds_mismatch).

    python test_slicing_helper_decisions.py
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

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import slicing_helper as h  # noqa: E402

SHIPPED = HERE / "export_slices.psjs"


def _box(l, t, r, b):
    return {"left": l, "top": t, "right": r, "bottom": b}


def _confirm(path, note=None, decision=None, relock=False):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = h.cmd_confirm(SimpleNamespace(manifest=str(path), note=note, relock=relock, decision=decision or []))
    return rc, json.loads(buf.getvalue())


def _cell(size=(30, 12), seed=0):
    w, hh = size
    a = np.zeros((hh, w, 4), dtype=np.uint8)
    a[..., 0] = (np.arange(w) * 7 + seed) % 256
    a[..., 1] = 120
    a[..., 2] = (np.arange(hh)[:, None] * 13) % 256
    a[..., 3] = 255
    return Image.fromarray(a, "RGBA")


def _layer(lid, parent=None, kind="pixel", visible=True, bounds=(0, 0, 10, 10), effective=None, opacity=100):
    return {"id": lid, "name": f"<layer {lid}>", "parent_id": parent, "kind": kind, "visible": visible,
            "effective_visible": visible if effective is None else effective, "opacity": opacity,
            "bounds": _box(*bounds) if bounds else None, "group": kind == "group"}


def _good_exporter():
    return {"release": h.SKILL_VERSION, "sha256": h.shipped_exporter_sha256(SHIPPED), "script_path": None}


# ---------------------------------------------------------------------------
# C. multi-layer entries
# ---------------------------------------------------------------------------

class TestMultiLayerBounds(unittest.TestCase):
    ENTRY = {"name": "row_bg", "layer_id": 21, "select_ids": [21, 22], "logical_bounds": [0, 0, 60, 12]}

    def test_legacy_log_subbox_inside_union_is_not_a_mismatch(self):
        item = {"logical_bounds": _box(0, 0, 30, 12)}  # only layer 21 measured
        self.assertEqual(h.entry_layer_bounds_conflict(self.ENTRY, item), 0)

    def test_union_from_new_log_is_compared(self):
        item = {"logical_bounds": _box(0, 0, 30, 12), "logical_bounds_union": _box(0, 0, 60, 12)}
        self.assertEqual(h.entry_layer_bounds_conflict(self.ENTRY, item), 0)
        item = {"logical_bounds": _box(0, 0, 30, 12),
                "select_bounds": [{"id": 21, "logical": _box(0, 0, 30, 12)}, {"id": 22, "logical": _box(30, 0, 61, 12)}]}
        self.assertEqual(h.entry_layer_bounds_conflict(self.ENTRY, item), 0)

    def test_union_elsewhere_is_still_a_mismatch(self):
        item = {"logical_bounds": _box(60, 0, 90, 12), "logical_bounds_union": _box(60, 0, 120, 12)}
        self.assertEqual(h.entry_layer_bounds_conflict(self.ENTRY, item), 60)

    def test_sibling_layer_id_on_multi_entry_is_still_a_mismatch(self):
        item = {"logical_bounds": _box(60, 0, 90, 12)}  # legacy log, box outside the manifest box
        self.assertEqual(h.entry_layer_bounds_conflict(self.ENTRY, item), 60)

    def test_single_layer_entry_keeps_the_strict_rule(self):
        entry = {"name": "a", "layer_id": 21, "logical_bounds": [0, 0, 60, 12]}
        self.assertEqual(h.entry_layer_bounds_conflict(entry, {"logical_bounds": _box(0, 0, 30, 12)}), 30)

    def test_build_layout_and_validate(self):
        tmp = Path(tempfile.mkdtemp())
        slices = tmp / "slices"
        slices.mkdir()
        m = {"schema": h.SCHEMA_MANIFEST, "status": "confirmed",
             "source": {"artboard": {"id": 1, "name": "<artboard>", "bounds": [0, 0, 90, 20], "size": [90, 20]}},
             "components": [{"id": "list", "type": "list", "shared": [
                 {"name": "row_bg", "role": "base", "kind": "image", "layer_id": 21, "select_ids": [21, 22],
                  "logical_bounds": [0, 0, 60, 12]},
                 {"name": "row_bg_copy", "role": "base", "kind": "image", "layer_id": 21, "select_ids": [21, 22],
                  "logical_bounds": [60, 0, 90, 12], "image_name": "row_bg"}]}],
             "reuse_decisions": []}
        h.flatten_components(m)
        mp = tmp / "manifest.json"
        mp.write_text(json.dumps(m), encoding="utf-8")
        _confirm(mp, note="test")
        _cell((60, 12)).save(slices / "row_bg.png")
        it = {"ok": True, "out_size": [60, 12], "visible_bounds_from_trim_insets": True,
              "visible_bounds": _box(0, 0, 60, 12), "logical_bounds": _box(0, 0, 30, 12)}
        log = {"ok": True, "items": [dict(it, name="row_bg"), dict(it, name="row_bg_copy")]}
        lp = tmp / "export_log.json"
        lp.write_text(json.dumps(log), encoding="utf-8")
        out = tmp / "layout.json"
        with contextlib.redirect_stdout(io.StringIO()):
            h.cmd_build_layout(SimpleNamespace(manifest=str(mp), export_log=str(lp), slices_dir=str(slices),
                                               out=str(out), group_effects=None))
        nodes = {n["name"]: n for n in json.loads(out.read_text(encoding="utf-8"))["nodes"]}
        self.assertNotIn("layer_bounds_mismatch", nodes["row_bg"])
        self.assertIn("layer_bounds_mismatch", nodes["row_bg_copy"])


# ---------------------------------------------------------------------------
# A. structured decisions; a confirm note is not an answer
# ---------------------------------------------------------------------------

def _decision_manifest():
    return {
        "schema": h.SCHEMA_MANIFEST, "status": "pending_review",
        "source": {"artboard": {"id": 1, "name": "<artboard>", "bounds": [0, 0, 60, 20], "size": [60, 20]}},
        "components": [], "entries": [],
        "reuse_decisions": [
            {"id": "keep_layer_31", "type": "layer_inclusion", "layer_ids": [31],
             "question": "Is <layer 31> part of the current design?", "options": ["include", "exclude"],
             "default": "exclude", "decision": "exclude"},  # agent copied its default: not an answer
            {"id": "state_32", "type": "hidden_layer_state", "layer_ids": [32],
             "question": "Is hidden <layer 32> an alternate state?", "options": ["state", "leftover"],
             "default": "state"},
            {"id": "reuse_rows", "type": "reuse", "instances": ["a", "b"], "default": "reuse"},
        ],
    }


class TestConfirmDecisions(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.m = self.tmp / "manifest.json"
        self.m.write_text(json.dumps(_decision_manifest()), encoding="utf-8")

    def test_note_locks_but_answers_nothing(self):
        rc, out = _confirm(self.m, note="清单确认")
        self.assertEqual(rc, 0)
        data = json.loads(self.m.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "confirmed")
        self.assertTrue(h.manifest_lock_state(self.m)["locked"])
        for d in data["reuse_decisions"]:
            self.assertNotEqual(d.get("decided_by"), "user")
        pend = {p["id"]: p for p in out["not_user_decided"]}
        self.assertEqual(set(pend), {"keep_layer_31", "state_32", "reuse_rows"})
        self.assertEqual(pend["keep_layer_31"]["type"], "layer_inclusion")
        self.assertTrue(pend["keep_layer_31"]["blocking"])
        self.assertFalse(pend["reuse_rows"]["blocking"])
        self.assertEqual(h.manifest_lock_state(self.m)["decision_log"], [])

    def test_decision_flag_records_the_answer_and_the_log(self):
        _confirm(self.m, note="清单确认")
        rc, out = _confirm(self.m, decision=["keep_layer_31=exclude"])
        self.assertEqual(rc, 0)
        d = {x["id"]: x for x in json.loads(self.m.read_text(encoding="utf-8"))["reuse_decisions"]}
        self.assertEqual(d["keep_layer_31"]["decided_by"], "user")
        self.assertEqual({p["id"] for p in out["not_user_decided"]}, {"state_32", "reuse_rows"})
        log = h.manifest_lock_state(self.m)["decision_log"]
        self.assertEqual([(r["id"], r["decision"]) for r in log], [("keep_layer_31", "exclude")])
        # the log survives a later relock
        _confirm(self.m, decision=["state_32=state"])
        self.assertEqual(len(h.manifest_lock_state(self.m)["decision_log"]), 2)

    def test_pending_user_decision_issues(self):
        _confirm(self.m, note="清单确认")
        lock = h.manifest_lock_state(self.m)
        decs = json.loads(self.m.read_text(encoding="utf-8"))["reuse_decisions"]
        issues = h.user_decision_issues(decs, lock)
        self.assertEqual(sorted(i["id"] for i in issues), ["keep_layer_31", "state_32"])
        self.assertTrue(all(i["type"] == "pending_user_decision" for i in issues))  # reuse keeps its own checks
        _confirm(self.m, decision=["keep_layer_31=exclude", "state_32=leftover"])
        lock = h.manifest_lock_state(self.m)
        decs = json.loads(self.m.read_text(encoding="utf-8"))["reuse_decisions"]
        self.assertEqual(h.user_decision_issues(decs, lock), [])

    def test_hand_written_decided_by_user_is_not_an_answer(self):
        data = _decision_manifest()
        data["reuse_decisions"][0].update(decided_by="user")  # written by the agent, not by confirm --decision
        self.m.write_text(json.dumps(data), encoding="utf-8")
        _confirm(self.m, note="清单确认")
        lock = h.manifest_lock_state(self.m)
        issues = [i for i in h.user_decision_issues(data["reuse_decisions"], lock) if i["id"] == "keep_layer_31"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["reason"], "decided_by_user_not_recorded_by_confirm")
        self.assertEqual(h.approved_exclusions(data["reuse_decisions"], lock), {})

    def test_affects_field_makes_any_type_blocking(self):
        d = {"id": "x", "type": "custom_question", "affects": "truth", "default": "a"}
        self.assertTrue(h.decision_is_blocking(d))
        self.assertEqual([i["id"] for i in h.user_decision_issues([d], {"decision_log": []})], ["x"])


# ---------------------------------------------------------------------------
# B. visible layers removed from the truth; exporter identity
# ---------------------------------------------------------------------------

def _layout(layers, hidden=(), decisions=(), decision_log=(), nodes=None, exporter="good"):
    return {
        "source": {"artboard": {"id": 1, "name": "<artboard>", "bounds": [0, 0, 60, 20], "size": [60, 20]}},
        "nodes": nodes if nodes is not None else [{"name": "bg", "layer_id": 30, "select_ids": None}],
        "reuse_decisions": list(decisions),
        "manifest_lock": {"locked": True, "decision_log": list(decision_log)},
        "export": {"exporter": _good_exporter() if exporter == "good" else exporter,
                   "layers_at_open": layers,
                   "full_render_hidden": [{"id": i, "name": f"<layer {i}>"} for i in hidden]},
    }


BASE_LAYERS = [
    _layer(1, kind="group", bounds=(0, 0, 60, 20)),
    _layer(30, parent=1, bounds=(0, 0, 60, 20)),
    _layer(31, parent=1, bounds=(5, 5, 50, 15)),
]
EXCLUDE_31 = {"id": "keep_layer_31", "type": "layer_inclusion", "layer_ids": [31], "default": "include",
              "decision": "exclude", "decided_by": "user"}
LOG_31 = {"id": "keep_layer_31", "decision": "exclude"}


def _types(issues):
    return sorted(i["type"] for i in issues)


class TestVisibleLayerHidden(unittest.TestCase):
    def test_hidden_without_decision(self):
        issues = h.export_truth_issues(_layout(BASE_LAYERS, hidden=[31]), SHIPPED)
        self.assertIn("visible_layer_hidden", _types(issues))
        bad = next(i for i in issues if i["type"] == "visible_layer_hidden")
        self.assertEqual([x["id"] for x in bad["layers"]], [31])

    def test_hidden_with_recorded_user_exclusion(self):
        issues = h.export_truth_issues(_layout(BASE_LAYERS, hidden=[31], decisions=[EXCLUDE_31],
                                               decision_log=[LOG_31]), SHIPPED)
        self.assertEqual(issues, [])

    def test_default_copied_or_unlogged_decision_does_not_allow_hiding(self):
        d = dict(EXCLUDE_31, decided_by=None)
        issues = h.export_truth_issues(_layout(BASE_LAYERS, hidden=[31], decisions=[d]), SHIPPED)
        self.assertIn("visible_layer_hidden", _types(issues))
        issues = h.export_truth_issues(_layout(BASE_LAYERS, hidden=[31], decisions=[EXCLUDE_31]), SHIPPED)
        self.assertIn("visible_layer_hidden", _types(issues))

    def test_include_decision_does_not_allow_hiding(self):
        d = dict(EXCLUDE_31, decision="include")
        issues = h.export_truth_issues(_layout(BASE_LAYERS, hidden=[31], decisions=[d],
                                               decision_log=[{"id": "keep_layer_31", "decision": "include"}]), SHIPPED)
        self.assertIn("visible_layer_hidden", _types(issues))


class TestVisibleLayerUnassigned(unittest.TestCase):
    def test_uncovered_visible_pixel_layer(self):
        issues = h.export_truth_issues(_layout(BASE_LAYERS), SHIPPED)
        self.assertEqual(_types(issues), ["visible_layer_unassigned"])
        self.assertEqual([x["id"] for x in issues[0]["layers"]], [31])

    def test_covered_by_parent_group_entry(self):
        nodes = [{"name": "all", "layer_id": 1}]
        self.assertEqual(h.export_truth_issues(_layout(BASE_LAYERS, nodes=nodes), SHIPPED), [])

    def test_covered_by_select_ids(self):
        nodes = [{"name": "bg", "layer_id": 30, "select_ids": [30, 31]}]
        self.assertEqual(h.export_truth_issues(_layout(BASE_LAYERS, nodes=nodes), SHIPPED), [])

    def test_excluded_by_user(self):
        issues = h.export_truth_issues(_layout(BASE_LAYERS, decisions=[EXCLUDE_31], decision_log=[LOG_31]), SHIPPED)
        self.assertEqual(issues, [])

    def test_hidden_empty_outside_and_transparent_layers_are_ignored(self):
        layers = BASE_LAYERS[:2] + [
            _layer(31, parent=1, visible=False),
            _layer(32, parent=1, bounds=(0, 0, 0, 0)),
            _layer(33, parent=1, bounds=(100, 100, 120, 120)),
            _layer(34, parent=1, opacity=0),
            _layer(35, parent=1, kind="levels"),
            _layer(36, kind="pixel", bounds=(0, 0, 60, 20)),  # not under the artboard
            _layer(37, parent=1, kind="group", bounds=(0, 0, 10, 10)),
            _layer(38, parent=37, visible=True, effective=False),  # inside a hidden group
        ]
        self.assertEqual(h.export_truth_issues(_layout(layers), SHIPPED), [])


class TestExporterModified(unittest.TestCase):
    def test_missing_record(self):
        lay = _layout(BASE_LAYERS, nodes=[{"name": "all", "layer_id": 1}], exporter=None)
        self.assertEqual(_types(h.export_truth_issues(lay, SHIPPED)), ["exporter_modified"])

    def test_missing_sha(self):
        lay = _layout(BASE_LAYERS, nodes=[{"name": "all", "layer_id": 1}],
                      exporter={"release": h.SKILL_VERSION, "sha256": None, "error": "exporter_url not set"})
        self.assertEqual(_types(h.export_truth_issues(lay, SHIPPED)), ["exporter_modified"])

    def test_wrong_sha(self):
        lay = _layout(BASE_LAYERS, nodes=[{"name": "all", "layer_id": 1}],
                      exporter={"release": h.SKILL_VERSION, "sha256": "0" * 64})
        self.assertEqual(_types(h.export_truth_issues(lay, SHIPPED)), ["exporter_modified"])

    def test_no_visibility_record(self):
        lay = _layout(None, nodes=[{"name": "all", "layer_id": 1}])
        self.assertEqual(_types(h.export_truth_issues(lay, SHIPPED)), ["exporter_modified"])

    def test_script_path_rehashed(self):
        tmp = Path(tempfile.mkdtemp())
        text = SHIPPED.read_text(encoding="utf-8")
        job = tmp / "export_slices.psjs"
        # a job copy that only points CONFIG_URL elsewhere (CRLF too) is still the shipped exporter
        job.write_bytes(text.replace('const CONFIG_URL = "file:/D:/work/ps-job/export_config.json";',
                                     'const CONFIG_URL = "file:/E:/other/job/cfg.json";').replace("\n", "\r\n").encode())
        ex = dict(_good_exporter(), script_path=str(job))
        lay = _layout(BASE_LAYERS, nodes=[{"name": "all", "layer_id": 1}], exporter=ex)
        self.assertEqual(h.export_truth_issues(lay, SHIPPED), [])
        # copy-and-edit: the reported hash still claims the shipped one, the file on disk does not
        job.write_text(text.replace("for (const layer of tmp.layers) layer.visible = true;",
                                    "for (const layer of tmp.layers) layer.visible = layer.id !== 31;", 1),
                       encoding="utf-8")
        self.assertEqual(_types(h.export_truth_issues(lay, SHIPPED)), ["exporter_modified"])

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_hash_matches_python(self):
        text = SHIPPED.read_text(encoding="utf-8")
        start, end = text.index("// <sha256>"), text.index("// </sha256>")
        js = text[start:end] + "\nconst fs_ = require('fs');\nprocess.stdout.write(exporterSha256(fs_.readFileSync(process.argv[2], 'utf8')));\n"
        tmp = Path(tempfile.mkdtemp()) / "h.js"
        tmp.write_text(js, encoding="utf-8")
        out = subprocess.run(["node", str(tmp), str(SHIPPED)], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), h.shipped_exporter_sha256(SHIPPED))


class TestValidateEndToEnd(unittest.TestCase):
    """build-layout copies the exporter record; validate turns the new guards into issues and ok:false."""

    def _run(self, hidden=(), decision=None):
        tmp = Path(tempfile.mkdtemp())
        slices = tmp / "slices"
        slices.mkdir()
        m = {"schema": h.SCHEMA_MANIFEST, "status": "pending_review",
             "source": {"artboard": {"id": 1, "name": "<artboard>", "bounds": [0, 0, 60, 20], "size": [60, 20]}},
             "components": [{"id": "panel", "type": "panel", "shared": [
                 {"name": "bg", "role": "base", "kind": "image", "layer_id": 30, "logical_bounds": [0, 0, 60, 20]}]}],
             "reuse_decisions": [{"id": "keep_layer_31", "type": "layer_inclusion", "layer_ids": [31],
                                  "question": "current design or old draft?", "options": ["include", "exclude"],
                                  "default": "include"}]}
        h.flatten_components(m)
        mp = tmp / "manifest.json"
        mp.write_text(json.dumps(m), encoding="utf-8")
        _confirm(mp, note="清单确认")
        if decision:
            _confirm(mp, decision=[f"keep_layer_31={decision}"])
        bg = _cell((60, 20))
        bg.save(slices / "bg.png")
        log = {"ok": True, "exporter": _good_exporter(), "layers_at_open": BASE_LAYERS,
               "items": [{"name": "bg", "ok": True, "out_size": [60, 20], "visible_bounds_from_trim_insets": True,
                          "visible_bounds": _box(0, 0, 60, 20), "logical_bounds": _box(0, 0, 60, 20)}],
               "full_render": {"ok": True, "hidden_at_render": [{"id": i, "name": "x"} for i in hidden]}}
        lp = tmp / "export_log.json"
        lp.write_text(json.dumps(log), encoding="utf-8")
        out = tmp / "layout.json"
        with contextlib.redirect_stdout(io.StringIO()):
            h.cmd_build_layout(SimpleNamespace(manifest=str(mp), export_log=str(lp), slices_dir=str(slices),
                                               out=str(out), group_effects=None))
        rp = tmp / "full_render.png"
        bg.save(rp)
        vdir = tmp / "validate"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = h.cmd_validate(SimpleNamespace(layout=str(out), slices_dir=str(slices), full_render=str(rp),
                                                out_dir=str(vdir), fonts_dir=None, group_effects=None))
        return rc, json.loads((vdir / "validate_report.json").read_text(encoding="utf-8"))

    def test_plain_confirm_and_hidden_layer(self):
        rc, rep = self._run(hidden=[31])
        types = _types(rep["issues"])
        self.assertIn("pending_user_decision", types)
        self.assertIn("visible_layer_hidden", types)
        self.assertFalse(rep["ok"])
        self.assertEqual(rc, 1)

    def test_user_exclusion_recorded(self):
        rc, rep = self._run(hidden=[31], decision="exclude")
        self.assertEqual(rep["issues"], [])
        self.assertTrue(rep["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
