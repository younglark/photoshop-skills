#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent slicing helper: infer a manifest, annotate a preview, build layout JSON, validate.

Requires: psd-tools, Pillow, numpy, and scipy (or psd-tools[composite]) for
preview compositing and nine-slice analysis. validate also needs uharfbuzz +
freetype-py (or uharfbuzz + skia-python) whenever the layout has
attribute-only text (dynamic_text or static_text) (text_render.py); without them
it exits non-zero before drawing any text. Prefer a project virtualenv; do
not install packages into the system Python. Open large documents with
``PSDImage.open(path, parse_limits=ParseLimits(max_objects=None))``
(psd-tools 1.26+: ``ParseLimits.max_objects`` defaults to 1_000_000; ``None``
disables that cap). See references/slicing.md.

Subcommands:
  prepare     Build agent materials: full-render preview, per-layer crops, layer tree, nine-slice candidates.
  flatten     Turn an agent-written component manifest into a flat export entry list.
  infer       Optional keyword draft only (draft_manifest.json); the agent must review or replace it.
  preview     Redraw the annotated preview from a (possibly edited) manifest.
  confirm     Lock the manifest after the user confirms it (manifest.confirmed.json + .sha256).
  check-lock  Exit non-zero if the manifest changed after confirmation (run before export).
  build-layout  Merge confirmed manifest + Photoshop export log + PNGs into layout JSON.
  validate    Recomposite the default state vs a full PS render; also render alternate states.

The helper prepares materials. An agent writes the manifest (components, parts,
states, names). Hidden layers appear in the tree and crops so the agent can
attach them to interactive states. Unrelated hidden layers stay unused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import text_render  # noqa: E402  (sibling module; optional deps are imported lazily inside)

try:
    from psd_tools import PSDImage
except ImportError:  # infer is the only subcommand that needs it
    PSDImage = None

try:
    from psd_tools.psd.parse_limits import ParseLimits
except ImportError:
    ParseLimits = None

SCHEMA_MANIFEST = "psjs-slice-manifest/v2"
SKILL_VERSION = "1.4.13"
SCHEMA_LAYOUT = "psjs-layout/v2"
SCHEMA_MANIFEST_V1 = "psjs-slice-manifest/v1"
SCHEMA_LAYOUT_V1 = "psjs-layout/v1"

KIND_COLORS = {
    "image": (0, 170, 255),
    "nine_slice": (255, 160, 0),
    "dynamic_text": (80, 220, 80),
    "static_text": (40, 200, 180),
    "art_text": (220, 80, 200),
}

# Attribute-only text: no PNG, engine (and validate/preview) renders from text attrs.
ATTR_TEXT_KINDS = frozenset({"dynamic_text", "static_text"})
# All text kinds: never receive nine_slice margins.
TEXT_KINDS = frozenset({"dynamic_text", "static_text", "art_text"})

# Effects an engine is expected to redraw for attribute-only text.
# Conservative: only a plain-color Stroke and/or DropShadow (plus a solid fill).
ATTR_TEXT_LAYER_EFFECTS = frozenset({"Stroke", "DropShadow"})
# Layer effects that must be baked as art_text. Listed explicitly; unknown
# types are also treated as unreplayable.
UNREPLAYABLE_TEXT_EFFECTS = frozenset({
    "GradientOverlay", "GradientFill",
    "PatternOverlay", "PatternFill",
    "Satin", "ChromeFX",
    "BevelEmboss",
    "InnerGlow",
    "InnerShadow",
    "OuterGlow",
    "ColorOverlay", "SolidFill",
})
# Descriptor / psd-tools aliases -> canonical type names used above.
TEXT_EFFECT_ALIASES = {
    "GradientFill": "GradientOverlay",
    "PatternFill": "PatternOverlay",
    "ChromeFX": "Satin",
    "SolidFill": "ColorOverlay",
    "frameFX": "Stroke",
    "dropShadow": "DropShadow",
    "outerGlow": "OuterGlow",
    "innerShadow": "InnerShadow",
    "innerGlow": "InnerGlow",
    "solidFill": "ColorOverlay",
    "gradientFill": "GradientOverlay",
    "patternFill": "PatternOverlay",
    "chromeFX": "Satin",
    "bevelEmboss": "BevelEmboss",
}
_SOLID_FILL_TYPES = frozenset({
    "solid", "color", "colour", "solidcolor", "colorfill", "solidcolorfill",
    "foregroundcolor", "foreground", "sc", "clr",
})


def is_attr_text(kind) -> bool:
    return kind in ATTR_TEXT_KINDS


def is_text_kind(kind) -> bool:
    return kind in TEXT_KINDS


def enum_val(v):
    if v is None:
        return None
    if hasattr(v, "name"):
        return v.name
    return str(v)


def layer_kind(layer) -> str:
    k = getattr(layer, "kind", None)
    return k if isinstance(k, str) else (enum_val(k) or type(layer).__name__)


def is_group(layer) -> bool:
    try:
        return bool(layer.is_group())
    except Exception:
        return layer_kind(layer).lower() in {"group", "artboard"}


def slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr, flush=True)


def normalize_box(x0, y0, x1, y1, *, width=None, height=None, name=None):
    """Return (left, top, right, bottom) with right >= left and bottom >= top.

    Inverted edges are swapped. If *width* / *height* are given, the box is
    clamped to ``[0, width-1]`` x ``[0, height-1]``. Empty or non-numeric
    boxes return None and emit a warning (they must not be drawn).
    """
    try:
        a = float(x0)
        b = float(y0)
        c = float(x1)
        d = float(y1)
    except (TypeError, ValueError):
        warn(f"skipping non-numeric bounds {x0!r},{y0!r},{x1!r},{y1!r}" + (f" ({name})" if name else ""))
        return None
    if c < a:
        a, c = c, a
    if d < b:
        b, d = d, b
    if width is not None:
        max_x = max(0, int(width) - 1)
        a = min(max(0.0, a), float(max_x))
        c = min(max(0.0, c), float(max_x))
        if c < a:
            a, c = c, a
    if height is not None:
        max_y = max(0, int(height) - 1)
        b = min(max(0.0, b), float(max_y))
        d = min(max(0.0, d), float(max_y))
        if d < b:
            b, d = d, b
    if c - a < 1 or d - b < 1:
        warn(
            "skipping empty or inverted bounds "
            f"[{x0}, {y0}, {x1}, {y1}] -> [{a}, {b}, {c}, {d}]"
            + (f" ({name})" if name else "")
        )
        return None
    return (a, b, c, d)


def find_token(text: str, pairs):
    low = text.lower()
    for src, dst in pairs:
        if src.lower() in low:
            return dst
    return None


# ---------------------------------------------------------------------------
# Layer effect parameters (for engine-drawn effects)
# ---------------------------------------------------------------------------

# Effects a game engine commonly redraws (shadow / glow / outline / tint).
ENGINE_FRIENDLY_EFFECTS = {"DropShadow", "OuterGlow", "Stroke", "ColorOverlay", "InnerShadow"}
# Effects that only change pixels inside the shape (no extent outside logical bounds).
INNER_EFFECTS = {"InnerShadow", "InnerGlow", "ColorOverlay", "GradientOverlay", "PatternOverlay", "Satin", "BevelEmboss"}
# Effects validate can approximate with Pillow when the engine draws them.
EMULATED_EFFECTS = {"DropShadow", "OuterGlow"}

_COLOR_KEYS_255 = (("Rd  ", "Grn ", "Bl  "),)
_COLOR_KEYS_FLOAT = (("redFloat", "greenFloat", "blueFloat"),)


def _desc_get(desc, key):
    """Read *key* from a psd-tools Descriptor or a plain dict (str / bytes keys)."""
    if desc is None:
        return None
    candidates = [key]
    if isinstance(key, str):
        candidates.append(key.encode("latin-1"))
    elif isinstance(key, bytes):
        candidates.append(key.decode("latin-1"))
    for k in candidates:
        try:
            if k in desc:
                return desc[k]
        except Exception:
            continue
    try:  # psd-tools Key enum lookup
        from psd_tools.terminology import Key  # type: ignore
        for member in Key:
            if member.value in candidates:
                try:
                    if member in desc:
                        return desc[member]
                except Exception:
                    pass
    except Exception:
        pass
    return None


def _num(v):
    if v is None:
        return None
    for attr in ("value",):
        if hasattr(v, attr) and not isinstance(v, (int, float)):
            try:
                v = getattr(v, attr)
            except Exception:
                pass
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def color_to_hex(desc):
    """RGB color descriptor -> '#rrggbb'. Returns None for other color models."""
    if desc is None:
        return None
    if isinstance(desc, str) and desc.startswith("#"):
        return desc.lower()
    for keys in _COLOR_KEYS_255:
        vals = [_num(_desc_get(desc, k)) for k in keys]
        if all(v is not None for v in vals):
            return "#" + "".join(f"{int(round(max(0.0, min(255.0, v)))):02x}" for v in vals)
    for keys in _COLOR_KEYS_FLOAT:
        vals = [_num(_desc_get(desc, k)) for k in keys]
        if all(v is not None for v in vals):
            return "#" + "".join(f"{int(round(max(0.0, min(1.0, v)) * 255)):02x}" for v in vals)
    return None


def _safe_attr(obj, name):
    try:
        return getattr(obj, name)
    except Exception:
        return None


def effect_params(effect):
    """Best-effort parameters of one psd-tools effect, never raising.

    Keys follow the engine_effects schema: type, enabled, blend_mode, color
    ('#rrggbb'), opacity (0-1), angle (deg), distance (px), size (px), spread or
    choke (Photoshop percent 0-100), position (stroke). Unknown effect classes
    keep their raw class name in ``type`` and set ``unknown: true``.
    """
    out = {"type": type(effect).__name__}
    try:
        enabled = _safe_attr(effect, "enabled")
        out["enabled"] = True if enabled is None else bool(enabled)
        bm = _safe_attr(effect, "blend_mode")
        if bm is not None:
            out["blend_mode"] = (enum_val(bm) or "").lower() or None
        color = _safe_attr(effect, "color")
        if color is not None:
            hx = color_to_hex(color)
            if hx:
                out["color"] = hx
            else:
                out["color_raw"] = str(getattr(color, "classID", type(color).__name__))
        op = _num(_safe_attr(effect, "opacity"))
        if op is not None:
            out["opacity"] = round(op / 100.0 if op > 1.0 else op, 4)
        for key in ("angle", "distance", "size", "noise"):
            val = _num(_safe_attr(effect, key))
            if val is not None:
                out[key] = round(val, 3)
        if _safe_attr(effect, "use_global_light") is not None:
            out["use_global_light"] = bool(_safe_attr(effect, "use_global_light"))
        choke = _num(_safe_attr(effect, "choke"))
        if choke is not None:
            out["choke" if out["type"] in INNER_EFFECTS else "spread"] = round(choke, 3)
        if out["type"] == "Stroke":
            pos = _safe_attr(effect, "position")
            out["position"] = (enum_val(pos) or "outside").lower() if pos is not None else "outside"
            ft = _safe_attr(effect, "fill_type")
            if ft is not None:
                out["fill_type"] = (enum_val(ft) or "").lower() or None
        if out["type"] in {"OuterGlow", "InnerGlow"}:
            gt = _safe_attr(effect, "glow_type")
            if gt is not None:
                out["technique"] = (enum_val(gt) or "").lower() or None
        if out["type"] not in ENGINE_FRIENDLY_EFFECTS | INNER_EFFECTS | {"OuterGlow"}:
            out["unknown"] = True
    except Exception as exc:  # never break prepare on an odd effect
        out["error"] = str(exc)
    return out


def _effect_outset(eff):
    """(left, top, right, bottom) pixels an outer effect may extend past the shape. Estimate only."""
    t = eff.get("type") or ""
    if eff.get("enabled") is False or t in INNER_EFFECTS:
        return (0.0, 0.0, 0.0, 0.0)
    size = float(eff.get("size") or 0)
    if t == "Stroke":
        pos = str(eff.get("position") or "outside").lower()
        grow = 0.0 if pos.startswith("in") else (size / 2.0 if pos.startswith("c") else size)
        return (grow, grow, grow, grow)
    if t == "DropShadow":
        import math
        d = float(eff.get("distance") or 0)
        a = math.radians(float(eff.get("angle") if eff.get("angle") is not None else 120))
        dx, dy = -d * math.cos(a), d * math.sin(a)
        return (max(0.0, size - dx), max(0.0, size - dy), max(0.0, size + dx), max(0.0, size + dy))
    if t == "OuterGlow":
        return (size, size, size, size)
    return (0.0, 0.0, 0.0, 0.0)


def estimate_effect_bounds(logical, effects):
    """Logical bounds expanded by each engine effect's outset. Estimate, not Photoshop's exact box."""
    if not logical:
        return None
    l, t, r, b = logical["left"], logical["top"], logical["right"], logical["bottom"]
    for eff in effects or []:
        if not isinstance(eff, dict):
            continue
        ol, ot, orr, ob = _effect_outset(eff)
        l, t, r, b = min(l, logical["left"] - ol), min(t, logical["top"] - ot), max(r, logical["right"] + orr), max(b, logical["bottom"] + ob)
    return {"left": round(l, 2), "top": round(t, 2), "right": round(r, 2), "bottom": round(b, 2)}


# ---------------------------------------------------------------------------
# Layer tree (visible-aware)
# ---------------------------------------------------------------------------

