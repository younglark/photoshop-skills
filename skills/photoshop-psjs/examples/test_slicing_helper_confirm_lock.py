#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the 1.4.17 confirm lock: unapproved_manifest_change (synthetic data).

A locked manifest may only pick up changes that are the recorded consequence of a user
decision (reuse_decisions answer fields, or adding / removing an entry for a layer the
user included / excluded / marked as a state). Applying leftover proposals (source layer,
logical_bounds, reparent, …) and then confirm --relock, or hiding those edits inside
confirm --decision, must fail with exit 6 and leave the lock bytes unchanged.

    python test_slicing_helper_confirm_lock.py
"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slicing_helper as h  # noqa: E402


def _box(l, t, r, b):
    return [l, t, r, b]


def _manifest():
    bg = {"name": "panel_bg", "kind": "image", "layer_id": 10, "logical_bounds": _box(0, 0, 60, 40)}
    tab = {"name": "panel_tab", "kind": "image", "layer_id": 11, "logical_bounds": _box(0, 40, 20, 60)}
    return {
        "schema": "psjs-slice-manifest/v2",
        "status": "pending_review",
        "source": {"file": "x.psb", "artboard": {"id": 1, "name": "a", "bounds": [0, 0, 60, 60], "size": [60, 60]}},
        "components": [{"id": "panel", "type": "panel", "shared": [bg, tab]}],
        "entries": [dict(bg), dict(tab)],
        "missing": [],
        "reuse_decisions": [
            {"id": "exclude_board_fill", "type": "layer_inclusion", "layer_ids": [99],
             "options": ["include", "exclude"], "default": "include",
             "question": "is the board fill part of the design?"},
        ],
    }


def _confirm(path, note=None, decision=None, relock=False):
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        rc = h.cmd_confirm(SimpleNamespace(manifest=str(path), note=note, relock=relock,
                                           decision=decision or []))
    out = buf.getvalue()
    data = json.loads(out) if out.strip() else {}
    return rc, data, err.getvalue()


def _lock_bytes(path):
    copy, sha = h.manifest_lock_paths(Path(path))
    return (copy.read_bytes() if copy.is_file() else None,
            sha.read_bytes() if sha.is_file() else None)


