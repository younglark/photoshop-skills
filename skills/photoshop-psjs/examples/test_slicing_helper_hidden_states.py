#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the 1.4.13 guards (synthetic data):

- an uncertain_hidden note on a component / state / part is only an index for people: its layer_ids must be
  covered by a blocking reuse_decisions entry (hidden_layer_state), else uncertain_hidden_without_decision;
  covered but unanswered stays pending_user_decision only (no double report);
- a layer hidden when the exporter opened the document, exported as a state asset (PNG entry), needs the user's
  recorded "export it as a state" answer, else hidden_layer_exported_without_decision. Exception: a highlight
  over a base that is visible at open in the same component's default state. Old logs without layers_at_open
  are not flagged by this check.

    python test_slicing_helper_hidden_states.py
"""
from __future__ import annotations

import contextlib
import io
import json
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


def _types(issues):
    return sorted(i["type"] for i in issues)


def _layer(lid, parent=1, visible=True, bounds=(0, 0, 60, 20), effective=None, kind="pixel"):
    return {"id": lid, "name": f"<layer {lid}>", "parent_id": parent, "kind": kind, "visible": visible,
            "effective_visible": visible if effective is None else effective, "opacity": 100,
            "bounds": _box(*bounds), "group": kind == "group"}


STATE_41 = {"id": "state_41", "type": "hidden_layer_state", "layer_ids": [41],
            "question": "Is hidden <layer 41> an alternate state of the button?",
            "options": ["state", "leftover"], "default": "state"}


def _answered(d, value):
    return dict(d, decision=value, decided_by="user"), {"id": d["id"], "decision": value}


# ---------------------------------------------------------------------------
# A. uncertain_hidden needs a decision entry
# ---------------------------------------------------------------------------

def _components(where="part"):
    note = {"layer_ids": [41, 42], "crops": ["<crop a>", "<crop b>"], "question": "state or old draft?"}
    part = {"name": "btn_bg", "role": "base", "layer_id": 40}
    comp = {"id": "btn", "type": "button", "shared": [part]}
    if where == "part":
        part["uncertain_hidden"] = note
    elif where == "component":
        comp["uncertain_hidden"] = [note]
    elif where == "state":
        comp["states"] = {"pressed": {"uncertain_hidden": note, "parts": []}}
    elif where == "child":
        comp["children"] = [{"id": "btn_icon", "type": "icon", "uncertain_hidden": {"layer_ids": [41, 42]}}]
    return [comp]


class TestUncertainHiddenWithoutDecision(unittest.TestCase):
    def test_note_without_any_decision(self):
        for where in ("part", "component", "state", "child"):
            issues = h.uncertain_hidden_issues(_components(where), [], {"decision_log": []})
            self.assertEqual(_types(issues), ["uncertain_hidden_without_decision"], where)
            self.assertEqual(issues[0]["layer_ids"], [41, 42], where)
            self.assertEqual(issues[0]["component_id"], "btn" if where != "child" else "btn_icon", where)

    def test_question_and_part_are_reported(self):
        issue = h.uncertain_hidden_issues(_components("part"), [], {})[0]
        self.assertEqual(issue["question"], "state or old draft?")
        self.assertEqual(issue["part"], "btn_bg")

    def test_partly_covered_reports_the_rest(self):
        issues = h.uncertain_hidden_issues(_components(), [STATE_41], {"decision_log": []})
        self.assertEqual(_types(issues), ["uncertain_hidden_without_decision"])
        self.assertEqual(issues[0]["layer_ids"], [42])

    def test_non_blocking_decision_does_not_cover(self):
        d = {"id": "r", "type": "reuse", "layer_ids": [41, 42], "default": "reuse"}
        self.assertEqual(_types(h.uncertain_hidden_issues(_components(), [d], {})),
                         ["uncertain_hidden_without_decision"])

    def test_covered_but_unanswered_is_pending_only(self):
        d = dict(STATE_41, layer_ids=[41, 42])
        lock = {"decision_log": []}
        self.assertEqual(h.uncertain_hidden_issues(_components(), [d], lock), [])
        self.assertEqual(_types(h.user_decision_issues([d], lock)), ["pending_user_decision"])

    def test_answered_reports_nothing(self):
        d, log = _answered(dict(STATE_41, layer_ids=[41, 42]), "leftover")
        lock = {"decision_log": [log]}
        self.assertEqual(h.uncertain_hidden_issues(_components(), [d], lock), [])
        self.assertEqual(h.user_decision_issues([d], lock), [])

    def test_list_of_ids_and_layer_id_forms(self):
        comp = [{"id": "c", "uncertain_hidden": [41, {"layer_id": 42}]}]
        self.assertEqual(h.uncertain_hidden_issues(comp, [], {})[0]["layer_ids"], [41, 42])
        self.assertEqual(h.uncertain_hidden_issues([{"id": "c", "uncertain_hidden": None}], [], {}), [])
        self.assertEqual(h.uncertain_hidden_issues([{"id": "c", "uncertain_hidden": []}], [], {}), [])

    def test_top_level_checklist_list(self):
        issues = h.uncertain_hidden_issues([], [], {}, extra=[{"layer_ids": [43], "question": "q"}])
        self.assertEqual(issues[0]["layer_ids"], [43])


# ---------------------------------------------------------------------------
# B. exporting a layer hidden at open needs the user's "state" answer
# ---------------------------------------------------------------------------

LAYERS = [
    _layer(1, parent=None, kind="group"),
    _layer(40),                     # visible base
    _layer(41, visible=False),      # hidden at open
    _layer(5, kind="group", visible=False),
    _layer(51, parent=5, effective=False),  # own visible, inside a hidden group
]


def _node(name, lid, state=None, role="base", comp="btn", image=True, default_state=None, **kw):
    n = {"name": name, "layer_id": lid, "select_ids": [lid], "component_id": comp, "part_role": role,
         "state": state, "default_state": default_state, "image": f"{name}.png" if image else None, "kind": "image"}
    n.update(kw)
    return n


def _layout(nodes, decisions=(), log=(), layers=LAYERS):
    return {"nodes": nodes, "reuse_decisions": list(decisions),
            "manifest_lock": {"locked": True, "decision_log": list(log)},
            "export": {"layers_at_open": layers}}


class TestHiddenLayerExported(unittest.TestCase):
    def test_hidden_layer_as_normal_state_without_decision(self):
        nodes = [_node("btn_bg_selected", 40, state="selected", default_state="selected"),
                 _node("btn_bg_normal", 41, state="normal", default_state="selected")]
        issues = h.hidden_layer_export_issues(_layout(nodes))
        self.assertEqual(_types(issues), ["hidden_layer_exported_without_decision"])
        self.assertEqual([x["layer_id"] for x in issues[0]["entries"]], [41])
        self.assertEqual(issues[0]["entries"][0]["name"], "btn_bg_normal")
        self.assertEqual(issues[0]["entries"][0]["reason"], "no_decision")

    def test_hidden_through_group_counts(self):
        nodes = [_node("x", 51, state="pressed")]
        self.assertEqual(_types(h.hidden_layer_export_issues(_layout(nodes))),
                         ["hidden_layer_exported_without_decision"])

    def test_recorded_state_answer_allows_it(self):
        d, log = _answered(STATE_41, "state")
        nodes = [_node("btn_bg_normal", 41, state="normal")]
        self.assertEqual(h.hidden_layer_export_issues(_layout(nodes, [d], [log])), [])

    def test_default_copied_or_unlogged_answer_does_not_count(self):
        nodes = [_node("btn_bg_normal", 41, state="normal")]
        copied = dict(STATE_41, decision="state")  # agent copied its default
        self.assertEqual(h.hidden_layer_export_issues(_layout(nodes, [copied])), [],
                         "unanswered blocking decision is reported once, as pending_user_decision")
        self.assertEqual(_types(h.user_decision_issues([copied], {"decision_log": []})), ["pending_user_decision"])
        unlogged = dict(STATE_41, decision="state", decided_by="user")
        self.assertEqual(_types(h.user_decision_issues([unlogged], {"decision_log": []})), ["pending_user_decision"])

    def test_user_answer_leftover_but_exported(self):
        d, log = _answered(STATE_41, "leftover")
        nodes = [_node("btn_bg_normal", 41, state="normal")]
        issues = h.hidden_layer_export_issues(_layout(nodes, [d], [log]))
        self.assertEqual(_types(issues), ["hidden_layer_exported_without_decision"])
        self.assertEqual(issues[0]["entries"][0]["reason"], "user_answer_is_not_export_as_state")

    def test_highlight_over_visible_default_base_is_exempt(self):
        nodes = [_node("tab_bg", 40, comp="tab"),
                 _node("tab_glow", 41, role="highlight", comp="tab")]
        self.assertEqual(h.hidden_layer_export_issues(_layout(nodes)), [])
        nodes = [_node("tab_bg", 40, state="selected", default_state="selected", comp="tab"),
                 _node("tab_glow", 41, state="selected", default_state="selected", role="highlight", comp="tab")]
        self.assertEqual(h.hidden_layer_export_issues(_layout(nodes)), [])

    def test_highlight_without_visible_base_is_not_exempt(self):
        nodes = [_node("tab_glow", 41, role="highlight", comp="tab")]
        self.assertEqual(_types(h.hidden_layer_export_issues(_layout(nodes))),
                         ["hidden_layer_exported_without_decision"])
        # the visible base is only in a non-default state
        nodes = [_node("tab_bg", 40, state="normal", default_state="selected", comp="tab"),
                 _node("tab_glow", 41, state="selected", default_state="selected", role="highlight", comp="tab")]
        self.assertEqual(_types(h.hidden_layer_export_issues(_layout(nodes))),
                         ["hidden_layer_exported_without_decision"])

    def test_old_log_without_layers_at_open_is_skipped(self):
        nodes = [_node("btn_bg_normal", 41, state="normal")]
        self.assertEqual(h.hidden_layer_export_issues(_layout(nodes, layers=None)), [])

    def test_text_and_bounds_only_entries_are_skipped(self):
        nodes = [_node("label", 41, image=False, kind="dynamic_text"),
                 _node("copy", 41, _bounds_only=True)]
        self.assertEqual(h.hidden_layer_export_issues(_layout(nodes)), [])

    def test_visible_layers_are_fine(self):
        self.assertEqual(h.hidden_layer_export_issues(_layout([_node("bg", 40)])), [])


# ---------------------------------------------------------------------------
# validate end to end
# ---------------------------------------------------------------------------

def _cell(size=(60, 20), seed=0):
    w, hh = size
    a = np.zeros((hh, w, 4), dtype=np.uint8)
    a[..., 0] = (np.arange(w) * 7 + seed) % 256
    a[..., 1] = 120
    a[..., 2] = (np.arange(hh)[:, None] * 13) % 256
    a[..., 3] = 255
    return Image.fromarray(a, "RGBA")


def _confirm(path, note=None, decision=None):
    with contextlib.redirect_stdout(io.StringIO()):
        return h.cmd_confirm(SimpleNamespace(manifest=str(path), note=note, relock=False, decision=decision or []))


class TestValidateEndToEnd(unittest.TestCase):
    def _run(self, decisions=(), answers=(), uncertain=True, export_hidden=True, layers=True):
        tmp = Path(tempfile.mkdtemp())
        slices = tmp / "slices"
        slices.mkdir()
        bg = {"name": "btn_bg_selected", "role": "base", "kind": "image", "layer_id": 40,
              "logical_bounds": [0, 0, 60, 20]}
        if uncertain:
            bg["uncertain_hidden"] = {"layer_ids": [41], "crops": ["<crop>"], "question": "state or old draft?"}
        comp = {"id": "btn", "type": "button", "default_state": "selected",
                "states": {"selected": {"parts": [bg]}}}
        if export_hidden:
            comp["states"]["normal"] = {"parts": [{"name": "btn_bg_normal", "role": "base", "kind": "image",
                                                  "layer_id": 41, "logical_bounds": [0, 0, 60, 20],
                                                  "from_hidden": True}]}
        m = {"schema": h.SCHEMA_MANIFEST, "status": "pending_review",
             "source": {"artboard": {"id": 1, "name": "<artboard>", "bounds": [0, 0, 60, 20], "size": [60, 20]}},
             "components": [comp], "reuse_decisions": list(decisions)}
        h.flatten_components(m)
        mp = tmp / "manifest.json"
        mp.write_text(json.dumps(m), encoding="utf-8")
        _confirm(mp, note="清单确认")
        if answers:
            _confirm(mp, decision=list(answers))
        img = _cell()
        items = []
        for e in m["entries"]:
            img.save(slices / f"{e['name']}.png")
            items.append({"name": e["name"], "ok": True, "out_size": [60, 20], "visible_bounds_from_trim_insets": True,
                          "visible_bounds": _box(0, 0, 60, 20), "logical_bounds": _box(0, 0, 60, 20)})
        log = {"ok": True, "exporter": {"release": h.SKILL_VERSION, "sha256": h.shipped_exporter_sha256(SHIPPED)},
               "layers_at_open": LAYERS[:3] if layers else None, "items": items,
               "full_render": {"ok": True, "hidden_at_render": []}}
        lp = tmp / "export_log.json"
        lp.write_text(json.dumps(log), encoding="utf-8")
        out = tmp / "layout.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            h.cmd_build_layout(SimpleNamespace(manifest=str(mp), export_log=str(lp), slices_dir=str(slices),
                                               out=str(out), group_effects=None))
        rp = tmp / "full_render.png"
        img.save(rp)
        vdir = tmp / "validate"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = h.cmd_validate(SimpleNamespace(layout=str(out), slices_dir=str(slices), full_render=str(rp),
                                                out_dir=str(vdir), fonts_dir=None, group_effects=None))
        return rc, json.loads((vdir / "validate_report.json").read_text(encoding="utf-8"))

    def test_uncertain_note_and_hidden_export_without_decision(self):
        rc, rep = self._run()
        types = _types(rep["issues"])
        self.assertIn("uncertain_hidden_without_decision", types)
        self.assertIn("hidden_layer_exported_without_decision", types)
        self.assertNotIn("pending_user_decision", types)
        self.assertFalse(rep["ok"])
        self.assertEqual(rc, 1)

    def test_decision_written_but_not_answered(self):
        rc, rep = self._run(decisions=[STATE_41])
        self.assertEqual(_types(rep["issues"]), ["pending_user_decision"])
        self.assertFalse(rep["ok"])

    def test_user_answered_state(self):
        rc, rep = self._run(decisions=[STATE_41], answers=["state_41=state"])
        self.assertEqual(rep["issues"], [])
        self.assertTrue(rep["ok"])

    def test_user_answered_leftover_and_not_exported(self):
        rc, rep = self._run(decisions=[STATE_41], answers=["state_41=leftover"], export_hidden=False)
        self.assertEqual(rep["issues"], [])

    def test_old_log_does_not_add_the_hidden_export_issue(self):
        rc, rep = self._run(uncertain=False, layers=False)
        types = _types(rep["issues"])
        self.assertNotIn("hidden_layer_exported_without_decision", types)
        self.assertIn("exporter_modified", types)  # 1.4.12 behaviour kept: a log without layers_at_open


class TestFlattenAndExporter(unittest.TestCase):
    def test_flatten_summary_lists_uncertain_without_decision(self):
        tmp = Path(tempfile.mkdtemp())
        m = {"schema": h.SCHEMA_MANIFEST, "status": "pending_review",
             "source": {"artboard": {"id": 1, "name": "<artboard>", "bounds": [0, 0, 60, 20], "size": [60, 20]}},
             "components": _components("part"), "reuse_decisions": []}
        mp = tmp / "manifest.json"
        mp.write_text(json.dumps(m), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            h.cmd_flatten(SimpleNamespace(manifest=str(mp), out=None, group_effects=None))
        summary = json.loads(buf.getvalue())
        self.assertEqual([i["layer_ids"] for i in summary["uncertain_hidden_without_decision"]], [[41, 42]])

    def test_exporter_records_snapshot_timing(self):
        text = SHIPPED.read_text(encoding="utf-8")
        self.assertIn("log.layers_at_open_ms = nowMs() - tSnap;", text)
        self.assertIn("fullItem.hidden_at_render_ms = nowMs() - tSnap2;", text)
        self.assertRegex(text, r'const EXPORTER_RELEASE = "1\.\d+\.\d+";')


if __name__ == "__main__":
    unittest.main(verbosity=2)