def _dict_get(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if k in obj:
            return obj[k]
        if isinstance(k, str):
            for alt in (k.lower(), k.upper(), k.encode("latin-1") if k.isascii() else None):
                if alt is not None and alt in obj:
                    return obj[alt]
    return None


def attach_text_style_hints(node, layer):
    """Best-effort warp / fill / non-affine transform hints for classify_text. Never raises."""
    try:
        tf = getattr(layer, "transform", None)
        if tf is not None and isinstance(tf, (list, tuple)) and len(tf) > 6:
            node["text_transform_non_affine"] = True
            node["transform"] = list(tf)
        elif tf is not None and isinstance(tf, (list, tuple)) and len(tf) >= 6:
            node["transform"] = {
                "xx": float(tf[0]), "xy": float(tf[1]), "yx": float(tf[2]), "yy": float(tf[3]),
                "tx": float(tf[4]), "ty": float(tf[5]),
            }
        engine = getattr(layer, "engine_dict", None)
        warp = None
        fill_type = None
        if isinstance(engine, dict):
            wobj = _dict_get(engine, "Warp", "warp")
            if wobj is None:
                # Some psd-tools builds nest DocumentResources / typetool extras alongside EngineDict.
                wobj = _dict_get(engine.get("DocumentResources") if isinstance(engine.get("DocumentResources"), dict) else {},
                                 "Warp", "warp")
            if isinstance(wobj, dict):
                warp = _dict_get(wobj, "DeformStyle", "deformStyle", "WarpStyle", "warpStyle", "warp")
            elif wobj not in (None, ""):
                warp = wobj
            # Gradient / pattern fills on the type itself (not a layer effect).
            style = None
            sr = _dict_get(engine, "StyleRun", "StyleSheet")
            if isinstance(sr, dict):
                arr = _dict_get(sr, "RunArray", "StyleSheetData")
                if isinstance(arr, list) and arr:
                    style = arr[0] if not isinstance(arr[0], dict) else (arr[0].get("StyleSheetData") or arr[0])
                elif isinstance(arr, dict):
                    style = arr
            if isinstance(style, dict):
                fill_type = _dict_get(style, "FillType", "fillType", "FillMethod")
                if _dict_get(style, "GradientFill", "Gradient", "gradient"):
                    fill_type = fill_type or "gradient"
                if _dict_get(style, "PatternFill", "Pattern", "pattern"):
                    fill_type = fill_type or "pattern"
        if warp not in (None, ""):
            name = enum_val(warp) if not isinstance(warp, (str, int, float)) else str(warp)
            if name and name.lower() not in {"0", "none", "warpnone", "warpstylenone"}:
                # Integer 0 is typically warpNone.
                if not (isinstance(warp, (int, float)) and int(warp) == 0):
                    node["warp"] = name
        if fill_type not in (None, ""):
            node["text_fill_type"] = enum_val(fill_type) if not isinstance(fill_type, str) else fill_type
    except Exception:
        pass


def collect_layer(layer, path, depth, index_in_parent, warnings):
    name = layer.name or ""
    kind = layer_kind(layer)
    try:
        lid = int(layer.layer_id) if getattr(layer, "layer_id", None) is not None else None
    except Exception:
        lid = None
        warnings.append(f"layer_id unreadable: {'/'.join(path + [name])}")
    try:
        bbox = [int(layer.left), int(layer.top), int(layer.right), int(layer.bottom)]
        width, height = int(layer.width), int(layer.height)
    except Exception as exc:
        bbox, width, height = None, 0, 0
        warnings.append(f"bbox failed {'/'.join(path + [name])}: {exc}")

    visible = bool(getattr(layer, "visible", True))
    kind_l = kind.lower()
    node = {
        "name": name,
        "path": "/".join(path + [name]) if path else name,
        "depth": depth,
        "index_in_parent": index_in_parent,
        "layer_id": lid,
        "kind": kind,
        "is_group": is_group(layer),
        "is_artboard": "artboard" in kind_l,
        "is_text": ("type" in kind_l or kind_l == "text"),
        "is_adjustment": "adjust" in kind_l or "gradientmap" in kind_l,
        "visible": visible,
        "opacity": getattr(layer, "opacity", None),
        "blend_mode": enum_val(getattr(layer, "blend_mode", None)),
        "clipping": bool(getattr(layer, "clipping", False)),
        "bbox": bbox,
        "width": width,
        "height": height,
        "effects": [],
        "text": None,
        "children": [],
        "_layer": layer,
    }
    try:
        fx = layer.effects
        if fx:
            params = []
            for effect in fx:
                enabled = getattr(effect, "enabled", True)
                if enabled:
                    node["effects"].append(effect.__class__.__name__)
                    params.append(effect_params(effect))
            if params:
                node["effect_params"] = params
    except Exception:
        pass
    try:
        so = getattr(layer, "smart_object", None) if "smart" in kind_l else None
        if so is not None:
            node["smart_object_id"] = str(getattr(so, "unique_id", "") or "") or None
            node["smart_object_file"] = str(getattr(so, "filename", "") or "") or None
    except Exception:
        pass
    if node["is_text"]:
        try:
            text = getattr(layer, "text", None)
            node["text"] = str(text) if text is not None else None
        except Exception as exc:
            warnings.append(f"text unreadable {node['path']}: {exc}")
        attach_text_style_hints(node, layer)
    if is_group(layer):
        # Walk hidden groups too: the agent needs hidden state layers in the tree.
        for i, child in enumerate(layer):
            node["children"].append(collect_layer(child, path + [name], depth + 1, i, warnings))
    return node


def visible_children(node):
    return [c for c in (node.get("children") or []) if c.get("visible")]


def attach_clips(children):
    """Children are stored top-to-bottom (PSD order). Clipped layers sit above their base."""
    units = []
    pending = []
    for child in children:
        if child.get("clipping"):
            pending.append(child)
            continue
        units.append({"base": child, "clipped": pending})
        pending = []
    if pending:
        units.append({"base": None, "clipped": pending})
    return units


def has_visible_text(node):
    if not node.get("visible"):
        return False
    if node.get("is_text"):
        return True
    return any(has_visible_text(c) for c in visible_children(node))


def descendant_visible_ids(node):
    ids = []
    if not node.get("visible"):
        return ids
    if node.get("layer_id") is not None:
        ids.append(node["layer_id"])
    for child in visible_children(node):
        ids.extend(descendant_visible_ids(child))
    return ids


# ---------------------------------------------------------------------------
# Naming rules (optional --rules JSON; generic defaults otherwise)
# ---------------------------------------------------------------------------

DEFAULT_RULES = {
    "default_module": "main",
    "chrome_module": "common",
    "module_keywords": [],
    "type_keywords": [
        ["button", "btn"], ["btn", "btn"],
        ["tab", "tab"],
        ["icon", "icon"],
        ["frame", "frame"],
        ["background", "bg"], ["panel", "bg"], ["bg", "bg"],
        ["deco", "deco"],
        ["mask", "mask"],
        ["item", "item"],
        ["img", "img"],
    ],
    "purpose_keywords": [
        ["navbar", "navbar"], ["topbar", "topbar"],
        ["timer", "timer"], ["title", "title"],
        ["avatar", "avatar"],
        ["screen", "screen"], ["list", "list"],
        ["slot", "slot"], ["divider", "divider"],
    ],
    "state_keywords": [
        ["_off", "off"], ["_on", "on"],
        ["off", "off"], ["on", "on"],
        ["normal", "normal"],
        ["empty", "empty"], ["full", "full"],
        ["pressed", "pressed"], ["disabled", "disabled"],
        ["hover", "hover"], ["selected", "on"],
    ],
    "skip_tokens": ["copy", "group", "layer"],
    "special_layer_names": {},
    "fixed_labels": [],
    "chrome_path_keywords": ["navbar", "topbar", "header", "footer"],
    "role_path_groups": {
        "tab_label": ["tab", "segment"],
        "nav_label": ["navbar", "footer"],
        "title_label": ["topbar", "header"],
    },
    "list_item_pattern": r"/list\s*(\d+)(?:/|$)",
    "self_row_keywords": ["self"],
    "art_text_name_keywords": ["title"],
    "value_name_keywords": ["username", "nickname"],
}


def _pairs(seq):
    out = []
    if isinstance(seq, dict):
        seq = seq.items()
    for item in seq or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append((str(item[0]), str(item[1])))
    return out


def merge_rules(base, overlay):
    out = json.loads(json.dumps(base))
    for key, val in (overlay or {}).items():
        if val is None:
            continue
        if key in {"module_keywords", "type_keywords", "purpose_keywords", "state_keywords"}:
            seen = set()
            merged = []
            for pair in _pairs(val) + _pairs(out.get(key)):
                if pair in seen:
                    continue
                seen.add(pair)
                merged.append(list(pair))
            out[key] = merged
        elif key in {
            "skip_tokens", "fixed_labels", "chrome_path_keywords",
            "self_row_keywords", "art_text_name_keywords", "value_name_keywords",
        }:
            seen = set()
            merged = []
            for item in list(val) + list(out.get(key) or []):
                mark = str(item).casefold()
                if mark in seen:
                    continue
                seen.add(mark)
                merged.append(item)
            out[key] = merged
        elif key == "special_layer_names":
            merged = dict(out.get(key) or {})
            merged.update(val)
            out[key] = merged
        elif key == "path_specials":
            out[key] = list(val) + list(out.get(key) or [])
        elif key == "role_path_groups":
            merged = dict(out.get(key) or {})
            for role, paths in (val or {}).items():
                old = [str(p) for p in (merged.get(role) or [])]
                new = [str(p) for p in (paths or [])]
                merged[role] = new + [p for p in old if p not in new]
            out[key] = merged
        else:
            out[key] = val
    return out


class NamingRules:
    def __init__(self, data):
        d = data or {}
        self.module_keywords = _pairs(d.get("module_keywords"))
        self.type_keywords = _pairs(d.get("type_keywords"))
        self.purpose_keywords = _pairs(d.get("purpose_keywords"))
        self.state_keywords = _pairs(d.get("state_keywords"))
        self.skip_tokens = {str(x).lower() for x in (d.get("skip_tokens") or [])}
        special = d.get("special_layer_names") or {}
        self.special_layer_names = {}
        for key, val in special.items():
            if isinstance(val, (list, tuple)) and len(val) >= 2:
                state = val[2] if len(val) > 2 else ""
                self.special_layer_names[str(key).strip().lower()] = (
                    val[0] or "", val[1] or "", state or "",
                )
        self.fixed_labels = {str(x).casefold() for x in (d.get("fixed_labels") or [])}
        self.chrome_path_keywords = [str(x).lower() for x in (d.get("chrome_path_keywords") or [])]
        self.chrome_module = d.get("chrome_module") or "common"
        groups = d.get("role_path_groups") or {}
        self.role_path_groups = {
            str(role): [str(p).lower() for p in (paths or [])]
            for role, paths in groups.items()
        }
        self.list_item_pattern = d.get("list_item_pattern") or r"/list\s*(\d+)(?:/|$)"
        self.self_row_keywords = [str(x).lower() for x in (d.get("self_row_keywords") or [])]
        self.art_text_name_keywords = [str(x).lower() for x in (d.get("art_text_name_keywords") or [])]
        self.default_module = d.get("default_module") or "main"
        self.value_name_keywords = [str(x).lower() for x in (d.get("value_name_keywords") or [])]
        self.path_specials = []
        for item in d.get("path_specials") or []:
            parts = item.get("parts") or [item.get("type"), item.get("purpose"), item.get("state")]
            self.path_specials.append({
                "path_includes": [str(x).lower() for x in (item.get("path_includes") or [])],
                "name": str(item.get("name") or "").strip().lower(),
                "name_prefix": str(item.get("name_prefix") or "").strip().lower(),
                "type": (parts[0] if len(parts) > 0 else "") or "",
                "purpose": (parts[1] if len(parts) > 1 else "") or "",
                "state": (parts[2] if len(parts) > 2 else "") or "",
            })

    @classmethod
    def load(cls, path=None):
        data = json.loads(json.dumps(DEFAULT_RULES))
        if path:
            overlay = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(overlay, dict):
                raise SystemExit(f"rules file must be a JSON object: {path}")
            data = merge_rules(data, overlay)
        return cls(data)


def looks_like_state_name(name: str, rules: NamingRules | None = None) -> bool:
    low = (name or "").lower()
    if re.search(r"(_on|_off)$", low) or "_on" in low or "_off" in low:
        return True
    rules = rules or NamingRules(DEFAULT_RULES)
    return any(src.lower() in low for src, _dst in rules.state_keywords)


def is_large_on(node, art_w, art_h):
    w, h = node.get("width") or 0, node.get("height") or 0
    return w >= art_w * 0.6 or h >= art_h * 0.4 or (w * h >= art_w * art_h * 0.2)


def should_descend(node, art_w, art_h, rules: NamingRules | None = None):
    if not node.get("is_group") or not node.get("visible"):
        return False
    if node.get("is_artboard"):
        return True
    kids = visible_children(node)
    if not kids:
        return False
    if has_visible_text(node):
        return True
    if any(looks_like_state_name(c.get("name") or "", rules) for c in kids):
        return True
    group_kids = [c for c in kids if c.get("is_group")]
    if len(group_kids) >= 2:
        return True
    # Large background + other siblings (icon / deco) must stay separate resources.
    siblings = [c for c in kids if not c.get("clipping")]
    siblings.sort(key=lambda c: (c.get("width") or 0) * (c.get("height") or 0))
    if len(siblings) >= 2 and is_large_on(siblings[-1], art_w, art_h):
        largest = siblings[-1]
        others = siblings[:-1]
        if any((c.get("width") or 0) < (largest.get("width") or 0) * 0.6 for c in others):
            return True
    if is_large_on(node, art_w, art_h) and len(group_kids) >= 1 and len(kids) >= 2:
        return True
    return False


def normalize_text(text: str) -> str:
    return re.sub(r"[\r\n]+", " ", text or "").strip()


def _norm_fill_type(value) -> str:
    s = str(value or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    return s


def _canonical_effect_type(name) -> str:
    raw = str(name or "").strip()
    return TEXT_EFFECT_ALIASES.get(raw, TEXT_EFFECT_ALIASES.get(raw[:1].upper() + raw[1:], raw))


def _enabled_text_effects(node):
    """Enabled, visible layer effects as dicts with at least ``type``.

    Hidden master switch (``effects_visible is False``) or per-effect
    ``enabled is False`` entries are omitted (they are not drawn).
    """
    if node.get("effects_visible") is False:
        return []
    text = node.get("text")
    if isinstance(text, dict) and text.get("effects_visible") is False:
        return []
    params = [e for e in (node.get("effect_params") or []) if isinstance(e, dict)]
    if not params and isinstance(text, dict):
        params = [e for e in (text.get("layer_effects") or []) if isinstance(e, dict)]
    if params:
        return [e for e in params if e.get("enabled") is not False]
    names = list(node.get("effects") or [])
    if not names and isinstance(text, dict):
        names = list(text.get("effects") or [])
    return [{"type": e} for e in names if e]


def text_bake_reasons(node) -> list:
    """Why a fixed text layer must be exported as art_text (PNG), or empty if the engine can draw it.

    Conservative: only a solid-color fill plus Stroke and/or DropShadow stays attribute-only.
    Unreplayable effects (listed in UNREPLAYABLE_TEXT_EFFECTS), unknown effects, a gradient
    or pattern fill (text or stroke), warp, a non-affine transform, or a rasterized /
    non-text layer are baked. Disabled or master-hidden effects are ignored.
    """
    reasons = []
    kind_l = str(node.get("kind") or node.get("source_kind") or "").lower()
    is_text = node.get("is_text")
    if is_text is False or (is_text is None and kind_l and kind_l not in {"", "type", "text", "typetool", "type tool"}):
        # Live Type layers stay editable; pixels / smart objects / shapes are baked.
        if is_text is False or any(tok in kind_l for tok in ("pixel", "smart", "shape", "solid", "raster")):
            reasons.append("rasterized_or_non_text")

    warp = node.get("warp")
    text = node.get("text")
    if isinstance(text, dict):
        warp = warp if warp not in (None, "") else text.get("warp")
    if warp and str(warp).lower() not in {"", "none", "warpnone", "null"}:
        reasons.append(f"warp:{warp}")

    if node.get("text_transform_non_affine") or node.get("non_affine"):
        reasons.append("non_affine_transform")
    tf = node.get("transform")
    if tf is None and isinstance(text, dict):
        tf = text.get("transform")
    if isinstance(tf, dict):
        extra = ("xz", "yz", "zx", "zy", "zz", "perspective", "persp")
        if any(tf.get(k) not in (None, 0, 0.0) for k in extra):
            reasons.append("non_affine_transform")
    elif isinstance(tf, (list, tuple)) and len(tf) > 6:
        reasons.append("non_affine_transform")

    fill = node.get("text_fill_type") or node.get("fill_type")
    if not fill and isinstance(text, dict):
        fill = text.get("text_fill_type") or text.get("fill_type")
    if fill and _norm_fill_type(fill) not in _SOLID_FILL_TYPES:
        reasons.append(f"text_fill:{fill}")

    for e in _enabled_text_effects(node):
        typ = _canonical_effect_type(e.get("type") or e.get("name"))
        if not typ:
            continue
        if typ in UNREPLAYABLE_TEXT_EFFECTS or typ not in ATTR_TEXT_LAYER_EFFECTS:
            reasons.append(typ)
            continue
        if typ == "Stroke":
            ft = e.get("fill_type")
            if ft and _norm_fill_type(ft) not in _SOLID_FILL_TYPES:
                reasons.append(f"stroke_fill:{ft}")
            bm = str(e.get("blend_mode") or "normal").lower()
            if bm not in {"normal", "none", ""}:
                reasons.append(f"Stroke_blend:{bm}")
        elif typ == "DropShadow":
            bm = str(e.get("blend_mode") or "multiply").lower()
            if bm not in {"normal", "multiply", "none", ""}:
                reasons.append(f"DropShadow_blend:{bm}")
    # unique, keep order
    seen, out = set(), []
    for r in reasons:
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
    return out


def has_heavy_effects(node) -> bool:
    """True when a text layer has unreplayable effects and should be baked as art_text."""
    return bool(text_bake_reasons(node))


def is_fixed_label(text: str, rules: NamingRules) -> bool:
    return normalize_text(text).casefold() in rules.fixed_labels


def is_value_text(text: str, rules: NamingRules) -> bool:
    """Numbers, names, scores, and timers — rendered by the engine, not baked."""
    t = normalize_text(text)
    if not t:
        return False
    if is_fixed_label(t, rules):
        return False
    if re.search(r"\d+\s*[hH小时].*\d+\s*[mM分]|\d+h\s*\d+m", t):
        return True
    if re.search(r"^(score|level)\b", t, re.I) or re.search(r"score\s*\d+", t, re.I):
        return True
    # Place / rank numbers are 1-2 digits only. Three or more digits, or a
    # leading-zero string, are treated as names / ids.
    if re.fullmatch(r"\d{1,2}", t):
        return True
    if re.fullmatch(r"0\d+", t) or re.fullmatch(r"\d{3,}", t):
        return True
    blob = t.casefold()
    if any(k and k in blob for k in rules.value_name_keywords):
        return True
    if re.search(r"[A-Za-z]+\d+", t):
        return True
    if re.search(r"[A-Za-z].*\s+[A-Za-z]", t):
        return True
    if re.fullmatch(r"[\d.,\s]+", t):
        return True
    return False


def classify_text(node, rules: NamingRules) -> str:
    """dynamic_text = runtime-variable content; static_text = fixed labels the engine can draw;
    art_text = fixed labels that must be exported as a PNG.
    """
    raw = node.get("text")
    text = raw.get("content") if isinstance(raw, dict) else (raw or "")
    # Name keywords such as "title" only mark a layer as a fixed label; they do not force a PNG.
    if is_value_text(text, rules):
        return "dynamic_text"
    if text_bake_reasons(node):
        return "art_text"
    return "static_text"


def text_role(path: str, rules: NamingRules) -> str | None:
    blob = (path or "").lower()
    for role, tokens in rules.role_path_groups.items():
        if any(tok and tok in blob for tok in tokens):
            return role
    return None


def apply_pair_consistency(entries, rules: NamingRules):
    """The same text role in an on/off or tab pair must share one kind.
    If any member is art_text, all become art_text so no visible label is dropped.
    """
    groups = {}
    for entry in entries:
        if entry.get("kind") not in TEXT_KINDS:
            continue
        content = normalize_text((entry.get("text") or {}).get("content") or "")
        if not content or is_value_text(content, rules):
            continue
        path = entry.get("source_path") or ""
        role = text_role(path, rules) or ("label:" + content.casefold())
        groups.setdefault(role, []).append(entry)
    for members in groups.values():
        if any(m.get("kind") == "art_text" for m in members):
            for member in members:
                if member.get("kind") != "art_text":
                    member["kind"] = "art_text"
                    member.setdefault("notes", []).append(
                        "kind raised to art_text to match on/off or tab-label pair"
                    )


def _path_relative_to_artboard(path: str, artboard_name: str) -> str:
    path_l = (path or "").lower()
    art_l = (artboard_name or "").lower()
    if art_l and (path_l == art_l or path_l.startswith(art_l + "/")):
        return path_l[len(art_l):].lstrip("/")
    return path_l


def infer_module(path: str, artboard_name: str, rules: NamingRules) -> str:
    art_l = (artboard_name or "").lower()
    rel = _path_relative_to_artboard(path, artboard_name)
    # Shared chrome is decided from the layer path after stripping the artboard
    # prefix, so a board named after the feature does not relabel its navbar.
    if any(s and s in rel for s in rules.chrome_path_keywords):
        for src, dst in rules.module_keywords:
            if src.lower() in rel and dst != rules.chrome_module:
                return dst
        return rules.chrome_module
    blob = f"{art_l}/{rel}"
    for src, dst in rules.module_keywords:
        if src.lower() in blob:
            return dst
    slug = slugify(artboard_name)
    return slug or rules.default_module


def list_item_index(path: str, rules: NamingRules):
    try:
        m = re.search(rules.list_item_pattern, path or "", re.I)
    except re.error:
        m = None
    return m.group(1) if m else None


def text_purpose_from_content(text: str, name: str, rules: NamingRules):
    blob = f"{name} {text}".replace("\r", " ").replace("\n", " ")
    if re.search(r"\d+\s*[hH小时].*\d+\s*[mM分]|\d+h\s*\d+m", blob):
        return "timer"
    if re.search(r"^\s*(score|level)\b", blob, re.I) or re.search(r"score\s*\d+", blob, re.I):
        return "score"
    compact = (text or "").replace("\r", "").replace("\n", "").strip()
    if re.fullmatch(r"\d{1,2}", compact):
        return "place"
    if re.fullmatch(r"0\d+", compact) or re.fullmatch(r"\d{3,}", compact):
        return "name"
    low = blob.casefold()
    if any(k and k in low for k in rules.value_name_keywords):
        return "name"
    if is_fixed_label(text or "", rules) or is_fixed_label(name, rules):
        return slugify(normalize_text(text) or name)
    if text and re.search(r"[A-Za-z]", text) and not re.search(r"score|level", text, re.I):
        return "name"
    return None


def infer_type_purpose_state(node, module: str, rules: NamingRules):
    name = node.get("name") or ""
    path = (node.get("path") or "").lower()
    key = name.strip().lower()
    typ = purpose = None
    state = ""
    if key in rules.special_layer_names:
        typ, purpose, state = rules.special_layer_names[key]
    for spec in rules.path_specials:
        if spec["path_includes"] and not any(tok and tok in path for tok in spec["path_includes"]):
            continue
        if spec["name"] and key != spec["name"]:
            continue
        if spec["name_prefix"] and not key.startswith(spec["name_prefix"]):
            continue
        if not spec["name"] and not spec["name_prefix"]:
            continue
        typ = typ or spec["type"]
        purpose = purpose or spec["purpose"]
        state = state or spec["state"]
    typ = typ or find_token(name + " " + path, rules.type_keywords)
    purpose = purpose or find_token(name + " " + path, rules.purpose_keywords)
    state = state or find_token(name + " " + path, rules.state_keywords) or ""
    item_n = list_item_index(node.get("path") or "", rules)
    if node.get("is_text") or node.get("kind_export") in TEXT_KINDS:
        purpose = text_purpose_from_content(node.get("text") or "", name, rules) or purpose
    if item_n:
        if typ in {None, "bg", "item"} and (purpose in {None, "item", "base", "list"} or typ == "bg"):
            typ = typ or "bg"
            purpose = f"item{item_n}"
        elif typ == "icon" and purpose:
            if not state:
                purpose = f"{purpose}_item{item_n}"
        elif purpose in {"score", "name", "place"}:
            purpose = f"{purpose}_item{item_n}"
    if item_n and not state and any(k and k in path for k in rules.self_row_keywords):
        state = "self"
    if node.get("is_text") or node.get("kind_export") in TEXT_KINDS:
        typ = typ or "img"
        purpose = purpose or slugify(re.sub(r"[\d.]+", " ", name)) or "label"
        if purpose in {"bg", "btn", "icon", "frame", "tab", "item", "deco"}:
            purpose = "label"
    if typ is None:
        if purpose in {"navbar", "topbar", "screen", "base", "item", "list", "title", "slot"}:
            typ = "bg"
        elif purpose in {"hline", "vline", "divider"}:
            typ = "deco"
        elif purpose and purpose.split("_")[0] in {"avatar", "badge", "icon"}:
            typ = "icon"
        elif (node.get("width") or 0) >= 200 and (node.get("height") or 0) >= 80:
            typ = "bg"
        elif (node.get("width") or 0) <= 200 and (node.get("height") or 0) <= 200:
            typ = "icon"
        else:
            typ = "img"
    if not purpose:
        latin = slugify(name)
        if latin and latin not in rules.skip_tokens and not re.fullmatch(r"l?\d+", latin):
            purpose = latin
        else:
            purpose = f"l{node.get('layer_id') or 0}"
    return typ, purpose, state


def unique_name(parts, used):
    parts = [p for p in parts if p]
    base = "_".join(parts)
    base = re.sub(r"_+", "_", base).strip("_").lower()
    if not base:
        base = "slice"
    candidate = base
    n = 2
    while candidate in used:
        candidate = f"{base}_{n}"
        n += 1
    used.add(candidate)
    return candidate



# ---------------------------------------------------------------------------
# Nine-slice: longest uniform adjacent column/row run -> margins
# ---------------------------------------------------------------------------

def detect_nine_slice(image, min_run=8, tol=1.0, min_side=48):
    """Return {left,top,right,bottom} insets, or None if not a stretchable panel."""
    arr = np.asarray(image.convert("RGBA"), dtype=np.float32)
    h, w = arr.shape[:2]
    if w < min_side or h < min_side:
        return None

    def longest_run(axis):
        seq = arr if axis == 1 else arr.transpose(1, 0, 2)
        n = seq.shape[1]
        same = []
        for i in range(n - 1):
            delta = np.abs(seq[:, i] - seq[:, i + 1])
            same.append(float(delta.max()) <= tol * 4 and float(delta.mean()) <= tol)
        best = (0, 0, 0)
        i = 0
        while i < n - 1:
            if same[i]:
                j = i
                while j < n - 1 and same[j]:
                    j += 1
                if j - i > best[0]:
                    best = (j - i, i, j)
                i = j
            else:
                i += 1
        return best

    rx, ry = longest_run(1), longest_run(0)
    left = right = top = bottom = 0
    ok = False
    if rx[0] >= min_run:
        left, right = rx[1] + 1, w - rx[2] - 1
        ok = True
    if ry[0] >= min_run:
        top, bottom = ry[1] + 1, h - ry[2] - 1
        ok = True
    if not ok:
        return None
    # Keep a stretchable center; treat full-bleed "uniform" images as not nine-slice.
    if left + right >= w - 2 or top + bottom >= h - 2:
        return None
    # A 1px "corner" on an otherwise uniform fill is not a real cap/stroke.
    if (left + right) < min_run and (top + bottom) < min_run:
        return None
    if left < 0 or right < 0 or top < 0 or bottom < 0:
        return None
    # Corners must remain (a 1px "corner" with a huge stretch is fine; reject 0-size image).
    return {"left": int(left), "top": int(top), "right": int(right), "bottom": int(bottom)}


def _visibility_chain(layer):
    chain = []
    current = layer
    while current is not None:
        chain.append(current)
        current = getattr(current, "parent", None)
    return chain


def try_composite(node, warnings, force=None):
    """Rasterize one layer. Hidden layers (or visible layers under a hidden
    ancestor) need the ancestor chain temporarily shown; psd-tools otherwise
    returns a fully transparent image. Visibility is always restored. The PSD
    file is never written.
    """
    layer = node.get("_layer")
    if layer is None:
        return None
    if force is None:
        force = not bool(node.get("visible", True))
    saved = []
    try:
        if force:
            for item in _visibility_chain(layer):
                try:
                    current = item.visible
                except Exception:
                    continue
                if current is not False:
                    continue
                try:
                    item.visible = True
                    saved.append((item, False))
                except Exception:
                    pass
        try:
            image = layer.composite(force=True) if force else layer.composite()
        except TypeError:
            image = layer.composite()
        if image is None:
            topil = getattr(layer, "topil", None)
            if callable(topil):
                try:
                    image = topil()
                except Exception:
                    image = None
        if image is None:
            return None
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        return image
    except Exception as exc:
        warnings.append(f"composite failed {node.get('path')}: {exc}")
        return None
    finally:
        for item, value in saved:
            try:
                item.visible = value
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def list_item_tag(path: str) -> str:
    """If the layer sits under a repeated list item, return a stable itemN tag."""
    return ""


def build_entries(artboard, art_w, art_h, module_hint, warnings, rules=None):
    rules = rules or NamingRules(DEFAULT_RULES)
    used = set()
    entries = []
    tree_nodes = []
    item_counters = {}

    def item_suffix(node):
        # Number repeated instances under the same parent path by sibling order.
        parent = "/".join((node.get("path") or "").split("/")[:-1])
        key = parent
        item_counters[key] = item_counters.get(key, 0) + 1
        return item_counters[key]

    def emit(node, clipped, parent_name, z_base, siblings_for_index):
        if node is None or not node.get("visible"):
            return None
        if node.get("is_adjustment") and not clipped:
            return None
        if (node.get("width") or 0) <= 0 or (node.get("height") or 0) <= 0:
            warnings.append(f"skip empty layer {node.get('path')}")
            return None

        select_ids = descendant_visible_ids(node)
        for c in clipped:
            select_ids.extend(descendant_visible_ids(c))
        # unique, keep order
        seen = set()
        ids = []
        for i in select_ids:
            if i not in seen:
                seen.add(i)
                ids.append(i)
        if not ids:
            return None

        kind = "image"
        text_attrs = None
        if node.get("is_text") and not clipped:
            kind = classify_text(node, rules)
            content = node.get("text") or ""
            if isinstance(content, dict):
                content = content.get("content") or ""
            text_attrs = {
                "content": content,
                "font": None,
                "size": None,
                "color": None,
                "alignment": None,
                "stroke": None,
                "opacity": (node.get("opacity") / 255.0) if isinstance(node.get("opacity"), (int, float)) else None,
                "effects": list(node.get("effects") or []),
                "layer_effects": list(node.get("effect_params") or []),
                "effects_visible": node.get("effects_visible", True),
            }
            if node.get("warp"):
                text_attrs["warp"] = node["warp"]

        module = infer_module(node.get("path") or "", module_hint, rules)
        typ, purpose, state = infer_type_purpose_state({**node, "kind_export": kind}, module, rules)
        if kind in TEXT_KINDS and typ not in {"img"}:
            typ = "img"
        name = unique_name([module, typ, purpose, state], used)

        nine = None
        image = None
        if not is_text_kind(kind):
            image = try_composite(node, warnings)
            if image is not None:
                nine = detect_nine_slice(image)
                if nine:
                    kind = "nine_slice"

        bbox = node.get("bbox") or [0, 0, 0, 0]
        entry = {
            "name": name,
            "layer_id": node.get("layer_id"),
            "select_ids": ids,
            "source_name": node.get("name") or "",
            "source_path": node.get("path") or "",
            "source_kind": node.get("kind"),
            "kind": kind,
            "logical_bounds": bbox,
            "nine_slice": nine,
            "text": text_attrs,
            "parent": parent_name,
            "opacity": node.get("opacity"),
            "blend_mode": node.get("blend_mode"),
            "notes": [],
        }
        if is_attr_text(kind):
            entry["notes"].append(f"{kind} is not rasterized; Photoshop export writes attributes only.")
        if kind == "art_text":
            reasons = text_bake_reasons(node)
            if reasons:
                entry["art_text_reasons"] = reasons
                entry["notes"].append("baked as art_text: " + ", ".join(reasons))
        if node.get("effects"):
            entry["notes"].append("has layer effects; psd-tools logical_bounds exclude effects.")
        if clipped:
            entry["notes"].append(f"includes {len(clipped)} visible clipped layer(s)")
        entries.append(entry)
        return {
            "name": name,
            "kind": kind,
            "layer_id": node.get("layer_id"),
            "children": [],
        }

    def walk(node, parent_name, z_base=0):
        children_out = []
        if should_descend(node, art_w, art_h, rules):
            container = {
                "name": None,
                "kind": "group",
                "layer_id": node.get("layer_id"),
                "children": [],
            }
            # Name the container for hierarchy only (not a slice).
            module = infer_module(node.get("path") or "", module_hint, rules)
            typ, purpose, state = infer_type_purpose_state(node, module, rules)
            # groups use a name that won't collide with slices: prefix group_
            gname = unique_name(["group", module, typ, purpose, state], used)
            container["name"] = gname
            units = attach_clips(visible_children(node))
            for unit in units:
                base, clipped = unit["base"], [c for c in unit["clipped"] if c.get("visible")]
                if base is None:
                    warnings.append(f"orphaned clipping layers under {node.get('path')}")
                    continue
                if not base.get("visible"):
                    continue
                if should_descend(base, art_w, art_h, rules) and not clipped:
                    child = walk(base, gname)
                    if child:
                        container["children"].append(child)
                else:
                    child = emit(base, clipped, gname, z_base, units)
                    if child:
                        container["children"].append(child)
            return container
        # export the group / layer as one unit
        return emit(node, [], parent_name, z_base, [])

    tree = walk(artboard, None)
    apply_pair_consistency(entries, rules)
    by_name = {e["name"]: e for e in entries}

    def sync_tree_kinds(node):
        if not node:
            return
        entry = by_name.get(node.get("name"))
        if entry:
            node["kind"] = entry["kind"]
        for child in node.get("children") or []:
            sync_tree_kinds(child)

    if tree:
        sync_tree_kinds(tree)
        tree_nodes.append(tree)
    return entries, tree_nodes


def list_artboards(roots):
    found = []

    def walk(nodes):
        for node in nodes:
            if node.get("is_artboard"):
                found.append(node)
            elif node.get("children"):
                walk(node["children"])

    walk(roots)
    return found


def make_canvas_target(psd, roots):
    return {
        "name": "canvas",
        "path": "",
        "depth": 0,
        "index_in_parent": 0,
        "layer_id": None,
        "kind": "artboard",
        "is_group": True,
        "is_artboard": True,
        "is_text": False,
        "is_adjustment": False,
        "visible": True,
        "opacity": 255,
        "blend_mode": "NORMAL",
        "clipping": False,
        "bbox": [0, 0, int(psd.width), int(psd.height)],
        "width": int(psd.width),
        "height": int(psd.height),
        "effects": [],
        "text": None,
        "children": roots,
        "source_is_canvas": True,
    }


def select_targets(roots, needle, psd=None):
    """Return one or more infer targets.

    --artboard <name or id> selects a single artboard (or top-level layer).
    With no --artboard, every visible top-level artboard is processed; if the
    document has none, the whole canvas is treated as one target.
    """
    artboards = list_artboards(roots)
    if needle is None:
        visible = [a for a in artboards if a.get("visible")]
        if visible:
            return visible
        if psd is not None:
            return [make_canvas_target(psd, roots)]
        if roots:
            return [r for r in roots if r.get("visible")] or list(roots)
        raise SystemExit("no visible artboard or canvas target")
    raw = str(needle)
    for node in artboards + [r for r in roots if r not in artboards]:
        if raw == str(node.get("layer_id")) or raw == (node.get("name") or ""):
            return [node]
        found = find_artboard_in([node], raw)
        if found:
            return [found]
    found = find_artboard_in(roots, raw)
    if found:
        return [found]
    raise SystemExit(f"artboard not found: {needle}")


def target_slug(target, used):
    slug = slugify(target.get("name") or "") or (
        f"artboard_{target.get('layer_id')}" if target.get("layer_id") is not None else "canvas"
    )
    return unique_name([slug], used)




def find_artboard_in(nodes, raw):
    for n in nodes:
        if n.get("is_artboard") and (raw == str(n.get("layer_id")) or raw == (n.get("name") or "")):
            return n
        got = find_artboard_in(n.get("children") or [], raw)
        if got:
            return got
    return None


def load_font(size):
    for path in (
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibri.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def artboard_image(artboard, warnings):
    image = try_composite(artboard, warnings)
    if image is not None:
        return image
    # Fallback: empty canvas
    w, h = artboard.get("width") or 1, artboard.get("height") or 1
    return Image.new("RGBA", (w, h), (24, 24, 24, 255))


def annotate_preview(base: Image.Image, entries, art_origin, out_path: Path, title: str):
    ox, oy = art_origin
    image = base.convert("RGBA")
    # Dim the artwork slightly so overlays read clearly.
    canvas = Image.new("RGBA", image.size, (0, 0, 0, 0))
    canvas.paste(image, (0, 0))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = load_font(16)
    font_sm = load_font(13)
    for entry in entries:
        bbox = entry.get("logical_bounds") or [0, 0, 0, 0]
        if len(bbox) < 4:
            warn(f"skipping entry with incomplete bounds: {entry.get('name')}")
            continue
        l, t, r, b = bbox[0], bbox[1], bbox[2], bbox[3]
        l, t, r, b = l - ox, t - oy, r - ox, b - oy
        box = normalize_box(l, t, r, b, width=overlay.width, height=overlay.height, name=entry.get("name"))
        if box is None:
            continue
        l, t, r, b = box
        # Pillow rectangles are inclusive; keep the far edge inside the box.
        r_draw, b_draw = max(l, r - 1), max(t, b - 1)
        color = KIND_COLORS.get(entry["kind"], (255, 255, 255))
        draw.rectangle([l, t, r_draw, b_draw], outline=color + (255,), width=2)
        if entry.get("effect_mode") == "engine" and entry.get("engine_effects"):
            eb = estimate_effect_bounds({"left": l, "top": t, "right": r, "bottom": b}, entry["engine_effects"])
            ebox = normalize_box(eb["left"], eb["top"], eb["right"], eb["bottom"], width=overlay.width, height=overlay.height)
            if ebox is not None and tuple(ebox) != (l, t, r, b):
                draw.rectangle([ebox[0], ebox[1], max(ebox[0], ebox[2] - 1), max(ebox[1], ebox[3] - 1)], outline=(255, 60, 200, 200), width=1)
        nine = entry.get("nine_slice")
        if nine and entry["kind"] == "nine_slice":
            guide = (255, 220, 80, 220)
            left_g = min(max(l, l + nine["left"]), r_draw)
            right_g = min(max(l, r_draw - nine["right"]), r_draw)
            top_g = min(max(t, t + nine["top"]), b_draw)
            bot_g = min(max(t, b_draw - nine["bottom"]), b_draw)
            draw.line([(left_g, t), (left_g, b_draw)], fill=guide, width=1)
            draw.line([(right_g, t), (right_g, b_draw)], fill=guide, width=1)
            draw.line([(l, top_g), (r_draw, top_g)], fill=guide, width=1)
            draw.line([(l, bot_g), (r_draw, bot_g)], fill=guide, width=1)
        label = f"{entry['name']}  [{entry['kind']}]"
        if entry.get("reuse_of") and entry.get("reuse_of") != entry.get("name"):
            label += f" → {entry['reuse_of']}"
        if entry.get("effect_mode"):
            fx = ",".join((e.get("type") or "?") for e in (entry.get("engine_effects") or []) if isinstance(e, dict))
            label += f" fx:{entry['effect_mode']}" + (f"({fx})" if fx else "")
        # Place the label above the box if there is room, else inside.
        ty = t - 16 if t >= 18 else t + 2
        tx = max(2, l)
        # background strip for readability
        try:
            tw = draw.textlength(label, font=font_sm)
        except Exception:
            tw = 8 * len(label)
        lab = normalize_box(tx, ty, tx + tw + 6, ty + 16, width=overlay.width, height=overlay.height, name=entry.get("name"))
        if lab is not None:
            lx0, ly0, lx1, ly1 = lab
            draw.rectangle([lx0, ly0, lx1, ly1], fill=(0, 0, 0, 160))
            draw.text((lx0 + 3, ly0 + 1), label, fill=color + (255,), font=font_sm)
    out = Image.alpha_composite(canvas, overlay).convert("RGB")
    # Legend bar at the top
    legend = Image.new("RGB", (out.width, 36), (20, 20, 20))
    ld = ImageDraw.Draw(legend)
    ld.text((8, 8), title, fill=(255, 255, 255), font=font)
    x = min(out.width - 10, 8 + int(ld.textlength(title, font=font) + 24) if hasattr(ld, "textlength") else 400)
    for kind, color in KIND_COLORS.items():
        ld.rectangle([x, 10, x + 12, 22], fill=color)
        ld.text((x + 16, 8), kind, fill=(220, 220, 220), font=font_sm)
        x += 16 + int(ld.textlength(kind, font=font_sm) if hasattr(ld, "textlength") else 80) + 16
    final = Image.new("RGB", (out.width, out.height + 36), (20, 20, 20))
    final.paste(legend, (0, 0))
    final.paste(out, (0, 36))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    final.save(out_path, "PNG")
    return out_path


def strip_runtime(entries):
    clean = []
    for e in entries:
        item = {k: v for k, v in e.items() if not k.startswith("_")}
        clean.append(item)
    return clean


def all_children(node):
    return list(node.get("children") or [])


def descendant_ids(node, include_hidden=True):
    ids = []
    if not include_hidden and not node.get("visible"):
        return ids
    if node.get("layer_id") is not None:
        ids.append(node["layer_id"])
    for child in all_children(node):
        ids.extend(descendant_ids(child, include_hidden=include_hidden))
    return ids


def strip_tree(node):
    if not node:
        return None
    out = {k: v for k, v in node.items() if not k.startswith("_") and k != "children"}
    out["children"] = [strip_tree(c) for c in (node.get("children") or [])]
    return out


def bbox_intersects(a, b):
    if not a or not b or len(a) < 4 or len(b) < 4:
        return False
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def iter_exportable_nodes(node, include_hidden=True):
    """Yield (base, clipped_list) for leaves and non-descended clip units."""
    kids = all_children(node)
    if not kids:
        if node.get("is_adjustment"):
            return
        if node.get("is_artboard"):
            return
        if (node.get("width") or 0) <= 0 or (node.get("height") or 0) <= 0:
            return
        if include_hidden or node.get("visible"):
            yield node, []
        return
    units = attach_clips(kids)
    for unit in units:
        base, clipped = unit["base"], unit["clipped"]
        if base is None:
            for c in clipped:
                if include_hidden or c.get("visible"):
                    yield from iter_exportable_nodes(c, include_hidden)
            continue
        if not include_hidden and not base.get("visible"):
            continue
        # Groups with children are opened so parts stay independent.
        if base.get("is_group") and all_children(base) and not base.get("is_text"):
            yield from iter_exportable_nodes(base, include_hidden)
            for c in clipped:
                if include_hidden or c.get("visible"):
                    yield from iter_exportable_nodes(c, include_hidden)
            continue
        clips = [c for c in clipped if include_hidden or c.get("visible")]
        yield base, clips


def public_crop_name(layer_id):
    return f"{int(layer_id)}.png"


def node_effect_names(node):
    return [e for e in (node.get("effects") or []) if e]


def is_effect_group(node) -> bool:
    """A folder/group (not an artboard) that carries its own layer effects."""
    return bool(
        node
        and node.get("is_group")
        and not node.get("is_artboard")
        and node.get("layer_id") is not None
        and node_effect_names(node)
    )


def nearest_effect_group(ancestors):
    """Nearest ancestor group that has layer effects. Artboards are skipped."""
    for anc in reversed(ancestors or []):
        if is_effect_group(anc):
            return anc
    return None


def iter_effect_groups(node, artboard_bbox=None, ancestors=None):
    """Yield (group, ancestors) for groups with layer effects under node."""
    ancestors = list(ancestors or [])
    if is_effect_group(node):
        if artboard_bbox is None or bbox_intersects(node.get("bbox"), artboard_bbox):
            yield node, ancestors
    for child in all_children(node):
        yield from iter_effect_groups(child, artboard_bbox, ancestors + [node])


def collect_descendant_leaf_ids(node):
    """Layer ids of descendant leaves under node (not the node itself)."""
    ids = []
    for child in all_children(node):
        grand = all_children(child)
        if grand:
            ids.extend(collect_descendant_leaf_ids(child))
        elif child.get("layer_id") is not None and not child.get("is_adjustment"):
            ids.append(child["layer_id"])
    return ids


def leaf_to_effect_group_map(root, artboard_bbox=None):
    """Map each leaf layer_id to the nearest ancestor group that has effects."""
    mapping = {}

    def walk(node, ancestors):
        kids = all_children(node)
        if not kids and node.get("layer_id") is not None and not node.get("is_group"):
            group = nearest_effect_group(ancestors)
            if group is not None:
                mapping[node["layer_id"]] = group
        for child in kids:
            walk(child, ancestors + [node])

    walk(root, [])
    return mapping


def effect_group_record(group, crop_rel=None, size=None, ancestors=None):
    lid = group["layer_id"]
    return {
        "layer_id": lid,
        "name": group.get("name") or "",
        "path": group.get("path") or "",
        "kind": group.get("kind") or "group",
        "is_group": True,
        "is_text": False,
        "effects": node_effect_names(group),
        "effect_params": list(group.get("effect_params") or []),
        "visible": bool(group.get("visible")),
        "bbox": group.get("bbox"),
        "child_leaf_ids": collect_descendant_leaf_ids(group),
        "child_ids": [i for i in descendant_ids(group) if i != lid],
        "ancestor_ids": [a["layer_id"] for a in (ancestors or []) if a.get("layer_id") is not None],
        "select_ids": [lid],
        "suggested_select_ids": [lid],
        "engine_select_ids": [
            c["layer_id"] for c in all_children(group)
            if c.get("layer_id") is not None and c.get("visible", True)
        ],
        "crop": crop_rel,
        "size": size,
        "notes": (
            "Layer effects such as DropShadow live on this group, not on the inner leaf. "
            "Pick an effect_mode per part. baked (default): layer_id and select_ids = this group id; "
            "visible bounds include the baked effect, logical bounds exclude it. "
            "engine: select the children (engine_select_ids), set effect_mode 'engine' and copy "
            "effect_params into engine_effects so the engine draws them. "
            "Never select only the children without one of these: the group effect would be lost silently. "
            "A missing crop for this id is not a reason to fall back to a child."
        ),
    }


def write_crop_sheet(items, out_path, thumb=160, cols=8):
    """Contact sheet of crops with id / visibility labels (agent overview)."""
    if not items:
        return None
    font = load_font(13)
    font_sm = load_font(11)
    rows = (len(items) + cols - 1) // cols
    cell_w, cell_h = thumb + 8, thumb + 36
    sheet = Image.new("RGB", (cols * cell_w + 8, rows * cell_h + 8), (30, 30, 30))
    draw = ImageDraw.Draw(sheet)
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    for i, item in enumerate(items):
        r, c = divmod(i, cols)
        x, y = 8 + c * cell_w, 8 + r * cell_h
        path = item.get("path")
        if path and Path(path).exists():
            try:
                im = Image.open(path).convert("RGBA")
                im.thumbnail((thumb, thumb), resampling)
                bg = Image.new("RGBA", (thumb, thumb), (40, 40, 40, 255))
                ox = (thumb - im.width) // 2
                oy = (thumb - im.height) // 2
                bg.paste(im, (ox, oy), im)
                sheet.paste(bg.convert("RGB"), (x, y))
            except Exception:
                draw.rectangle([x, y, x + thumb, y + thumb], fill=(60, 40, 40))
        flag = "HID" if not item.get("visible") else "vis"
        extra = item.get("flag")
        if extra:
            flag = f"{flag}/{extra}"
        label = f"{item.get('layer_id')} {flag}"
        draw.text((x, y + thumb + 2), label, fill=(255, 220, 80) if flag == "HID" else (200, 200, 200), font=font_sm)
        name = (item.get("name") or "")[:18]
        draw.text((x, y + thumb + 16), name, fill=(180, 180, 180), font=font_sm)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=85)
    return out_path


def open_psd(psd_path, warnings=None):
    if PSDImage is None:
        raise SystemExit("psd-tools is required. Install it in a project venv (not system Python); see references/slicing.md.")
    psd_path = Path(psd_path)
    open_kwargs = {}
    if ParseLimits is not None:
        open_kwargs["parse_limits"] = ParseLimits(
            max_objects=None, max_total_bytes=None, max_read_bytes=None
        )
    return PSDImage.open(str(psd_path), **open_kwargs)


def walk_document(psd):
    warnings = []
    roots = []
    for i, layer in enumerate(psd):
        roots.append(collect_layer(layer, [], 1, i, warnings))
    return roots, warnings


def prepare_target(target, psd, psd_path, dest: Path, time_base, warnings):
    dest.mkdir(parents=True, exist_ok=True)
    bbox = target.get("bbox") or [0, 0, target["width"], target["height"]]
    art_w, art_h = target["width"], target["height"]
    print(
        f"target {target.get('name')} id={target.get('layer_id')} {art_w}x{art_h} bbox={bbox}",
        flush=True,
    )
    tree = strip_tree(target)
    tree_path = dest / "tree.json"
    tree_doc = {
        "source": {
            "file": str(psd_path),
            "doc_size": [int(psd.width), int(psd.height)],
            "color_mode": enum_val(getattr(psd, "color_mode", None)),
            "depth": getattr(psd, "depth", None),
            "artboard": {
                "id": target.get("layer_id"),
                "name": target.get("name"),
                "bounds": bbox,
                "size": [art_w, art_h],
            },
        },
        "tree": tree,
        "notes": [
            "Includes hidden layers. Visibility is in each node's visible flag.",
            "The agent uses this tree with the full-render preview and crops to write the manifest.",
            "Do not copy raw PSD layer names into export file names.",
        ],
    }
    tree_path.write_text(json.dumps(tree_doc, ensure_ascii=False, indent=2), encoding="utf-8")

    t3 = time.time()
    preview_base = artboard_image(target, warnings)
    print(f"artboard composite in {time.time()-t3:.1f}s size={preview_base.size}", flush=True)
    render_path = dest / "full_render_preview.png"
    preview_base.convert("RGB").save(render_path, "PNG")

    crops_dir = dest / "crops"
    if crops_dir.exists():
        for old in crops_dir.glob("*.png"):
            try:
                old.unlink()
            except Exception:
                pass
    crops_dir.mkdir(parents=True, exist_ok=True)

    crop_records = []
    nine_candidates = []
    t4 = time.time()
    units = list(iter_exportable_nodes(target, include_hidden=True))
    for base, clipped in units:
        if not bbox_intersects(base.get("bbox"), bbox) and not any(
            bbox_intersects(c.get("bbox"), bbox) for c in clipped
        ):
            continue
        lid = base.get("layer_id")
        if lid is None:
            continue
        image = try_composite(base, warnings, force=True)
        if image is None or image.size[0] <= 0 or image.size[1] <= 0:
            continue
        arr = __import__("numpy").asarray(image)
        if arr.size == 0 or int(arr[..., 3].max()) == 0:
            continue
        out_png = crops_dir / public_crop_name(lid)
        image.save(out_png, "PNG")
        nine = None
        if not base.get("is_text"):
            nine = detect_nine_slice(image)
        rec = {
            "layer_id": lid,
            "name": base.get("name") or "",
            "path": base.get("source_path") or base.get("path") or "",
            "kind": base.get("kind"),
            "is_text": bool(base.get("is_text")),
            "text": base.get("text"),
            "effects": list(base.get("effects") or []),
            "effect_params": list(base.get("effect_params") or []),
            "smart_object_id": base.get("smart_object_id"),
            "smart_object_file": base.get("smart_object_file"),
            "visible": bool(base.get("visible")),
            "clipping": bool(base.get("clipping")),
            "opacity": base.get("opacity"),
            "blend_mode": base.get("blend_mode"),
            "bbox": base.get("bbox"),
            "size": list(image.size),
            "select_ids": [lid] + [c["layer_id"] for c in clipped if c.get("layer_id") is not None],
            "clipped_ids": [c["layer_id"] for c in clipped if c.get("layer_id") is not None],
            "crop": f"crops/{public_crop_name(lid)}",
            "path": base.get("path") or "",
            "nine_slice": nine,
        }
        # fix duplicate path key — keep source path
        rec["path"] = base.get("path") or ""
        crop_records.append(rec)
        if nine:
            nine_candidates.append({
                "layer_id": lid,
                "name": rec["name"],
                "path": rec["path"],
                "nine_slice": nine,
                "crop": rec["crop"],
            })

    # Annotate leaves whose nearest parent group carries the effects (e.g. DropShadow).
    leaf_groups = leaf_to_effect_group_map(target, bbox)
    for rec in crop_records:
        group = leaf_groups.get(rec["layer_id"])
        if not group:
            continue
        rec["parent_group_id"] = group["layer_id"]
        rec["parent_effects"] = node_effect_names(group)
        rec["parent_effect_params"] = list(group.get("effect_params") or [])
        rec["suggested_select_ids"] = [group["layer_id"]]
        notes = rec.get("notes") or []
        if isinstance(notes, str):
            notes = [notes]
        notes.append(
            "Nearest parent group has layer effects. baked: export that group id "
            "(suggested_select_ids). engine: export the children with effect_mode 'engine' and "
            "engine_effects copied from parent_effect_params. Never only this leaf without either."
        )
        rec["notes"] = notes

    group_effect_records = []
    existing_ids = {r["layer_id"] for r in crop_records}
    t_g = time.time()
    for group, _anc in iter_effect_groups(target, bbox):
        lid = group.get("layer_id")
        if lid is None or lid in existing_ids:
            continue
        image = None
        try:
            image = try_composite(group, warnings, force=True)
        except Exception as exc:
            warnings.append(f"group composite failed {group.get('path')}: {exc}")
            image = None
        crop_rel = None
        size = None
        if image is not None and image.size[0] > 0 and image.size[1] > 0:
            arr = __import__("numpy").asarray(image)
            if arr.size and int(arr[..., 3].max()) > 0:
                out_png = crops_dir / public_crop_name(lid)
                image.save(out_png, "PNG")
                crop_rel = f"crops/{public_crop_name(lid)}"
                size = list(image.size)
        rec = effect_group_record(group, crop_rel=crop_rel, size=size, ancestors=_anc)
        rec["nine_slice"] = None
        crop_records.append(rec)
        existing_ids.add(lid)
        group_effect_records.append(rec)
    print(
        f"wrote {len(crop_records)} crop-index entries "
        f"({len(group_effect_records)} effect groups) in {time.time()-t4:.1f}s "
        f"(groups {time.time()-t_g:.1f}s)",
        flush=True,
    )
    (dest / "group_effects.json").write_text(
        json.dumps(
            {"count": len(group_effect_records), "groups": group_effect_records},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        reuse = compute_reuse_candidates(crop_records, dest, dest / "reuse_compare")
    except Exception as exc:
        warnings.append(f"reuse candidates failed: {exc}")
        reuse = {"count": 0, "groups": [], "error": str(exc)}
    (dest / "reuse_candidates.json").write_text(json.dumps(reuse, ensure_ascii=False, indent=2), encoding="utf-8")
    (dest / "crops_index.json").write_text(
        json.dumps({"count": len(crop_records), "crops": crop_records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (dest / "nine_slice_candidates.json").write_text(
        json.dumps({"count": len(nine_candidates), "candidates": nine_candidates}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    sheet_items = []
    for rec in crop_records:
        crop_name = rec.get("crop")
        item = {
            "layer_id": rec["layer_id"],
            "name": rec["name"],
            "visible": rec["visible"],
            "path": (dest / crop_name) if crop_name else None,
        }
        if rec.get("is_group"):
            item["flag"] = "FX"
        sheet_items.append(item)
    sheet_path = dest / "crops_sheet.jpg"
    write_crop_sheet(sheet_items, sheet_path)
    hidden_n = sum(1 for r in crop_records if not r["visible"])
    text_n = sum(1 for r in crop_records if r.get("is_text"))
    frame_cands = frame_split_candidates(tree)
    (dest / "frame_split_candidates.json").write_text(json.dumps({
        "candidates": frame_cands,
        "notes": [
            "Frame-like groups (panel, popup, card, button base) with several visible layers. Default: export each "
            "visual layer group as its own part (outer frame / title bar / inset panel nine-slice, decoration as a "
            "non-stretchable image). Bake the whole frame only if the split fails; then set bake_reason.",
            "layer_roles are a box heuristic (outer >= 85 % of the union, header = wide strip at the top, decoration = "
            "non-normal blend or pattern-like name); check them against the crops.",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    materials = {
        "source": tree_doc["source"],
        "full_render_preview": str(render_path),
        "tree": str(tree_path),
        "crops_dir": str(crops_dir),
        "crops_index": str(dest / "crops_index.json"),
        "crops_sheet": str(sheet_path),
        "nine_slice_candidates": str(dest / "nine_slice_candidates.json"),
        "group_effects": str(dest / "group_effects.json"),
        "reuse_candidates": str(dest / "reuse_candidates.json"),
        "frame_split_candidates": str(dest / "frame_split_candidates.json"),
        "stats": {
            "frame_split_candidates": len(frame_cands),
            "reuse_groups": reuse.get("count", 0),
            "crop_count": len(crop_records),
            "hidden_crops": hidden_n,
            "text_crops": text_n,
            "effect_groups": len(group_effect_records),
            "nine_slice_candidates": len(nine_candidates),
            "visible_nodes": sum(1 for r in crop_records if r["visible"]),
            "prepare_sec": round(time.time() - time_base, 2),
        },
        "notes": [
            "Look at full_render_preview.png (visible/default state; psd-tools may omit some layer effects),",
            "the crops (filename is layer_id), crops_sheet.jpg, group_effects.json, and tree.json together.",
            "Then write manifest.json with components / parts / states. Hidden crops are candidates for alternate states.",
            "Do not copy raw PSD layer names. Naming is 模块_类型_用途_状态, lowercase.",
            "If a parent group has layer effects and the inner leaf does not, export the group "
            "(suggested_select_ids / group_effects.json). A missing crop for a group id is not "
            "a reason to fall back to the child. Visible bounds must include baked effects.",
            "psd-tools thumbnails may omit those baked effects; Photoshop export of the group bakes them.",
            "reuse_candidates.json lists repeated elements with pixel similarity and logical evidence. "
            "The agent decides reuse (reuse_of); groups with evidence but pixel differences need a "
            "reuse_decisions entry for the user (see reuse_compare/).",
            "avatar_merge_suspects (in reuse_candidates.json): avatar pictures merged with their frame. Run split-avatar "
            "and split them before asking the user.",
        ],
    }
    (dest / "materials.json").write_text(json.dumps(materials, ensure_ascii=False, indent=2), encoding="utf-8")
    return materials


# ---------------------------------------------------------------------------
# Repeated elements: reuse evidence (helper provides evidence; the agent decides)
# ---------------------------------------------------------------------------

REUSE_MAD_TOL = 2.0  # mean abs diff (0-255) on premultiplied RGBA below which crops count as identical


def name_pattern(text):
    """Name/path with digit runs replaced by '#', lowercased (e.g. 'row 3/bg' -> 'row #/bg')."""
    return re.sub(r"\d+", "#", (text or "").strip().lower())


# Generic game-UI priors (keywords on layer name/path). Defaults only: pixel checks
# against the design can override them, and conflicts go to the user.
UI_ROLE_PRIORS = [
    # (role, prior, keywords)
    ("avatar_frame", "likely_reuse", ("avatar frame", "avatar_frame", "avatarframe", "head frame", "headframe", "头像框", "头像边框")),
    ("avatar", "dynamic_placeholder", ("avatar", "portrait", "headicon", "head_icon", "头像")),
    ("list_row_bg", "likely_reuse", ("row", "cell", "list item", "listitem", "rank item", "rankitem", "slot bg", "条目", "列表", "行底", "排行项")),
    ("rank_badge", "small_fixed_set", ("medal", "badge", "rank icon", "rankicon", "trophy", "crown", "奖牌", "徽章", "名次", "奖杯", "皇冠")),
    ("currency_icon", "likely_reuse", ("coin", "gold", "gem", "diamond", "currency", "金币", "钻石", "宝石", "货币")),
    ("item_slot", "likely_reuse", ("slot", "item frame", "itemframe", "grid", "格子", "道具框", "物品框")),
    ("button", "likely_reuse", ("btn", "button", "按钮")),
    ("progress_bar", "likely_reuse", ("progress", "bar fill", "bar_bg", "track", "进度")),
    ("icon", "likely_reuse", ("icon", "图标")),
]


def ui_role_prior(name, path=""):
    """Best-effort UI role prior from name/path keywords. Returns dict or None."""
    hay = f"{name or ''} {path or ''}".lower()
    own = (name or "").lower()
    for role, prior, keys in UI_ROLE_PRIORS:
        hit = next((k for k in keys if k in own), None) or next((k for k in keys if k in hay), None)
        if hit:
            return {"type": "ui_role_prior", "role": role, "prior": prior, "matched": hit}
    return None


AVATAR_KEYS = ("avatar", "portrait", "headicon", "head_icon", "头像")
FRAME_KEYS = ("frame", "border", "ring", "框")


def _center_opaque(path):
    """True when the middle of a crop is mostly opaque (a frame alone has a see-through centre)."""
    try:
        a = np.asarray(Image.open(path).convert("RGBA"))[..., 3]
    except Exception:
        return None
    h, w = a.shape
    if h < 4 or w < 4:
        return None
    mid = a[h // 3: h - h // 3, w // 3: w - w // 3]
    return bool(mid.size and mid.mean() > 128)


def avatar_merge_suspects(records, base_dir=None):
    """Leaves that may hold an avatar picture merged with its frame (flag for a split decision).

    Suspect when the name mentions an avatar and either it is a smart object (often a
    placed avatar+frame) or it also mentions a frame and its crop centre is opaque.
    """
    out = []
    for rec in records or []:
        if rec.get("is_group") or rec.get("is_text"):
            continue
        name = (rec.get("name") or "").lower()
        if not any(k in name for k in AVATAR_KEYS):
            continue
        has_frame_word = any(k in name for k in FRAME_KEYS)
        smart = bool(rec.get("smart_object_id"))
        center = _center_opaque(Path(base_dir) / rec["crop"]) if base_dir and rec.get("crop") else None
        if smart or (has_frame_word and center):
            out.append({
                "layer_id": rec.get("layer_id"),
                "name": rec.get("name"),
                "path": rec.get("path"),
                "crop": rec.get("crop"),
                "evidence": {
                    "type": "ui_role_prior",
                    "role": "avatar",
                    "name_mentions_frame": has_frame_word,
                    "smart_object": smart,
                    "crop_center_opaque": center,
                },
                "problem": "Avatar picture may be merged with its frame. The avatar is runtime content and the frame is "
                           "a reusable resource: try to split first (split-avatar; references/slicing.md). Only when "
                           "the split fails or does not match, add a split_avatar decision with attempts and compare_image.",
            })
    return out


# ---------------------------------------------------------------------------
# Avatar / frame split (1.4.4). The avatar picture is runtime content and must be a
# separate placeholder part. When the PSD merges it with its frame, the agent tries to
# split first and only asks the user when every method fails or the split does not match.
# ---------------------------------------------------------------------------

# Ordered by reliability (references/slicing.md, "头像与框合在一起").
SPLIT_METHODS = (
    "smart_object_contents",   # inner layers of an embedded smart object, exported in Photoshop (Edit Contents on a temp copy)
    "mask_or_clip_layers",     # avatar and frame are separate layers in the document (clipping / mask); export each
    "frame_from_instance",     # frame taken from an unmerged instance of the same frame elsewhere (reuse_of)
    "pixel_mask_estimate",     # mask estimated from pixels / shapes; always approximate
)
APPROXIMATE_SPLIT_METHODS = {"pixel_mask_estimate"}
# A split_avatar decision may not default to exporting avatar and frame merged.
MERGED_DEFAULTS = {"accept_placeholder", "merged", "export_merged", "keep_merged", "accept_merged", "combined"}
RASTER_KINDS = {"pixel", "smartobject", "smart_object", "image"}


def _blend_name(mode):
    if mode is None:
        return None
    text = str(getattr(mode, "name", mode)).lower()
    return text.replace("blendmode.", "")


NORMAL_BLENDS = {None, "normal", "pass_through", "passthrough"}


def frame_blend_risks(records, cls):
    """Frame-side layers (or groups holding both sides) whose blending reaches the avatar pixels:
    a non-normal blend mode, effects with a non-normal blend (inner shadow multiply...), fill 0 with
    effects, or a group effect over both sides. A plain "frame over avatar" cannot reproduce them."""
    if not cls.get("avatar") or not cls.get("frame"):
        return []
    by_id = {r["id"]: r for r in records}
    av = {x["id"] for x in cls["avatar"]}
    fr = {x["id"] for x in cls["frame"]}
    av_boxes = [by_id[i]["bbox"] for i in av if by_id.get(i, {}).get("bbox")]

    def overlaps(b):
        return b and any(b[0] < a[2] and b[2] > a[0] and b[1] < a[3] and b[3] > a[1] for a in av_boxes)

    risks = []
    for r in records:
        if not r["visible"]:
            continue
        if r["is_group"]:
            inside = {x["id"] for x in records if r["id"] in x["parents"]}
            if not (inside & av and inside & fr):
                continue
        elif r["id"] not in fr:
            continue
        odd_fx = [e for e in r.get("effects") or [] if e.get("blend_mode") not in NORMAL_BLENDS]
        why = []
        if not r["is_group"] and r.get("blend_mode") not in NORMAL_BLENDS:
            why.append(f"blend {r['blend_mode']}")
        if odd_fx:
            why.append("effects " + ", ".join(f"{e['type']}({e['blend_mode']})" for e in odd_fx))
        if r["is_group"] and r.get("effects"):
            why.append("group effects over avatar and frame: " + ", ".join(e["type"] for e in r["effects"]))
        if not r["is_group"] and r.get("fill_opacity") == 0 and r.get("effects"):
            why.append("fill 0, effect-only overlay")
        if why and overlaps(r.get("bbox")):
            risks.append({"id": r["id"], "name": r["name"], "why": why})
    return risks


def _inner_layer_records(psd):
    """Flat records for every layer of an (inner) PSDImage with parent chain and clip base."""
    out = []

    def walk(container, parents):
        prev_base = None
        for layer in container:
            kind = str(getattr(layer, "kind", "")).lower()
            try:
                lid = int(layer.layer_id)
            except Exception:
                lid = None
            clipping = bool(getattr(layer, "clipping", False))
            fx = []
            try:
                for eff in (layer.effects or []):
                    if getattr(eff, "enabled", True):
                        fx.append({"type": eff.__class__.__name__, "blend_mode": _blend_name(getattr(eff, "blend_mode", None))})
            except Exception:
                pass
            rec = {
                "id": lid,
                "name": layer.name,
                "kind": kind,
                "visible": bool(layer.visible),
                "clipping": clipping,
                "is_group": layer.is_group(),
                "bbox": [int(v) for v in layer.bbox] if getattr(layer, "bbox", None) else None,
                "parents": list(parents),
                "clip_base": prev_base if clipping else None,
                "blend_mode": _blend_name(getattr(layer, "blend_mode", None)),
                "fill_opacity": getattr(layer, "fill_opacity", None) if hasattr(layer, "fill_opacity") else None,
                "effects": fx,
            }
            out.append(rec)
            if not clipping:
                prev_base = lid
            if layer.is_group():
                walk(layer, parents + [lid])
    walk(psd, [])
    return out


def classify_inner_layers(records):
    """Split inner smart-object layers into avatar and frame leaves.

    Avatar: a visible clipping stack (base + clipped layers) where a clipped layer is a raster
    (pixel / smart object) - the usual "photo clipped into a shape" - or a visible raster leaf whose
    name mentions an avatar but not a frame. Frame: every other visible leaf. Hidden layers
    (and leaves under hidden groups) are ignored. Returns None for avatar when nothing qualifies.
    """
    by_id = {r["id"]: r for r in records}

    def shown(r):
        if not r["visible"]:
            return False
        return all(by_id.get(p, {}).get("visible", True) for p in r["parents"])

    leaves = [r for r in records if not r["is_group"] and shown(r)]
    avatar = set()
    for r in leaves:
        if r["clipping"] and r["kind"] in RASTER_KINDS and r["clip_base"] is not None:
            avatar.add(r["id"])
            avatar.add(r["clip_base"])
    for r in leaves:
        if r["clipping"] and r["clip_base"] in avatar:
            avatar.add(r["id"])
    if not avatar:
        for r in leaves:
            name = (r["name"] or "").lower()
            if r["kind"] in RASTER_KINDS and any(k in name for k in AVATAR_KEYS) and not any(k in name for k in FRAME_KEYS):
                avatar.add(r["id"])
    avatar_leaves = [r for r in leaves if r["id"] in avatar]
    frame_leaves = [r for r in leaves if r["id"] not in avatar]
    return {
        "avatar": [{"id": r["id"], "name": r["name"]} for r in avatar_leaves] or None,
        "frame": [{"id": r["id"], "name": r["name"]} for r in frame_leaves] or None,
    }


def _affine_from_quad(w, h, quad):
    """Affine map (a, b, c, d, e, f): x' = a x + b y + c, y' = d x + e y + f, from the inner
    document rectangle to the first three corners of a smart object's transform box.
    Returns (coeffs, max_corner_error_px) - a large error means a non-affine transform."""
    x0, y0, x1, y1, x2, y2, x3, y3 = [float(v) for v in quad]
    a, b, c = (x1 - x0) / w, (x3 - x0) / h, x0
    d, e, f = (y1 - y0) / w, (y3 - y0) / h, y0
    px, py = a * w + b * h + c, d * w + e * h + f
    return (a, b, c, d, e, f), float(np.hypot(px - x2, py - y2))


def map_inner_to_document(im, quad, doc_box):
    """Place an inner-document RGBA image into document space (cropped to doc_box l,t,r,b)."""
    w, h = im.size
    (a, b, c, d, e, f), err = _affine_from_quad(w, h, quad)
    det = a * e - b * d
    if abs(det) < 1e-9:
        raise ValueError("degenerate smart object transform")
    l, t, r, btm = doc_box
    # Inverse map for PIL: inner = M^-1 (doc - offset); doc pixel (u, v) is at (u + l, v + t).
    ia, ib = e / det, -b / det
    id_, ie = -d / det, a / det
    cx, cy = l - c, t - f
    coeffs = (ia, ib, ia * cx + ib * cy, id_, ie, id_ * cx + ie * cy)
    pm = Image.fromarray(np.clip(premultiplied(im), 0, 255).astype(np.uint8), "RGBA")
    if abs(b) < 1e-9 and abs(d) < 1e-9 and a > 0 and e > 0:
        # Axis-aligned scale: filtered resize (antialiased when shrinking, like Photoshop's bicubic).
        pad = int(max(w, h)) + 4
        padded = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
        padded.paste(pm, (pad, pad))
        src_box = ((l - c) / a + pad, (t - f) / e + pad, (r - c) / a + pad, (btm - f) / e + pad)
        out = padded.resize((r - l, btm - t), Image.BICUBIC, box=src_box)
    else:
        out = pm.transform((r - l, btm - t), Image.AFFINE, coeffs, resample=Image.BICUBIC)
    arr = np.asarray(out).astype(np.float32)
    alpha = arr[..., 3:4]
    rgb = np.where(alpha > 0, arr[..., :3] * 255.0 / np.maximum(alpha, 1e-6), 0)
    return Image.fromarray(np.clip(np.concatenate([rgb, alpha], -1), 0, 255).astype(np.uint8), "RGBA"), err


def over(top, bottom):
    """Porter-Duff src-over of two same-size RGBA images."""
    t, b = premultiplied(top) / 255.0, premultiplied(bottom) / 255.0
    o = t + b * (1.0 - t[..., 3:4])
    a = o[..., 3:4]
    rgb = np.where(a > 0, o[..., :3] / np.maximum(a, 1e-6), 0)
    return Image.fromarray(np.clip(np.concatenate([rgb, a], -1) * 255.0 + 0.5, 0, 255).astype(np.uint8), "RGBA")


def split_match(recomposite, design):
    """MAD (premultiplied, 0-255), % pixels off by > 16 and PSNR of a recomposited split vs the design."""
    a, b = premultiplied(recomposite), premultiplied(design)
    mad, pct = crop_mad(a, b)
    h, w = max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])
    mse = float(((pad_to(a, w, h) - pad_to(b, w, h)) ** 2).mean())
    psnr = 99.0 if mse <= 1e-9 else float(10 * np.log10(255.0 ** 2 / mse))
    return {"mad": round(mad, 3), "pct_px_over_16": round(pct, 3), "psnr": round(psnr, 2)}


SPLIT_MATCH_PSNR = 35.0  # recomposite (frame over placeholder avatar) vs design; below this the split "does not match"
# Offline smart-object check: psd-tools + Pillow resampling alone costs a few dB against Photoshop's
# stored raster (about 36 dB on a 0.625x probe), so the split itself is judged against the whole inner
# document resampled the same way (split_consistency), and the layer pixels only need this floor.
SPLIT_LAYER_FLOOR_PSNR = 30.0


def estimate_avatar_mask(merged, frame, tol=24):
    """Pixel estimate of the avatar region: where the merged image differs from the frame alone.

    ``merged`` and ``frame`` are aligned same-size RGBA images (frame from an unmerged instance).
    Returns (avatar RGBA cut from merged, recomposite frame-over-avatar, match). Approximate."""
    m, f = premultiplied(merged), premultiplied(frame)
    h, w = max(m.shape[0], f.shape[0]), max(m.shape[1], f.shape[1])
    m, f = pad_to(m, w, h), pad_to(f, w, h)
    diff = np.abs(m - f).max(axis=-1) > tol
    src = np.asarray(merged.convert("RGBA"))
    src = np.pad(src, ((0, h - src.shape[0]), (0, w - src.shape[1]), (0, 0)))
    av = src.copy()
    av[..., 3] = np.where(diff, src[..., 3], 0)
    avatar = Image.fromarray(av, "RGBA")
    fr = Image.fromarray(np.pad(np.asarray(frame.convert("RGBA")), ((0, h - frame.height), (0, w - frame.width), (0, 0))), "RGBA")
    rec = over(fr, avatar)
    return avatar, rec, split_match(rec, Image.fromarray(src, "RGBA"))


def split_smart_object_layer(layer, out_dir, open_kw=None):
    """Try the smart-object split for one document layer (psd-tools, offline).

    Renders frame-only and avatar-only from the embedded document, maps them back with the
    smart object transform, recomposites frame over avatar and compares with the layer's own
    pixels (Photoshop's stored raster). psd-tools renders the inner layers, so pixels are an
    approximation; the export itself is done by Photoshop (``split.hide_inner``)."""
    out_dir = Path(out_dir)
    lid = int(layer.layer_id)
    rec = {"layer_id": lid, "name": layer.name, "method": "smart_object_contents", "status": None}
    so = getattr(layer, "smart_object", None)
    if so is None:
        rec.update(status="not_smart_object", reason="layer is not a smart object")
        return rec
    rec["smart_object"] = {"kind": so.kind, "filetype": str(so.filetype), "filename": so.filename,
                           "unique_id": str(so.unique_id or "")}
    if so.kind != "data" or not so.is_psd():
        rec.update(status="not_embedded_psd",
                   reason="linked or non-PSD smart object: inner layers are not readable offline")
        return rec
    import io
    if open_kw is None:
        open_kw = {"parse_limits": ParseLimits(max_objects=None, max_total_bytes=None, max_read_bytes=None)} if ParseLimits is not None else {}
    try:
        with so.open() as fh:
            inner = PSDImage.open(io.BytesIO(fh.read()), **open_kw)
    except Exception as exc:
        rec.update(status="error", reason=f"cannot open embedded document: {exc}")
        return rec
    records = _inner_layer_records(inner)
    cls = classify_inner_layers(records)
    rec["inner_size"] = list(inner.size)
    rec["inner_layers"] = [{k: r[k] for k in ("id", "name", "kind", "visible", "clipping", "bbox", "parents")} for r in records]
    rec["avatar_inner"], rec["frame_inner"] = cls["avatar"], cls["frame"]
    if not cls["avatar"] or not cls["frame"]:
        rec.update(status="no_separable_layers",
                   reason="no clipped raster (photo) layer or no frame layer inside the smart object")
        return rec
    rec["frame_blend_risks"] = frame_blend_risks(records, cls)
    rec["hide_inner_for_avatar"] = cls["frame"]
    rec["hide_inner_for_frame"] = cls["avatar"]
    out_dir.mkdir(parents=True, exist_ok=True)
    av_ids = {x["id"] for x in cls["avatar"]}
    fr_ids = {x["id"] for x in cls["frame"]}

    def render(keep):
        def filt(l):
            if l.is_group():
                return l.visible
            return l.visible and int(l.layer_id) in keep
        return inner.composite(layer_filter=filt, force=True).convert("RGBA")

    try:
        av_in, fr_in = render(av_ids), render(fr_ids)
        all_in = render(av_ids | fr_ids)
        quad = list(so.transform_box) if so.transform_box else [0, 0, inner.width, 0, inner.width, inner.height, 0, inner.height]
        box = tuple(int(v) for v in layer.bbox)
        av_doc, err = map_inner_to_document(av_in, quad, box)
        fr_doc, _ = map_inner_to_document(fr_in, quad, box)
        all_doc, _ = map_inner_to_document(all_in, quad, box)
        design = layer.topil()
        design = design.convert("RGBA") if design is not None else layer.composite().convert("RGBA")
    except Exception as exc:
        rec.update(status="error", reason=f"render failed: {exc}")
        return rec
    recomposite = over(fr_doc, av_doc)
    rec["transform_box"] = quad
    rec["non_affine_px"] = round(err, 2)
    rec["match"] = split_match(recomposite, design)
    rec["split_consistency"] = split_match(recomposite, all_doc)
    rec["resample_floor"] = split_match(all_doc, design)
    for name, im in (("avatar", av_doc), ("frame", fr_doc), ("recomposite", recomposite)):
        im.save(out_dir / f"{name}.png")
    sheet = write_compare_sheet([(f"{lid} {layer.name}", design, recomposite)], out_dir / "compare.png",
                                titles=("design", "frame over avatar", "diff x3"))
    rec["files"] = {k: str(out_dir / f"{k}.png") for k in ("avatar", "frame", "recomposite")}
    rec["compare_image"] = str(sheet) if sheet else None
    rec["approximate_render"] = True
    rec["frame_hole_fraction"] = alpha_hole_mask(fr_doc)[1]
    rec["outer_effect_risks"] = outer_effect_risks(layer_effect_list(layer), fr_doc)
    rec["edge_effect_risks"] = inner_edge_effect_risks(records, cls)
    rec["effect_fix"] = effect_fix_suggestion(rec)
    reasons = []
    if rec["split_consistency"]["psnr"] < SPLIT_MATCH_PSNR:
        reasons.append(f"frame over avatar vs whole contents PSNR {rec['split_consistency']['psnr']} dB (< {SPLIT_MATCH_PSNR})")
    if err > 1.0:
        reasons.append(f"non-affine transform ({err:.1f} px)")
    if reasons:
        rec["status"], rec["reason"] = "mismatch", "; ".join(reasons)
    elif rec["outer_effect_risks"]:
        rec["status"] = "candidate_unverified"
        rec["reason"] = ("edge effects on the smart object layer (" + ", ".join(x["type"] for x in rec["outer_effect_risks"]) +
                         ") are redrawn along the hole of the frame-only export; export merged / content / frame_plain "
                         "(see effect_fix) and verify with split-fix")
    elif rec["frame_blend_risks"]:
        rec["status"] = "candidate_unverified"
        rec["reason"] = ("frame layers blend onto the avatar (" + "; ".join(
            f"{x['name']}: {', '.join(x['why'])}" for x in rec["frame_blend_risks"]) +
            "); frame over avatar may not match - export with split and compare with the merged export")
    elif rec["resample_floor"]["psnr"] < SPLIT_LAYER_FLOOR_PSNR:
        # The split is consistent, but psd-tools does not reproduce the inner rendering (layer effects on
        # shapes, blend modes...). Photoshop has to export it; validate is the check.
        rec["status"] = "candidate_unverified"
        rec["reason"] = (f"psd-tools render of the contents vs the layer pixels is only {rec['resample_floor']['psnr']} dB "
                         f"(< {SPLIT_LAYER_FLOOR_PSNR}); export with split and confirm with validate")
    else:
        rec["status"] = "candidate"
    return rec


def frame_instance_candidates(records, suspect, size_tol=0.12):
    """Unmerged frame layers elsewhere (name mentions a frame, similar size) that could supply the frame."""
    sw, sh = suspect.get("width") or 0, suspect.get("height") or 0
    out = []
    for rec in records or []:
        if rec.get("layer_id") == suspect.get("layer_id") or rec.get("is_group") or rec.get("is_text"):
            continue
        name = (rec.get("name") or "").lower()
        if not any(k in name for k in FRAME_KEYS):
            continue
        w, h = rec.get("width") or 0, rec.get("height") or 0
        if sw and sh and w and h and abs(w - sw) <= size_tol * sw and abs(h - sh) <= size_tol * sh:
            out.append({"layer_id": rec.get("layer_id"), "name": rec.get("name"), "path": rec.get("path"),
                        "size": [w, h], "crop": rec.get("crop")})
    return out


def suggested_split_parts(rec, base_name="<module>_img_avatar"):
    """Manifest part snippets for a successful smart-object split (agent renames / adds reuse_of)."""
    ev = {"split_tool": "split-avatar", "match": rec.get("match"), "compare_image": rec.get("compare_image"),
          "approximate_render": rec.get("approximate_render", True)}
    return [
        {"name": base_name, "role": "avatar", "placeholder": True, "layer_id": rec["layer_id"],
         "select_ids": [rec["layer_id"]], "split_method": "smart_object_contents",
         "split": {"hide_inner": rec["hide_inner_for_avatar"]}, "split_evidence": ev},
        {"name": base_name.replace("avatar", "avatar_frame"), "role": "frame", "layer_id": rec["layer_id"],
         "select_ids": [rec["layer_id"]], "split_method": "smart_object_contents",
         "split": {"hide_inner": rec["hide_inner_for_frame"]}, "split_evidence": ev},
    ]


def _descendant_ids(tree):
    """layer_id -> set of descendant layer ids (inclusive) from a prepare layer tree."""
    out = {}

    def walk(node):
        ids = {node.get("layer_id")} if node.get("layer_id") is not None else set()
        for child in node.get("children") or []:
            ids |= walk(child)
        if node.get("layer_id") is not None:
            out[node["layer_id"]] = ids
        return ids
    roots = tree if isinstance(tree, list) else ([tree] if isinstance(tree, dict) else [])
    for root in roots:
        if isinstance(root, dict):
            walk(root)
    return out


def load_layer_tree(manifest=None, manifest_path=None):
    """Layer tree from the manifest, or tree.json next to it (prepare output)."""
    tree = layer_tree_from_manifest(manifest or {})
    if tree:
        return tree
    if manifest_path:
        p = Path(manifest_path).parent / "tree.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")).get("tree")
            except Exception as exc:
                warn(f"cannot read {p}: {exc}")
    return None


def load_avatar_suspects(manifest=None, manifest_path=None):
    """avatar_merge_suspects from the manifest, or reuse_candidates.json next to it."""
    if manifest and manifest.get("avatar_merge_suspects") is not None:
        return list(manifest["avatar_merge_suspects"])
    if manifest_path:
        p = Path(manifest_path).parent / "reuse_candidates.json"
        if p.exists():
            try:
                return list(json.loads(p.read_text(encoding="utf-8")).get("avatar_merge_suspects") or [])
            except Exception as exc:
                warn(f"cannot read {p}: {exc}")
    return []



# ---------------------------------------------------------------------------
# 1.4.9: thresholds (overridable via the project rules file), recorded user decisions,
# manifest lock after confirmation.
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS = {
    # split part(s) recomposited vs the merged design (Photoshop pixels), dB
    "split_match_psnr": 35.0,
    # reused image vs each instance in the design, inside the shared image's opaque region
    "reuse_max_mae": 4.0,
    "reuse_max_pct_gt16": 1.0,
}
REUSE_ACCEPT_DECISIONS = {"reuse", "accept", "accepted", "reuse_accepted", "accept_mismatch", "accept_reuse"}
PENDING_DECISIONS = {"", "pending", "wait_for_user", "none", "null"}


def thresholds_from_rules(rules_data):
    t = (rules_data or {}).get("thresholds") if isinstance(rules_data, dict) else None
    return {k: float(v) for k, v in (t or {}).items() if k in DEFAULT_THRESHOLDS and v is not None}


def thresholds_from_rules_file(path):
    if not path or not Path(path).is_file():
        return {}
    try:
        return thresholds_from_rules(json.loads(Path(path).read_text(encoding="utf-8")))
    except Exception as exc:  # unreadable rules file: keep defaults, but say so
        warn(f"thresholds: cannot read {path}: {exc}")
        return {}


def resolve_thresholds(*overlays):
    """Later overlays win. Each overlay is a dict of threshold keys (None values ignored)."""
    out = dict(DEFAULT_THRESHOLDS)
    for o in overlays:
        for k, v in (o or {}).items():
            if k in DEFAULT_THRESHOLDS and v is not None:
                out[k] = float(v)
    return out


def _decided(d):
    v = d.get("decision")
    return v is not None and str(v).strip().lower() not in PENDING_DECISIONS


def user_decision_for(name, decisions, types, reuse_of=None, accept=None):
    """A recorded (non-pending) user decision covering *name*, or None.

    Only entries in reuse_decisions with a filled ``decision`` AND ``decided_by: "user"`` count (``confirm
    --decision ID=VALUE`` records both). A decision filled with the option the agent proposed as ``default``
    but not recorded as the user's, and a part's own flags (``reuse_accepted``, ``split_best_effort`` in
    evidence), never do."""
    for d in decisions or []:
        if d.get("type") not in types or not _decided(d):
            continue
        if str(d.get("decided_by") or "").strip().lower() != "user":
            continue
        inst = d.get("instances") or []
        hit = name in inst or (not inst and reuse_of and d.get("reuse_of") == reuse_of)
        if not hit:
            continue
        if accept is not None and str(d.get("decision")).strip().lower() not in accept:
            continue
        return d
    return None


def manifest_lock_paths(manifest_path):
    p = Path(manifest_path)
    return p.with_name(p.stem + ".confirmed.json"), p.with_name(p.stem + ".confirmed.sha256")


def file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def manifest_lock_state(manifest_path):
    """{manifest, sha256, locked, confirmed_sha256, changed}. changed is True when a lock exists
    and the manifest bytes differ from the confirmed copy."""
    mp = Path(manifest_path)
    copy, sha = manifest_lock_paths(mp)
    st = {"manifest": str(mp), "sha256": file_sha256(mp) if mp.is_file() else None,
          "locked": sha.is_file(), "confirmed_sha256": None, "confirmed_at": None, "changed": False,
          "decision_log": []}
    if sha.is_file():
        try:
            info = json.loads(sha.read_text(encoding="utf-8"))
        except Exception:
            info = {"sha256": sha.read_text(encoding="utf-8").strip().split()[0]}
        st["confirmed_sha256"] = info.get("sha256")
        st["confirmed_at"] = info.get("confirmed_at")
        st["decision_log"] = [r for r in (info.get("decision_log") or []) if isinstance(r, dict)]
        st["changed"] = st["sha256"] != st["confirmed_sha256"]
    return st


# 1.4.12: every question the user has to answer is a structured entry in reuse_decisions. Types whose answer changes
# the truth (what the full render / export shows) block validate until the user's own answer is recorded with
# `confirm --decision ID=VALUE`, which also appends it to the lock's decision_log. A confirm note ("checklist
# confirmed") locks the manifest but answers no question; a decided_by written by hand is not in the log.
TRUTH_DECISION_TYPES = {"layer_inclusion", "exclude_visible_layer", "hidden_layer_state"}
EXCLUDE_DECISION_TYPES = {"layer_inclusion", "exclude_visible_layer"}
EXCLUDE_DECISIONS = {"exclude", "excluded", "hide", "hidden", "omit", "not_exported", "no_export", "skip", "drop",
                     "old_draft", "leftover", "remove"}
BLOCKING_AFFECTS = {"truth", "export", "full_render"}


def _norm_decision(v):
    return str(v if v is not None else "").strip().lower()


def decision_is_blocking(d):
    """True when the answer changes what the export or the full render shows (validate stops until answered)."""
    if not isinstance(d, dict):
        return False
    if d.get("type") in TRUTH_DECISION_TYPES or d.get("blocking") is True:
        return True
    aff = d.get("affects")
    affs = aff if isinstance(aff, (list, tuple)) else [aff]
    return any(_norm_decision(a) in BLOCKING_AFFECTS for a in affs)


def decision_recorded(d, decision_log):
    """The decision's current value was recorded by `confirm --decision` (lock decision_log)."""
    did, val = str(d.get("id")), _norm_decision(d.get("decision"))
    last = None
    for r in decision_log or []:
        if str(r.get("id")) == did:
            last = _norm_decision(r.get("decision"))
    return last is not None and last == val


def decision_answer_state(d, decision_log):
    """None when the user answered; else the reason it is still open."""
    if not _decided(d):
        return "no_decision" if d.get("decision") in (None, "") else "decision_pending"
    if _norm_decision(d.get("decided_by")) != "user":
        return "not_decided_by_user"
    if decision_is_blocking(d) and not decision_recorded(d, decision_log):
        return "decided_by_user_not_recorded_by_confirm"
    return None


def pending_user_decisions(decisions, decision_log):
    """Every reuse_decisions entry the user has not answered (any type), with why and whether it blocks validate."""
    out = []
    for d in decisions or []:
        if not isinstance(d, dict):
            continue
        reason = decision_answer_state(d, decision_log)
        if reason is None:
            continue
        out.append({"id": d.get("id"), "type": d.get("type"), "question": d.get("question"),
                    "layer_ids": d.get("layer_ids"), "default": d.get("default"), "decision": d.get("decision"),
                    "blocking": decision_is_blocking(d), "reason": reason})
    return out


PENDING_USER_DECISION_HINT = (
    "this question changes what the export / full render shows and the user has not answered it. Ask the user "
    "this exact question and record the answer with `confirm --decision ID=VALUE`. A plain confirm of the "
    "checklist, the agent's default copied into decision, or a decided_by written by hand is not an answer. Until "
    "then keep the safe default: export what the document shows (never hide a visible layer)."
)


def user_decision_issues(decisions, lock_info):
    """``pending_user_decision`` for blocking decisions without the user's recorded answer."""
    log = (lock_info or {}).get("decision_log") or []
    return [dict(p, type="pending_user_decision", decision_type=p["type"], message=PENDING_USER_DECISION_HINT)
            for p in pending_user_decisions(decisions, log) if p["blocking"]]


def approved_exclusions(decisions, lock_info):
    """{layer_id: decision_id} the user chose to leave out (recorded by confirm --decision). Only these may be
    hidden by the exporter for slices and the full render."""
    log = (lock_info or {}).get("decision_log") or []
    out = {}
    for d in decisions or []:
        if not isinstance(d, dict) or not d.get("layer_ids"):
            continue
        if d.get("type") not in EXCLUDE_DECISION_TYPES and not decision_is_blocking(d):
            continue
        if decision_answer_state(d, log) is not None or _norm_decision(d.get("decision")) not in EXCLUDE_DECISIONS:
            continue
        for lid in d.get("layer_ids") or []:
            out[lid] = d.get("id")
    return out


# The exporter is identified by the sha256 of its text with CRLF -> LF, a BOM dropped and the CONFIG_URL line
# blanked (the one line a job edits). export_slices.psjs computes the same hash of itself (config exporter_url).
EXPORTER_CONFIG_LINE = re.compile(r"^const CONFIG_URL = .*$", re.M)
SHIPPED_EXPORTER = Path(__file__).resolve().with_name("export_slices.psjs")


def normalize_exporter_text(text):
    text = text.lstrip("\ufeff").replace("\r\n", "\n")
    return EXPORTER_CONFIG_LINE.sub('const CONFIG_URL = "";', text, count=1)


def exporter_text_sha256(text):
    return hashlib.sha256(normalize_exporter_text(text).encode("utf-8")).hexdigest()


def shipped_exporter_sha256(path=None):
    p = Path(path) if path else SHIPPED_EXPORTER
    return exporter_text_sha256(p.read_bytes().decode("utf-8")) if p.is_file() else None


EXPORTER_MODIFIED_HINT = (
    "the export was not made by the shipped examples/export_slices.psjs of this skill version. Copy it unchanged "
    "(edit only CONFIG_URL), set exporter_url in the export config to the copy, and export again. Do not edit "
    "the exporter to hide layers or to silence a check: exclusions come only from a user decision recorded with "
    "confirm --decision; a false positive is reported, not patched in a job copy."
)
VISIBLE_HIDDEN_HINT = (
    "layers visible when the document was opened were hidden for the full render (the validate reference) without "
    "a recorded user decision to exclude them. The score then measures an altered reference. Export again with the "
    "shipped exporter; if a layer may be an old draft, ask the user with a layer_inclusion decision."
)
VISIBLE_UNASSIGNED_HINT = (
    "visible pixel layers inside the artboard are covered by no entry (layer_id / select_ids, or a selected "
    "ancestor group). Add them to an entry, or ask the user with a layer_inclusion decision whether they belong "
    "to the current design."
)
PIXEL_LAYER_KINDS = {"pixel", "normal", "text", "smartobject", "solidcolor", "solidfill", "gradientfill",
                     "patternfill", "shape", "layer", "video", "3d"}


def _layer_rect(b):
    r = _rect_to_dict(b)
    if not r or r["right"] <= r["left"] or r["bottom"] <= r["top"]:
        return None
    return r


def exporter_issues(exporter, layers_at_open, shipped=None):
    reasons = []
    shipped_sha = shipped_exporter_sha256(shipped)
    if shipped_sha is None:
        reasons.append(f"shipped exporter not found at {shipped or SHIPPED_EXPORTER}; cannot verify the export")
    if not isinstance(exporter, dict) or not exporter:
        reasons.append("export log has no exporter record (an older or edited export_slices.psjs)")
    else:
        sha = exporter.get("sha256")
        if not sha:
            reasons.append("exporter sha256 missing" + (f": {exporter.get('error')}" if exporter.get("error") else ""))
        elif shipped_sha and sha != shipped_sha:
            reasons.append("exporter sha256 differs from the shipped export_slices.psjs")
        sp = exporter.get("script_path")
        if sp and Path(sp).is_file() and shipped_sha:
            try:
                live = exporter_text_sha256(Path(sp).read_bytes().decode("utf-8"))
            except Exception as exc:
                live = f"unreadable: {exc}"
            if live != shipped_sha:
                reasons.append(f"script at script_path differs from the shipped export_slices.psjs: {sp}")
    if layers_at_open is None:
        reasons.append("export log has no layers_at_open (layer visibility when the document was opened)")
    if not reasons:
        return []
    return [{"type": "exporter_modified", "reasons": reasons,
             "sha256": (exporter or {}).get("sha256") if isinstance(exporter, dict) else None,
             "shipped_sha256": shipped_sha, "script_path": (exporter or {}).get("script_path") if isinstance(exporter, dict) else None,
             "message": EXPORTER_MODIFIED_HINT}]


def export_truth_issues(layout, shipped=None):
    """1.4.12 checks on the export record copied into the layout by build-layout:

    - exporter_modified: no exporter record / sha256, or not the shipped exporter, or no layers_at_open;
    - visible_layer_hidden: layers visible when the document was opened but hidden for the full render, without
      a recorded user exclusion;
    - visible_layer_unassigned: visible pixel layers inside the artboard that no entry covers and the user did
      not exclude.
    """
    exp = (layout or {}).get("export") or {}
    layers = exp.get("layers_at_open")
    issues = exporter_issues(exp.get("exporter"), layers, shipped)
    excluded = approved_exclusions(layout.get("reuse_decisions") or [], layout.get("manifest_lock"))
    if not layers:
        return issues
    by_id = {l.get("id"): l for l in layers if isinstance(l, dict)}

    def ancestors(lid):
        seen = set()
        cur = by_id.get(lid)
        while cur is not None and cur.get("parent_id") is not None and cur["parent_id"] not in seen:
            seen.add(cur["parent_id"])
            yield cur["parent_id"]
            cur = by_id.get(cur["parent_id"])

    def under(lid, ids):
        return lid in ids or any(a in ids for a in ancestors(lid))

    hidden = [x for x in (exp.get("full_render_hidden") or []) if isinstance(x, dict)]
    bad = [x for x in hidden if not under(x.get("id"), excluded)]
    if bad:
        issues.append({"type": "visible_layer_hidden", "layers": [{"id": x.get("id"), "name": x.get("name")} for x in bad],
                       "message": VISIBLE_HIDDEN_HINT})

    art = (layout.get("source") or {}).get("artboard") or {}
    art_id = art.get("id") if art.get("id") in by_id else None
    ab = art.get("bounds")
    ab = _rect_to_dict(ab) if ab else None
    covered = set()
    for n in layout.get("nodes") or []:
        if n.get("layer_id") is not None:
            covered.add(n["layer_id"])
        covered.update(i for i in (n.get("select_ids") or []) if i is not None)
    loose = []
    for l in layers:
        if not isinstance(l, dict) or l.get("group") or not l.get("effective_visible", l.get("visible")):
            continue
        kind = re.sub(r"[^a-z0-9]", "", str(l.get("kind") or "pixel").lower().split(".")[-1])
        if kind not in PIXEL_LAYER_KINDS:
            continue
        if l.get("opacity") is not None and float(l["opacity"]) <= 0:
            continue
        r = _layer_rect(l.get("bounds"))
        if r is None:
            continue
        if ab and (r["right"] <= ab["left"] or r["left"] >= ab["right"] or r["bottom"] <= ab["top"] or r["top"] >= ab["bottom"]):
            continue
        lid = l.get("id")
        if art_id is not None and art_id not in set(ancestors(lid)):
            continue
        if under(lid, covered) or under(lid, excluded):
            continue
        loose.append({"id": lid, "name": l.get("name"), "kind": l.get("kind"), "bounds": r})
    if loose:
        issues.append({"type": "visible_layer_unassigned", "count": len(loose), "layers": loose[:50],
                       "message": VISIBLE_UNASSIGNED_HINT})
    return issues


# 1.4.13: an uncertain_hidden note (on a component, a state or a part, or the manifest's top-level checklist list)
# is only an index for people. The question to the user is the reuse_decisions entry (hidden_layer_state) whose
# layer_ids cover it; without one, nobody was asked (uncertain_hidden_without_decision). Covered but unanswered
# stays pending_user_decision only.
UNCERTAIN_HIDDEN_HINT = (
    "uncertain_hidden on the manifest is only an index for the reviewer; it asks the user nothing. For every "
    "layer id listed there write a reuse_decisions entry of type hidden_layer_state (id, question, options, "
    "layer_ids, crops), ask the user, and record the answer with `confirm --decision ID=VALUE`. If the layer is "
    "clearly a state variant or clearly an old draft, drop it from uncertain_hidden instead (exporting a hidden "
    "layer as a state still needs the user's recorded answer)."
)
HIDDEN_EXPORT_HINT = (
    "these entries export a layer that was hidden when the document was opened as a state asset, and the user "
    "did not choose to export it as a state. Adopting a hidden layer as a state (normal / unselected / locked / "
    "alternate background, or reading the visible layer as 'selected' and a hidden one as 'normal') needs a "
    "hidden_layer_state decision covering the layer, answered by the user with `confirm --decision ID=state`. "
    "Until then keep the safe default: do not export the hidden layer and do not let it replace what the "
    "document shows. Only a highlight over a base visible at open in the same component's default state is exempt."
)
STATE_EXPORT_DECISIONS = {"state", "states", "export", "export_as_state", "as_state", "alternate_state",
                          "state_variant", "variant", "include", "included", "use", "use_as_state", "keep", "yes"}


def _lid(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return v


def _uncertain_ids(note):
    """layer ids in an uncertain_hidden value: a dict, a list of dicts / ids, or a bare id."""
    if note is None or note == "" or note is False:
        return []
    items = note if isinstance(note, (list, tuple)) else [note]
    out = []
    for it in items:
        if isinstance(it, dict):
            ids = list(it.get("layer_ids") or [])
            if it.get("layer_id") is not None:
                ids.append(it["layer_id"])
        else:
            ids = [it]
        for i in ids:
            if i is not None and not isinstance(i, (dict, list, bool)) and _lid(i) not in out:
                out.append(_lid(i))
    return out


def _uncertain_question(note):
    items = note if isinstance(note, (list, tuple)) else [note]
    qs = [it.get("question") for it in items if isinstance(it, dict) and it.get("question")]
    return qs[0] if len(qs) == 1 else (qs or None)


def iter_uncertain_hidden(components, extra=None):
    """Yield {component_id, state, part, layer_ids, question} for every uncertain_hidden note in the tree."""
    seen = set()

    def emit(note, comp_id, state=None, part=None):
        ids = _uncertain_ids(note)
        key = (comp_id, state, part, tuple(map(str, ids)))
        if not ids or key in seen:
            return None
        seen.add(key)
        return {"component_id": comp_id, "state": state, "part": part, "layer_ids": ids,
                "question": _uncertain_question(note)}

    def walk(comp):
        if not isinstance(comp, dict):
            return
        cid = comp.get("id") or comp.get("name")
        rec = emit(comp.get("uncertain_hidden"), cid)
        if rec:
            yield rec
        for part in component_shared_parts(comp):
            rec = emit(part.get("uncertain_hidden"), cid, part=part.get("name"))
            if rec:
                yield rec
        states = comp.get("states") or {}
        if isinstance(states, dict):
            for sname, sobj in states.items():
                if not isinstance(sobj, dict):
                    continue
                rec = emit(sobj.get("uncertain_hidden"), cid, state=sname)
                if rec:
                    yield rec
                for part in sobj.get("parts") or []:
                    if isinstance(part, dict):
                        rec = emit(part.get("uncertain_hidden"), cid, state=sname, part=part.get("name"))
                        if rec:
                            yield rec
        for child in comp.get("children") or []:
            yield from walk(child)

    for comp in components or []:
        yield from walk(comp)
    rec = emit(extra, None)
    if rec:
        yield rec


def _decisions_covering(decisions, lid):
    return [d for d in decisions or [] if isinstance(d, dict) and decision_is_blocking(d)
            and lid in {_lid(i) for i in (d.get("layer_ids") or [])}]


def uncertain_hidden_issues(components, decisions, lock_info=None, extra=None):
    """uncertain_hidden_without_decision for noted layer ids that no blocking reuse_decisions entry covers.
    Covered ids are left to pending_user_decision (unanswered) or are answered."""
    issues = []
    for rec in iter_uncertain_hidden(components, extra):
        bare = [i for i in rec["layer_ids"] if not _decisions_covering(decisions, i)]
        if bare:
            issues.append(dict(rec, type="uncertain_hidden_without_decision", layer_ids=bare,
                               message=UNCERTAIN_HIDDEN_HINT))
    return issues


def hidden_layer_export_issues(layout):
    """hidden_layer_exported_without_decision: a PNG entry whose layer_id / select_ids include a layer hidden at
    open (own or through an ancestor, from layers_at_open) without a blocking decision covering it that the user
    answered with an export-as-state value (recorded by confirm --decision). Skipped: logs without
    layers_at_open, attribute-only text and bounds-only reuse instances, ids covered by an unanswered decision
    (pending_user_decision reports those), and a highlight over a base visible at open in the same component's
    default state."""
    layers = ((layout or {}).get("export") or {}).get("layers_at_open")
    if not layers:
        return []
    vis = {}
    for l in layers:
        if isinstance(l, dict) and l.get("id") is not None:
            vis[_lid(l["id"])] = bool(l.get("effective_visible", l.get("visible", True))) and l.get("visible", True) is not False
    decisions = layout.get("reuse_decisions") or []
    log = (layout.get("manifest_lock") or {}).get("decision_log") or []
    nodes = [n for n in layout.get("nodes") or [] if isinstance(n, dict)]

    def ids_of(n):
        ids = n.get("select_ids") or ([n.get("layer_id")] if n.get("layer_id") is not None else [])
        return [_lid(i) for i in ids if i is not None]

    def in_default_state(n):
        st = n.get("state")
        return st is None or (n.get("default_state") is not None and st == n.get("default_state"))

    def has_visible_default_base(n):
        cid = n.get("component_id")
        if cid is None:
            return False
        for o in nodes:
            if o is n or o.get("component_id") != cid or not in_default_state(o):
                continue
            if (o.get("part_role") or o.get("role") or "").lower() == "highlight":
                continue
            if any(vis.get(i) is True for i in ids_of(o)):
                return True
        return False

    bad = []
    for n in nodes:
        if not n.get("image") or n.get("_bounds_only") or is_attr_text(n.get("kind")):
            continue
        hidden = [i for i in ids_of(n) if vis.get(i) is False]
        if not hidden:
            continue
        if (n.get("part_role") or n.get("role") or "").lower() == "highlight" and has_visible_default_base(n):
            continue
        for lid in hidden:
            cover = _decisions_covering(decisions, lid)
            if any(decision_answer_state(d, log) is not None for d in cover):
                continue  # pending_user_decision
            if any(_norm_decision(d.get("decision")) in STATE_EXPORT_DECISIONS for d in cover):
                continue
            bad.append({"name": n.get("name"), "layer_id": lid, "component_id": n.get("component_id"),
                        "state": n.get("state"), "part_role": n.get("part_role") or n.get("role"),
                        "decision_ids": [d.get("id") for d in cover],
                        "reason": "user_answer_is_not_export_as_state" if cover else "no_decision"})
    if not bad:
        return []
    return [{"type": "hidden_layer_exported_without_decision", "count": len(bad), "entries": bad[:50],
             "message": HIDDEN_EXPORT_HINT}]


MANIFEST_CHANGED_HINT = (
    "the manifest was edited after the user confirmed it. Do not edit a confirmed manifest during "
    "export / build-layout / validate: restore manifest.confirmed.json, write the wanted change to "
    "manifest.proposal.json (what, why, evidence), and stop for the user to re-confirm (then run confirm again)."
)


def cmd_confirm(args):
    path = Path(args.manifest)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    st = manifest_lock_state(path)
    if st["locked"] and st["changed"] and not getattr(args, "relock", False):
        print(json.dumps(dict(st, error="manifest_changed_after_confirm", hint=MANIFEST_CHANGED_HINT),
                         ensure_ascii=False, indent=2))
        return 3
    status = manifest.get("status")
    if status not in (None, "pending_review", "confirmed"):
        raise SystemExit(f"manifest.status is {status!r}; only pending_review / confirmed can be confirmed")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest["status"] = "confirmed"
    manifest["confirmed_at"] = stamp
    if getattr(args, "note", None):
        manifest["confirmed_note"] = args.note
    decs = manifest.get("reuse_decisions") or []
    by_id = {str(d.get("id")): d for d in decs if d.get("id") is not None}
    recorded = []
    for spec in getattr(args, "decision", None) or []:
        if "=" not in spec:
            raise SystemExit(f"--decision expects ID=VALUE, got {spec!r}")
        did, val = (x.strip() for x in spec.split("=", 1))
        if did not in by_id:
            raise SystemExit(f"--decision: no reuse_decisions entry with id {did!r}")
        by_id[did].update(decision=val, decided_by="user", decided_at=stamp)
        recorded.append({"id": did, "decision": val})
    decision_log = list(st.get("decision_log") or [])
    for r in recorded:
        decision_log.append({"id": r["id"], "decision": r["decision"], "decided_at": stamp})
    pending = pending_user_decisions(decs, decision_log)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    copy, sha = manifest_lock_paths(path)
    copy.write_bytes(path.read_bytes())
    digest = file_sha256(path)
    sha.write_text(json.dumps({"sha256": digest, "confirmed_at": stamp, "manifest": path.name,
                               "note": getattr(args, "note", None), "decision_log": decision_log},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "manifest": str(path), "sha256": digest, "confirmed_copy": str(copy),
                      "lock": str(sha), "user_decisions_recorded": recorded,
                      "not_user_decided": pending,
                      "blocking_not_user_decided": [p["id"] for p in pending if p["blocking"]],
                      "note": ("the manifest is locked, but not_user_decided entries are not answered: a confirm note "
                               "answers no question. They do not count for split_below_threshold / reuse over cap, and "
                               "blocking ones (layer_inclusion / exclude_visible_layer / hidden_layer_state / affects) are "
                               "pending_user_decision in validate. Only the user's own answer, recorded with "
                               "--decision ID=VALUE, counts.")},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_check_lock(args):
    st = manifest_lock_state(args.manifest)
    if st["changed"]:
        print(json.dumps(dict(st, error="manifest_changed_after_confirm", hint=MANIFEST_CHANGED_HINT),
                         ensure_ascii=False, indent=2))
        return 3
    if not st["locked"]:
        print(json.dumps(dict(st, error="manifest_not_locked",
                              hint="run `confirm` after the user confirms the manifest"), ensure_ascii=False, indent=2))
        return 4
    print(json.dumps(dict(st, ok=True), ensure_ascii=False, indent=2))
    return 0


def lock_issues(lock_info):
    """validate issues from the manifest lock recorded in the layout (re-checked against the file)."""
    if not lock_info or not lock_info.get("locked"):
        return [{"type": "manifest_not_locked",
                 "message": "no confirmed-manifest lock; run `confirm` after the user confirms, then build-layout"}]
    cur = manifest_lock_state(lock_info["manifest"]) if lock_info.get("manifest") and Path(lock_info["manifest"]).is_file() else None
    changed = bool(lock_info.get("changed")) or bool(cur and cur["changed"])
    if changed:
        return [{"type": "manifest_changed_after_confirm", "manifest": lock_info.get("manifest"),
                 "confirmed_sha256": lock_info.get("confirmed_sha256"),
                 "sha256": (cur or lock_info).get("sha256"), "hint": MANIFEST_CHANGED_HINT}]
    return []


def _evidence_psnr(ev):
    """Photoshop-verified recomposite dB from split_evidence (None if only offline / prose)."""
    if not isinstance(ev, dict):
        return None, None
    vals = []
    for key in ("verified_psnr", "recomposite_psnr"):
        if isinstance(ev.get(key), (int, float)):
            vals.append((float(ev[key]), key))
    sf = ev.get("split_fix")
    if isinstance(sf, dict):
        m = (sf.get("match") or {}).get("psnr")
        if isinstance(m, (int, float)):
            vals.append((float(m), "split_fix.match"))
        sw = sf.get("swap_min_psnr")
        if isinstance(sw, (int, float)):
            vals.append((float(sw), "split_fix.swap_min_psnr"))
    if isinstance(ev.get("swap_min_psnr"), (int, float)):
        vals.append((float(ev["swap_min_psnr"]), "swap_min_psnr"))
    if not vals:
        return None, None
    return min(vals)


def check_avatar_splits(entries, decisions, suspects=None, tree=None, thresholds=None):
    """Issues for the avatar/frame rule: try a split first, ask the user only after it fails.

    - split_avatar_merged_default: a split_avatar decision defaults to exporting merged.
    - split_avatar_not_attempted: a split_avatar decision without attempts (method + reason) or compare_image.
    - avatar_merged_unsplit: an avatar-merge suspect is exported as one image, with no split and no decision.
    - split_*: parts that claim a split but are incomplete (unknown method, no evidence, avatar not a
      placeholder, smart-object split without hide_inner, pixel estimate not marked approximate).
    """
    issues = []
    thr = resolve_thresholds(thresholds)
    decided = set()
    for d in decisions or []:
        if d.get("type") != "split_avatar":
            continue
        decided.update(d.get("instances") or [])
        did = d.get("id") or d.get("instances")
        if str(d.get("default") or "").lower() in MERGED_DEFAULTS:
            issues.append({"type": "split_avatar_merged_default", "decision": did, "default": d.get("default"),
                           "message": "a split_avatar decision may not default to a merged export; default to "
                                      "the best split candidate or to waiting for the user"})
        attempts = [a for a in (d.get("attempts") or []) if isinstance(a, dict) and a.get("method") and (a.get("reason") or a.get("result"))]
        if not attempts or not d.get("compare_image"):
            issues.append({"type": "split_avatar_not_attempted", "decision": did,
                           "message": "try the split methods first (references/slicing.md); a split_avatar decision "
                                      "needs attempts [{method, result, reason}] and compare_image"})
    for e in entries or []:
        method = e.get("split_method")
        if not method:
            continue
        name = e.get("name")
        if method not in SPLIT_METHODS:
            issues.append({"type": "split_method_unknown", "name": name, "split_method": method})
        if not e.get("split_evidence"):
            issues.append({"type": "split_evidence_missing", "name": name})
        role = (e.get("part_role") or e.get("role") or "").lower()
        if role == "avatar" and not e.get("placeholder"):
            issues.append({"type": "split_avatar_not_placeholder", "name": name})
        if method == "smart_object_contents" and not ((e.get("split") or {}).get("hide_inner")):
            issues.append({"type": "split_spec_missing", "name": name,
                           "message": "smart_object_contents needs split.hide_inner (see split-avatar output)"})
        if method in APPROXIMATE_SPLIT_METHODS and not (e.get("split_approximate") or (e.get("split_evidence") or {}).get("approximate")):
            issues.append({"type": "split_approximate_unmarked", "name": name})
        ev = e.get("split_evidence") or {}
        if role in ("frame", "avatar") and isinstance(ev, dict) and not user_decision_for(name, decisions, {"split_avatar"}):
            gate = thr["split_match_psnr"]
            db, src = _evidence_psnr(ev)
            if db is not None and db < gate:
                issues.append({"type": "split_below_threshold", "name": name, "psnr_db": round(db, 2), "source": src,
                               "threshold_psnr": gate, "message": SPLIT_BELOW_HINT})
            elif db is None and ev.get("status") == "candidate_unverified":
                issues.append({"type": "split_unverified", "name": name, "threshold_psnr": gate,
                               "message": "split-avatar said candidate_unverified: export merged / frame / content in "
                                          "Photoshop, measure (split-fix) and record split_evidence.verified_psnr, or stop "
                                          "and ask the user with a split_avatar decision"})
    desc = _descendant_ids(tree) if tree else {}
    for s in suspects or []:
        sid = s.get("layer_id")
        if sid is None:
            continue
        users = []
        for e in entries or []:
            ids = list(e.get("select_ids") or []) or ([e.get("layer_id")] if e.get("layer_id") is not None else [])
            if any(i == sid or sid in desc.get(i, ()) for i in ids):
                users.append(e)
        if not users:
            continue
        split_users = [e for e in users if e.get("split_method")]
        unsplit = [e.get("name") for e in users if not e.get("split_method") and e.get("name") not in decided]
        if unsplit and not split_users:
            issues.append({"type": "avatar_merged_unsplit", "layer_id": sid, "names": unsplit,
                           "message": "avatar picture and frame are exported as one image; run split-avatar and "
                                      "split, or add a split_avatar decision with attempts and compare_image"})
    return issues


SPLIT_BELOW_HINT = (
    "split recomposite is below the threshold. 1) Ownership default: outer effects around the content "
    "(drop shadow, outer glow, stroke) belong to the frame: bake them into the frame part, never onto the "
    "content or into the frame's hole. 2) Still failing: localize by toggling effects layer by layer and pick a fix "
    "(references/slicing.md). 3) Still below: STOP and ask the user with a split_avatar decision (attempts, dB, "
    "compare_image). Never mark it best-effort / accepted yourself."
)


def frame_from_instance(merged_layer, frame_layer, out_dir, tol=24):
    """Fallback: the frame comes from an unmerged instance; the avatar region is a pixel estimate."""
    out_dir = Path(out_dir)
    rec = {"layer_id": int(merged_layer.layer_id), "name": merged_layer.name, "method": "frame_from_instance",
           "frame_layer_id": int(frame_layer.layer_id), "frame_name": frame_layer.name, "approximate": True}
    merged, frame = merged_layer.topil(), frame_layer.topil()
    if merged is None or frame is None:
        rec.update(status="error", reason="layer pixels unreadable")
        return rec
    merged, frame = merged.convert("RGBA"), frame.convert("RGBA")
    if abs(merged.width - frame.width) > 1 or abs(merged.height - frame.height) > 1:
        rec.update(status="size_mismatch", reason=f"merged {merged.size} vs frame {frame.size}")
        return rec
    avatar, recomposite, match = estimate_avatar_mask(merged, frame, tol)
    out_dir.mkdir(parents=True, exist_ok=True)
    avatar.save(out_dir / "avatar_estimate.png")
    recomposite.save(out_dir / "recomposite.png")
    sheet = write_compare_sheet([(f"{rec['layer_id']} {merged_layer.name}", merged, recomposite)], out_dir / "compare.png",
                                titles=("design", "frame over avatar", "diff x3"))
    bbox = avatar.getbbox()
    l, t = int(merged_layer.left), int(merged_layer.top)
    rec["avatar_rect_estimate"] = [bbox[0] + l, bbox[1] + t, bbox[2] + l, bbox[3] + t] if bbox else None
    rec["match"] = match
    rec["compare_image"] = str(sheet) if sheet else None
    rec["status"] = "candidate" if match["psnr"] >= SPLIT_MATCH_PSNR and bbox else "mismatch"
    return rec


def cmd_split_avatar(args):
    """Try to split avatar pictures from their frames (offline evidence + manifest snippets)."""
    psd = open_psd(args.psd)
    ids = [int(x) for x in (args.layer_id or [])]
    cand_path = Path(args.candidates) if args.candidates else None
    suspects = []
    if cand_path and cand_path.exists():
        suspects = json.loads(cand_path.read_text(encoding="utf-8")).get("avatar_merge_suspects") or []
    if not ids:
        ids = [int(s["layer_id"]) for s in suspects if s.get("layer_id") is not None]
    if not ids:
        raise SystemExit("no layers: pass --layer-id or --candidates <reuse_candidates.json> with avatar_merge_suspects")
    frames = {}
    for spec in args.frame_instance or []:
        a, _, b = spec.partition(":")
        frames[int(a)] = int(b)
    layers = {}
    for layer in psd.descendants():
        try:
            layers[int(layer.layer_id)] = layer
        except Exception:
            continue
    out_dir = Path(args.out_dir)
    results = []
    for lid in ids:
        layer = layers.get(lid)
        if layer is None:
            results.append({"layer_id": lid, "status": "not_found"})
            continue
        attempts = []
        rec = split_smart_object_layer(layer, out_dir / str(lid))
        attempts.append({"method": rec["method"], "result": rec["status"], "reason": rec.get("reason"),
                         "match": rec.get("match")})
        if rec["status"] in {"candidate", "candidate_unverified"}:
            rec["suggested_parts"] = suggested_split_parts(rec)
        elif lid in frames and frames[lid] in layers:
            alt = frame_from_instance(layer, layers[frames[lid]], out_dir / f"{lid}_frame_{frames[lid]}")
            attempts.append({"method": alt["method"], "result": alt["status"], "reason": alt.get("reason"),
                             "match": alt.get("match")})
            if alt["status"] == "candidate":
                alt["smart_object_attempt"] = {k: rec.get(k) for k in ("status", "reason", "match")}
                rec = alt
        rec["attempts"] = attempts
        results.append(rec)
    summary = {
        "psd": str(args.psd),
        "results": results,
        "split_ok": [r["layer_id"] for r in results if r.get("status") == "candidate"],
        "split_needs_photoshop_check": [r["layer_id"] for r in results if r.get("status") == "candidate_unverified"],
        "needs_other_method": [r["layer_id"] for r in results if r.get("status") not in {"candidate", "candidate_unverified"}],
        "notes": [
            "status candidate: frame over avatar matches the whole contents (split_consistency >= %.0f dB) and the layer's "
            "stored pixels within the resampling floor. Use suggested_parts "
            "(split_method smart_object_contents: Photoshop exports each part from a temporary copy via Edit Contents, "
            "hiding split.hide_inner), then confirm with validate." % SPLIT_MATCH_PSNR,
            "Inner layers are rendered by psd-tools here, so the match is evidence, not the final pixels.",
            "Other statuses: try mask/clip layers in the document, a frame from an unmerged instance (--frame-instance "
            "SUSPECT:FRAME), or a pixel/shape mask (approximate). Only when all fail, add a split_avatar decision with "
            "attempts and compare_image.",
            "frame_from_instance: the avatar region is a pixel estimate (approximate: true).",
            "outer_effect_risks / edge_effect_risks: effects redrawn along the frame's new inner edge, or frame-side "
            "blends that degrade to normal when exported alone. Default fix is two parts (content + frame.png): "
            "split-fix bakes those layers into the frame (least-squares src-over approx, verified on a content swap). "
            "Use at >= %.0f dB on the placeholder and the swap; only then, if it fails, offer overlay parts "
            "(--emit-overlays) or keep merged, and ask the user (layer, why, fix, dB, compare image)." % TWO_PART_PSNR,
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "split_candidates.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("split_ok", "split_needs_photoshop_check", "needs_other_method")} | {"out": str(out_dir / "split_candidates.json")},
                     ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Effects along the new inner edge (1.4.5). Layer effects are drawn along the alpha edge of the
# layer / group that owns them. Splitting content out of a frame turns a solid shape into a ring, so
# a drop shadow, outer glow, stroke, inner shadow... on the owner gains a second, inner edge in the
# frame-only export (e.g. the shadow falls into the hole and is frozen into the frame; normal-over
# on the content then darkens the inner rim). A frame-side layer with a non-normal blend, exported
# alone, blends with transparency and degrades to normal. See references/slicing.md
# ("拆开后拼回去对不上：先定位效果").
# ---------------------------------------------------------------------------

# psd-tools effect class names (also what prepare writes into tree.json "effects").
EDGE_EFFECTS_OUTER = ("DropShadow", "OuterGlow", "Stroke", "BevelEmboss")
EDGE_EFFECTS_INNER = ("InnerShadow", "InnerGlow", "Satin")
EDGE_EFFECTS = EDGE_EFFECTS_OUTER + EDGE_EFFECTS_INNER
# Blend modes an engine can replay from a separate overlay image (premultiplied blend functions;
# linear_burn needs a reverse-subtract blend equation: dst - a * (1 - src)).
ENGINE_BLENDS = {"normal", "multiply", "screen", "linear_burn", "lighten", "darken", "linear_dodge"}
# Results depend strongly on the content underneath: hand the decision to the user (with evidence).
CONTENT_DEPENDENT_BLENDS = {"color_dodge", "color_burn", "overlay", "soft_light", "hard_light", "vivid_light",
                            "linear_light", "pin_light", "hard_mix", "difference", "exclusion", "subtract",
                            "divide", "hue", "saturation", "color", "luminosity", "darker_color",
                            "lighter_color", "dissolve"}
CONTENT_DEPENDENT_KINDS = {"adjustment", "brightnesscontrast", "levels", "curves", "exposure", "vibrance",
                           "huesaturation", "colorbalance", "blackandwhite", "photofilter", "channelmixer",
                           "colorlookup", "invert", "posterize", "threshold", "selectivecolor", "gradientmap"}
EFFECT_FIX_PSNR = 40.0     # exact overlay replay (content + frame + blend_mode parts) vs merged; measured 42 dB on PS 2026
AA_BAND_SMOOTH_RADIUS = 1  # 1.4.10: pool the LSQ fit over a 3x3 neighbourhood on the frame's anti-aliased hole edge
TWO_PART_PSNR = 35.0       # two-part approx (frame.png over content vs merged, and vs a simulated swap); below this, offer overlays or ask
HOLE_MIN_FRACTION = 0.05   # enclosed transparent area / bbox area for a frame to count as a ring


def _norm_blend(mode):
    s = _blend_name(mode) if mode is not None else None
    return (s or "normal").replace(" ", "_").replace("-", "_")


def alpha_hole_mask(alpha, thr=8):
    """Enclosed transparent pixels of an alpha mask (the hole of a ring). Returns (mask, fraction of bbox)."""
    a = np.asarray(alpha)
    if a.ndim == 3:
        a = a[..., 3]
    clear = a <= thr
    if clear.all():
        return np.zeros_like(clear), 0.0
    pad = np.pad(clear, 1, constant_values=True)
    im = Image.fromarray(np.where(pad, 255, 0).astype("uint8"), "L").copy()  # copy: fromarray may share a read-only buffer
    ImageDraw.floodfill(im, (0, 0), 128)
    hole = (np.asarray(im) == 255)[1:-1, 1:-1]
    ys, xs = np.nonzero(~clear)
    area = float((ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1))
    return hole, round(float(hole.sum()) / area, 4)


def layer_effect_list(layer):
    """Enabled effects of a psd-tools layer as [{type, blend_mode}]."""
    out = []
    try:
        for eff in (layer.effects or []):
            if getattr(eff, "enabled", True):
                out.append({"type": eff.__class__.__name__, "blend_mode": _blend_name(getattr(eff, "blend_mode", None))})
    except Exception:
        pass
    return out


def outer_effect_risks(effects, frame_alpha=None, owner="smart object layer"):
    """Edge effects on the owner of both sides (smart object layer or common group) that the split
    redraws along the frame's new inner edge. Only reported when the frame-only alpha is a ring
    (or when frame_alpha is None, i.e. unknown)."""
    fx = [e for e in effects or [] if (e.get("type") if isinstance(e, dict) else e) in EDGE_EFFECTS]
    if not fx:
        return []
    frac = None
    if frame_alpha is not None:
        _, frac = alpha_hole_mask(frame_alpha)
        if frac < HOLE_MIN_FRACTION:
            return []
    out = []
    for e in fx:
        t = e.get("type") if isinstance(e, dict) else e
        mech = ("cast into the hole of the frame-only export and frozen into the frame; normal-over on the "
                "content darkens/tints the inner rim" if t in ("DropShadow", "OuterGlow") else
                "drawn a second time along the inner edge of the ring")
        out.append({"scope": "outer", "owner": owner, "type": t,
                    "blend_mode": e.get("blend_mode") if isinstance(e, dict) else None,
                    "hole_fraction": frac, "mechanism": mech})
    return out


def inner_edge_effect_risks(records, cls):
    """Inner-layer causes of a split mismatch, with the fix each one needs.

    - group holding both sides with edge effects: redrawn along the ring's inner edge (fix: take the
      frame pixels the content never shows through from the merged export).
    - frame-side layer over the content with a non-normal blend / fill-0 effect overlay: exported alone it
      blends with transparency (non-normal degrades to normal); fix: export it alone as an overlay part
      with blend_mode, drawn by the engine above the content.
    - content-dependent blends / adjustment layers: needs_user."""
    if not cls.get("avatar") or not cls.get("frame"):
        return []
    av = {x["id"] for x in cls["avatar"]}
    fr = {x["id"] for x in cls["frame"]}
    by_id = {r["id"]: r for r in records}
    av_boxes = [by_id[i]["bbox"] for i in av if by_id.get(i, {}).get("bbox")]

    def overlaps(b):
        return bool(b) and any(b[0] < a[2] and b[2] > a[0] and b[1] < a[3] and b[3] > a[1] for a in av_boxes)

    out = []
    for r in records:
        if not r.get("visible"):
            continue
        blend = _norm_blend(r.get("blend_mode"))
        fx = [e for e in r.get("effects") or [] if e.get("type") in EDGE_EFFECTS]
        if r.get("is_group"):
            inside = {x["id"] for x in records if r["id"] in (x.get("parents") or [])}
            if inside & av and inside & fr and fx:
                out.append({"scope": "inner_group", "id": r["id"], "name": r["name"],
                            "effects": [f"{e['type']}({e.get('blend_mode') or 'normal'})" for e in fx],
                            "mechanism": "group effect redrawn along the inner edge of the frame-only shape",
                            "fix": "frame_from_merged"})
            continue
        kind = str(r.get("kind") or "").lower().replace("_", "")
        if r["id"] not in fr or not overlaps(r.get("bbox")):
            continue
        if kind in CONTENT_DEPENDENT_KINDS or blend in CONTENT_DEPENDENT_BLENDS:
            out.append({"scope": "inner_layer", "id": r["id"], "name": r["name"], "blend_mode": blend,
                        "mechanism": "result depends on the content underneath", "fix": "needs_user"})
            continue
        effect_only = r.get("fill_opacity") == 0 and bool(r.get("effects"))
        if blend not in NORMAL_BLENDS or effect_only:
            out.append({"scope": "inner_layer", "id": r["id"], "name": r["name"], "blend_mode": blend,
                        "effect_only": effect_only,
                        "mechanism": ("exported alone it blends with transparency, so %s degrades to normal" % blend)
                        if blend not in NORMAL_BLENDS else "fill-0 effect overlay drawn over the content",
                        "fix": "overlay_part" if blend in ENGINE_BLENDS else "needs_user"})
    return out


ENGINE_BLEND_NOTES = {
    "normal": "src-over",
    "multiply": "dst * src (premultiplied: ONE_MINUS_SRC_ALPHA + DST_COLOR)",
    "screen": "1 - (1 - dst)(1 - src) (ONE, ONE_MINUS_SRC_COLOR)",
    "linear_burn": "max(0, dst + src - 1) mixed by alpha = dst - a*(1 - src): reverse-subtract blend equation, "
                   "shader outputs a*(1 - src), factors ONE, ONE",
    "lighten": "max(dst, src) mixed by alpha (MAX equation needs a premultiplied pass or a shader)",
    "darken": "min(dst, src) mixed by alpha (MIN equation needs a premultiplied pass or a shader)",
    "linear_dodge": "min(1, dst + src) (additive: ONE, ONE on premultiplied src)",
}


def effect_fix_suggestion(rec, base_name="<module>_img_avatar"):
    """Default two parts (content + frame.png with effects baked). Overlay parts are a fallback."""
    outer = rec.get("outer_effect_risks") or []
    inner = rec.get("edge_effect_risks") or []
    if not outer and not inner:
        return None
    needs_user = [x for x in inner if x.get("fix") == "needs_user"]
    overlays = [x for x in inner if x.get("fix") == "overlay_part"]
    frame_name = base_name.replace("avatar", "avatar_frame")
    parts = [{"name": frame_name, "role": "frame", "split_method": rec.get("method", "smart_object_contents"),
              "split_fix": "two_part_approx", "blend_mode": "normal",
              "build": "pixels the content never shows through (outside the content, opaque frame) from the merged "
                       "export; content-dependent frame-side layers (non-normal blends, fill-0 glows) approximated "
                       "as one src-over layer by least squares on the placeholder and a bank of representative "
                       "contents, then baked into this frame.png"}]
    optional = []
    for i, x in enumerate(overlays):
        optional.append({"name": f"{frame_name}_rim{i + 1}", "role": "overlay",
                         "blend_mode": x["blend_mode"] if x["blend_mode"] not in NORMAL_BLENDS else "normal",
                         "inner_layer": {"id": x["id"], "name": x["name"]}, "split_fix": "overlay_part",
                         "export": "only this inner layer visible; owner (outer) and group edge effects off; "
                                   "keep only the zone where the content shows through",
                         "engine": ENGINE_BLEND_NOTES.get(x["blend_mode"] if x["blend_mode"] not in NORMAL_BLENDS else "normal"),
                         "when": f"offer to the user if two-part approx drops below ~{TWO_PART_PSNR:.0f} dB on a content swap"})
    exports = {
        "merged": "the layer as designed",
        "content": "content side only; owner edge effects off and group effects off (a group's inner shadow / glow / "
                   "stroke would otherwise be recomputed on the content-only alpha and paint a frame-coloured rim)",
        "probes": "preferred: the merged export again with the content pixels hidden and the content shape filled with "
                  "solid colours (e.g. black, white, mid gray, plus a colour); pass as split-fix --probe R,G,B=PATH",
        "frame_plain": "frame side only; owner edge effects off, group edge effects off, overlay layers hidden",
    }
    for p in optional:
        exports[p["name"]] = p["export"]
    return {"parts": parts, "optional_overlays": optional, "photoshop_exports": exports,
            "verify": "split-fix --merged M --content C --frame F --probe 0,0,0=P0 --probe 255,255,255=P1 --probe 128,128,128=P2 "
                      "(preferred: fitted and checked on real renders), or [--overlay O --overlay-blend MODE] "
                      "[--swap-content OTHER.png] (simulated); default bakes overlays into frame.png (two parts). "
                      f"--emit-overlays keeps them as extra parts. Use the two-part split at >= {TWO_PART_PSNR:.0f} dB "
                      f"on the placeholder and on a swapped content (simulated); otherwise offer overlays or keep merged.",
            "needs_user": [{"id": x["id"], "name": x["name"], "why": x["mechanism"]} for x in needs_user]}


def _blend_fn(mode):
    m = _norm_blend(mode)
    if m in NORMAL_BLENDS:
        return lambda b, s: s
    table = {
        "multiply": lambda b, s: b * s,
        "screen": lambda b, s: b + s - b * s,
        "linear_burn": lambda b, s: np.clip(b + s - 1.0, 0, 1),
        "lighten": np.maximum,
        "darken": np.minimum,
        "linear_dodge": lambda b, s: np.clip(b + s, 0, 1),
    }
    if m not in table:
        raise ValueError(f"blend mode {m} is not replayable (supported: {sorted(ENGINE_BLENDS)})")
    return table[m]


def blend_arrays(dst, src, mode="normal"):
    """W3C separable compositing of straight-alpha RGBA float arrays (0-255): src over dst with blend B."""
    d, s = dst.astype(float) / 255.0, src.astype(float) / 255.0
    ab, as_ = d[..., 3:], s[..., 3:]
    B = _blend_fn(mode)(d[..., :3], s[..., :3])
    co = as_ * (1 - ab) * s[..., :3] + as_ * ab * B + (1 - as_) * ab * d[..., :3]
    ao = as_ + ab * (1 - as_)
    c = np.where(ao > 0, co / np.maximum(ao, 1e-9), 0)
    return np.concatenate([c, ao], -1) * 255.0


def blend_onto(canvas, im, xy, mode=None):
    """alpha_over with an optional replayable blend mode (normal / None -> plain src-over)."""
    if _norm_blend(mode) in NORMAL_BLENDS:
        return alpha_over(canvas, im, xy)
    layer = alpha_over(Image.new("RGBA", canvas.size, (0, 0, 0, 0)), im, xy)
    out = blend_arrays(np.asarray(canvas), np.asarray(layer), mode)
    return Image.fromarray(np.clip(np.round(out), 0, 255).astype("uint8"), "RGBA")


def compose_effect_fix(merged, content, frame_plain, overlays=()):
    """Build the content-independent frame and masked overlays from Photoshop exports, recomposite, compare.

    All images share one canvas. frame = merged where the content never shows through (content alpha 0, or
    opaque plain frame), else the plain frame (anti-aliased inner edge). Overlays [(image, blend_mode)] are
    kept only where the content shows through and drawn above content + frame with their blend mode."""
    m = np.asarray(merged.convert("RGBA")).astype(float)
    c = np.asarray(content.convert("RGBA")).astype(float)
    f = np.asarray(frame_plain.convert("RGBA")).astype(float)
    if not (m.shape == c.shape == f.shape):
        raise ValueError(f"images must share one canvas: merged {m.shape}, content {c.shape}, frame {f.shape}")
    from_merged = (c[..., 3:] == 0) | (f[..., 3:] == 255)
    frame = np.where(from_merged, m, f)
    zone = (c[..., 3:] > 0) & (f[..., 3:] < 255)
    to_im = lambda a: Image.fromarray(np.clip(np.round(a), 0, 255).astype("uint8"), "RGBA")
    rec = blend_arrays(c, frame, "normal")
    outs = []
    for im, mode in overlays:
        o = np.asarray(im.convert("RGBA")).astype(float)
        if o.shape != m.shape:
            raise ValueError(f"overlay must share the canvas: {o.shape} vs {m.shape}")
        o = o * zone
        rec = blend_arrays(rec, o, mode)
        outs.append((to_im(o), _norm_blend(mode)))
    recomposite = to_im(rec)
    return {"frame": to_im(frame), "overlays": outs, "recomposite": recomposite,
            "match": split_match(recomposite, merged.convert("RGBA")),
            "content_zone_px": int(zone.sum())}



def _as_rgba_f(im):
    if isinstance(im, np.ndarray):
        return im.astype(float)
    return np.asarray(im.convert("RGBA")).astype(float)


def _to_rgba_img(a):
    return Image.fromarray(np.clip(np.round(a), 0, 255).astype("uint8"), "RGBA")


def content_show_through_zone(content, frame_plain):
    """Pixels where the content is visible through the plain (hole + anti-aliased inner edge)."""
    c, f = _as_rgba_f(content), _as_rgba_f(frame_plain)
    return (c[..., 3] > 0) & (f[..., 3] < 255)


def frame_pixels_from_merged(merged, content, frame_plain):
    """Frame pixels the content never shows through come from the merged export; the inner AA edge from the plain frame."""
    m, c, f = _as_rgba_f(merged), _as_rgba_f(content), _as_rgba_f(frame_plain)
    from_merged = (c[..., 3:] == 0) | (f[..., 3:] == 255)
    return np.where(from_merged, m, f)


def _fit_extra_content_to_placeholder(src, placeholder):
    """Place an external content image onto the placeholder's alpha bbox without a dark fringe.

    Crop ``src`` to its opaque core (alpha >= 250 bbox), take a centered inscribed
    square-ish region (~0.80 of the core radius, so rounded transparent corners are
    excluded), premultiply alpha, resize with LANCZOS to the placeholder's alpha bbox,
    unpremultiply, set alpha 255, and paste onto a canvas aligned to that bbox. The
    caller copies RGB only where the placeholder is opaque.
    """
    P = placeholder
    H, W = P.shape[:2]
    src = _as_rgba_f(src)
    ys, xs = np.nonzero(src[..., 3] >= 250)
    if ys.size == 0:
        ys, xs = np.nonzero(src[..., 3] > 0)
    if ys.size == 0:
        return np.zeros((H, W, 4), dtype=float)
    cy, cx = (ys.min() + ys.max()) / 2, (xs.min() + xs.max()) / 2
    r = min(ys.max() - ys.min(), xs.max() - xs.min()) / 2 * 0.80
    x0, y0, x1, y1 = int(cx - r), int(cy - r), int(cx + r), int(cy + r)
    if x1 <= x0:
        x1 = x0 + 1
    if y1 <= y0:
        y1 = y0 + 1
    src_im = Image.fromarray(np.clip(np.round(src), 0, 255).astype("uint8"), "RGBA")
    core = src_im.crop((x0, y0, x1, y1))
    if core.size[0] == 0 or core.size[1] == 0:
        return np.zeros((H, W, 4), dtype=float)
    pa = P[..., 3] > 0
    py, px = np.nonzero(pa)
    if py.size == 0:
        return np.zeros((H, W, 4), dtype=float)
    bw, bh = int(px.max() - px.min() + 1), int(py.max() - py.min() + 1)
    o = np.asarray(core).astype(float)
    premul = np.dstack([o[..., :3] * o[..., 3:4] / 255.0, o[..., 3]])
    pm = Image.fromarray(np.clip(np.round(premul), 0, 255).astype("uint8"), "RGBA")
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    pm = np.asarray(pm.resize((bw, bh), resampling)).astype(float)
    rgb = np.where(pm[..., 3:4] > 0, pm[..., :3] * 255.0 / np.maximum(pm[..., 3:4], 1), 0)
    canvas = np.zeros((H, W, 4), dtype=float)
    y0p, x0p = int(py.min()), int(px.min())
    canvas[y0p:y0p + bh, x0p:x0p + bw, :3] = np.clip(rgb, 0, 255)
    canvas[y0p:y0p + bh, x0p:x0p + bw, 3] = 255
    return canvas


def representative_contents(placeholder, extras=None):
    """A bank of contents that share the placeholder's alpha: itself, inverted / darker / brighter,
    solid colours, and ramps. ``extras`` is a list of images (or (name, image)). Each is fitted
    into the placeholder footprint: if the shape differs, or a same-size image has transparent
    pixels inside the footprint, the opaque core is cropped and resized (premultiplied LANCZOS)
    onto the placeholder's alpha bbox; otherwise RGB is copied in place. Transparent corners of
    the source are not stretched into the footprint."""
    P = _as_rgba_f(placeholder)
    H, W = P.shape[:2]
    alpha = P[..., 3:4]

    def with_rgb(rgb):
        out = np.zeros((H, W, 4), dtype=float)
        out[..., :3] = rgb
        out[..., 3:4] = alpha
        out[P[..., 3] == 0] = 0
        return out

    items = {"placeholder": P.copy()}
    items["inverted"] = with_rgb(255 - P[..., :3])
    items["darker"] = with_rgb(P[..., :3] * 0.45)
    items["brighter"] = with_rgb(np.minimum(P[..., :3] * 1.4 + 40, 255))
    solids = {
        "black": (0, 0, 0), "white": (255, 255, 255), "gray": (128, 128, 128),
        "dkgray": (48, 48, 48), "ltgray": (200, 200, 200),
        "red": (200, 40, 40), "green": (40, 180, 60), "blue": (30, 80, 200),
        "yellow": (230, 200, 40), "cyan": (40, 200, 210), "magenta": (200, 40, 180),
        "orange": (220, 120, 30), "skin": (210, 160, 130),
    }
    for key, rgb in solids.items():
        items[key] = with_rgb(np.array(rgb, dtype=float))
    yy, xx = np.mgrid[0:H, 0:W]
    items["gray_h"] = with_rgb(np.stack([xx * 255 / max(W - 1, 1)] * 3, -1))
    items["gray_v"] = with_rgb(np.stack([yy * 255 / max(H - 1, 1)] * 3, -1))
    items["color_ramp"] = with_rgb(np.stack(
        [xx * 255 / max(W - 1, 1), yy * 255 / max(H - 1, 1), np.full((H, W), 180.0)], -1))
    for i, item in enumerate(extras or []):
        name, src = item if isinstance(item, tuple) else (f"extra_{i}", item)
        src = _as_rgba_f(src)
        interior = P[..., 3] > 0
        # Size mismatch, or a same-size source with holes inside the footprint (rounded
        # transparent corners, padding): crop the opaque core and scale it to the
        # placeholder bbox. A same-size source that already fills the interior is copied.
        if src.shape[:2] != (H, W) or np.any(interior & (src[..., 3] < 250)):
            src = _fit_extra_content_to_placeholder(src, P)
        out = P.copy()
        out[interior, :3] = src[interior, :3]
        items[name] = out
    return items


def simulate_overlay_stack(content, frame, overlays, zone):
    """content, then frame (normal), then each overlay with its blend_mode, masked to ``zone``.

    This is a formula simulation of Photoshop (see blend_arrays), not a re-render."""
    rec = blend_arrays(_as_rgba_f(content), _as_rgba_f(frame), "normal")
    z = zone[..., None] if getattr(zone, "ndim", 2) == 2 else zone
    for im, mode in overlays:
        rec = blend_arrays(rec, _as_rgba_f(im) * z, mode)
    return rec


def _box_sum(a, r, axes=(0, 1)):
    """Sum over a (2r+1)x(2r+1) window on the first two axes (zero padded)."""
    if r <= 0:
        return a
    pad = [(r, r), (r, r)] + [(0, 0)] * (a.ndim - 2)
    p = np.pad(a, pad)
    c = p.cumsum(0).cumsum(1)
    c = np.pad(c, [(1, 0), (1, 0)] + [(0, 0)] * (a.ndim - 2))
    k = 2 * r + 1
    H, W = a.shape[:2]
    return c[k:k + H, k:k + W] - c[0:H, k:k + W] - c[k:k + H, 0:W] + c[0:H, 0:W]


def fit_src_over_layer(bases, targets, zone, weights=None, smooth_mask=None, smooth_radius=1):
    """Per-pixel least squares: a straight-alpha src-over ``(C, a)`` such that ``(1-a)*B + a*C ≈ T``
    over a set of (base, target) samples. ``bases`` / ``targets`` are name -> HxWx3 or HxWx4 arrays.
    Only zone pixels where the base is opaque (alpha 255, if present) vote. Returns an RGBA float layer.

    ``smooth_mask`` (1.4.10): pixels (e.g. the frame's anti-aliased inner edge) where a per-pixel solve is
    ill-conditioned (low alpha, targets that only approximate the real stack). There the normal equations
    are pooled over a ``(2r+1)^2`` neighbourhood inside the zone, so neighbouring pixels share one stable
    colour / alpha instead of alternating dark / bright solutions (stair-step on the hole edge)."""
    names = list(bases)
    Ds = [_as_rgba_f(bases[n])[..., :3] for n in names]
    Ts = [_as_rgba_f(targets[n])[..., :3] for n in names]
    H, W = Ds[0].shape[:2]
    D, T = np.stack(Ds, 0), np.stack(Ts, 0)
    wlist = []
    for n in names:
        w = zone.astype(float) * (1.0 if not weights else float(weights.get(n, 1.0)))
        b = _as_rgba_f(bases[n])
        if b.shape[-1] == 4:
            w = w * (b[..., 3] >= 255)
        t = _as_rgba_f(targets[n])
        if t.shape[-1] == 4:
            w = w * np.where(t[..., 3] < 250, 0.25, 1.0)
        wlist.append(w)
    w = np.stack(wlist, 0)
    Y = T - D
    sum_w = w.sum(0)
    ATA = np.zeros((H, W, 4, 4))
    ATb = np.zeros((H, W, 4))
    ATA[..., 0, 0] = ATA[..., 1, 1] = ATA[..., 2, 2] = sum_w
    for ch in range(3):
        s_wD = (w * D[..., ch]).sum(0)
        ATA[..., ch, 3] = ATA[..., 3, ch] = -s_wD
        ATb[..., ch] = (w * Y[..., ch]).sum(0)
    ATA[..., 3, 3] = (w * (D ** 2).sum(-1)).sum(0)
    ATb[..., 3] = -(w * (D * Y).sum(-1)).sum(0)
    if smooth_mask is not None and np.any(smooth_mask) and smooth_radius > 0:
        zm = np.asarray(zone, dtype=bool)
        ATA_s = _box_sum(ATA * zm[..., None, None], smooth_radius)
        ATb_s = _box_sum(ATb * zm[..., None], smooth_radius)
        sm = np.asarray(smooth_mask, dtype=bool) & zm
        ATA[sm] = ATA_s[sm]
        ATb[sm] = ATb_s[sm]
        sum_w = np.where(sm, _box_sum(sum_w * zm, smooth_radius), sum_w)
    ATA = ATA + 1e-6 * np.eye(4)
    X = np.linalg.solve(ATA, ATb[..., None])[..., 0]
    m, a = X[..., :3], np.clip(X[..., 3], 0, 1)
    m = np.minimum(np.clip(m, 0, 255), a[..., None] * 255.0)
    C = np.zeros_like(m)
    nz = a > 1e-6
    C[nz] = m[nz] / a[nz][..., None]
    C = np.clip(C, 0, 255)
    empty = sum_w < 1e-9
    C[empty] = 0
    a[empty] = 0
    out = np.zeros((H, W, 4))
    out[..., :3] = C
    out[..., 3] = a * 255.0
    out[~np.asarray(zone, dtype=bool)] = 0
    return out


def compose_two_part_frame(merged, content, frame_plain, overlays=(), extra_contents=None):
    """Default 1.4.6 fix: one normal ``frame.png`` plus the content (placeholder / runtime).

    Pixels the content never shows through come from the merged export (drop shadows, the solid ring).
    Content-dependent frame-side overlays (linear_burn, inner glow, ...) are approximated as a single
    src-over layer: for each pixel a colour and alpha are fitted by least squares so that
    ``src-over(frame, content)`` stays close to the true stack on the placeholder (the real merged
    export) and on a bank of representative contents. The "true" stack on those other contents is
    **simulated** with ``blend_arrays`` (linear_burn + normal + ...), not re-rendered in Photoshop.
    Baking a thin rim of the placeholder photo into the frame is not used: it fails as soon as the
    content is swapped.
    """
    m = _as_rgba_f(merged)
    c = _as_rgba_f(content)
    frame0 = frame_pixels_from_merged(merged, content, frame_plain)
    zone = content_show_through_zone(content, frame_plain)
    aa_band = zone & (_as_rgba_f(frame_plain)[..., 3] > 0)
    contents = representative_contents(content, extra_contents)
    sim = {k: simulate_overlay_stack(v, frame0, overlays, zone) for k, v in contents.items()}
    if overlays:
        targets = dict(sim)
        targets["placeholder"] = m
        bases = {k: blend_arrays(_as_rgba_f(v), frame0, "normal") for k, v in contents.items()}
        # 1.4.10: never solve the anti-aliased hole edge pixel by pixel (stair-step / dark ring);
        # pool the normal equations there with the neighbours.
        residual = fit_src_over_layer(bases, targets, zone, smooth_mask=aa_band, smooth_radius=AA_BAND_SMOOTH_RADIUS)
        frame = blend_arrays(frame0, residual, "normal")
    else:
        residual = np.zeros_like(frame0)
        frame = frame0
    frame_im = _to_rgba_img(frame)
    rec_im = _to_rgba_img(blend_arrays(c, frame, "normal"))
    merged_im = merged.convert("RGBA") if not isinstance(merged, np.ndarray) else _to_rgba_img(m)
    swaps = {}
    for k, v in contents.items():
        if k == "placeholder":
            continue
        got = _to_rgba_img(blend_arrays(_as_rgba_f(v), frame, "normal"))
        swaps[k] = split_match(got, _to_rgba_img(sim[k]))
    # Gate on contents that stand in for a swapped picture (brightness / invert / provided extras),
    # not on solid-colour probes — those stay in `swap` as diagnostics. See references/slicing.md 10.11.
    probe = [k for k in swaps if k in {"inverted", "darker", "brighter"} or k.startswith("extra_") or k.startswith("swap_")]
    probe_vals = [swaps[k]["psnr"] for k in probe] or [99.0]
    all_vals = [x["psnr"] for x in swaps.values()] or [99.0]
    ph = split_match(rec_im, merged_im)
    cc = content_vs_merged_where_frame_clear(merged, content, frame)
    return {
        "frame": frame_im,
        "recomposite": rec_im,
        "residual": _to_rgba_img(residual),
        "frame_from_merged": _to_rgba_img(frame0),
        "match": ph,
        "swap": swaps,
        "swap_min_psnr": round(float(min(probe_vals)), 2),
        "swap_mean_psnr": round(float(np.mean(probe_vals)), 2),
        "swap_all_min_psnr": round(float(min(all_vals)), 2),
        "swap_all_mean_psnr": round(float(np.mean(all_vals)), 2),
        "simulated_swap": True,
        "content_zone_px": int(np.asarray(zone).sum()),
        "aa_band_px": int(aa_band.sum()),
        "aa_band_smoothed": bool(overlays) and AA_BAND_SMOOTH_RADIUS > 0,
        "aa_band_noise": aa_band_noise(frame, aa_band),
        "content_check": cc,
        "method": "two_part_approx",
        "approx": "least_squares_src_over" if overlays else "frame_from_merged_only",
        "threshold_psnr": TWO_PART_PSNR,
        "pass": ph["psnr"] >= TWO_PART_PSNR and min(probe_vals) >= TWO_PART_PSNR and not cc["rim_suspect"],
    }


def aa_band_noise(frame, band):
    """Diagnostic: mean |premultiplied RGB - its 3x3 median| over the anti-aliased band (0-255 scale).

    A median keeps real edges and smooth gradients but not isolated outliers, so high values mean
    neighbouring edge pixels got alternating colours (stair-step / speckled ring). Report only; compare
    before / after a change on the same inputs."""
    band = np.asarray(band, dtype=bool)
    if not band.any():
        return None
    f = _as_rgba_f(frame)
    pm = f[..., :3] * f[..., 3:4] / 255.0
    H, W = pm.shape[:2]
    p = np.pad(pm, ((1, 1), (1, 1), (0, 0)), mode="edge")
    med = np.median(np.stack([p[dy:dy + H, dx:dx + W] for dy in range(3) for dx in range(3)], 0), 0)
    return round(float(np.abs(pm - med)[band].mean()), 3)


def content_vs_merged_where_frame_clear(merged, content, frame):
    """Where the frame is fully clear and the content opaque, the content export must equal the merged design.

    A mismatch there means the content export carries pixels the design does not show, e.g. a group's
    inner shadow / inner glow / stroke recomputed on the content-only alpha after the frame layers were
    hidden (a frame-coloured rim along the content edge). Those effects belong to the frame: turn them
    off for the content export (references/batchplay.md, group effects) and keep them in the frame."""
    m, c, f = _as_rgba_f(merged), _as_rgba_f(content), _as_rgba_f(frame)
    clear = (f[..., 3] == 0) & (c[..., 3] >= 255) & (m[..., 3] >= 255)
    n = int(clear.sum())
    if n == 0:
        return {"px": 0, "psnr": None, "pct_px_diff_gt16": None, "rim_suspect": False}
    d = np.abs(m[..., :3] - c[..., :3]).max(-1)[clear]
    mse = float(((m[..., :3] - c[..., :3]) ** 2)[clear].mean())
    psnr = 99.0 if mse <= 1e-9 else round(10 * math.log10(255.0 ** 2 / mse), 2)
    pct = round(float((d > 16).mean() * 100), 3)
    return {"px": n, "psnr": psnr, "pct_px_diff_gt16": pct,
            "rim_suspect": bool(psnr < TWO_PART_PSNR)}


def _parse_probe_spec(spec):
    """``R,G,B=PATH`` or ``#RRGGBB=PATH`` (PATH may carry ``#X,Y``)."""
    key, sep, path = str(spec).partition("=")
    if not sep:
        raise SystemExit(f"--probe {spec}: expected R,G,B=PATH or #RRGGBB=PATH")
    key = key.strip()
    if key.startswith("#") and len(key) == 7:
        rgb = tuple(int(key[i:i + 2], 16) for i in (1, 3, 5))
    else:
        try:
            rgb = tuple(int(round(float(v))) for v in key.split(","))
        except ValueError:
            raise SystemExit(f"--probe {spec}: bad colour {key!r}")
    if len(rgb) != 3 or any(v < 0 or v > 255 for v in rgb):
        raise SystemExit(f"--probe {spec}: colour must be 3 values 0-255")
    return rgb, path


def _solid_like(content, rgb):
    c = _as_rgba_f(content)
    out = np.zeros_like(c)
    out[..., :3] = np.array(rgb, dtype=float)
    out[..., 3] = c[..., 3]
    return out


def compose_two_part_frame_from_probes(merged, content, probes, frame_plain=None):
    """1.4.10: two-part split fitted on real Photoshop renders instead of simulated blend formulas.

    ``probes`` is a list of ``(rgb, image)``: the same merged export, but with the content replaced by a solid
    colour of the same shape (hide the content pixels, recolour the clipping base / content shape). Frame-side
    effects (inner shadows, inner glow, linear-burn / multiply overlays, group effects) are rendered by
    Photoshop over each probe, so nothing is simulated.

    - ``zone``: pixels where the probe renders differ (the content shows through). Outside it the frame is the
      merged export (outer shadows etc., content-independent by construction).
    - inside the zone, one src-over ``(C, a)`` per pixel by least squares over all probes. With probes that span
      dark and bright colours the system is well conditioned, also on the low-alpha anti-aliased edge.
    - swap check: leave-one-out, i.e. the frame fitted without probe k predicts the real render of probe k.
    - placeholder check: content + frame vs merged; content check: content vs merged where the frame is clear.
    """
    m = _as_rgba_f(merged)
    c = _as_rgba_f(content)
    probes = [(tuple(int(v) for v in rgb), _as_rgba_f(im)) for rgb, im in probes]
    if len(probes) < 2:
        raise ValueError("need at least 2 probe renders (dark and bright)")
    for rgb, im in probes:
        if im.shape != m.shape:
            raise ValueError(f"probe {rgb} must share the merged canvas: {im.shape} vs {m.shape}")
    lum = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in (p[0] for p in probes)]
    stack = np.stack([p[1][..., :3] for p in probes], 0)
    # Content shows through where the probe renders differ by more than one 8-bit step.
    zone = (stack.max(0) - stack.min(0)).max(-1) > 1.0
    opaque = c[..., 3] >= 255
    partial = zone & ~opaque
    zone_fit = zone & opaque

    def fit(sel):
        bases = {i: _solid_like(content, probes[i][0]) for i in sel}
        targets = {i: probes[i][1] for i in sel}
        layer = fit_src_over_layer(bases, targets, zone_fit)
        return np.where(zone_fit[..., None], layer, m)

    idx = list(range(len(probes)))
    frame = fit(idx)
    # Partly transparent content under a non-opaque frame: the merged pixel already holds content; keep it
    # (counted in the report) rather than double it at runtime.
    frame_im = _to_rgba_img(frame)
    rec = blend_arrays(c, frame, "normal")
    rec_im = _to_rgba_img(rec)
    ph = split_match(rec_im, _to_rgba_img(m))
    loo = {}
    if len(probes) >= 3:
        for k in idx:
            fk = fit([i for i in idx if i != k])
            got = _to_rgba_img(blend_arrays(_solid_like(content, probes[k][0]), fk, "normal"))
            loo["%d,%d,%d" % probes[k][0]] = split_match(got, _to_rgba_img(probes[k][1]))
    insample = {}
    for rgb, im in probes:
        got = _to_rgba_img(blend_arrays(_solid_like(content, rgb), frame, "normal"))
        insample["%d,%d,%d" % rgb] = split_match(got, _to_rgba_img(im))
    loo_vals = [v["psnr"] for v in loo.values()]
    band = zone_fit & (frame[..., 3] > 0) & (frame[..., 3] < 255)
    cc = content_vs_merged_where_frame_clear(merged, content, frame)
    swap_min = round(float(min(loo_vals)), 2) if loo_vals else None
    passed = ph["psnr"] >= TWO_PART_PSNR and swap_min is not None and swap_min >= TWO_PART_PSNR
    return {
        "frame": frame_im, "recomposite": rec_im, "match": ph,
        "swap_loo": loo, "swap_in_sample": insample,
        "swap_min_psnr": swap_min,
        "swap_mean_psnr": round(float(np.mean(loo_vals)), 2) if loo_vals else None,
        "swap_is_real_render": True, "simulated_swap": False,
        "probe_colours": [list(p[0]) for p in probes],
        "probe_luma_span": round(float(max(lum) - min(lum)), 1),
        "content_zone_px": int(zone.sum()), "zone_partial_content_px": int(partial.sum()),
        "aa_band_px": int(band.sum()), "aa_band_noise": aa_band_noise(frame, band),
        "content_check": cc,
        "method": "two_part_approx", "approx": "probe_fit_src_over",
        "threshold_psnr": TWO_PART_PSNR, "pass": bool(passed and not cc["rim_suspect"]),
    }


def _open_canvas_image(spec, size=None):
    """PATH or PATH#X,Y (offset on a shared canvas of `size`)."""
    path, _, off = str(spec).partition("#")
    im = Image.open(path).convert("RGBA")
    if not off:
        return im
    x, y = (int(round(float(v))) for v in off.split(","))
    if size is None:
        raise SystemExit("offsets (#X,Y) need --canvas W,H")
    return alpha_over(Image.new("RGBA", size, (0, 0, 0, 0)), im, (x, y))


def cmd_split_fix(args):
    """Rebuild a split from Photoshop exports. Default: bake overlays into one frame.png (two parts).

    ``--emit-overlays`` keeps the 1.4.5 behaviour (exact blend_mode parts, threshold 40 dB)."""
    size = tuple(int(v) for v in args.canvas.split(",")) if args.canvas else None
    merged = _open_canvas_image(args.merged, size)
    size = size or merged.size
    content = _open_canvas_image(args.content, size)
    frame = _open_canvas_image(args.frame, size)
    blends = list(args.overlay_blend or [])
    ovs = list(args.overlay or [])
    if len(blends) != len(ovs):
        raise SystemExit("give one --overlay-blend per --overlay")
    for b in blends:
        if _norm_blend(b) not in ENGINE_BLENDS:
            raise SystemExit(f"--overlay-blend {b}: not replayable; supported {sorted(ENGINE_BLENDS)} "
                             "(content-dependent blends go to the user)")
    overlays = [(_open_canvas_image(p, size), b) for p, b in zip(ovs, blends)]
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    emit = bool(getattr(args, "emit_overlays", False))
    if emit:
        res = compose_effect_fix(merged, content, frame, overlays)
        res["frame"].save(out / "frame_from_merged.png")
        parts = [{"file": str(out / "frame_from_merged.png"), "blend_mode": "normal", "role": "frame"}]
        for i, (im, mode) in enumerate(res["overlays"]):
            path = out / f"overlay{i + 1}_{mode}.png"
            im.save(path)
            parts.append({"file": str(path), "blend_mode": mode, "role": "overlay", "engine": ENGINE_BLEND_NOTES.get(mode)})
        threshold = args.threshold if args.threshold is not None else EFFECT_FIX_PSNR
        passed = res["match"]["psnr"] >= threshold
        sheet = write_compare_sheet([(f"split-fix overlays {res['match']['psnr']} dB", merged, res["recomposite"])],
                                    out / "compare.png", titles=("merged export", "content + frame + overlays", "diff x3"))
        res["recomposite"].save(out / "recomposite.png")
        summary = {"mode": "emit_overlays", "match": res["match"], "threshold_psnr": threshold, "pass": passed,
                   "parts": parts, "compare_image": str(sheet) if sheet else None,
                   "content_zone_px": res["content_zone_px"],
                   "draw_order": ["content (runtime)"] + [p["file"] for p in parts],
                   "plain_frame_over_content": split_match(blend_arrays_img(content, frame), merged)}
    elif getattr(args, "probe", None):
        probes = []
        for spec in args.probe:
            rgb, path = _parse_probe_spec(spec)
            probes.append((rgb, _open_canvas_image(path, size)))
        res = compose_two_part_frame_from_probes(merged, content, probes, frame)
        res["frame"].save(out / "frame.png")
        res["recomposite"].save(out / "recomposite.png")
        parts = [{"file": str(out / "frame.png"), "blend_mode": "normal", "role": "frame",
                  "split_fix": "two_part_approx", "approx": res["approx"]}]
        threshold = args.threshold if args.threshold is not None else TWO_PART_PSNR
        swap_ok = res["swap_min_psnr"] is not None and res["swap_min_psnr"] >= threshold
        passed = res["match"]["psnr"] >= threshold and swap_ok and not res["content_check"]["rim_suspect"]
        sheet = write_compare_sheet(
            [(f"two-part probe fit {res['match']['psnr']} dB (swap LOO min {res['swap_min_psnr']}, real renders)",
              merged, res["recomposite"])],
            out / "compare.png", titles=("merged export", "frame over content", "diff x3"))
        keep = ("match", "swap_loo", "swap_in_sample", "swap_min_psnr", "swap_mean_psnr", "swap_is_real_render",
                "simulated_swap", "probe_colours", "probe_luma_span", "content_zone_px", "zone_partial_content_px",
                "aa_band_px", "aa_band_noise", "content_check", "approx")
        summary = {"mode": "two_part_approx", **{k: res[k] for k in keep},
                   "threshold_psnr": threshold, "pass": passed, "parts": parts,
                   "compare_image": str(sheet) if sheet else None,
                   "draw_order": ["content (runtime)", str(out / "frame.png")],
                   "note": "frame.png fitted on real Photoshop renders with the content replaced by solid colours "
                           "(--probe). Swap dB is leave-one-out on those renders, not a formula simulation. "
                           + ("Fewer than 3 probes: no held-out swap check; add one (e.g. mid gray). "
                              if res["swap_min_psnr"] is None else "")
                           + ("content_check.rim_suspect: the content export differs from the design where the "
                              "frame is clear (group inner shadow / glow / stroke recomputed on the content alpha?); "
                              "turn those effects off for the content export and re-run. "
                              if res["content_check"]["rim_suspect"] else "")}
    else:
        extras = []
        for i, spec in enumerate(getattr(args, "swap_content", None) or []):
            extras.append((f"swap_{i}", _open_canvas_image(spec, size)))
        res = compose_two_part_frame(merged, content, frame, overlays, extras)
        res["frame"].save(out / "frame.png")
        res["frame_from_merged"].save(out / "frame_from_merged.png")
        res["residual"].save(out / "residual.png")
        res["recomposite"].save(out / "recomposite.png")
        parts = [{"file": str(out / "frame.png"), "blend_mode": "normal", "role": "frame",
                  "split_fix": "two_part_approx", "approx": res["approx"]}]
        threshold = args.threshold if args.threshold is not None else TWO_PART_PSNR
        passed = (res["match"]["psnr"] >= threshold and res["swap_min_psnr"] >= threshold
                  and not res["content_check"]["rim_suspect"])
        sheet = write_compare_sheet(
            [(f"two-part {res['match']['psnr']} dB (swap min {res['swap_min_psnr']}, simulated)",
              merged, res["recomposite"])],
            out / "compare.png", titles=("merged export", "frame over content", "diff x3"))
        summary = {"mode": "two_part_approx", "match": res["match"],
                   "swap_min_psnr": res["swap_min_psnr"], "swap_mean_psnr": res["swap_mean_psnr"], "swap_all_min_psnr": res["swap_all_min_psnr"], "swap_all_mean_psnr": res["swap_all_mean_psnr"],
                   "swap": res["swap"], "simulated_swap": True, "approx": res["approx"],
                   "threshold_psnr": threshold, "pass": passed, "parts": parts,
                   "compare_image": str(sheet) if sheet else None,
                   "content_zone_px": res["content_zone_px"],
                   "aa_band_px": res["aa_band_px"], "aa_band_smoothed": res["aa_band_smoothed"],
                   "aa_band_noise": res["aa_band_noise"], "content_check": res["content_check"],
                   "draw_order": ["content (runtime)", str(out / "frame.png")],
                   "note": "frame.png is normal src-over. Swap dB is vs a formula simulation of the overlay "
                           "stack (linear_burn / multiply / ...), not a Photoshop re-render; prefer --probe renders. If pass "
                           "is false, offer --emit-overlays or keep the merged export, and ask the user."}
    (out / "split_fix.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    keys = [k for k in ("mode", "match", "swap_min_psnr", "swap_mean_psnr", "pass", "compare_image",
                        "plain_frame_over_content") if k in summary]
    print(json.dumps({k: summary[k] for k in keys}, ensure_ascii=False, indent=2))
    return 0 if summary["pass"] else 2


def blend_arrays_img(bottom, top, mode="normal"):
    out = blend_arrays(np.asarray(bottom.convert("RGBA")), np.asarray(top.convert("RGBA")), mode)
    return Image.fromarray(np.clip(np.round(out), 0, 255).astype("uint8"), "RGBA")


def _tree_parent_index(tree):
    """layer_id -> (node, parent ids outermost first) for a prepare layer tree."""
    out = {}

    def walk(node, parents):
        lid = node.get("layer_id")
        if lid is not None:
            out[lid] = (node, list(parents))
        for ch in node.get("children") or []:
            walk(ch, parents + ([lid] if lid is not None else []))
    roots = tree if isinstance(tree, list) else ([tree] if isinstance(tree, dict) else [])
    for r in roots:
        if isinstance(r, dict):
            walk(r, [])
    return out


def check_split_effects(entries, tree=None):
    """Issues for splits whose owner carries edge effects, and for overlay blend modes.

    - outer_effect_into_hole: a frame part of a split exported from a smart object (or with a common parent
      group) that carries DropShadow / OuterGlow / Stroke / ...; the frame-only export redraws them along the
      hole unless the part says how it was handled (split_fix: frame_from_merged, or split.outer_effects).
    - blend_mode_content_dependent: a part's blend_mode depends on the content underneath (needs the user).
    - blend_mode_not_replayable: a blend_mode outside ENGINE_BLENDS (engine can only approximate with normal)."""
    issues = []
    idx = _tree_parent_index(tree) if tree else {}
    by_comp = {}
    for e in entries or []:
        by_comp.setdefault(e.get("component_id"), []).append(e)
    for e in entries or []:
        name = e.get("name")
        bm = e.get("blend_mode")
        if bm:
            nb = _norm_blend(bm)
            if nb in CONTENT_DEPENDENT_BLENDS:
                issues.append({"type": "blend_mode_content_dependent", "name": name, "blend_mode": nb,
                               "message": "result depends on the content underneath; ask the user (layer, why, fix, dB, compare image)"})
            elif nb not in ENGINE_BLENDS and nb not in NORMAL_BLENDS:
                issues.append({"type": "blend_mode_not_replayable", "name": name, "blend_mode": nb})
        role = (e.get("part_role") or e.get("role") or "").lower()
        if not e.get("split_method") or role != "frame":
            continue
        # 1.4.9: only a recorded fix counts (split_fix, or split.outer_effects saying who owns them);
        # a prose effects_checked note does not.
        handled = e.get("split_fix") or (e.get("split") or {}).get("outer_effects")
        if handled or not idx:
            continue
        owners = []
        lid = e.get("layer_id")
        if e.get("split_method") == "smart_object_contents" and lid in idx:
            owners.append(lid)
        else:
            # common ancestors with the content part of the same component
            mine = set()
            for i in (e.get("select_ids") or [lid]):
                if i in idx:
                    mine |= {p for p in idx[i][1] if p in idx}
            for o in by_comp.get(e.get("component_id"), []):
                if (o.get("part_role") or o.get("role") or "").lower() != "avatar":
                    continue
                for i in (o.get("select_ids") or [o.get("layer_id")]):
                    if i in idx:
                        owners += [p for p in idx[i][1] if p in mine]
        seen = set()
        for oid in owners:
            if oid in seen or oid not in idx:
                continue
            seen.add(oid)
            fx = [x for x in node_effect_names(idx[oid][0]) if x in EDGE_EFFECTS]
            if fx:
                issues.append({"type": "outer_effect_into_hole", "name": name, "owner_layer_id": oid, "effects": fx,
                               "message": "the owner of both sides has edge effects; the frame-only export redraws them "
                                          "along the hole (e.g. shadow frozen into the frame). Default ownership: outer "
                                          "effects around the content belong to the frame, baked outside the hole (e.g. "
                                          "split-fix two_part_approx). Record split_fix or split.outer_effects: \"frame\""})
    return issues


# ---------------------------------------------------------------------------
# Frame-like elements (1.4.4): panels, popups, cards, button bases. Default: split by visual
# layer into stretchable / reusable parts (outer frame, title bar strip, inset panel,
# non-stretchable decoration). Bake the whole frame only when the split fails, with a reason.
# ---------------------------------------------------------------------------

FRAME_LIKE_KEYS = ("frame", "panel", "popup", "dialog", "window", "card", "bg", "base", "border",
                   "框", "底", "弹窗", "面板", "卡片", "背景", "窗")
DECORATION_KEYS = ("pattern", "dot", "halftone", "deco", "texture", "glow", "light", "shine",
                   "纹", "点", "光", "花纹", "装饰", "图案", "形状")
FRAME_MIN_SIDE = 160        # px; smaller images are rarely worth splitting
FRAME_MIN_LAYERS = 3        # visible layers merged into one image


def classify_frame_layers(layers):
    """Classify the layers of a merged frame by their boxes relative to the union.

    ``layers``: dicts with layer_id, name, bbox [l, t, r, b], blend_mode, effects.
    Roles: outer (covers >= 85 % of the union both ways), header (wide strip near the top),
    decoration (non-normal blend mode or a decoration-like name, inside the union), inset (the rest).
    Heuristic evidence only; the agent checks it against the crops."""
    boxes = [l for l in layers if l.get("bbox") and l["bbox"][2] > l["bbox"][0] and l["bbox"][3] > l["bbox"][1]]
    if not boxes:
        return []
    ul = min(l["bbox"][0] for l in boxes); ut = min(l["bbox"][1] for l in boxes)
    ur = max(l["bbox"][2] for l in boxes); ub = max(l["bbox"][3] for l in boxes)
    uw, uh = max(1, ur - ul), max(1, ub - ut)
    out = []
    for l in boxes:
        x0, y0, x1, y1 = l["bbox"]
        fw, fh = (x1 - x0) / uw, (y1 - y0) / uh
        blend = str(l.get("blend_mode") or "normal").lower().replace("blendmode.", "")
        name = (l.get("name") or "").lower()
        if fw >= 0.85 and fh >= 0.85:
            role = "outer"
        elif fw >= 0.7 and fh <= 0.35 and (y0 - ut) <= 0.15 * uh:
            role = "header"
        elif blend not in {"normal", "pass_through", "passthrough", "none"} or any(k in name for k in DECORATION_KEYS):
            role = "decoration"
        else:
            role = "inset"
        out.append({"layer_id": l.get("layer_id"), "name": l.get("name"), "role": role, "bbox": [x0, y0, x1, y1],
                    "blend_mode": blend})
    return out


def frame_split_suggestion(classified):
    """Group classified layers into suggested parts (one export per part, layers exported together)."""
    parts = {}
    for c in classified:
        parts.setdefault(c["role"], []).append(c["layer_id"])
    sugg = []
    if parts.get("outer"):
        sugg.append({"role": "frame", "select_ids": parts["outer"], "kind": "nine_slice",
                     "note": "outer frame; nine-slice"})
    if parts.get("header"):
        sugg.append({"role": "header", "select_ids": parts["header"], "kind": "nine_slice",
                     "note": "title bar; keep with the outer frame if it only stretches sideways, otherwise "
                             "export a horizontal three-slice strip so a vertical gradient is not stretched"})
    if parts.get("inset"):
        sugg.append({"role": "inset", "select_ids": parts["inset"], "kind": "nine_slice", "note": "inset panel; nine-slice"})
    if parts.get("decoration"):
        sugg.append({"role": "decoration", "select_ids": parts["decoration"], "kind": "image",
                     "note": "decoration / pattern: separate non-stretchable image placed on top; keep blend_mode "
                             "(or bake it onto a non-stretchable crop); never inside a nine-slice stretch zone"})
    return sugg


def _tree_index(tree):
    idx = {}

    def walk(node):
        if node.get("layer_id") is not None:
            idx[node["layer_id"]] = node
        for c in node.get("children") or []:
            walk(c)
    roots = tree if isinstance(tree, list) else ([tree] if isinstance(tree, dict) else [])
    for r in roots:
        if isinstance(r, dict):
            walk(r)
    return idx


def _visible_leaves(node):
    out = []
    for c in node.get("children") or []:
        if not c.get("visible", True):
            continue
        if c.get("children"):
            out.extend(_visible_leaves(c))
        elif not c.get("is_text"):
            out.append(c)
    return out


def _entry_layers(entry, idx):
    ids = list(entry.get("select_ids") or []) or ([entry.get("layer_id")] if entry.get("layer_id") is not None else [])
    layers = []
    for i in ids:
        node = idx.get(i)
        if node is None:
            continue
        if node.get("children"):
            layers.extend(_visible_leaves(node))
        elif not node.get("is_text"):
            layers.append(node)
    return layers


def _size_of(entry, layers):
    for key in ("visible_bounds", "logical_bounds"):
        b = entry.get(key)
        if isinstance(b, dict) and b.get("right") is not None:
            return b["right"] - b["left"], b["bottom"] - b["top"]
    if layers:
        bb = [l["bbox"] for l in layers if l.get("bbox")]
        if bb:
            return max(b[2] for b in bb) - min(b[0] for b in bb), max(b[3] for b in bb) - min(b[1] for b in bb)
    return 0, 0


def check_frame_bakes(entries, tree=None):
    """frame_baked_whole: a large frame-like image made of several layers exported as one picture
    without nine_slice and without bake_reason. decoration_in_stretch_zone: a decoration layer of a
    nine-slice part overlaps its stretchable centre bands. Each issue carries a suggested split."""
    idx = _tree_index(tree) if tree else {}
    issues = []
    for e in entries or []:
        if e.get("kind") not in {"image", "nine_slice"} or e.get("bake_reason"):
            continue
        layers = _entry_layers(e, idx)
        n_layers = len(layers) or len(e.get("select_ids") or [])
        w, h = _size_of(e, layers)
        text = " ".join(str(e.get(k) or "") for k in ("name", "source_name", "source_path", "part_role")).lower()
        frame_like = any(k in text for k in FRAME_LIKE_KEYS)
        classified = classify_frame_layers([{"layer_id": l.get("layer_id"), "name": l.get("name"), "bbox": l.get("bbox"),
                                             "blend_mode": l.get("blend_mode"), "effects": l.get("effects")} for l in layers])
        if e.get("kind") == "image" and not e.get("nine_slice") and frame_like and n_layers >= FRAME_MIN_LAYERS \
                and min(w, h) >= FRAME_MIN_SIDE:
            issues.append({"type": "frame_baked_whole", "name": e.get("name"), "layers": n_layers, "size": [w, h],
                           "layer_roles": classified, "suggested_parts": frame_split_suggestion(classified),
                           "message": "frame-like element baked as one image: split it into outer frame / title bar / "
                                      "inset panel / decoration (nine-slice where possible), or set bake_reason"})
        ns = e.get("nine_slice")
        if ns and classified:
            bb = [c["bbox"] for c in classified]
            ul, ut = min(b[0] for b in bb), min(b[1] for b in bb)
            ur, ub = max(b[2] for b in bb), max(b[3] for b in bb)
            sx0, sx1 = ul + ns.get("left", 0), ur - ns.get("right", 0)
            sy0, sy1 = ut + ns.get("top", 0), ub - ns.get("bottom", 0)
            for c in classified:
                if c["role"] != "decoration":
                    continue
                x0, y0, x1, y1 = c["bbox"]
                if (x0 < sx1 and x1 > sx0) or (y0 < sy1 and y1 > sy0):
                    issues.append({"type": "decoration_in_stretch_zone", "name": e.get("name"), "layer_id": c["layer_id"],
                                   "message": "decoration / pattern overlaps the nine-slice stretch zone; export it as a "
                                              "separate non-stretchable image"})
    return issues


def frame_split_candidates(tree):
    """prepare: visible groups that look like frames (name, size, >= 3 visible layers), with layer roles."""
    out = []
    for node in _tree_index(tree).values():
        if not node.get("children") or not node.get("visible", True) or node.get("is_artboard"):
            continue
        name = (node.get("name") or "").lower()
        if not any(k in name for k in FRAME_LIKE_KEYS):
            continue
        leaves = _visible_leaves(node)
        bbox = node.get("bbox")
        if len(leaves) < FRAME_MIN_LAYERS or not bbox or min(bbox[2] - bbox[0], bbox[3] - bbox[1]) < FRAME_MIN_SIDE:
            continue
        classified = classify_frame_layers(leaves)
        roles = {c["role"] for c in classified}
        if len(roles) < 2:
            continue
        out.append({"layer_id": node["layer_id"], "name": node.get("name"), "path": node.get("path"), "bbox": bbox,
                    "layer_roles": classified, "suggested_parts": frame_split_suggestion(classified)})
    return out


def premultiplied(im):
    arr = np.asarray(im.convert("RGBA"), dtype=np.float32)
    a = arr[..., 3:4] / 255.0
    return np.concatenate([arr[..., :3] * a, arr[..., 3:4]], axis=-1)


def pad_to(arr, w, h):
    out = np.zeros((h, w, arr.shape[2]), dtype=arr.dtype)
    out[: arr.shape[0], : arr.shape[1]] = arr
    return out


def crop_mad(a, b):
    """Mean abs diff of two premultiplied RGBA arrays (padded to the larger size), plus pct of px > 16."""
    h, w = max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])
    pa, pb = pad_to(a, w, h), pad_to(b, w, h)
    d = np.abs(pa - pb)
    return float(d.mean()), float((d.max(axis=-1) > 16).mean() * 100)


def _diff_heat(a, b):
    h, w = max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])
    d = np.abs(pad_to(a, w, h) - pad_to(b, w, h)).max(axis=-1)
    v = np.clip(d * 3, 0, 255).astype(np.uint8)
    heat = np.zeros((h, w, 3), dtype=np.uint8)
    heat[..., 0] = v
    heat[..., 1] = (v * 0.3).astype(np.uint8)
    return Image.fromarray(heat, "RGB")


def write_compare_sheet(rows, out_path, titles=("design", "reused", "diff x3")):
    """rows: [(label, design_img, reused_img)] -> one row per instance: design | reused | diff."""
    if not rows:
        return None
    font = load_font(12)
    cell_w = max(max(r[1].width, r[2].width) for r in rows) + 12
    cell_h = max(max(r[1].height, r[2].height) for r in rows) + 22
    cell_w, cell_h = max(cell_w, 90), max(cell_h, 40)
    sheet = Image.new("RGB", (cell_w * 3, 22 + cell_h * len(rows)), (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    for i, t in enumerate(titles):
        d.text((i * cell_w + 6, 4), t, fill=(255, 255, 255), font=font)
    checker = (70, 70, 70)
    for r, (label, design, reused) in enumerate(rows):
        y = 22 + r * cell_h
        da, ra = premultiplied(design), premultiplied(reused)
        for c, im in enumerate((design.convert("RGBA"), reused.convert("RGBA"), _diff_heat(da, ra).convert("RGBA"))):
            bg = Image.new("RGBA", im.size, checker + (255,))
            sheet.paste(Image.alpha_composite(bg, im).convert("RGB"), (c * cell_w + 6, y + 18))
        d.text((6, y + 2), str(label)[:60], fill=(255, 220, 120), font=font)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return out_path


def compute_reuse_candidates(records, base_dir, compare_dir, tol=REUSE_MAD_TOL):
    """Group repeated crops and attach evidence. Does not decide reuse.

    A group forms from logical evidence (same smart-object source id, or same
    name pattern + kind with near-equal size) or from pixel identity (same size and
    premultiplied MAD <= tol). Each group gets pixel clusters, metrics, evidence and a
    hint: ``reuse`` (identical pixels), ``split`` (no logical evidence, pixels differ) or
    ``needs_decision`` (evidence says reuse, pixels differ; a compare image is written).
    """
    base_dir = Path(base_dir)
    items = []
    for rec in records or []:
        if rec.get("is_group") or rec.get("is_text") or not rec.get("crop"):
            continue
        path = base_dir / rec["crop"]
        if not path.exists():
            continue
        try:
            im = Image.open(path).convert("RGBA")
        except Exception:
            continue
        items.append({"rec": rec, "im": im, "arr": premultiplied(im)})

    def size_close(a, b, slack=2):
        return abs(a.width - b.width) <= slack and abs(a.height - b.height) <= slack

    # Union-find over evidence links.
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(len(items)):
        ri = items[i]["rec"]
        for j in range(i + 1, len(items)):
            rj = items[j]["rec"]
            a, b = items[i]["im"], items[j]["im"]
            so = ri.get("smart_object_id") and ri.get("smart_object_id") == rj.get("smart_object_id")
            same_pattern = (
                name_pattern(ri.get("name")) == name_pattern(rj.get("name"))
                and (ri.get("kind") == rj.get("kind"))
                and size_close(a, b)
            )
            identical = a.size == b.size and crop_mad(items[i]["arr"], items[j]["arr"])[0] <= tol
            if so or same_pattern or identical:
                union(i, j)

    buckets = {}
    for i in range(len(items)):
        buckets.setdefault(find(i), []).append(i)

    groups = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda k: items[k]["rec"]["layer_id"])
        recs = [items[k]["rec"] for k in members]
        # Pixel clusters (greedy against each cluster's first member).
        clusters = []
        for k in members:
            for cl in clusters:
                ref = items[cl[0]]
                if ref["im"].size == items[k]["im"].size and crop_mad(ref["arr"], items[k]["arr"])[0] <= tol:
                    cl.append(k)
                    break
            else:
                clusters.append([k])
        rep_k = max(clusters, key=len)[0]
        per_instance = []
        for k in members:
            mad, pct = crop_mad(items[rep_k]["arr"], items[k]["arr"])
            per_instance.append({
                "layer_id": items[k]["rec"]["layer_id"],
                "name": items[k]["rec"].get("name"),
                "size": list(items[k]["im"].size),
                "mad_vs_representative": round(mad, 3),
                "pct_px_diff_gt16": round(pct, 3),
            })
        so_ids = {r.get("smart_object_id") for r in recs if r.get("smart_object_id")}
        patterns = {name_pattern(r.get("name")) for r in recs}
        path_patterns = {name_pattern(r.get("path")) for r in recs}
        nines = {json.dumps(r.get("nine_slice"), sort_keys=True) for r in recs}
        sizes = {tuple(items[k]["im"].size) for k in members}
        priors = [ui_role_prior(r.get("name"), r.get("path")) for r in recs]
        roles = {p["role"] for p in priors if p}
        role_prior = next((p for p in priors if p), None) if len(roles) == 1 and all(priors) else None
        evidence = {
            "ui_role_prior": role_prior,
            "same_smart_object": next(iter(so_ids)) if len(so_ids) == 1 and all(r.get("smart_object_id") for r in recs) else None,
            "smart_object_ids": sorted(so_ids),
            "same_name_pattern": next(iter(patterns)) if len(patterns) == 1 else None,
            "same_path_pattern": next(iter(path_patterns)) if len(path_patterns) == 1 else None,
            "same_size": len(sizes) == 1,
            "same_nine_slice": len(nines) == 1,
            "same_kind": len({r.get("kind") for r in recs}) == 1,
        }
        prior_reuse = bool(role_prior and role_prior["prior"] in {"likely_reuse", "small_fixed_set"})
        logical = bool(evidence["same_smart_object"] or evidence["same_name_pattern"] or evidence["same_path_pattern"] or prior_reuse)
        identical = len(clusters) == 1
        hint = "reuse" if identical else ("needs_decision" if logical else "split")
        if role_prior and role_prior["prior"] == "dynamic_placeholder":
            # Runtime content (e.g. avatar pictures): differences are expected; export as placeholder.
            hint = "dynamic_placeholder"
        gid = f"reuse_{len(groups) + 1:03d}"
        group = {
            "id": gid,
            "layer_ids": [r["layer_id"] for r in recs],
            "names": [r.get("name") for r in recs],
            "representative_layer_id": items[rep_k]["rec"]["layer_id"],
            "pixel_clusters": [[items[k]["rec"]["layer_id"] for k in cl] for cl in clusters],
            "max_mad": round(max(p["mad_vs_representative"] for p in per_instance), 3),
            "instances": per_instance,
            "evidence": evidence,
            "hint": hint,
            "compare_image": None,
        }
        if not identical:
            rows = [
                (f"{items[k]['rec']['layer_id']} {items[k]['rec'].get('name') or ''}", items[k]["im"], items[rep_k]["im"])
                for k in members
            ]
            out = write_compare_sheet(rows, Path(compare_dir) / f"{gid}.png")
            if out is not None:
                try:
                    group["compare_image"] = str(Path(out).relative_to(base_dir))
                except ValueError:
                    group["compare_image"] = str(out)
        groups.append(group)
    return {
        "count": len(groups),
        "tolerance_mad": tol,
        "groups": groups,
        "avatar_merge_suspects": avatar_merge_suspects(records, base_dir),
        "notes": [
            "evidence.ui_role_prior is a keyword-based game-UI prior (default only; pixels can override, conflicts go to the user).",
            "hint=dynamic_placeholder: runtime content such as avatar pictures; export one placeholder part, separate from its frame.",
            "Evidence only; the agent decides. hint=reuse: pixel-identical repeats, export once (reuse_of).",
            "hint=needs_decision: logical evidence for reuse but pixels differ; add a reuse_decisions entry with compare_image.",
            "hint=split: similar by size/pixels only; export separately unless the design logic says otherwise.",
            "Crops are psd-tools composites of the layer alone; Photoshop-baked effects may differ.",
        ],
    }


def cmd_prepare(args):
    t0 = time.time()
    psd_path = Path(args.psd)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"opening {psd_path} ({psd_path.stat().st_size} bytes)", flush=True)
    psd = open_psd(psd_path)
    print(f"opened in {time.time()-t0:.1f}s: {psd.width}x{psd.height}", flush=True)
    t1 = time.time()
    roots, warnings = walk_document(psd)
    print(f"tree walked in {time.time()-t1:.1f}s, top-level {len(roots)}", flush=True)
    targets = select_targets(roots, args.artboard, psd=psd)
    if args.artboard:
        hidden = [t for t in targets if not t.get("visible")]
        if hidden:
            raise SystemExit(f"artboard is hidden: {hidden[0].get('name')}")
    targets = [t for t in targets if t.get("visible")]
    if not targets:
        raise SystemExit("no visible artboard or canvas target")

    slug_used = set()
    if len(targets) == 1:
        result = prepare_target(targets[0], psd, psd_path, out_dir, t0, warnings)
        result["warnings"] = warnings
        print(json.dumps({k: v for k, v in result.items() if k != "notes"} | {"notes": result.get("notes"), "warning_count": len(warnings)}, ensure_ascii=False, indent=2))
        return 0
    index = []
    for target in targets:
        slug = target_slug(target, slug_used)
        dest = out_dir / slug
        item = prepare_target(target, psd, psd_path, dest, time.time(), warnings)
        item["out_dir"] = str(dest)
        item["slug"] = slug
        index.append({k: v for k, v in item.items() if k != "notes"})
    payload = {"targets": index, "count": len(index)}
    (out_dir / "index.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"index": str(out_dir / "index.json"), **payload}, ensure_ascii=False, indent=2))
    return 0


def component_shared_parts(component):
    """Invariant parts: shared, common_parts, and parts (synonyms). State is omitted."""
    out, seen = [], set()
    for key in ("shared", "common_parts", "parts"):
        for part in component.get(key) or []:
            ident = part.get("name") or (id(part),)
            if ident in seen:
                continue
            seen.add(ident)
            out.append(part)
    return out


def iter_component_parts(component, ancestors=None):
    """Yield (component, state_name or None, state_obj or None, part) for every part."""
    ancestors = ancestors or []
    for part in component_shared_parts(component):
        yield component, None, None, part
    states = component.get("states") or {}
    if isinstance(states, dict):
        for state_name, state_obj in states.items():
            if not isinstance(state_obj, dict):
                continue
            for part in state_obj.get("parts") or []:
                yield component, state_name, state_obj, part
    for child in component.get("children") or []:
        yield from iter_component_parts(child, ancestors + [component])



def paint_index_map(tree):
    """Map layer_id -> painter's order (smaller is under).

    psd-tools / our collect_layer store children bottom-to-top, which matches
    Photoshop compositing. Walk pre-order so a group is indexed among its
    siblings, then its descendants (who would sit above a group background).
    """
    acc = {}

    def walk(node):
        if not node:
            return
        lid = node.get("layer_id")
        if lid is not None and lid not in acc:
            acc[int(lid)] = len(acc)
        for child in node.get("children") or []:
            walk(child)

    if isinstance(tree, dict) and (tree.get("layer_id") is not None or tree.get("children")):
        walk(tree)
    elif isinstance(tree, list):
        for node in tree:
            walk(node)
    return acc


def layer_tree_from_manifest(manifest):
    return manifest.get("layer_tree") or manifest.get("source_tree") or manifest.get("tree")


def sort_by_paint_order(items, tree, id_of=lambda x: x.get("layer_id")):
    if not tree:
        return list(items)
    index = paint_index_map(tree)
    if not index:
        return list(items)

    def key(item):
        lid = id_of(item)
        try:
            lid = int(lid) if lid is not None else None
        except (TypeError, ValueError):
            lid = None
        if lid in index:
            return (0, index[lid])
        # Fall back to the first select_id that is in the tree.
        for sid in item.get("select_ids") or []:
            try:
                sid = int(sid)
            except (TypeError, ValueError):
                continue
            if sid in index:
                return (0, index[sid])
        return (1, 0)

    return sorted(items, key=key)


def flatten_components(manifest):
    """Build a flat export entry list from the component tree. Existing entries are replaced."""
    used = set()
    entries = []
    components = manifest.get("components") or []

    def walk_assign(comp):
        pass

    for component in components:
        for comp, state_name, state_obj, part in iter_component_parts(component):
            name = part.get("name") or unique_name(["slice", f"l{part.get('layer_id') or 0}"], used)
            if name in used:
                name = unique_name([name], used)
            else:
                used.add(name)
            if part.get("hidden") is not None:
                hidden = bool(part.get("hidden"))
            elif state_obj is not None and state_obj.get("from_hidden"):
                hidden = True
            else:
                hidden = bool(part.get("from_hidden"))
            is_default_state = (state_name is None) or (comp.get("default_state") in {None, state_name})
            entry = {
                "name": name,
                "layer_id": part.get("layer_id"),
                "select_ids": list(part.get("select_ids") or ([part["layer_id"]] if part.get("layer_id") is not None else [])),
                "source_name": part.get("source_name") or "",
                "source_path": part.get("source_path") or "",
                "kind": part.get("kind") or "image",
                "logical_bounds": part.get("logical_bounds"),
                "nine_slice": part.get("nine_slice"),
                "text": part.get("text"),
                "parent": comp.get("id") or comp.get("name"),
                "opacity": part.get("opacity"),
                "blend_mode": part.get("blend_mode"),
                "notes": list(part.get("notes") or []),
                "effect_mode": part.get("effect_mode"),
                "engine_effects": list(part.get("engine_effects") or []),
                "effect_reason": part.get("effect_reason"),
                "reuse_of": part.get("reuse_of"),
                "reuse_accepted": bool(part.get("reuse_accepted")),
                "placeholder": bool(part.get("placeholder")),
                "bake_reason": part.get("bake_reason"),
                "split_method": part.get("split_method"),
                "split": part.get("split"),
                "split_evidence": part.get("split_evidence"),
                "split_fix": part.get("split_fix"),
                "split_approximate": bool(part.get("split_approximate") or (part.get("split_evidence") or {}).get("approximate")),
                "component_id": comp.get("id"),
                "component_type": comp.get("type"),
                "part_role": part.get("role"),
                "state": state_name,
                "default_state": comp.get("default_state"),
                "hidden": hidden,
                "from_hidden": hidden,
                "default": bool(is_default_state and not hidden),
            }
            if hidden and not entry["default"]:
                entry["notes"] = list(entry["notes"]) + ["reveal hidden layer for export, then restore"]
            entries.append(entry)
    entries = sort_by_paint_order(entries, layer_tree_from_manifest(manifest))
    assign_reuse(entries)
    manifest["entries"] = entries
    return entries


def assign_reuse(entries):
    """Shared images: entries with the same ``reuse_of`` use one PNG named after it.

    The source instance (the entry whose name equals reuse_of, else the first default
    one, else the first) exports ``<reuse_of>.png``; the others get ``export_png: false``
    and are placed by build-layout at their own bounds. Entries without reuse_of export
    ``<name>.png`` as before.
    """
    groups = {}
    for e in entries:
        e["image_name"] = e["name"]
        e["export_png"] = not is_attr_text(e.get("kind"))
        shared = e.get("reuse_of")
        if shared and not is_attr_text(e.get("kind")):
            groups.setdefault(shared, []).append(e)
    for shared, members in groups.items():
        source = next((m for m in members if m["name"] == shared), None)
        source = source or next((m for m in members if m.get("default")), None) or members[0]
        for m in members:
            m["image_name"] = shared
            m["reuse_source"] = m["name"] if m is source else source["name"]
            m["export_png"] = m is source
    return groups


def effect_groups_from_tree(tree):
    """Compact effect-group records (id, effects, params, descendants, ancestors) from a layer tree."""
    roots = tree if isinstance(tree, list) else ([tree] if isinstance(tree, dict) else [])
    out = []
    for root in roots:
        if not root:
            continue
        for group, anc in iter_effect_groups(root):
            out.append({
                "layer_id": group["layer_id"],
                "effects": node_effect_names(group),
                "effect_params": list(group.get("effect_params") or []),
                "child_ids": [i for i in descendant_ids(group) if i != group["layer_id"]],
                "ancestor_ids": [a["layer_id"] for a in anc if a.get("layer_id") is not None],
            })
    return out


def load_effect_groups(manifest=None, manifest_path=None, group_effects_path=None):
    """group_effects.json (explicit or next to the manifest) or the manifest layer tree."""
    candidates = []
    if group_effects_path:
        candidates.append(Path(group_effects_path))
    if manifest_path:
        candidates.append(Path(manifest_path).parent / "group_effects.json")
    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                warn(f"cannot read {path}: {exc}")
                continue
            groups = data.get("groups") if isinstance(data, dict) else data
            out = []
            for g in groups or []:
                if g.get("layer_id") is None:
                    continue
                out.append({
                    "layer_id": g["layer_id"],
                    "effects": list(g.get("effects") or []),
                    "effect_params": list(g.get("effect_params") or []),
                    "child_ids": list(g.get("child_ids") or g.get("child_leaf_ids") or []),
                    "ancestor_ids": list(g.get("ancestor_ids") or []),
                })
            return out
    if manifest is not None:
        tree = layer_tree_from_manifest(manifest)
        if tree:
            return effect_groups_from_tree(tree)
    return []


def _int_ids(values):
    out = set()
    for v in values or []:
        try:
            out.add(int(v))
        except (TypeError, ValueError):
            continue
    return out


def find_silent_effect_loss(items, effect_groups):
    """Parts that select children of an effect group but neither bake the group nor record engine_effects.

    *items* are manifest entries or layout nodes. A part is fine when it selects the
    effect group itself (or one of its ancestors), or when it has effect_mode 'engine'
    with a non-empty engine_effects list. Attribute-only text (dynamic_text /
    static_text) is skipped.
    """
    problems = []
    for item in items or []:
        if is_attr_text(item.get("kind")):
            continue
        ids = _int_ids(item.get("select_ids") or ([item.get("layer_id")] if item.get("layer_id") is not None else []))
        if not ids:
            continue
        mode = item.get("effect_mode")
        engine_ok = mode == "engine" and bool(item.get("engine_effects"))
        for g in effect_groups or []:
            gid = g.get("layer_id")
            covering = {gid} | _int_ids(g.get("ancestor_ids"))
            if ids & covering:
                continue
            if not ids & _int_ids(g.get("child_ids")):
                continue
            if engine_ok:
                continue
            problems.append({
                "type": "group_effect_lost",
                "name": item.get("name"),
                "effect_group_id": gid,
                "group_effects": list(g.get("effects") or []),
                "select_ids": sorted(ids),
                "effect_mode": mode,
                "hint": (
                    "select_ids are children of a group that carries layer effects. Either bake: "
                    "select the group id (effect_mode 'baked'), or set effect_mode 'engine' and copy the "
                    "group's effect_params into engine_effects."
                ),
            })
    return problems


def cmd_flatten(args):
    path = Path(args.manifest)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not manifest.get("components"):
        raise SystemExit("manifest has no components; the agent must write the component tree first")
    entries = flatten_components(manifest)
    out = Path(args.out) if args.out else path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    groups = load_effect_groups(manifest, path, getattr(args, "group_effects", None))
    effect_issues = find_silent_effect_loss(entries, groups)
    decisions = manifest.get("reuse_decisions") or []
    pending = [d.get("id") or d.get("instances") for d in decisions if not d.get("decision") or d.get("decision") == "pending"]
    tree_for_checks = load_layer_tree(manifest, path)
    thr = resolve_thresholds(thresholds_from_rules_file(manifest.get("naming_rules_file")))
    avatar_issues = check_avatar_splits(entries, decisions, load_avatar_suspects(manifest, path), tree_for_checks, thr)
    frame_issues = check_frame_bakes(entries, tree_for_checks)
    avatar_issues = avatar_issues + check_split_effects(entries, tree_for_checks)
    summary = {
        "manifest": str(out),
        "shared_images": dict(Counter(e["image_name"] for e in entries if e.get("reuse_of"))),
        "pending_reuse_decisions": pending,
        "pending_user_decisions": pending_user_decisions(decisions, manifest_lock_state(path).get("decision_log")
                                                         if path.is_file() else []),
        "uncertain_hidden_without_decision": uncertain_hidden_issues(manifest.get("components") or [], decisions,
                                                             None, manifest.get("uncertain_hidden")),
        "entry_count": len(entries),
        "by_kind": dict(Counter(e["kind"] for e in entries)),
        "hidden_entries": sum(1 for e in entries if e.get("hidden")),
        "by_effect_mode": dict(Counter(e.get("effect_mode") or "-" for e in entries)),
        "effect_issues": effect_issues,
        "avatar_split_issues": avatar_issues,
        "frame_split_issues": frame_issues,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for problem in summary["uncertain_hidden_without_decision"]:
        warn(f"uncertain_hidden without a hidden_layer_state decision: layers {problem['layer_ids']} "
             f"({problem.get('component_id')}); validate will report it")
    for problem in effect_issues:
        warn(f"group effect would be lost: {problem['name']} (group {problem['effect_group_id']})")
    for problem in frame_issues:
        warn(f"frame split: {problem['type']} {problem.get('name')}")
    for problem in avatar_issues:
        warn(f"avatar split: {problem['type']} {problem.get('name') or problem.get('decision') or problem.get('names') or ''}")
    return 0




def cmd_infer(args):
    if PSDImage is None:
        raise SystemExit("psd-tools is required for infer. Install it in a project venv (not system Python); see references/slicing.md.")
    t0 = time.time()
    psd_path = Path(args.psd)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"opening {psd_path} ({psd_path.stat().st_size} bytes)", flush=True)
    open_kwargs = {}
    if ParseLimits is not None:
        # Large UI PSBs easily exceed the default 1_000_000 object cap (实测).
        open_kwargs["parse_limits"] = ParseLimits(
            max_objects=None, max_total_bytes=None, max_read_bytes=None
        )
    psd = PSDImage.open(str(psd_path), **open_kwargs)
    print(f"opened in {time.time()-t0:.1f}s: {psd.width}x{psd.height}", flush=True)
    warnings = []
    roots = []
    t1 = time.time()
    for i, layer in enumerate(psd):
        roots.append(collect_layer(layer, [], 1, i, warnings))
    print(f"tree walked in {time.time()-t1:.1f}s, top-level {len(roots)}", flush=True)

    rules = NamingRules.load(getattr(args, "rules", None))
    targets = select_targets(roots, args.artboard, psd=psd)
    hidden = [t for t in targets if not t.get("visible")]
    if hidden and args.artboard:
        raise SystemExit(f"artboard is hidden: {hidden[0].get('name')}")
    targets = [t for t in targets if t.get("visible")]
    if not targets:
        raise SystemExit("no visible artboard or canvas target")

    def infer_one(target, dest: Path, time_base):
        dest.mkdir(parents=True, exist_ok=True)
        bbox = target.get("bbox") or [0, 0, target["width"], target["height"]]
        art_w, art_h = target["width"], target["height"]
        print(
            f"target {target.get('name')} id={target.get('layer_id')} {art_w}x{art_h} bbox={bbox}",
            flush=True,
        )
        t2 = time.time()
        entries, tree = build_entries(
            target, art_w, art_h, target.get("name") or "", warnings, rules
        )
        print(f"inferred {len(entries)} entries in {time.time()-t2:.1f}s", flush=True)
        t3 = time.time()
        preview_base = artboard_image(target, warnings)
        print(f"artboard composite in {time.time()-t3:.1f}s size={preview_base.size}", flush=True)
        kinds = Counter(e["kind"] for e in entries)
        manifest = {
            "schema": SCHEMA_MANIFEST,
            "status": "pending_review",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "source": {
                "file": str(psd_path),
                "doc_size": [int(psd.width), int(psd.height)],
                "color_mode": enum_val(getattr(psd, "color_mode", None)),
                "depth": getattr(psd, "depth", None),
                "artboard": {
                    "id": target.get("layer_id"),
                    "name": target.get("name"),
                    "bounds": bbox,
                    "size": [art_w, art_h],
                },
            },
            "naming_rule": "模块_类型_用途_状态，小写英文；禁止直接使用 PSD 图层名",
            "naming_rules_file": str(args.rules) if getattr(args, "rules", None) else None,
            "review_gate": "Edit this JSON to confirm. Nothing is exported until status is set to confirmed.",
            "entries": strip_runtime(entries),
            "tree": tree,
            "warnings": warnings,
            "stats": {
                "entry_count": len(entries),
                "by_kind": dict(kinds),
                "infer_sec": round(time.time() - time_base, 2),
            },
        }
        manifest["status"] = "draft_for_agent_review"
        manifest["review_gate"] = (
            "This is a keyword-based DRAFT only. The agent must read the prepare materials "
            "(full render, crops, tree) and write manifest.json with components, parts, and states."
        )
        manifest_path = dest / "draft_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        preview_path = dest / "draft_preview_annotated.png"
        annotate_preview(
            preview_base,
            manifest["entries"],
            (bbox[0], bbox[1]),
            preview_path,
            f"{target.get('name')}  {len(entries)} DRAFT slices  (agent must review)",
        )
        return {
            "draft_manifest": str(manifest_path),
            "draft_preview": str(preview_path),
            "stats": manifest["stats"],
            "artboard": manifest["source"]["artboard"],
            "note": "draft only; write manifest.json from prepare materials",
        }

    slug_used = set()
    if len(targets) == 1:
        result = infer_one(targets[0], out_dir, t0)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    index = []
    for target in targets:
        slug = target_slug(target, slug_used)
        dest = out_dir / slug
        item = infer_one(target, dest, time.time())
        item["out_dir"] = str(dest)
        item["slug"] = slug
        index.append(item)
    index_path = out_dir / "index.json"
    payload = {"targets": index, "count": len(index)}
    index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"index": str(index_path), **payload}, ensure_ascii=False, indent=2))
    return 0


def cmd_preview(args):
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    if manifest.get("components") and not manifest.get("entries"):
        flatten_components(manifest)
    if not manifest.get("entries"):
        raise SystemExit("manifest has no entries; write components and run flatten first")
    src = manifest["source"]["artboard"]
    ox, oy = src["bounds"][0], src["bounds"][1]
    if args.ref:
        base = Image.open(args.ref).convert("RGBA")
    else:
        w, h = src["size"]
        base = Image.new("RGBA", (w, h), (24, 24, 24, 255))
    out = Path(args.out)
    origin = (0, 0) if args.ref_is_document else (ox, oy)
    hidden_n = sum(1 for e in manifest["entries"] if e.get("hidden"))
    title = f"{src.get('name')}  {len(manifest['entries'])} slices"
    if hidden_n:
        title += f"  ({hidden_n} hidden-state)"
    annotate_preview(base, manifest["entries"], origin, out, title)
    print(f"wrote {out}")
    return 0


def _rect_to_dict(rect):
    if rect is None:
        return None
    if isinstance(rect, dict):
        if "left" in rect:
            return {k: float(rect[k]) for k in ("left", "top", "right", "bottom")}
        return rect
    if isinstance(rect, (list, tuple)) and len(rect) == 4:
        l, t, r, b = rect
        return {"left": float(l), "top": float(t), "right": float(r), "bottom": float(b)}
    return None


def _center(bounds):
    if not bounds:
        return None
    return {
        "x": round((bounds["left"] + bounds["right"]) / 2.0, 2),
        "y": round((bounds["top"] + bounds["bottom"]) / 2.0, 2),
    }


def _shift(bounds, ox, oy):
    if not bounds:
        return None
    return {
        "left": bounds["left"] - ox,
        "top": bounds["top"] - oy,
        "right": bounds["right"] - ox,
        "bottom": bounds["bottom"] - oy,
    }


def extract_bounds_from_export_item(item):
    """Prefer Photoshop-reported bounds; fall back to manifest logical_bounds."""
    vis = item.get("visible_bounds") or item.get("bounds_with_effects") or item.get("bounds")
    logi = item.get("logical_bounds") or item.get("bounds_no_effects") or item.get("boundsNoEffects")
    return _rect_to_dict(vis), _rect_to_dict(logi)



def enrich_components(components, nodes_by_name):
    """Copy the agent component tree and attach export image / bounds to each part."""

    def enrich_part(part):
        out = dict(part)
        node = nodes_by_name.get(part.get("name"))
        if node:
            out["image"] = node.get("image")
            out["visible_bounds"] = node.get("visible_bounds")
            out["logical_bounds"] = node.get("logical_bounds")
            out["nine_slice"] = node.get("nine_slice")
            out["kind"] = node.get("kind") or out.get("kind")
            for key in ("effect_mode", "engine_effects", "effect_reason", "effect_bounds", "effect_bounds_estimated", "reuse_of", "reuse_source"):
                if node.get(key) is not None:
                    out[key] = node[key]
            if node.get("text"):
                out["text"] = node["text"]
        return out

    def enrich(comp):
        out = {k: v for k, v in comp.items() if k not in {"parts", "shared", "common_parts", "states", "children"}}
        shared = [enrich_part(part) for part in component_shared_parts(comp)]
        out["shared"] = shared
        out["parts"] = shared
        out["common_parts"] = shared
        states = comp.get("states") or {}
        if isinstance(states, dict):
            out["states"] = {}
            for name, sobj in states.items():
                if not isinstance(sobj, dict):
                    out["states"][name] = sobj
                    continue
                st = dict(sobj)
                st["parts"] = [enrich_part(part) for part in (sobj.get("parts") or [])]
                out["states"][name] = st
        out["missing"] = list(comp.get("missing") or [])
        out["children"] = [enrich(child) for child in (comp.get("children") or [])]
        return out

    return [enrich(c) for c in components]


LAYER_BOUNDS_TOL_PX = 2


def layer_bounds_conflict(manifest_bounds, measured_bounds, tol=LAYER_BOUNDS_TOL_PX):
    """Largest edge difference (px) between the manifest box of an entry and the logical box Photoshop measured
    on its layer_id, when it is over ``tol``; else 0. A large value means the entry points at another layer than
    the one it describes (an instance whose layer_id was copied from its sibling), so it is placed at the wrong
    spot and the instance it names is missing."""
    a, b = _rect_to_dict(manifest_bounds), _rect_to_dict(measured_bounds)
    if not a or not b:
        return 0
    d = max(abs(a[k] - b[k]) for k in ("left", "top", "right", "bottom"))
    return int(round(d)) if d > tol else 0


def _union_rect(rects):
    rs = [r for r in (_rect_to_dict(x) for x in rects) if r]
    if not rs:
        return None
    return {"left": min(r["left"] for r in rs), "top": min(r["top"] for r in rs),
            "right": max(r["right"] for r in rs), "bottom": max(r["bottom"] for r in rs)}


def _inside(inner, outer, tol):
    a, b = _rect_to_dict(inner), _rect_to_dict(outer)
    return bool(a and b and a["left"] >= b["left"] - tol and a["top"] >= b["top"] - tol
                and a["right"] <= b["right"] + tol and a["bottom"] <= b["bottom"] + tol)


def entry_layer_bounds_conflict(entry, item, tol=LAYER_BOUNDS_TOL_PX):
    """layer_bounds_conflict for one manifest entry and its export log item.

    An entry that merges several layers (select_ids) has the union of their boxes as its manifest box. 1.4.12
    logs carry that union (logical_bounds_union / select_bounds) and it is compared instead of the layer_id box.
    For older logs (only the layer_id box) a measured box inside the manifest box (+tol) is a part of the union,
    not a mismatch; a box elsewhere (an instance pointing at its sibling's layer) is still reported."""
    if not item:
        return 0
    man = entry.get("logical_bounds")
    union = _rect_to_dict(item.get("logical_bounds_union"))
    if union is None and item.get("select_bounds"):
        union = _union_rect([(b or {}).get("logical") or (b or {}).get("visible") for b in item["select_bounds"]])
    if union is not None:
        return layer_bounds_conflict(man, union, tol)
    _, logi = extract_bounds_from_export_item(item)
    d = layer_bounds_conflict(man, logi, tol)
    multi = len([i for i in (entry.get("select_ids") or []) if i is not None]) > 1
    if d and multi and _inside(logi, man, tol):
        return 0
    return d


def place_reused_instances(nodes):
    """Place bounds-only instances of a shared image: source visible box shifted by the logical-origin delta."""
    by_name = {n["name"]: n for n in nodes}
    for node in nodes:
        bounds_only = node.pop("_bounds_only", False)
        if not bounds_only:
            continue
        src = by_name.get(node.get("reuse_source"))
        if not src or not src.get("visible_bounds"):
            continue
        sv, sl, il = src["visible_bounds"], src.get("logical_bounds"), node.get("logical_bounds")
        if sl and il:
            dx, dy = il["left"] - sl["left"], il["top"] - sl["top"]
        elif node.get("visible_bounds"):
            dx, dy = node["visible_bounds"]["left"] - sv["left"], node["visible_bounds"]["top"] - sv["top"]
        else:
            continue
        node["visible_bounds"] = _shift(sv, -dx, -dy)
        if sl:
            node["logical_bounds"] = _shift(sl, -dx, -dy)
            node["anchor"] = _center(node["logical_bounds"])
        node["kind"] = src.get("kind", node.get("kind"))
        node["nine_slice"] = src.get("nine_slice")
        node["image"] = src.get("image")
        if src.get("effect_bounds"):
            node["effect_bounds"] = _shift(src["effect_bounds"], -dx, -dy)
    return nodes


def cmd_build_layout(args):
    lock = manifest_lock_state(args.manifest)
    if lock["changed"]:
        print(json.dumps(dict(lock, error="manifest_changed_after_confirm", hint=MANIFEST_CHANGED_HINT),
                         ensure_ascii=False, indent=2))
        print("error: manifest_changed_after_confirm; layout not written", file=sys.stderr, flush=True)
        return 3
    if not lock["locked"]:
        warn("manifest is not locked (run `confirm` after the user confirms); validate will report manifest_not_locked")
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    export = json.loads(Path(args.export_log).read_text(encoding="utf-8"))
    slices_dir = Path(args.slices_dir)
    art = manifest["source"]["artboard"]
    ox, oy = art["bounds"][0], art["bounds"][1]
    by_name = {e["name"]: e for e in manifest["entries"]}
    export_items = {}
    for item in export.get("items") or []:
        export_items[item.get("name")] = item

    def refine_nine(entry, png_path):
        if is_text_kind(entry.get("kind")):
            return None
        if not png_path.exists():
            return entry.get("nine_slice")
        try:
            image = Image.open(png_path)
        except Exception:
            return entry.get("nine_slice")
        detected = detect_nine_slice(image)
        return detected if detected is not None else entry.get("nine_slice")

    nodes = []
    for entry in manifest["entries"]:
        name = entry["name"]
        item = export_items.get(name, {})
        vis, logi = extract_bounds_from_export_item(item)
        if logi is None:
            logi = _rect_to_dict(entry.get("logical_bounds"))
        if vis is None:
            vis = logi
        bounds_conflict = entry_layer_bounds_conflict(entry, item) if item else 0
        # Prefer the post-trim inset origin computed in export_slices.psjs.
        # Only fall back to "PNG size == logical size → use logical origin"
        # when the exporter did not record trim insets (older logs).
        png_size = item.get("out_size")
        if (not item.get("visible_bounds_from_trim_insets")
                and logi and png_size and len(png_size) == 2):
            lw = logi["right"] - logi["left"]
            lh = logi["bottom"] - logi["top"]
            if abs(lw - png_size[0]) <= 1 and abs(lh - png_size[1]) <= 1:
                vis = dict(logi)
        vis_ab = _shift(vis, ox, oy) if vis else None
        logi_ab = _shift(logi, ox, oy) if logi else None
        png_name = None if is_attr_text(entry["kind"]) else f"{entry.get('image_name') or name}.png"
        png_path = slices_dir / png_name if png_name else None
        kind = entry["kind"]
        nine = None if is_text_kind(kind) else (refine_nine(entry, png_path) if png_path else None)
        if not is_text_kind(kind):
            if nine and kind == "image":
                kind = "nine_slice"
            if not nine and kind == "nine_slice":
                # Keep the reviewed kind if insets were confirmed, else downgrade.
                if entry.get("nine_slice"):
                    nine = entry["nine_slice"]
                else:
                    kind = "image"
        text = item.get("text") or entry.get("text")
        if is_attr_text(kind) and isinstance(text, dict):
            text = text_render.complete_text_attrs(text)
        node = {
            "id": name,
            "name": name,
            "kind": kind,
            "layer_id": entry.get("layer_id"),
            "select_ids": entry.get("select_ids"),
            "parent": entry.get("parent"),
            "source_name": entry.get("source_name"),
            "source_path": entry.get("source_path"),
            "visible_bounds": vis_ab,
            "logical_bounds": logi_ab,
            "anchor": _center(logi_ab),
            "pivot": {"x": 0.5, "y": 0.5},
            "nine_slice": nine,
            "image": png_name,
            "text": text,
            "opacity": entry.get("opacity"),
            "blend_mode": entry.get("blend_mode"),
            "component_id": entry.get("component_id"),
            "component_type": entry.get("component_type"),
            "part_role": entry.get("part_role"),
            "state": entry.get("state"),
            "default_state": entry.get("default_state"),
            "default": entry.get("default", True),
            "hidden": entry.get("hidden", False),
        }
        if bounds_conflict:
            man_ab = _shift(_rect_to_dict(entry.get("logical_bounds")), ox, oy)
            node["layer_bounds_mismatch"] = {
                "manifest_logical_bounds": man_ab,
                "measured_logical_bounds": logi_ab,
                "max_edge_px": bounds_conflict,
            }
            if entry.get("reuse_of") and entry.get("export_png") is False and not entry.get("default", True):
                # A non-default state drawn with another instance's image (no layer of its own at that spot):
                # the manifest box says where it goes; shift the shared image there.
                node["logical_bounds"] = dict(man_ab)
                node["anchor"] = _center(man_ab)
                node["layer_bounds_mismatch"]["placed_from"] = "manifest_logical_bounds"
            else:
                warn(f"{name}: layer {entry.get('layer_id')} measures {logi_ab} but the manifest box is "
                     f"{man_ab} (wrong layer_id / copied instance?)")
        if entry.get("reuse_of"):
            node["reuse_of"] = entry["reuse_of"]
            node["reuse_source"] = entry.get("reuse_source")
            node["reuse_accepted"] = bool(entry.get("reuse_accepted"))
            node["_bounds_only"] = entry.get("export_png") is False
        if entry.get("placeholder"):
            node["placeholder"] = True
        if entry.get("bake_reason"):
            node["bake_reason"] = entry["bake_reason"]
        if entry.get("split_method"):
            node["split_method"] = entry["split_method"]
            node["split_evidence"] = entry.get("split_evidence")
            if entry.get("split"):
                node["split"] = entry["split"]
            if entry.get("split_approximate"):
                node["split_approximate"] = True
            if entry.get("split_fix"):
                node["split_fix"] = entry["split_fix"]
        if entry.get("effect_mode"):
            node["effect_mode"] = entry["effect_mode"]
            node["effect_reason"] = entry.get("effect_reason")
        if entry.get("engine_effects"):
            node["engine_effects"] = list(entry["engine_effects"])
        if entry.get("effect_mode") == "engine":
            # The PNG has no effect pixels: its box is the logical box.
            base = vis_ab or logi_ab
            if base:
                node["logical_bounds"] = dict(base)
                node["anchor"] = _center(base)
            node["effect_bounds"] = estimate_effect_bounds(base, node.get("engine_effects"))
            node["effect_bounds_estimated"] = True
        nodes.append(node)

    place_reused_instances(nodes)
    nodes = sort_by_paint_order(nodes, layer_tree_from_manifest(manifest))
    nodes_by_name = {n["name"]: n for n in nodes}
    components = enrich_components(manifest.get("components") or [], nodes_by_name)
    effect_groups = load_effect_groups(manifest, args.manifest, getattr(args, "group_effects", None))
    effect_issues = find_silent_effect_loss(nodes, effect_groups)
    for problem in effect_issues:
        warn(f"group effect lost: {problem['name']} (group {problem['effect_group_id']})")
    psd_for_text = (manifest.get("source") or {}).get("file")
    if psd_for_text:
        fill_layout_leading_types({"nodes": nodes}, psd_for_text)
    for p in user_decision_issues(manifest.get("reuse_decisions") or [], lock):
        warn(f"pending user decision {p['id']} ({p['decision_type']}): {p['reason']}; validate will report it")
    full_item = export.get("full_render") or {}
    layout = {
        "schema": SCHEMA_LAYOUT,
        "source": manifest["source"],
        "export": {
            "ok": export.get("ok"),
            "started": export.get("started"),
            "finished": export.get("finished"),
            "log": str(Path(args.export_log)),
            "exporter": export.get("exporter"),
            "layers_at_open": export.get("layers_at_open"),
            "full_render_hidden": full_item.get("hidden_at_render") if isinstance(full_item, dict) else None,
            "excluded_by_user_decision": export.get("excluded_by_user_decision"),
            "pending_user_decisions": export.get("pending_user_decisions"),
        },
        "nodes": nodes,
        "components": components,
        "missing": manifest.get("missing") or [],
        "tree": manifest.get("tree") or load_layer_tree(manifest, Path(args.manifest)) or [],
        "effect_groups": effect_groups,
        "effect_issues": effect_issues,
        "reuse_decisions": manifest.get("reuse_decisions") or [],
        "uncertain_hidden": manifest.get("uncertain_hidden"),
        "manifest_lock": lock,
        "thresholds": resolve_thresholds(thresholds_from_rules_file(manifest.get("naming_rules_file"))),
        "avatar_merge_suspects": [
            {"layer_id": s_.get("layer_id"), "name": s_.get("name")} for s_ in load_avatar_suspects(manifest, Path(args.manifest))
        ],
        "notes": [
            "Coordinates are artboard-relative pixels (1x).",
            "Hierarchy is component semantics (screen > tab_group > tab > state > part).",
            "source_name / source_path / layer_id are PSD references only.",
            "visible_bounds include baked layer effects; logical_bounds do not.",
            "effect_mode 'engine': the PNG has no effect pixels (visible == logical); the engine draws "
            "engine_effects. effect_bounds is an estimate of their extent, not Photoshop's exact box.",
            "anchor is the center of logical_bounds; pivot is normalized (0.5, 0.5).",
            "Nodes with reuse_of share one image (image = <reuse_of>.png); each instance keeps its own bounds.",
            "Engine importers are out of scope for this schema.",
            "dynamic_text / static_text text: size / size_px / leading_px are device pixels (layer transform applied); "
            "size_pt / leading / leading_pt are Photoshop text-space points. text_attrs_version 2 adds runs, "
            "paragraphs, kerning, anti_alias, effects_visible and layer_effects (references/slicing.md). "
            "Stroke and DropShadow live in text.layer_effects (and the legacy text.stroke field).",
        ],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out} ({len(nodes)} nodes)")
    return 0


# ---------------------------------------------------------------------------
# Validation / ΔE
# ---------------------------------------------------------------------------

def srgb_to_linear(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def rgb_to_lab(rgb):
    rgb = np.asarray(rgb, dtype=np.float64)
    lin = srgb_to_linear(rgb)
    m = np.array([
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ])
    xyz = lin @ m.T
    w = np.array([0.95047, 1.0, 1.08883])
    x = xyz / w
    eps, k = 216 / 24389, 24389 / 27
    f = np.where(x > eps, np.cbrt(x), (k * x + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def ciede2000(lab1, lab2):
    """Vectorized CIEDE2000. lab1/lab2:[...,3]."""
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    kL = kC = kH = 1.0
    C1 = np.sqrt(a1 ** 2 + b1 ** 2)
    C2 = np.sqrt(a2 ** 2 + b2 ** 2)
    Cbar = (C1 + C2) / 2.0
    C7 = Cbar ** 7
    G = 0.5 * (1 - np.sqrt(C7 / (C7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.sqrt(a1p ** 2 + b1 ** 2), np.sqrt(a2p ** 2 + b2 ** 2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp = L2 - L1
    dCp = C2p - C1p
    dhp = h2p - h1p
    dhp = np.where(dhp > 180, dhp - 360, dhp)
    dhp = np.where(dhp < -180, dhp + 360, dhp)
    dhp = np.where((C1p * C2p) == 0, 0, dhp)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp / 2.0))
    Lbar = (L1 + L2) / 2.0
    Cbarp = (C1p + C2p) / 2.0
    hsum = h1p + h2p
    hbarp = np.where(np.abs(h1p - h2p) > 180, (hsum + 360) / 2.0, hsum / 2.0)
    hbarp = np.where((C1p * C2p) == 0, hsum, hbarp)
    T = (1
         - 0.17 * np.cos(np.radians(hbarp - 30))
         + 0.24 * np.cos(np.radians(2 * hbarp))
         + 0.32 * np.cos(np.radians(3 * hbarp + 6))
         - 0.20 * np.cos(np.radians(4 * hbarp - 63)))
    dtheta = 30 * np.exp(-(((hbarp - 275) / 25) ** 2))
    Rc = 2 * np.sqrt(Cbarp ** 7 / (Cbarp ** 7 + 25 ** 7))
    SL = 1 + (0.015 * (Lbar - 50) ** 2) / np.sqrt(20 + (Lbar - 50) ** 2)
    SC = 1 + 0.045 * Cbarp
    SH = 1 + 0.015 * Cbarp * T
    RT = -np.sin(np.radians(2 * dtheta)) * Rc
    dE = np.sqrt(
        (dLp / (kL * SL)) ** 2
        + (dCp / (kC * SC)) ** 2
        + (dHp / (kH * SH)) ** 2
        + RT * (dCp / (kC * SC)) * (dHp / (kH * SH))
    )
    return dE


def alpha_over(canvas: Image.Image, im: Image.Image, xy) -> Image.Image:
    """True src-over. Image.paste(..., mask=im) is not a correct Porter-Duff over."""
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    x, y = int(xy[0]), int(xy[1])
    cw, ch = canvas.size
    iw, ih = im.size
    sx = 0 if x >= 0 else -x
    sy = 0 if y >= 0 else -y
    dx, dy = max(x, 0), max(y, 0)
    tw, th = min(iw - sx, cw - dx), min(ih - sy, ch - dy)
    if tw <= 0 or th <= 0:
        return canvas
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    layer.paste(im.crop((sx, sy, sx + tw, sy + th)), (dx, dy))
    return Image.alpha_composite(canvas, layer)


def hex_to_rgba(s, alpha=255):
    s = (s or "#ffffff").lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) < 6:
        return (255, 255, 255, int(alpha))
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return (r, g, b, int(max(0, min(255, alpha))))


def draw_dynamic_text(canvas, node, fonts_dir, doc_size, art_origin, text_opts=None):
    """Approximate an attribute-only text node (dynamic_text / static_text; see text_render.py).

    Not a PS typography emulator. Uses HarfBuzz + FreeType (or Skia); never falls back to Pillow.

    *text_opts*: {"renderer": auto|freetype|skia, "mixed_leading_k", "snap_glyph_origin", ...}.
    Raises text_render.TextRendererUnavailable / TextRenderError; never switches renderer
    or skips a text that has content.
    """
    text = node.get("text") or {}
    content = (text.get("content") or "")
    if not content or not str(content).strip():
        return canvas, {"name": node["name"], "skipped": "empty content (nothing to draw)"}
    opts = dict(text_opts or {})
    requested = opts.pop("renderer", "auto")
    bounds = node.get("logical_bounds") or node.get("visible_bounds") or {}
    try:
        im, xy, info = text_render.render_text(
            text, renderer=requested, fonts_dir=fonts_dir, doc_size=doc_size,
            art_origin=art_origin, bounds=bounds, options=opts,
        )
    except text_render.TextRendererUnavailable:
        raise
    except Exception as exc:
        raise text_render.TextRenderError(f"{node.get('kind') or 'text'} {node.get('name')!r}: {exc}") from exc
    if im is None:
        raise text_render.TextRenderError(
            f"{node.get('kind') or 'text'} {node.get('name')!r}: no glyphs rendered ({info.get('skipped')})"
        )
    info = {"name": node["name"], "rendered": True, "xy": list(xy), **info}
    canvas = alpha_over(canvas, im, xy)
    return canvas, info


def _fx_opacity(eff):
    op = eff.get("opacity")
    if op is None:
        return 0.75
    op = float(op)
    return max(0.0, min(1.0, op / 100.0 if op > 1.0 else op))


def emulate_outer_effect(im, eff):
    """Approximate a DropShadow / OuterGlow from a part's alpha. Returns (layer, dx, dy) or None.

    Pillow approximation only: spread dilates the alpha by spread% of size, the
    rest of size becomes a Gaussian blur (sigma ~ size/3), the effect is drawn with
    normal blending (Photoshop's multiply/screen are not emulated).
    """
    import math
    from PIL import ImageFilter

    t = eff.get("type")
    if t not in EMULATED_EFFECTS or eff.get("enabled") is False:
        return None
    size = max(0.0, float(eff.get("size") or 0))
    spread = max(0.0, min(100.0, float(eff.get("spread") or 0))) / 100.0
    dist = float(eff.get("distance") or 0) if t == "DropShadow" else 0.0
    ang = math.radians(float(eff.get("angle") if eff.get("angle") is not None else 120))
    dx, dy = int(round(-dist * math.cos(ang))), int(round(dist * math.sin(ang)))
    pad = int(math.ceil(size + abs(dx) + abs(dy))) + 2
    alpha = im.getchannel("A")
    big = Image.new("L", (im.width + 2 * pad, im.height + 2 * pad), 0)
    big.paste(alpha, (pad, pad))
    grow = int(round(size * spread))
    if grow > 0:
        big = big.filter(ImageFilter.MaxFilter(min(2 * grow + 1, 51)))
    sigma = size * (1.0 - spread) / 3.0
    if sigma > 0.25:
        big = big.filter(ImageFilter.GaussianBlur(sigma))
    rgb = hex_to_rgba(eff.get("color") or ("#000000" if t == "DropShadow" else "#ffffff"))[:3]
    a = (np.asarray(big, dtype=np.float32) * _fx_opacity(eff)).clip(0, 255).astype(np.uint8)
    layer = Image.new("RGBA", big.size, rgb + (0,))
    layer.putalpha(Image.fromarray(a, "L"))
    return layer, dx - pad, dy - pad


def render_engine_effects(canvas, node, im, x, y):
    """Draw emulated engine effects under a part. Returns (canvas, emulated, unemulated)."""
    emulated, unemulated = [], []
    if node.get("effect_mode") != "engine":
        return canvas, emulated, unemulated
    for eff in node.get("engine_effects") or []:
        if not isinstance(eff, dict) or eff.get("enabled") is False:
            continue
        res = None
        try:
            res = emulate_outer_effect(im, eff)
        except Exception as exc:
            warn(f"effect emulation failed for {node.get('name')}: {exc}")
        if res is None:
            unemulated.append(eff.get("type") or "?")
            continue
        layer, ox, oy = res
        canvas = alpha_over(canvas, layer, (x + ox, y + oy))
        emulated.append(eff.get("type"))
    return canvas, emulated, unemulated


def unemulated_effect_mask(layout, cw, ch):
    """Mask of engine-effect areas validate cannot emulate (ring for outer, box for inner effects)."""
    mask = np.zeros((ch, cw), dtype=bool)
    listed = []
    for node in layout.get("nodes") or []:
        if node.get("effect_mode") != "engine":
            continue
        effs = [e for e in (node.get("engine_effects") or []) if isinstance(e, dict) and e.get("enabled") is not False]
        todo = [e for e in effs if e.get("type") not in EMULATED_EFFECTS]
        if not todo:
            continue
        logi = node.get("logical_bounds") or node.get("visible_bounds")
        if not logi:
            continue
        outer = estimate_effect_bounds(logi, todo) or logi
        inner = any(e.get("type") in INNER_EFFECTS for e in todo) or any(
            e.get("type") == "Stroke" and not str(e.get("position") or "outside").lower().startswith("out") for e in todo
        )

        def clip(b):
            l, t, r, bt = [int(round(v)) for v in (b["left"], b["top"], b["right"], b["bottom"])]
            return max(0, l), max(0, t), min(cw, r), min(ch, bt)

        l, t, r, bt = clip(outer)
        if r > l and bt > t:
            ring = np.zeros_like(mask)
            ring[t:bt, l:r] = True
            if not inner:
                il, it, ir, ib = clip(logi)
                if ir > il and ib > it:
                    ring[it:ib, il:ir] = False
            mask |= ring
        listed.append({
            "name": node.get("name"),
            "effects": [e.get("type") for e in todo],
            "masked_bounds": outer,
            "area": "box" if inner else "ring",
        })
    return mask, listed


def recomposite(layout, slices_dir, canvas_size, render_text=False, fonts_dir=None, emulate_effects=True,
                text_opts=None):
    w, h = canvas_size
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    placed = []
    src = layout.get("source") or {}
    art = src.get("artboard") or {}
    doc_size = src.get("doc_size") or [None, None]
    art_origin = (art.get("bounds") or [0, 0])[0:2]
    for node in layout["nodes"]:
        if is_attr_text(node["kind"]):
            if render_text:
                canvas, info = draw_dynamic_text(canvas, node, fonts_dir, doc_size, art_origin, text_opts)
                placed.append(info)
            continue
        image_name = node.get("image")
        if not image_name:
            continue
        path = Path(slices_dir) / image_name
        if not path.exists():
            placed.append({"name": node["name"], "missing_file": True})
            continue
        im = Image.open(path).convert("RGBA")
        vb = node.get("visible_bounds") or node.get("logical_bounds")
        if not vb:
            placed.append({"name": node["name"], "missing_bounds": True})
            continue
        x, y = int(round(vb["left"])), int(round(vb["top"]))
        info = {"name": node["name"], "xy": [x, y], "size": list(im.size)}
        if emulate_effects and node.get("effect_mode") == "engine":
            canvas, emulated, unemulated = render_engine_effects(canvas, node, im, x, y)
            info["effects_emulated"] = emulated
            info["effects_unemulated"] = unemulated
        bm = node.get("blend_mode")
        try:
            canvas = blend_onto(canvas, im, (x, y), bm)
            if _norm_blend(bm) not in NORMAL_BLENDS:
                info["blend_mode"] = _norm_blend(bm)
        except ValueError:
            canvas = alpha_over(canvas, im, (x, y))
            info["blend_mode_approximated_normal"] = _norm_blend(bm)
        placed.append(info)
    return canvas, placed


def text_has_mixed_size_lines(text):
    """True when a multi-line text has runs of different sizes on different lines (mixed_leading_k applies)."""
    content = str(text.get("content") or "").replace("\n", "\r")
    runs = text.get("runs") or []
    if "\r" not in content or len(runs) < 2:
        return False
    sizes, pos = [], 0
    for line in content.split("\r"):
        end = pos + len(line)
        sz = [r.get("size_px") or r.get("size_pt") for r in runs if r.get("from", 0) < max(end, pos + 1) and r.get("to", 0) > pos]
        sz = [x for x in sz if x]
        if sz:
            sizes.append(round(max(sz), 2))
        pos = end + 1
    return len(set(sizes)) > 1


def text_region_mask(layout, cw, ch):
    mask = np.zeros((ch, cw), dtype=bool)
    for node in layout["nodes"]:
        if not is_attr_text(node.get("kind")):
            continue
        b = node.get("visible_bounds") or node.get("logical_bounds")
        if not b:
            continue
        l, top, r, bt = [int(round(v)) for v in (b["left"], b["top"], b["right"], b["bottom"])]
        l, top = max(0, l), max(0, top)
        r, bt = min(cw, r), min(ch, bt)
        if r > l and bt > top:
            mask[top:bt, l:r] = True
    return mask


def metrics(a, b, mask=None):
    rgb_a, rgb_b = a[..., :3], b[..., :3]
    if mask is not None:
        keep = ~mask
        if not keep.any():
            return {}
        rgb_a, rgb_b = rgb_a[keep], rgb_b[keep]
        alpha_diff = np.abs(a[..., 3].astype(np.int16) - b[..., 3].astype(np.int16))[keep]
    else:
        alpha_diff = np.abs(a[..., 3].astype(np.int16) - b[..., 3].astype(np.int16))
    diff = np.abs(rgb_a.astype(np.int16) - rgb_b.astype(np.int16))
    chan_max = diff.max(axis=-1)
    mae = float(diff.mean())
    mse = float((diff.astype(np.float64) ** 2).mean())
    psnr = 10 * np.log10(255 ** 2 / mse) if mse else 99.0
    lab_a = rgb_to_lab(rgb_a.reshape(-1, 3) if mask is not None else rgb_a)
    lab_b = rgb_to_lab(rgb_b.reshape(-1, 3) if mask is not None else rgb_b)
    dE = ciede2000(lab_a, lab_b)
    return {
        "mae": round(mae, 3),
        "psnr_db": round(float(psnr), 2),
        "pct_px_diff_gt16": round(float((chan_max > 16).mean() * 100), 3),
        "pct_px_diff_gt48": round(float((chan_max > 48).mean() * 100), 3),
        "mean_delta_e": round(float(dE.mean()), 3),
        "p95_delta_e": round(float(np.percentile(dE, 95)), 3),
        "pct_delta_e_gt2": round(float((dE > 2).mean() * 100), 3),
        "pct_delta_e_gt5": round(float((dE > 5).mean() * 100), 3),
        "mean_alpha_diff": round(float(alpha_diff.mean()), 3),
    }


def per_element_diffs(layout, ref, rec, cw, ch):
    rows = []
    for node in layout["nodes"]:
        b = node.get("visible_bounds") or node.get("logical_bounds")
        if not b:
            continue
        l, top, r, bt = [int(round(v)) for v in (b["left"], b["top"], b["right"], b["bottom"])]
        l, top = max(0, l), max(0, top)
        r, bt = min(cw, r), min(ch, bt)
        if r <= l or bt <= top:
            continue
        aa = ref[top:bt, l:r]
        bb = rec[top:bt, l:r]
        diff = np.abs(aa[..., :3].astype(np.int16) - bb[..., :3].astype(np.int16))
        chan_max = diff.max(axis=-1)
        mae = float(diff.mean())
        mse = float((diff.astype(np.float64) ** 2).mean())
        psnr = 10 * np.log10(255 ** 2 / mse) if mse else 99.0
        lab_a = rgb_to_lab(aa[..., :3].reshape(-1, 3))
        lab_b = rgb_to_lab(bb[..., :3].reshape(-1, 3))
        dE = ciede2000(lab_a, lab_b)
        rows.append({
            "name": node["name"],
            "kind": node["kind"],
            "bounds": [l, top, r, bt],
            "mae": round(mae, 3),
            "psnr_db": round(float(psnr), 2),
            "mean_delta_e": round(float(dE.mean()), 3),
            "p95_delta_e": round(float(np.percentile(dE, 95)), 3),
            "pct_delta_e_gt5": round(float((dE > 5).mean() * 100), 3),
            "pct_px_diff_gt16": round(float((chan_max > 16).mean() * 100), 3),
        })
    rows.sort(key=lambda row: (-row["mean_delta_e"], -row["mae"]))
    return rows[:10], rows


def write_side_by_side(ref, rec, path, titles):
    ch, cw = ref.shape[0], ref.shape[1]
    d_rgb = np.abs(ref[..., :3].astype(np.int16) - rec[..., :3].astype(np.int16)).max(axis=2)
    heat = np.zeros((ch, cw, 3), dtype=np.uint8)
    v = np.clip(d_rgb * 3, 0, 255).astype(np.uint8)
    heat[..., 0] = v
    heat[..., 1] = (v * 0.3).astype(np.uint8)
    g = (ref[..., :3].mean(axis=2) * 0.25).astype(np.uint8)
    heat = np.maximum(heat, np.stack([g, g, g], axis=2))
    scale = 0.5
    rw, rh = int(cw * scale), int(ch * scale)
    canvas = Image.new("RGB", (rw * 3 + 40, rh + 50), (30, 30, 30))
    dr = ImageDraw.Draw(canvas)
    font = load_font(16)
    images = [
        (Image.fromarray(ref[..., :3]), titles[0]),
        (Image.fromarray(rec[..., :3]), titles[1]),
        (Image.fromarray(heat), titles[2]),
    ]
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    for i, (im, title) in enumerate(images):
        canvas.paste(im.resize((rw, rh), resampling), (i * (rw + 20), 40))
        dr.text((i * (rw + 20) + 8, 10), title, fill=(255, 255, 255), font=font)
    canvas.save(path, quality=88)
    return path



def iter_layout_components(components):
    for comp in components or []:
        yield comp
        yield from iter_layout_components(comp.get("children") or [])


def default_nodes(layout):
    nodes = layout.get("nodes") or []
    if not any(n.get("state") or n.get("hidden") or n.get("default") is False for n in nodes):
        return nodes
    return [n for n in nodes if n.get("default")]


def layout_with_nodes(layout, nodes):
    out = dict(layout)
    out["nodes"] = nodes
    return out


def component_parent_map(components):
    parent_of = {}

    def walk(nodes, parent=None):
        for comp in nodes or []:
            if parent is not None:
                parent_of[comp.get("id")] = parent
            walk(comp.get("children") or [], comp)

    walk(components)
    return parent_of


def sibling_fallback_state(sibling):
    states = set((sibling.get("states") or {}).keys())
    for cand in ("normal", "off", "idle", "unselected"):
        if cand in states:
            return cand
    return sibling.get("default_state")


def nodes_for_activation(layout, activation):
    """Pick parts for a set of {component_id: state_name} plus default everything else."""
    act = activation or {}
    out = []
    for node in layout.get("nodes") or []:
        cid = node.get("component_id")
        if cid in act:
            st = node.get("state")
            if st is None or st == act[cid]:
                out.append(node)
        elif node.get("default", True):
            out.append(node)
    return out


def collect_alternate_activations(layout):
    """Each interactive component's non-default state, with exclusive sibling switching."""
    components = layout.get("components") or []
    parent_of = component_parent_map(components)
    previews = []
    for comp in iter_layout_components(components):
        states = comp.get("states") or {}
        if not isinstance(states, dict) or not states:
            continue
        default = comp.get("default_state")
        parent = parent_of.get(comp.get("id"))
        exclusive = bool(
            parent and (
                parent.get("exclusive")
                or parent.get("type") in {"tab_group", "navbar", "nav_bar", "segmented"}
            )
        )
        state_names = list(states.keys())
        for item in comp.get("missing") or []:
            name = item if isinstance(item, str) else (item or {}).get("state")
            if name and name not in state_names:
                state_names.append(name)
        for state_name in state_names:
            if default is not None and state_name == default:
                continue
            activation = {comp.get("id"): state_name}
            if exclusive and state_name in {"selected", "on", "active", "pressed"}:
                for sib in parent.get("children") or []:
                    if sib.get("id") == comp.get("id"):
                        continue
                    fallback = sibling_fallback_state(sib)
                    if fallback:
                        activation[sib.get("id")] = fallback
            stem = f"{comp.get('id')}_{state_name}"
            previews.append({
                "id": stem,
                "component_id": comp.get("id"),
                "component_type": comp.get("type"),
                "state": state_name,
                "activation": activation,
                "from_hidden": bool((states.get(state_name) or {}).get("from_hidden")) if state_name in states else False,
            })
    return previews



def _bounds_dict(b):
    if not b:
        return None
    if isinstance(b, dict) and "left" in b:
        return {k: float(b[k]) for k in ("left", "top", "right", "bottom")}
    if isinstance(b, (list, tuple)) and len(b) == 4:
        return {"left": float(b[0]), "top": float(b[1]), "right": float(b[2]), "bottom": float(b[3])}
    return None


def component_slot_bounds(layout, component_id):
    """Best-effort box for a missing state-dependent part: the item's base/highlight."""
    preferred, fallback = None, None
    for node in layout.get("nodes") or []:
        if node.get("component_id") != component_id:
            continue
        b = _bounds_dict(node.get("visible_bounds") or node.get("logical_bounds"))
        if not b:
            continue
        if node.get("part_role") in {"base", "highlight"}:
            preferred = b
            break
        if fallback is None:
            fallback = b
    return preferred or fallback


def missing_markers_for_state(layout, component_id, state_name):
    """Red-box markers only for state-dependent parts that are truly missing."""
    markers = []
    slot = component_slot_bounds(layout, component_id)
    for comp in iter_layout_components(layout.get("components") or []):
        if comp.get("id") != component_id:
            continue
        for item in comp.get("missing") or []:
            if isinstance(item, str):
                if item != state_name:
                    continue
                markers.append({
                    "label": "missing",
                    "state": state_name,
                    "role": None,
                    "bounds": slot,
                })
            elif isinstance(item, dict) and item.get("state") == state_name:
                role = item.get("role")
                b = _bounds_dict(item.get("bounds") or item.get("logical_bounds") or item.get("visible_bounds")) or slot
                markers.append({
                    "label": f"missing: {role}" if role else "missing",
                    "state": state_name,
                    "role": role,
                    "bounds": b,
                    "reason": item.get("reason"),
                })
        break
    # Merge markers that share the same box so one red rect can carry several roles.
    merged = []
    for marker in markers:
        key = tuple(marker["bounds"][k] for k in ("left", "top", "right", "bottom")) if marker.get("bounds") else None
        found = next((m for m in merged if m.get("_key") == key), None) if key is not None else None
        if found:
            if marker.get("role") and marker["role"] not in (found.get("roles") or []):
                found.setdefault("roles", [])
                found["roles"].append(marker["role"])
                found["label"] = "missing: " + ", ".join(found["roles"])
        else:
            marker["_key"] = key
            marker["roles"] = [marker["role"]] if marker.get("role") else []
            merged.append(marker)
    for marker in merged:
        marker.pop("_key", None)
    return merged


def annotate_missing_state(image, markers):
    """Draw a red box and a 'missing' label on each truly missing state-dependent part."""
    if not markers:
        return image
    draw = ImageDraw.Draw(image)
    font = load_font(24)
    for marker in markers:
        b = marker.get("bounds")
        label = marker.get("label") or "missing"
        if b:
            raw = [b.get(k) for k in ("left", "top", "right", "bottom")]
            box = normalize_box(*raw, width=image.width, height=image.height, name=label)
            if box is not None:
                l, top, r, bt = [int(round(v)) for v in box]
                for w in range(4):
                    inner = normalize_box(l + w, top + w, r - w, bt - w, width=image.width, height=image.height)
                    if inner is None:
                        break
                    draw.rectangle([inner[0], inner[1], inner[2], inner[3]], outline=(220, 30, 30))
                tx, ty = l, max(0, top - 28)
            else:
                tx, ty = 8, 8
        else:
            tx, ty = 8, 8
        try:
            tw = draw.textlength(label, font=font)
        except Exception:
            tw = 10 * len(label)
        lab = normalize_box(tx, ty, tx + int(tw) + 12, ty + 26, width=image.width, height=image.height, name=label)
        if lab is not None:
            lx0, ly0, lx1, ly1 = lab
            draw.rectangle([lx0, ly0, lx1, ly1], fill=(220, 30, 30))
            draw.text((lx0 + 6, ly0 + 2), label, fill=(255, 255, 255), font=font)
    return image


def write_state_previews(layout, slices_dir, canvas_size, out_dir, fonts_dir=None):
    states_dir = Path(out_dir) / "states"
    states_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for item in collect_alternate_activations(layout):
        nodes = nodes_for_activation(layout, item["activation"])
        canvas, placed = recomposite(
            layout_with_nodes(layout, nodes), slices_dir, canvas_size, render_text=False
        )
        markers = missing_markers_for_state(layout, item.get("component_id"), item.get("state"))
        rgb = canvas.convert("RGB")
        if markers:
            annotate_missing_state(rgb, markers)
        path = states_dir / f"{item['id']}.png"
        rgb.save(path)
        item_out = dict(item)
        item_out["path"] = str(path)
        item_out["placed"] = len(placed)
        item_out["missing"] = [
            {k: v for k, v in m.items() if k != "bounds"} | ({"bounds": m["bounds"]} if m.get("bounds") else {})
            for m in markers
        ]
        written.append(item_out)
    index = {"count": len(written), "previews": written}
    (states_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return written


def _node_opaque_mask(node, slices_dir, region_wh, thr=128):
    """Opaque region (alpha >= thr) of the node's own image, placed at its visible box. None if unknown."""
    if not slices_dir or not node.get("image"):
        return None
    path = Path(slices_dir) / node["image"]
    if not path.is_file():
        return None
    a = np.asarray(Image.open(path).convert("RGBA"))[..., 3]
    w, h = region_wh
    out = np.zeros((h, w), dtype=bool)
    hh, ww = min(h, a.shape[0]), min(w, a.shape[1])
    out[:hh, :ww] = a[:hh, :ww] >= thr
    return out


def _covered_above(nodes, idx, box, slices_dir):
    """Pixels of ``box`` (l, t, r, b canvas coords) touched by nodes painted after ``nodes[idx]``: any non-zero
    alpha of their image (grown by 1 px for resampling), else their visible box (text nodes use their box).
    Error there belongs to the upper node, not to this one."""
    l, t, r, bt = box
    cov = np.zeros((bt - t, r - l), dtype=bool)
    for up in nodes[idx + 1:]:
        b = up.get("visible_bounds") or up.get("logical_bounds")
        if not b:
            continue
        L, T = int(round(b["left"])), int(round(b["top"]))
        R, B = int(round(b["right"])), int(round(b["bottom"]))
        il, it, ir, ib = max(l, L), max(t, T), min(r, R), min(bt, B)
        if ir <= il or ib <= it:
            continue
        m = None
        if not is_attr_text(up.get("kind")) and not up.get("nine_slice"):
            m = _node_opaque_mask(up, slices_dir, (R - L, B - T), thr=1)
            if m is not None:
                g = m.copy()
                g[1:] |= m[:-1]; g[:-1] |= m[1:]; g[:, 1:] |= m[:, :-1]; g[:, :-1] |= m[:, 1:]
                m = g
        if m is None:
            cov[it - t:ib - t, il - l:ir - l] = True
        else:
            cov[it - t:ib - t, il - l:ir - l] |= m[it - T:ib - T, il - L:ir - L]
    return cov


def layer_bounds_issues(nodes):
    """``layer_bounds_mismatch`` for nodes whose layer measured somewhere else than their manifest box."""
    out = []
    for n in nodes:
        m = n.get("layer_bounds_mismatch")
        if not m or m.get("placed_from") == "manifest_logical_bounds":
            continue
        out.append({"type": "layer_bounds_mismatch", "name": n.get("name"), "layer_id": n.get("layer_id"),
                    "source_path": n.get("source_path"), **m,
                    "hint": ("The layer_id / select_ids of this entry measure at another box than its logical_bounds: "
                             "it probably points at a sibling instance (copied entry), so the instance it names is "
                             "not drawn and its sibling is drawn twice. Point it at the layer that is really at "
                             "logical_bounds (source_path), relock, re-export.")})
    return out


PLACEMENT_MAX_SHIFT = 2
PLACEMENT_MIN_PX = 64
PLACEMENT_MAX_SAMPLES = 200000


def placement_offset_checks(layout, ref, mask, slices_dir, max_shift=PLACEMENT_MAX_SHIFT):
    """Is each image drawn where Photoshop draws it? Compare the node's own opaque pixels (alpha 255, not covered by
    nodes above, outside text / unemulated masks) with the PS render at its placed box and at every shift up to
    ``max_shift`` px. A shift that clearly fits better (half the MAE or less, and at least 0.5 lower) means the
    recorded position is off: ``placement_offset`` with the shift that fits. Normal-blend, full-opacity images (and
    nine-slices drawn at their native size) only.
    Returns (issues, checked)."""
    issues, checked = [], []
    if not slices_dir:
        return issues, checked
    nodes = layout.get("nodes") or []
    ch, cw = ref.shape[0], ref.shape[1]
    pad = max_shift
    refp = np.zeros((ch + 2 * pad, cw + 2 * pad, 3), dtype=np.int16)
    refp[pad:pad + ch, pad:pad + cw] = ref[..., :3]
    inside = np.zeros((ch + 2 * pad, cw + 2 * pad), dtype=bool)
    inside[pad:pad + ch, pad:pad + cw] = True
    for idx, node in enumerate(nodes):
        if is_attr_text(node.get("kind")) or not node.get("image"):
            continue
        if str(node.get("blend_mode") or "normal").lower() not in ("normal", "passthrough", "pass_through"):
            continue
        op = node.get("opacity")
        if op is not None and float(op) not in (1.0, 100.0):
            continue
        b = node.get("visible_bounds")
        path = Path(slices_dir) / node["image"]
        if not b or not path.is_file():
            continue
        img = np.asarray(Image.open(path).convert("RGBA"))
        L, T = int(round(b["left"])), int(round(b["top"]))
        h, w = img.shape[0], img.shape[1]
        if node.get("nine_slice") and (int(round(b["right"])) - L, int(round(b["bottom"])) - T) != (w, h):
            continue  # stretched nine-slice: its pixels are not the PNG's 1:1
        l, t, r, bt = max(0, L), max(0, T), min(cw, L + w), min(ch, T + h)
        if r <= l or bt <= t:
            continue
        keep = img[t - T:bt - T, l - L:r - L, 3] == 255
        if mask is not None:
            keep &= ~mask[t:bt, l:r]
        above = _covered_above(nodes, idx, (l, t, r, bt), slices_dir)
        keep &= ~above
        ys, xs = np.nonzero(keep)
        if ys.size < PLACEMENT_MIN_PX:
            continue
        if ys.size > PLACEMENT_MAX_SAMPLES:
            step = int(np.ceil(ys.size / PLACEMENT_MAX_SAMPLES))
            ys, xs = ys[::step], xs[::step]
        own = img[t - T:bt - T, l - L:r - L, :3].astype(np.int16)[ys, xs]
        gy, gx = ys + t + pad, xs + l + pad
        scores = {}
        for dy in range(-max_shift, max_shift + 1):
            for dx in range(-max_shift, max_shift + 1):
                ok = inside[gy + dy, gx + dx]
                if ok.sum() < PLACEMENT_MIN_PX:
                    continue
                scores[(dx, dy)] = float(np.abs(refp[gy + dy, gx + dx][ok] - own[ok]).mean())
        if (0, 0) not in scores:
            continue
        mae0 = scores[(0, 0)]
        (bdx, bdy), best = min(scores.items(), key=lambda kv: (kv[1], abs(kv[0][0]) + abs(kv[0][1])))
        row = {"name": node["name"], "px": int(ys.size), "mae": round(mae0, 3)}
        checked.append(row)
        if (bdx, bdy) != (0, 0) and best <= 0.5 * mae0 and mae0 - best >= 0.5:
            issues.append(dict(row, type="placement_offset", fits_at_shift=[bdx, bdy], mae_at_shift=round(best, 3),
                               hint=("The image matches the design better when moved by fits_at_shift px: its "
                                     "recorded visible_bounds are off (a trim-inset origin measured on live effects, "
                                     "or a wrong layer). Re-export with the current export_slices.psjs; do not "
                                     "nudge the layout by hand.")))
    return issues, checked


def reuse_region_checks(layout, ref, rec, mask, out_dir, tol_mae=None, tol_pct=None, slices_dir=None,
                        decisions=None):
    """Per-instance diff for nodes that share an image. Returns (mismatches, accepted, checked).

    Metric (1.4.9): inside the shared image's opaque region (alpha >= 128, minus attribute-text / unemulated
    effect masks, minus pixels covered by nodes painted above it -- their error is theirs): MAE (0-255, RGB) and % of pixels whose max channel diff > 16. Over either cap is a
    ``reuse_mismatch`` issue. ``reuse_accepted`` alone (an agent flag) does not bypass the cap; only a
    reuse_decisions entry with a recorded user ``decision`` (reuse / accept ...) moves it to accepted."""
    tol_mae = DEFAULT_THRESHOLDS["reuse_max_mae"] if tol_mae is None else tol_mae
    tol_pct = DEFAULT_THRESHOLDS["reuse_max_pct_gt16"] if tol_pct is None else tol_pct
    decisions = decisions if decisions is not None else (layout.get("reuse_decisions") or [])
    ch, cw = ref.shape[0], ref.shape[1]
    mismatches, accepted, checked = [], [], []
    nodes = layout.get("nodes") or []
    for idx, node in enumerate(nodes):
        if not node.get("reuse_of") or is_attr_text(node.get("kind")):
            continue
        b = node.get("visible_bounds") or node.get("logical_bounds")
        if not b:
            continue
        L, T = int(round(b["left"])), int(round(b["top"]))
        R, B = int(round(b["right"])), int(round(b["bottom"]))
        l, t, r, bt = max(0, L), max(0, T), min(cw, R), min(ch, B)
        if r <= l or bt <= t:
            continue
        keep = ~mask[t:bt, l:r] if mask is not None else np.ones((bt - t, r - l), dtype=bool)
        opaque = None if node.get("nine_slice") else _node_opaque_mask(node, slices_dir, (R - L, B - T))
        region = "visible_bounds"
        if opaque is not None:
            sub = opaque[t - T:bt - T, l - L:r - L]
            if sub.shape == keep.shape and (keep & sub).any():
                keep = keep & sub
                region = "opaque_alpha>=128"
        above = _covered_above(nodes, idx, (l, t, r, bt), slices_dir)
        if above.any() and (keep & ~above).any():
            keep = keep & ~above
            region += ",not_covered_above"
        if not keep.any():
            continue
        aa = ref[t:bt, l:r, :3].astype(np.int16)[keep]
        bb = rec[t:bt, l:r, :3].astype(np.int16)[keep]
        d = np.abs(aa - bb)
        row = {
            "name": node["name"],
            "reuse_of": node["reuse_of"],
            "bounds": [l, t, r, bt],
            "region": region,
            "mae": round(float(d.mean()), 3),
            "pct_px_diff_gt16": round(float((d.max(axis=-1) > 16).mean() * 100), 3),
            "cap": {"mae": tol_mae, "pct_px_diff_gt16": tol_pct},
        }
        checked.append(row)
        if row["mae"] <= tol_mae and row["pct_px_diff_gt16"] <= tol_pct:
            continue
        img = write_compare_sheet(
            [(node["name"], Image.fromarray(ref[t:bt, l:r]), Image.fromarray(rec[t:bt, l:r]))],
            Path(out_dir) / "reuse" / f"{node['name']}.png",
            titles=("design (PS)", "shared image", "diff x3"),
        )
        row["compare_image"] = str(img) if img else None
        d_user = user_decision_for(node["name"], decisions, {"reuse"}, reuse_of=node.get("reuse_of"),
                                   accept=REUSE_ACCEPT_DECISIONS)
        if d_user:
            accepted.append(dict(row, decision_id=d_user.get("id"), decision=d_user.get("decision")))
            continue
        extra = {"reuse_accepted_by_agent_only": True} if node.get("reuse_accepted") else {}
        mismatches.append(dict(row, type="reuse_mismatch", **extra, hint=(
            "Reused image differs from the design here (over the cap). First suspect the reuse analysis and export "
            "this instance separately; if logical evidence still says reuse, add a reuse_decisions entry for the "
            "user. reuse_accepted set without a recorded user decision does not count.")))
    return mismatches, accepted, checked


def split_region_checks(layout, ref, rec, mask, out_dir, threshold=None, decisions=None):
    """Measured split check (1.4.9): for each component with split parts in the default state, compare the
    recomposite of the exported parts with the Photoshop render over the union of their visible boxes
    (attribute-text / unemulated masks excluded). Below the threshold -> ``split_below_threshold``
    unless a split_avatar decision with a recorded user decision covers one of its parts."""
    threshold = DEFAULT_THRESHOLDS["split_match_psnr"] if threshold is None else threshold
    decisions = decisions if decisions is not None else (layout.get("reuse_decisions") or [])
    ch, cw = ref.shape[0], ref.shape[1]
    groups = {}
    for node in layout.get("nodes") or []:
        if not node.get("split_method") or is_attr_text(node.get("kind")):
            continue
        b = node.get("visible_bounds") or node.get("logical_bounds")
        if not b:
            continue
        groups.setdefault(node.get("component_id") or node["name"], []).append(node)
    bad, accepted, checked = [], [], []
    for cid, nodes in groups.items():
        roles = {(n.get("part_role") or n.get("role") or "").lower() for n in nodes}
        if "frame" not in roles:
            continue  # a lone placeholder is the merged image itself; nothing was split here
        l = max(0, int(min(n["visible_bounds"]["left"] for n in nodes if n.get("visible_bounds"))))
        t = max(0, int(min(n["visible_bounds"]["top"] for n in nodes if n.get("visible_bounds"))))
        r = min(cw, int(max(n["visible_bounds"]["right"] for n in nodes if n.get("visible_bounds"))))
        bt = min(ch, int(max(n["visible_bounds"]["bottom"] for n in nodes if n.get("visible_bounds"))))
        if r <= l or bt <= t:
            continue
        m = mask[t:bt, l:r] if mask is not None else None
        met = metrics(ref[t:bt, l:r], rec[t:bt, l:r], m)
        if not met:
            continue
        row = {"component_id": cid, "names": [n["name"] for n in nodes], "bounds": [l, t, r, bt],
               "psnr_db": met["psnr_db"], "mae": met["mae"], "pct_px_diff_gt16": met["pct_px_diff_gt16"],
               "threshold_psnr": threshold}
        checked.append(row)
        if met["psnr_db"] >= threshold:
            continue
        img = write_compare_sheet([(f"{cid} {met['psnr_db']} dB", Image.fromarray(ref[t:bt, l:r]),
                                    Image.fromarray(rec[t:bt, l:r]))],
                                  Path(out_dir) / "splits" / f"{slugify(str(cid))}.png",
                                  titles=("design (PS)", "exported parts", "diff x3"))
        row["compare_image"] = str(img) if img else None
        d_user = next((user_decision_for(n, decisions, {"split_avatar"}) for n in row["names"]
                       if user_decision_for(n, decisions, {"split_avatar"})), None)
        if d_user:
            accepted.append(dict(row, decision_id=d_user.get("id"), decision=d_user.get("decision")))
        else:
            bad.append(dict(row, type="split_below_threshold", message=SPLIT_BELOW_HINT))
    return bad, accepted, checked


def layout_nodes_as_preview_entries(nodes):
    """Layout nodes -> annotate_preview entries, boxed by visible_bounds (artboard coords)."""
    entries = []
    for node in nodes or []:
        b = node.get("visible_bounds") or node.get("logical_bounds")
        if not b:
            continue
        entries.append({
            "name": node.get("name"),
            "kind": node.get("kind") or "image",
            "logical_bounds": [b["left"], b["top"], b["right"], b["bottom"]],
            "nine_slice": node.get("nine_slice"),
            "effect_mode": node.get("effect_mode"),
            "engine_effects": node.get("engine_effects"),
            "reuse_of": node.get("reuse_of"),
        })
    return entries


def write_exported_preview(layout, rec, ref, out_dir, title=None):
    """Final deliverable: annotated preview drawn on the recomposite of the exported PNGs.

    Writes preview_exported_annotated.png and, when *ref* is given, a side-by-side with
    the Photoshop full render (preview_exported_vs_ps.png). Returns the written paths.
    """
    out_dir = Path(out_dir)
    nodes = (layout.get("nodes") or [])
    entries = layout_nodes_as_preview_entries(nodes)
    bg = Image.new("RGBA", rec.size, (24, 24, 24, 255))
    base = Image.alpha_composite(bg, rec.convert("RGBA"))
    art = (layout.get("source") or {}).get("artboard") or {}
    title = title or f"{art.get('name') or 'artboard'}  exported {len(entries)} slices"
    annotated = out_dir / "preview_exported_annotated.png"
    annotate_preview(base, entries, (0, 0), annotated, title)
    paths = {"annotated": str(annotated)}
    if ref is not None:
        left = ref.convert("RGBA")
        left = Image.alpha_composite(Image.new("RGBA", left.size, (24, 24, 24, 255)), left).convert("RGB")
        right = Image.open(annotated).convert("RGB")
        top = 36  # legend height of annotate_preview
        lw, lh = left.size
        gap = 20
        side = Image.new("RGB", (lw + gap + right.width, max(lh + top, right.height)), (30, 30, 30))
        side.paste(left, (0, top))
        side.paste(right, (lw + gap, 0))
        d = ImageDraw.Draw(side)
        d.text((8, 8), "Photoshop full render", fill=(255, 255, 255), font=load_font(16))
        sbs = out_dir / "preview_exported_vs_ps.png"
        side.save(sbs)
        paths["side_by_side"] = str(sbs)
    return paths



def fill_layout_leading_types(layout, psd_path):
    """Copy EngineData LeadingType onto attribute-only text nodes that lack leading_type (descriptor often omits it)."""
    if not psd_path or not Path(psd_path).is_file():
        return 0
    mapping = text_render.leading_types_from_psd(psd_path)
    if not mapping:
        return 0
    n = 0
    for node in layout.get("nodes") or []:
        if not is_attr_text(node.get("kind")):
            continue
        text = node.get("text")
        if not isinstance(text, dict):
            continue
        lid = node.get("layer_id")
        if lid is None:
            continue
        lt = mapping.get(int(lid))
        if not lt:
            continue
        if not text.get("leading_type"):
            text["leading_type"] = lt
            n += 1
        for para in text.get("paragraphs") or []:
            if not para.get("leading_type"):
                para["leading_type"] = lt
    return n


def resolve_layout_psd(layout, explicit=None):
    if explicit:
        return explicit
    src = (layout or {}).get("source") or {}
    path = src.get("file") or src.get("path")
    return path if path and Path(path).is_file() else None


def cmd_validate(args):
    layout = json.loads(Path(args.layout).read_text(encoding="utf-8"))
    psd_for_text = resolve_layout_psd(layout, getattr(args, "psd", None))
    if psd_for_text:
        fill_layout_leading_types(layout, psd_for_text)
    slices_dir = Path(args.slices_dir)
    full = Image.open(args.full_render).convert("RGBA")
    art = layout["source"]["artboard"]
    w, h = art["size"]
    if full.size != (w, h):
        print(f"warning: full render size {full.size} != artboard {w}x{h}", flush=True)

    issues = []
    names = [n["name"] for n in layout["nodes"]]
    dup = [k for k, v in Counter(names).items() if v > 1]
    if dup:
        issues.append({"type": "duplicate_names", "names": dup})

    empty = []
    missing = []
    for node in layout["nodes"]:
        if is_text_kind(node.get("kind")) and node.get("nine_slice"):
            issues.append({
                "type": "text_has_nine_slice",
                "name": node.get("name"),
                "kind": node.get("kind"),
                "nine_slice": node.get("nine_slice"),
                "hint": "text entries must not have nine_slice margins; set nine_slice to null.",
            })
        if is_attr_text(node.get("kind")):
            if not (node.get("text") or {}).get("content") and not (node.get("text") or {}).get("font"):
                issues.append({"type": "missing_text_attrs", "name": node["name"]})
            continue
        image_name = node.get("image")
        path = slices_dir / image_name if image_name else None
        if path is None or not path.exists():
            missing.append(node["name"])
            continue
        im = Image.open(path)
        arr = np.asarray(im.convert("RGBA"))
        if arr.size == 0 or int(arr[..., 3].max()) == 0:
            empty.append(node["name"])
    if missing:
        issues.append({"type": "missing_slices", "names": missing})
    if empty:
        issues.append({"type": "empty_images", "names": empty})
    effect_groups = layout.get("effect_groups")
    if getattr(args, "group_effects", None) or not effect_groups:
        effect_groups = load_effect_groups(None, None, getattr(args, "group_effects", None)) or effect_groups or []
    if not effect_groups and (layout.get("layer_tree") or layout.get("tree")):
        effect_groups = effect_groups_from_tree(layout.get("layer_tree") or layout.get("tree"))
    issues.extend(find_silent_effect_loss(layout["nodes"], effect_groups))
    thr = resolve_thresholds(layout.get("thresholds"),
                             thresholds_from_rules_file(getattr(args, "rules", None)),
                             {"split_match_psnr": getattr(args, "split_threshold", None),
                              "reuse_max_mae": getattr(args, "reuse_tol_mae", None),
                              "reuse_max_pct_gt16": getattr(args, "reuse_tol_pct", None)})
    issues.extend(lock_issues(layout.get("manifest_lock")))
    issues.extend(user_decision_issues(layout.get("reuse_decisions") or [], layout.get("manifest_lock")))
    issues.extend(export_truth_issues(layout, getattr(args, "shipped_exporter", None)))
    issues.extend(uncertain_hidden_issues(layout.get("components") or [], layout.get("reuse_decisions") or [],
                                          layout.get("manifest_lock"), layout.get("uncertain_hidden")))
    issues.extend(hidden_layer_export_issues(layout))
    issues.extend(check_avatar_splits(layout["nodes"], layout.get("reuse_decisions") or [],
                                      layout.get("avatar_merge_suspects") or [], layout.get("tree") or None, thr))
    issues.extend(check_frame_bakes(layout["nodes"], layout.get("tree") or None))
    issues.extend(check_split_effects(layout["nodes"], layout.get("tree") or None))

    fonts_dir = getattr(args, "fonts_dir", None)
    text_opts = {
        "renderer": getattr(args, "text_renderer", None) or "auto",
        "mixed_leading_k": getattr(args, "mixed_leading_k", None),
        "snap_glyph_origin": getattr(args, "snap_glyph_origin", None),
    }
    text_opts = {k: v for k, v in text_opts.items() if v is not None}
    if getattr(args, "text_legacy_rules", False):
        text_opts = dict(text_render.LEGACY_1_3_OPTIONS, **text_opts)
    default_layout = layout_with_nodes(layout, default_nodes(layout))
    has_text = any(
        is_attr_text(n.get("kind")) and str((n.get("text") or {}).get("content") or "").strip()
        for n in default_layout["nodes"]
    )
    out_dir = Path(args.out_dir)
    renderer_used = None

    def fail(kind, exc, install=None):
        msg = str(exc)
        print(f"error: {msg}", file=sys.stderr, flush=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        failed = {
            "ok": False,
            "error": {"type": kind, "message": msg, "install": install},
            "text_renderer": {"requested": text_opts["renderer"], "used": renderer_used},
            "artboard": art,
            "issues": issues + [{"type": kind, "message": msg}],
            "notes": ["validate stopped: no recomposite or metrics were produced."],
        }
        (out_dir / "validate_report.json").write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")
        return 2

    if has_text:
        try:
            renderer_used = text_render.select_renderer(text_opts["renderer"])
        except text_render.TextRendererUnavailable as exc:
            return fail("text_renderer_unavailable", exc, exc.install)
    rec_masked, placed_masked = recomposite(default_layout, slices_dir, (w, h), render_text=False)
    try:
        rec_text, placed_text = recomposite(
            default_layout, slices_dir, (w, h), render_text=True, fonts_dir=fonts_dir, text_opts=text_opts
        )
    except text_render.TextRenderError as exc:
        return fail("text_render_failed", exc)
    text_infos = [p for p in placed_text if "renderer" in p or p.get("skipped")]
    legacy_v1 = [
        n["name"] for n in default_layout["nodes"]
        if is_attr_text(n.get("kind")) and not text_render.is_v2(n.get("text") or {})
    ]
    if legacy_v1:
        warn(f"{len(legacy_v1)} attribute-only text node(s) use legacy v1 text attributes (single run, no effect "
             "visibility); re-export with the current export_slices.psjs")

    cw, ch = min(full.width, w), min(full.height, h)
    ref = np.asarray(full.convert("RGBA").crop((0, 0, cw, ch)))
    rec_m = np.asarray(rec_masked.convert("RGBA").crop((0, 0, cw, ch)))
    rec_t = np.asarray(rec_text.convert("RGBA").crop((0, 0, cw, ch)))
    text_mask = text_region_mask(default_layout, cw, ch)
    fx_mask, unemulated = unemulated_effect_mask(default_layout, cw, ch)
    masked_all = text_mask | fx_mask

    m_text_all = metrics(ref, rec_t, None)
    m_text_masked_regions = metrics(ref, rec_t, text_mask)
    m_masked = metrics(ref, rec_m, masked_all)
    m_no_text_all = metrics(ref, rec_m, None)
    worst10, _all = per_element_diffs(default_layout, ref, rec_t, cw, ch)
    worst10_masked, _ = per_element_diffs(default_layout, ref, rec_m, cw, ch)

    out_dir.mkdir(parents=True, exist_ok=True)
    rec_text.save(out_dir / "recomposite.png")
    rec_masked.save(out_dir / "recomposite_text_masked.png")
    write_side_by_side(
        ref, rec_t, out_dir / "validate_side_by_side.jpg",
        ("Photoshop full render", f"Recomposite + {renderer_used or 'no'} text (approx.)", "|diff| x3"),
    )
    write_side_by_side(
        ref, rec_m, out_dir / "validate_side_by_side_masked.jpg",
        ("Photoshop full render", "Recomposite, dynamic text omitted", "|diff| x3"),
    )

    state_previews = write_state_previews(layout, slices_dir, (w, h), out_dir, fonts_dir=fonts_dir)
    reuse_bad, reuse_ok, reuse_checked = reuse_region_checks(
        default_layout, ref, rec_m, masked_all, out_dir,
        tol_mae=thr["reuse_max_mae"], tol_pct=thr["reuse_max_pct_gt16"], slices_dir=slices_dir,
        decisions=layout.get("reuse_decisions") or [],
    )
    issues.extend(reuse_bad)
    split_bad, split_ok, split_checked = split_region_checks(
        default_layout, ref, rec_m, masked_all, out_dir, threshold=thr["split_match_psnr"],
        decisions=layout.get("reuse_decisions") or [],
    )
    issues.extend(split_bad)
    issues.extend(layer_bounds_issues(layout["nodes"]))
    placement_bad, placement_checked = placement_offset_checks(default_layout, ref, masked_all, slices_dir)
    issues.extend(placement_bad)
    exported_preview = write_exported_preview(default_layout, rec_text, full.crop((0, 0, cw, ch)), out_dir)

    rendered = sum(1 for p in placed_text if p.get("rendered"))
    mixed_nodes = [n["name"] for n in default_layout["nodes"]
                   if is_attr_text(n.get("kind")) and text_has_mixed_size_lines(n.get("text") or {})]
    if mixed_nodes:
        warn("note: %d attribute-only text node(s) have lines of different font sizes (%s). "
             "The extra line drop follows each layer's leadingType (leadingAbove k=0.86, leadingBelow 0). "
             "See references/slicing.md 10.7."
             % (len(mixed_nodes), ", ".join(mixed_nodes[:5])))
    report = {
        "artboard": art,
        "full_render": str(args.full_render),
        "full_render_size": list(full.size),
        "canvas": [w, h],
        "node_count": len(layout["nodes"]),
        "default_node_count": len(default_layout["nodes"]),
        "by_kind": dict(Counter(n["kind"] for n in default_layout["nodes"])),
        "fonts_dir": fonts_dir,
        "text_renderer": {
            "requested": text_opts["renderer"],
            "used": renderer_used,
            "options": {k: v for k, v in dict(text_render.DEFAULT_OPTIONS, **text_opts).items() if k != "renderer"},
            "legacy_v1_text_attrs": len(legacy_v1),
            "legacy_v1_nodes": legacy_v1,
            "mixed_size_line_nodes": mixed_nodes,
        },
        "dynamic_text_rendered": rendered,
        "dynamic_text_render_info": [
            {k: v for k, v in p.items() if k in (
                "name", "renderer", "fonts", "runs", "kern", "kern_reason",
                "effects_drawn", "effects_skipped_hidden", "effects_unrendered", "approximations", "skipped")}
            for p in text_infos
        ],
        "diff_text_rendered": m_text_all,
        "diff_text_rendered_excluding_text_boxes": m_text_masked_regions,
        "diff_text_masked": m_masked,
        "diff_no_text_unmasked": m_no_text_all,
        "worst_10_elements_text_rendered": worst10,
        "worst_10_elements_text_masked": worst10_masked[:10],
        "effect_modes": dict(Counter(n.get("effect_mode") or "-" for n in default_layout["nodes"])),
        "engine_effects_emulated": [
            {"name": p["name"], "effects": p["effects_emulated"]} for p in placed_masked if p.get("effects_emulated")
        ],
        "engine_effects_unemulated": unemulated,
        "exported_preview": exported_preview,
        "reuse_checked": reuse_checked,
        "reuse_mismatch_accepted": reuse_ok,
        "split_checked": split_checked,
        "placement_checked": len(placement_checked),
        "split_below_threshold_user_accepted": split_ok,
        "thresholds": thr,
        "manifest_lock": layout.get("manifest_lock"),
        "reuse_decisions": layout.get("reuse_decisions") or [],
        "state_previews": [
            {k: v for k, v in item.items() if k != "activation"} | {"activation": item["activation"]}
            for item in state_previews
        ],
        "notes": [
            "Default-state recomposite only (parts currently shown in the PSD) is compared to the PS render.",
            "diff_text_rendered: slices + an approximation of attribute-only text "
            "(dynamic_text / static_text, text_renderer.used) vs the PS render. "
            "Preview text is approximate; judge slice accuracy by diff_text_masked.",
            "text_renderer: freetype = HarfBuzz + FreeType (no hinting), skia = HarfBuzz + Skia paths. "
            "There is no fallback: missing packages or an unrenderable text stop validate with an error.",
            "legacy_v1_text_attrs > 0: the export log predates text_attrs_version 2 (single run, no effect "
            "visibility); re-export with the current export_slices.psjs for a closer text preview.",
            "diff_text_masked: slices only; attribute-only text boxes (dynamic_text / static_text) and unemulated engine-effect areas excluded from the metrics.",
            "effect_mode 'engine': DropShadow / OuterGlow are approximated with Pillow (normal blend, Gaussian blur); "
            "other engine effects are listed in engine_effects_unemulated and masked.",
            "preview_exported_annotated.png is drawn on the recomposite of the exported PNGs; show it to the user as the final check.",
            "Photoshop's own text rasteriser, blend modes and effect algorithms are not reproduced; residual error "
            "on dynamic_text / static_text is expected. For pixel-identical text, rasterise the text layers in Photoshop.",
            "visible_bounds use post-trim insets (before.left - after.left) so the PNG origin matches the tight opaque box.",
            "Alternate states are written to validate/states/<component>_<state>.png for visual check, not measured against the default render.",
            "split_checked: exported split parts recomposited vs the PS render over their union box; below "
            "thresholds.split_match_psnr is an issue unless a user decision is recorded (split_avatar).",
            "reuse_checked: MAE / % px diff>16 inside the shared image's opaque region; over the cap is an issue unless "
            "a user decision is recorded in reuse_decisions (reuse_accepted alone does not count).",
            "manifest_lock: build-layout records the confirmed-manifest lock; a change after confirmation is "
            "manifest_changed_after_confirm.",
            "pending_user_decision: a blocking reuse_decisions entry (layer_inclusion / exclude_visible_layer / "
            "hidden_layer_state / affects truth|export) without the user's answer recorded by confirm --decision.",
            "visible_layer_hidden / visible_layer_unassigned / exporter_modified: the full render must show what the "
            "document showed when opened (minus user-recorded exclusions), every visible pixel layer needs an entry "
            "or an exclusion, and the export must come from the shipped export_slices.psjs.",
            "uncertain_hidden_without_decision: an uncertain_hidden note whose layer_ids no blocking reuse_decisions "
            "entry (hidden_layer_state) covers; the note asks nobody.",
            "hidden_layer_exported_without_decision: a PNG entry uses a layer hidden at open (layers_at_open) as a "
            "state asset without the user's recorded export-as-state answer (a highlight over a base visible at "
            "open in the same component's default state is exempt; logs without layers_at_open are skipped).",
        ],
        "issues": issues,
        "placed": len(placed_text),
        "ok": not issues,
    }
    (out_dir / "validate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


TEXT_PROFILE_REMOVED = (
    "--text-profile was removed in 1.4.2. Text rules are always chosen per layer: leadingAbove layers use "
    "mixed_leading_k 0.86, a non-uniform layer transform turns off anti-alias gamma (references/slicing.md 10.7). "
    "Drop the option; to override the line drop use --mixed-leading-k."
)


class RemovedOption(argparse.Action):
    """Reject an option that no longer exists with a clear message instead of ignoring it."""

    def __call__(self, parser, namespace, values, option_string=None):
        parser.error(TEXT_PROFILE_REMOVED)


def build_parser():
    p = argparse.ArgumentParser(description="Agent Photoshop slicing helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("prepare", help="Write agent materials: render preview, crops, layer tree, nine-slice candidates")
    s.add_argument("--psd", required=True)
    s.add_argument("--out-dir", required=True)
    s.add_argument(
        "--artboard",
        default=None,
        help="Artboard name or layer_id. If omitted, every visible top-level artboard is processed "
             "(or the whole canvas when the document has none).",
    )
    s.set_defaults(func=cmd_prepare)

    s = sub.add_parser("split-avatar", help="Try to split avatar pictures from their frames (smart-object inner layers, frame instance)")
    s.add_argument("--psd", required=True)
    s.add_argument("--out-dir", required=True)
    s.add_argument("--candidates", help="reuse_candidates.json from prepare (its avatar_merge_suspects are tried)")
    s.add_argument("--layer-id", action="append", help="layer id to try (repeatable); default: all suspects")
    s.add_argument("--frame-instance", action="append", help="SUSPECT_ID:FRAME_ID, frame from an unmerged instance (fallback)")
    s.set_defaults(func=cmd_split_avatar)

    s = sub.add_parser("split-fix", help="Rebuild a two-part split (frame.png + content) from Photoshop exports; optionally emit overlay parts")
    s.add_argument("--merged", required=True, help="merged export (PATH or PATH#X,Y with --canvas)")
    s.add_argument("--content", required=True, help="content-side export, owner/group edge effects off")
    s.add_argument("--frame", required=True, help="plain frame export: owner/group edge effects off, overlay layers hidden")
    s.add_argument("--overlay", action="append", help="overlay export (one inner layer alone); baked into frame.png by default")
    s.add_argument("--overlay-blend", action="append", help="blend mode for each --overlay (normal, multiply, screen, linear_burn, lighten, darken, linear_dodge)")
    s.add_argument("--swap-content", action="append", help="another content image used to check the two-part approx (and as an extra LS sample); repeatable")
    s.add_argument("--probe", action="append", help="R,G,B=PATH (or #RRGGBB=PATH): the merged export with the content replaced by that solid colour "
                   "(same shape). >= 3 probes spanning dark and bright (e.g. black, white, gray): frame.png is fitted on these real "
                   "renders and checked leave-one-out; --overlay / --swap-content are then not needed. Preferred over the simulation.")
    s.add_argument("--emit-overlays", action="store_true", help="keep overlays as separate parts with blend_mode (exact replay) instead of baking them into frame.png")
    s.add_argument("--canvas", help="W,H of the shared canvas when inputs carry #X,Y offsets")
    s.add_argument("--threshold", type=float, default=None, help="dB gate (default 35 for two-part, 40 for --emit-overlays)")
    s.add_argument("--out-dir", required=True)
    s.set_defaults(func=cmd_split_fix)

    s = sub.add_parser("flatten", help="Flatten an agent component manifest into export entries")
    s.add_argument("--manifest", required=True)
    s.add_argument("--out", default=None, help="Defaults to overwriting --manifest")
    s.add_argument("--group-effects", default=None, help="group_effects.json (default: next to --manifest)")
    s.set_defaults(func=cmd_flatten)

    s = sub.add_parser("infer", help="Optional keyword draft only; writes draft_manifest.json for the agent to review")
    s.add_argument("--psd", required=True)
    s.add_argument("--out-dir", required=True)
    s.add_argument(
        "--artboard",
        default=None,
        help="Artboard name or layer_id. If omitted, every visible top-level artboard is processed "
             "(or the whole canvas when the document has none).",
    )
    s.add_argument(
        "--rules",
        default=None,
        help="Optional project naming-convention JSON. Never a substitute for the agent manifest.",
    )
    s.set_defaults(func=cmd_infer)

    s = sub.add_parser("preview", help="Redraw annotated preview from a (edited) manifest")
    s.add_argument("--manifest", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--ref", default=None, help="Optional artboard PNG to draw on")
    s.add_argument("--ref-is-document", action="store_true", help="Ref is full-document sized, not artboard-cropped")
    s.set_defaults(func=cmd_preview)

    s = sub.add_parser("confirm", help="After the user confirms: set status confirmed and lock the manifest")
    s.add_argument("--manifest", required=True)
    s.add_argument("--note", default=None, help="Who confirmed / what (kept in the lock file)")
    s.add_argument("--relock", action="store_true", help="Re-lock after the user re-confirmed a changed manifest")
    s.add_argument("--decision", action="append", default=[], metavar="ID=VALUE",
                   help="Record the user's own answer to a reuse_decisions entry (decided_by=user, appended to the "
                        "lock's decision_log); repeatable. A --note answers no decision")
    s.set_defaults(func=cmd_confirm)

    s = sub.add_parser("check-lock", help="Exit 3 if the manifest changed after confirm, 4 if not locked")
    s.add_argument("--manifest", required=True)
    s.set_defaults(func=cmd_check_lock)

    s = sub.add_parser("build-layout", help="Build engine-agnostic layout JSON from export results")
    s.add_argument("--manifest", required=True)
    s.add_argument("--export-log", required=True)
    s.add_argument("--slices-dir", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--group-effects", default=None, help="group_effects.json (default: next to --manifest)")
    s.set_defaults(func=cmd_build_layout)

    s = sub.add_parser("validate", help="Recomposite and compare against a full Photoshop render")
    s.add_argument("--layout", required=True)
    s.add_argument("--slices-dir", required=True)
    s.add_argument("--full-render", required=True)
    s.add_argument("--out-dir", required=True)
    s.add_argument("--fonts-dir", default=None, help="Directory of TTF/OTF files used to approximate attribute-only text")
    s.add_argument("--text-renderer", choices=list(text_render.RENDERERS), default="auto",
                   help="attribute-only text preview renderer: auto (freetype, else skia), freetype, skia. "
                        "Missing packages are an error, not a fallback")
    s.add_argument("--mixed-leading-k", type=float, default=None,
                   help="Override the extra drop of a following line as a fraction of its font-size change "
                        "(by default 0.86 on leadingAbove layers, 0 otherwise; references/slicing.md 10.7)")
    s.add_argument("--text-profile", nargs="?", action=RemovedOption, help=argparse.SUPPRESS)
    s.add_argument("--psd", default=None,
                   help="Optional PSD/PSB used only to read EngineData LeadingType when the layout omits leading_type. "
                        "Defaults to layout.source.file when that path exists")
    s.add_argument("--text-legacy-rules", action="store_true", default=False,
                   help="Draw attribute-only text with the 1.3.x rules (no anti-alias rules, legacy stroke / spread, "
                        "no faux bold / italic, mixed_leading_k 0.86) for comparison")
    s.add_argument("--snap-glyph-origin", dest="snap_glyph_origin", action="store_true", default=None,
                   help="Round glyph pen positions to whole pixels (default on)")
    s.add_argument("--no-snap-glyph-origin", dest="snap_glyph_origin", action="store_false",
                   help="Keep sub-pixel glyph positions")
    s.add_argument("--group-effects", default=None, help="Optional group_effects.json for the silent effect-loss check")
    s.add_argument("--reuse-tol-mae", type=float, default=None,
                   help="Override thresholds.reuse_max_mae (default 4): per-instance MAE cap for a reused image")
    s.add_argument("--reuse-tol-pct", type=float, default=None,
                   help="Override thresholds.reuse_max_pct_gt16 (default 1): per-instance %% of pixels with diff > 16")
    s.add_argument("--split-threshold", type=float, default=None,
                   help="Override thresholds.split_match_psnr (default 35 dB) for split parts vs the PS render")
    s.add_argument("--rules", default=None,
                   help="Project rules JSON; its \"thresholds\" object overrides the defaults (CLI flags win)")
    s.add_argument("--shipped-exporter", default=None,
                   help="The skill's examples/export_slices.psjs (default: next to slicing_helper.py); the export "
                        "log's exporter sha256 must match it (exporter_modified)")
    s.set_defaults(func=cmd_validate)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