class TestUnapprovedManifestChange(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.m = self.tmp / "manifest.json"
        self.m.write_text(json.dumps(_manifest(), ensure_ascii=False, indent=2), encoding="utf-8")
        rc, data, _ = _confirm(self.m, note="checklist confirmed")
        self.assertEqual(rc, 0)
        self.assertTrue(data.get("ok"))
        self.locked_copy, self.locked_sha = _lock_bytes(self.m)

    def _load(self):
        return json.loads(self.m.read_text(encoding="utf-8"))

    def _dump(self, data):
        self.m.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _assert_lock_untouched(self):
        copy, sha = _lock_bytes(self.m)
        self.assertEqual(copy, self.locked_copy)
        self.assertEqual(sha, self.locked_sha)

    def test_clean_decision_only_succeeds(self):
        rc, data, _ = _confirm(self.m, decision=["exclude_board_fill=exclude"])
        self.assertEqual(rc, 0, data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["user_decisions_recorded"],
                         [{"id": "exclude_board_fill", "decision": "exclude"}])
        man = self._load()
        dec = man["reuse_decisions"][0]
        self.assertEqual(dec["decision"], "exclude")
        self.assertEqual(dec["decided_by"], "user")
        log = json.loads(h.manifest_lock_paths(self.m)[1].read_text(encoding="utf-8"))["decision_log"]
        self.assertEqual(log[-1]["id"], "exclude_board_fill")
        self.assertEqual(log[-1]["decision"], "exclude")
        # entries are untouched
        self.assertEqual(man["entries"][1]["layer_id"], 11)
        self.assertEqual(man["entries"][1]["logical_bounds"], [0, 40, 20, 60])

    def test_relock_with_no_extra_edits_succeeds(self):
        rc, data, _ = _confirm(self.m, decision=["exclude_board_fill=exclude"])
        self.assertEqual(rc, 0, data)
        self.locked_copy, self.locked_sha = _lock_bytes(self.m)
        rc, data, _ = _confirm(self.m, note="reconfirm unchanged", relock=True)
        self.assertEqual(rc, 0, data)
        self.assertTrue(data["ok"])
        # lock rewritten (new confirmed_at / note) but entries unchanged
        man = self._load()
        self.assertEqual(man["entries"][1]["logical_bounds"], [0, 40, 20, 60])
        self.assertEqual(man["confirmed_note"], "reconfirm unchanged")

    def test_relock_of_unapproved_source_and_bounds_fails(self):
        rc, data, _ = _confirm(self.m, decision=["exclude_board_fill=exclude"])
        self.assertEqual(rc, 0, data)
        self.locked_copy, self.locked_sha = _lock_bytes(self.m)
        man = self._load()
        man["entries"][1]["layer_id"] = 22
        man["entries"][1]["logical_bounds"] = [0, 40, 24, 60]
        man["components"][0]["shared"][1]["layer_id"] = 22
        man["components"][0]["shared"][1]["logical_bounds"] = [0, 40, 24, 60]
        self._dump(man)
        rc, data, err = _confirm(self.m, note="to pass validate this round", relock=True)
        self.assertEqual(rc, h.UNAPPROVED_MANIFEST_EXIT)
        self.assertFalse(data.get("ok", True))
        self.assertEqual(data["error"], h.UNAPPROVED_MANIFEST_CHANGE)
        self.assertGreaterEqual(data["count"], 2)
        fields = {(c.get("name"), c.get("field") or c.get("op")) for c in data["changes"]}
        self.assertTrue(any(n == "panel_tab" and f in ("layer_id", "logical_bounds", "change")
                            for n, f in fields), data["changes"])
        self.assertIn("unapproved_manifest_change", err)
        self._assert_lock_untouched()
        # dirty manifest is left as the agent wrote it; the lock still matches the previous confirm
        st = h.manifest_lock_state(self.m)
        self.assertTrue(st["locked"] and st["changed"])
        self.assertEqual(st["confirmed_sha256"], json.loads(self.locked_sha)["sha256"]
                         if self.locked_sha.strip().startswith(b"{") else st["confirmed_sha256"])

    def test_decision_plus_dirty_manifest_fails_even_with_relock(self):
        # locked A (no decision yet). Agent records the user's answer AND applies leftover proposals.
        man = self._load()
        man["entries"][1]["logical_bounds"] = [0, 40, 24, 60]
        man["entries"][1]["layer_id"] = 22
        self._dump(man)
        for relock in (False, True):
            rc, data, _ = _confirm(self.m, decision=["exclude_board_fill=exclude"], relock=relock)
            self.assertEqual(rc, h.UNAPPROVED_MANIFEST_EXIT, (relock, data))
            self.assertEqual(data["error"], h.UNAPPROVED_MANIFEST_CHANGE)
            self.assertTrue(any(c.get("name") == "panel_tab" for c in data["changes"]), data["changes"])
            self._assert_lock_untouched()
            # the decision must not have been written into the lock
            log = json.loads(h.manifest_lock_paths(self.m)[1].read_text(encoding="utf-8")).get("decision_log") or []
            self.assertEqual(log, [])

    def test_include_then_add_entry_relock_succeeds(self):
        man = self._load()
        man["reuse_decisions"].append({
            "id": "keep_layer_30", "type": "layer_inclusion", "layer_ids": [30],
            "options": ["include", "exclude"], "default": "include", "question": "include this layer?",
        })
        self._dump(man)
        # adding an unanswered question after lock, then recording the answer, is allowed
        rc, data, _ = _confirm(self.m, decision=["keep_layer_30=include"], relock=True)
        self.assertEqual(rc, 0, data)
        man = self._load()
        man["entries"].append({"name": "panel_icon", "kind": "image", "layer_id": 30,
                               "logical_bounds": _box(40, 0, 60, 20)})
        man["components"][0]["shared"].append(dict(man["entries"][-1]))
        self._dump(man)
        rc, data, _ = _confirm(self.m, note="added the included layer", relock=True)
        self.assertEqual(rc, 0, data)
        man = self._load()
        self.assertEqual(man["entries"][-1]["name"], "panel_icon")
        self.assertEqual(man["entries"][-1]["layer_id"], 30)

    def test_authorised_add_without_relock_is_still_exit_3(self):
        man = self._load()
        man["reuse_decisions"].append({
            "id": "keep_layer_30", "type": "layer_inclusion", "layer_ids": [30],
            "options": ["include", "exclude"], "default": "include", "question": "include this layer?",
        })
        self._dump(man)
        rc, data, _ = _confirm(self.m, decision=["keep_layer_30=include"], relock=True)
        self.assertEqual(rc, 0, data)
        man = self._load()
        man["entries"].append({"name": "panel_icon", "kind": "image", "layer_id": 30,
                               "logical_bounds": _box(40, 0, 60, 20)})
        self._dump(man)
        rc, data, _ = _confirm(self.m, note=None, relock=False)
        self.assertEqual(rc, 3, data)
        self.assertEqual(data["error"], "manifest_changed_after_confirm")

    def test_hidden_state_add_and_leftover_remove(self):
        man = self._load()
        man["reuse_decisions"].append({
            "id": "state_40", "type": "hidden_layer_state", "layer_ids": [40],
            "options": ["state", "leftover"], "default": "leftover", "question": "is this a state?",
        })
        self._dump(man)
        rc, data, _ = _confirm(self.m, decision=["state_40=state"], relock=True)
        self.assertEqual(rc, 0, data)
        man = self._load()
        man["entries"].append({"name": "panel_off", "kind": "image", "layer_id": 40, "hidden": True,
                               "logical_bounds": _box(0, 0, 10, 10)})
        self._dump(man)
        rc, data, _ = _confirm(self.m, relock=True)
        self.assertEqual(rc, 0, data)
        # now the user changes their mind: leftover — removing that entry is allowed
        rc, data, _ = _confirm(self.m, decision=["state_40=leftover"], relock=True)
        self.assertEqual(rc, 0, data)
        man = self._load()
        man["entries"] = [e for e in man["entries"] if e.get("name") != "panel_off"]
        self._dump(man)
        rc, data, _ = _confirm(self.m, relock=True)
        self.assertEqual(rc, 0, data)
        self.assertFalse(any(e.get("name") == "panel_off" for e in self._load()["entries"]))

    def test_plain_confirm_of_unchanged_manifest_still_works(self):
        rc, data, _ = _confirm(self.m, note=None, relock=False)
        self.assertEqual(rc, 0, data)


class TestUnapprovedHelpers(unittest.TestCase):
    def test_diff_lists_entry_fields(self):
        a = _manifest()
        b = json.loads(json.dumps(a))
        b["entries"][1]["logical_bounds"] = [0, 40, 24, 60]
        changes = h.unapproved_manifest_changes(a, b, [], recorded_ids=[])
        self.assertTrue(any(c.get("name") == "panel_tab" and c.get("field") == "logical_bounds"
                            for c in changes), changes)

    def test_decision_field_only_is_approved(self):
        a = _manifest()
        b = json.loads(json.dumps(a))
        b["reuse_decisions"][0].update(decision="exclude", decided_by="user", decided_at="t")
        log = [{"id": "exclude_board_fill", "decision": "exclude", "decided_at": "t"}]
        changes = h.unapproved_manifest_changes(a, b, log, recorded_ids=["exclude_board_fill"])
        self.assertEqual(changes, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
