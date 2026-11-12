#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Approximate Photoshop point text for validate / preview images.

This is a preview renderer, not a Photoshop typography emulator. It reads the
dynamic_text attributes written to layout JSON (see references/slicing.md,
"dynamic_text attributes") and draws them with:

  * ``freetype`` (default): HarfBuzz shaping (uharfbuzz) + FreeType outlines,
    no hinting, outline filled directly; strokes with FreeType's round-join
    stroker.
  * ``skia``: HarfBuzz shaping + Skia glyph paths (skia-python).

There is no fallback renderer. When the packages a renderer needs are missing,
``select_renderer`` raises ``TextRendererUnavailable`` (with the pip command) and
validate stops before drawing any text. A text that cannot be rendered (font
not found, glyph load error) raises ``TextRenderError``; nothing is skipped or
redrawn with another renderer.

Install into the project virtualenv:

    pip install uharfbuzz freetype-py          # freetype renderer (auto prefers it)
    pip install uharfbuzz skia-python          # only for --text-renderer skia

Parameter reading (the large error source, not the rasterizer):

  1. Glyphs are laid out in text space (points) and mapped through the text
     layer's transform, so font size and leading are scaled by the matrix.
  2. Font, size, color and leading come from each style run, not the first run.
  3. When a later line uses a larger size than the previous one, Photoshop pushes
     it further down. ``mixed_leading_k`` (default 0.86 x size increase) is an
     empirical fit on a handful of two-line samples; it is not a documented
     Photoshop rule.
  4. Layer effects are drawn only when the layer's effects are visible and the
     effect itself is enabled.
  5. ``kerning: manual`` with no manual pairs disables the OpenType kern
     feature; tracking is applied as tracking/1000 em per glyph.
  6. Strokes are a round-join outset of the glyph outline; drop shadows /
     outer glows follow their size / spread / distance / angle.
  7. ``snap_glyph_origin`` (default on) rounds each glyph's pen position to a
     whole device pixel before rasterising. On the one sample set measured
     (Latin point text, one font family, up to two lines) this matched
     Photoshop far better than sub-pixel positioning; it is an observation, not
     a documented Photoshop rule. Turn it off with --no-snap-glyph-origin.

Everything not covered (warped text, paragraph wrapping, faux bold / italic,
horizontal / vertical scale, gradient strokes, blend modes other than normal,
inner effects except Color Overlay) is listed per text in the render info so
the report never hides it.
"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path

import numpy as np
from PIL import Image

try:  # optional
    import uharfbuzz as _hb  # type: ignore
except Exception as _exc:  # pragma: no cover - depends on the environment
    _hb = None
    _HB_ERR = f"uharfbuzz not importable: {_exc}"
else:
    _HB_ERR = None

try:  # optional
    import freetype as _ft  # type: ignore
except Exception as _exc:  # pragma: no cover
    _ft = None
    _FT_ERR = f"freetype-py not importable: {_exc}"
else:
    _FT_ERR = None

_SKIA = None
_SKIA_ERR = None


def _skia():
    global _SKIA, _SKIA_ERR
    if _SKIA is None and _SKIA_ERR is None:
        try:
            import skia  # type: ignore
            _SKIA = skia
        except Exception as exc:  # pragma: no cover
            _SKIA_ERR = f"skia-python not importable: {exc}"
    return _SKIA


try:
    from scipy import ndimage as _ndi  # type: ignore
except Exception:  # pragma: no cover
    _ndi = None

RENDERERS = ("auto", "freetype", "skia")
INSTALL_FREETYPE = "pip install uharfbuzz freetype-py"
INSTALL_SKIA = "pip install uharfbuzz skia-python"


class TextRendererUnavailable(RuntimeError):
    """The requested dynamic_text renderer cannot run (missing packages)."""

    def __init__(self, message, install):
        super().__init__(f"{message}. Install with: {install}")
        self.install = install


class TextRenderError(RuntimeError):
    """One dynamic_text could not be rendered (e.g. its font was not found)."""
DEFAULT_OPTIONS = {
    "renderer": "auto",
    "mixed_leading_k": 0.86,
    "auto_leading_pct": 1.2,
    "snap_glyph_origin": True,
    "shadow_sigma_div": 2.0,
}

# Photoshop layerEffects keys -> engine_effects type names used elsewhere in the skill.
PS_EFFECT_TYPES = {
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
STROKE_POSITIONS = {"outsetFrame": "outside", "insetFrame": "inside", "centeredFrame": "center"}
DRAWN_EFFECTS = {"Stroke", "DropShadow", "OuterGlow", "ColorOverlay"}


# ---------------------------------------------------------------------------
# Renderer selection
# ---------------------------------------------------------------------------

def select_renderer(requested="auto"):
    """Return the renderer to use: freetype or skia. auto = freetype, else skia.

    Raises TextRendererUnavailable (message + pip command) when the packages are missing,
    ValueError for an unknown name. Never falls back to another kind of renderer.
    """
    requested = (requested or "auto").lower()
    if requested not in RENDERERS:
        raise ValueError(f"unknown text renderer {requested!r}; choose one of {RENDERERS}")
    ft_ok = _hb is not None and _ft is not None
    if requested == "freetype" or (requested == "auto" and ft_ok):
        if ft_ok:
            return "freetype"
        missing = "; ".join(x for x in (_HB_ERR, _FT_ERR) if x)
        raise TextRendererUnavailable(f"dynamic_text renderer 'freetype' needs uharfbuzz and freetype-py ({missing})",
                                      INSTALL_FREETYPE)
    if _hb is not None and _skia() is not None:
        return "skia"
    missing = "; ".join(x for x in (_HB_ERR, _FT_ERR if requested == "auto" else None, _SKIA_ERR) if x)
    if requested == "auto":
        raise TextRendererUnavailable(
            "dynamic_text rendering needs uharfbuzz + freetype-py (or uharfbuzz + skia-python) "
            f"({missing})", INSTALL_FREETYPE)
    raise TextRendererUnavailable(f"dynamic_text renderer 'skia' needs uharfbuzz and skia-python ({missing})",
                                  INSTALL_SKIA)


# ---------------------------------------------------------------------------
# Attribute normalisation (layout JSON text dict -> render spec)
# ---------------------------------------------------------------------------

def _f(v, default=None):
    if isinstance(v, dict) and "_value" in v:
        v = v["_value"]
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def hex_rgb(s, default=(255, 255, 255)):
    if isinstance(s, (list, tuple)) and len(s) >= 3:
        return tuple(float(x) for x in s[:3])
    s = (s or "").lstrip("#") if isinstance(s, str) else ""
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) < 6:
        return tuple(float(x) for x in default)
    return (float(int(s[0:2], 16)), float(int(s[2:4], 16)), float(int(s[4:6], 16)))


def _unit_opacity(v, default=1.0):
    v = _f(v)
    if v is None:
        return default
    return max(0.0, min(1.0, v / 100.0 if v > 1.0 else v))


def matrix_of(text):
    tf = text.get("transform") or {}
    xx = _f(tf.get("xx"), 1.0)
    xy = _f(tf.get("xy"), 0.0)
    yx = _f(tf.get("yx"), 0.0)
    yy = _f(tf.get("yy"), 1.0)
    return (xx, xy, yx, yy)


def matrix_scale(m):
    """(sx, sy): lengths of the transformed unit x / y vectors."""
    xx, xy, yx, yy = m
    return math.hypot(xx, xy) or 1.0, math.hypot(yx, yy) or 1.0


def is_v2(text):
    return bool(text) and (int(text.get("text_attrs_version") or 1) >= 2 or bool(text.get("runs")))


def _norm_runs(text, m):
    _sx, sy = matrix_scale(m)
    base_color = text.get("color") or "#ffffff"
    runs = []
    for r in text.get("runs") or []:
        size_pt = _f(r.get("size_pt"))
        if size_pt is None and _f(r.get("size_px")) is not None:
            size_pt = _f(r.get("size_px")) / sy
        if size_pt is None:
            size_pt = (_f(text.get("size_pt")) or (_f(text.get("size"), 24.0) / sy))
        lead_pt = _f(r.get("leading_pt"))
        if lead_pt is None and _f(r.get("leading_px")) is not None:
            lead_pt = _f(r.get("leading_px")) / sy
        runs.append({
            "from": int(r.get("from") or 0),
            "to": int(r.get("to") if r.get("to") is not None else 1 << 30),
            "font": r.get("font") or text.get("font"),
            "size_pt": size_pt,
            "color": hex_rgb(r.get("color") or base_color),
            "tracking": _f(r.get("tracking"), 0.0) or 0.0,
            "auto_leading": r.get("auto_leading") if r.get("auto_leading") is not None else (lead_pt is None),
            "leading_pt": lead_pt,
            "baseline_shift_pt": _f(r.get("baseline_shift_pt"), 0.0) or 0.0,
            "faux_bold": bool(r.get("faux_bold")),
            "faux_italic": bool(r.get("faux_italic")),
            "horizontal_scale": _f(r.get("horizontal_scale"), 100.0),
            "vertical_scale": _f(r.get("vertical_scale"), 100.0),
            "auto_kern": r.get("auto_kern"),
        })
    return runs


def _legacy_runs(text, m):
    """v1 attributes: one run; ``size`` was already multiplied by the y scale, ``leading`` was not."""
    _sx, sy = matrix_scale(m)
    size_px = _f(text.get("size"), 24.0)
    lead = _f(text.get("leading"))
    auto = text.get("auto_leading")
    if auto is None:
        auto = lead is None
    return [{
        "from": 0, "to": 1 << 30,
        "font": text.get("font"),
        "size_pt": size_px / sy,
        "color": hex_rgb(text.get("color") or "#ffffff"),
        "tracking": _f(text.get("tracking"), 0.0) or 0.0,
        "auto_leading": bool(auto) or lead is None,
        "leading_pt": lead,
        "baseline_shift_pt": 0.0,
        "faux_bold": False, "faux_italic": False,
        "horizontal_scale": 100.0, "vertical_scale": 100.0,
        "auto_kern": text.get("kerning"),
    }]


def _norm_effect(e):
    if not isinstance(e, dict):
        return None
    t = e.get("type") or ""
    t = PS_EFFECT_TYPES.get(t, t)
    out = dict(e)
    out["type"] = t
    out["enabled"] = e.get("enabled") is not False
    if t == "Stroke":
        pos = str(e.get("position") or "outside")
        out["position"] = STROKE_POSITIONS.get(pos, pos).lower()
    return out


def text_effects(text):
    """(active, hidden) effect lists. hidden entries carry a ``reason``."""
    active, hidden = [], []
    if "layer_effects" in text or "effects_visible" in text:
        visible = text.get("effects_visible") is not False
        for raw in text.get("layer_effects") or []:
            e = _norm_effect(raw)
            if e is None:
                continue
            if not visible:
                hidden.append(dict(e, reason="layer effects hidden"))
            elif not e["enabled"]:
                hidden.append(dict(e, reason="effect disabled"))
            else:
                active.append(e)
        return active, hidden
    # v1: only the stroke was exported
    st = text.get("stroke")
    if isinstance(st, dict) and st.get("size"):
        e = _norm_effect(dict(st, type="Stroke"))
        (active if e["enabled"] else hidden).append(e if e["enabled"] else dict(e, reason="effect disabled"))
    return active, hidden


def kern_enabled(text, runs=None):
    """(kern feature on?, reason). manual kerning without manual pairs -> off."""
    mode = text.get("kerning")
    if mode is None and runs:
        mode = runs[0].get("auto_kern")
    mode = str(mode or "").lower()
    manual_pairs = int(text.get("manual_kerning_ranges") or 0)
    if mode in ("", "manual"):
        if manual_pairs:
            return False, "manual kerning pairs are not exported; kern off"
        return False, "kerning manual, no manual pairs"
    if mode in ("metrics", "metricskern"):
        return True, "metrics kerning"
    if mode in ("optical", "opticalkern"):
        return True, "optical kerning approximated with font kern pairs"
    return False, f"unknown kerning mode {mode!r}; kern off"


def text_spec(text, *, doc_size=None, art_origin=(0, 0), bounds=None, options=None):
    """Normalise a layout JSON ``text`` dict into what the renderers need."""
    opts = dict(DEFAULT_OPTIONS, **(options or {}))
    text = text or {}
    content = str(text.get("content") or "")
    m = matrix_of(text)
    runs = _norm_runs(text, m) if text.get("runs") else _legacy_runs(text, m)
    paragraphs = []
    for p in text.get("paragraphs") or []:
        paragraphs.append({
            "from": int(p.get("from") or 0),
            "to": int(p.get("to") if p.get("to") is not None else 1 << 30),
            "align": str(p.get("align") or "left").lower(),
            "auto_leading_pct": _f(p.get("auto_leading_pct")),
        })
    if not paragraphs:
        paragraphs = [{"from": 0, "to": 1 << 30, "align": str(text.get("alignment") or "left").lower(),
                       "auto_leading_pct": _f(text.get("auto_leading_pct"))}]
    ox, oy = (art_origin or (0, 0))[:2]
    cp = text.get("click_point") or {}
    dw, dh = (doc_size or (None, None))[:2] if doc_size else (None, None)
    anchor_from = "click_point"
    if cp and cp.get("horizontal") is not None and dw and dh:
        ax = _f(cp["horizontal"]) / 100.0 * dw - ox
        ay = _f(cp["vertical"]) / 100.0 * dh - oy
    else:
        anchor_from = "bounds"
        b = bounds or {}
        align0 = paragraphs[0]["align"]
        ax = b.get("left", 0.0)
        if "center" in align0:
            ax = (b.get("left", 0.0) + b.get("right", 0.0)) / 2.0
        elif "right" in align0:
            ax = b.get("right", 0.0)
        ay = b.get("bottom", 0.0)
    active, hidden = text_effects(text)
    kern, kern_reason = kern_enabled(text, runs)
    opacity = _unit_opacity(text.get("opacity"), 1.0)
    fill_opacity = _unit_opacity(text.get("fill_opacity"), 1.0)
    return {
        "content": content,
        "matrix": m,
        "anchor": (ax, ay),
        "anchor_from": anchor_from,
        "runs": runs,
        "paragraphs": paragraphs,
        "kern": kern,
        "kern_reason": kern_reason,
        "effects": active,
        "effects_hidden": hidden,
        "opacity": opacity,
        "fill_opacity": fill_opacity,
        "anti_alias": text.get("anti_alias"),
        "warp": text.get("warp"),
        "shape": text.get("shape") or "point",
        "box_bounds_pt": text.get("box_bounds_pt"),
        "version": 2 if is_v2(text) else 1,
        "options": opts,
    }


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

_FONT_INDEX = {}


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def iter_font_files(fonts_dir):
    if not fonts_dir:
        return
    root = Path(fonts_dir)
    folders = [root]
    if root.is_dir():
        for extra in (root.parent / "fonts_install", root.parent / "fonts"):
            if extra.is_dir() and extra not in folders:
                folders.append(extra)
    seen = set()
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() in (".ttf", ".otf", ".ttc") and path not in seen:
                seen.add(path)
                yield path


def _ps_name(path):
    if _ft is None:
        return None
    try:
        name = _ft.Face(str(path)).postscript_name
        return name.decode("latin-1") if isinstance(name, bytes) else name
    except Exception:
        return None


def find_font_file(name, fonts_dir=None):
    """(path or None, how). Exact file stem / PostScript name first, then partial file-stem match.

    No substitute font: returns (None, "none") when nothing matches.
    """
    key = (str(fonts_dir or ""), name or "")
    if key in _FONT_INDEX:
        return _FONT_INDEX[key]
    files = list(iter_font_files(fonts_dir))
    needle = _norm(name)
    found = (None, "none")
    if needle:
        for p in files:
            if _norm(p.stem) == needle:
                found = (p, "file name")
                break
        if found[0] is None:
            for p in files:
                if _norm(_ps_name(p)) == needle:
                    found = (p, "postscript name")
                    break
        if found[0] is None:
            for p in files:
                stem = _norm(p.stem)
                if stem and (needle in stem or stem in needle):
                    found = (p, "partial file name")
                    break
    _FONT_INDEX[key] = found
    return found


# ---------------------------------------------------------------------------
# Shaping / layout in text space (points)
# ---------------------------------------------------------------------------

class _HBFont:
    _cache = {}

    def __init__(self, path):
        blob = _hb.Blob.from_file_path(str(path))
        face = _hb.Face(blob)
        self.font = _hb.Font(face)
        self.upem = face.upem
        try:
            ext = self.font.get_font_extents("ltr")
            self.ascender = ext.ascender / self.upem
        except Exception:
            self.ascender = 0.8

    @classmethod
    def get(cls, path):
        key = str(path)
        if key not in cls._cache:
            cls._cache[key] = cls(path)
        return cls._cache[key]

    def shape(self, s, size, kern):
        buf = _hb.Buffer()
        buf.add_str(s)
        buf.guess_segment_properties()
        _hb.shape(self.font, buf, {"kern": bool(kern), "liga": True})
        k = size / self.upem
        return [(i.codepoint, p.x_advance * k, p.x_offset * k, p.y_offset * k)
                for i, p in zip(buf.glyph_infos, buf.glyph_positions)]


def _run_at(runs, i):
    for r in runs:
        if r["from"] <= i < r["to"]:
            return r
    return runs[-1]


def _para_at(paras, i):
    for p in paras:
        if p["from"] <= i < p["to"]:
            return p
    return paras[-1]


def line_advance(prev_line_runs, line_runs, opts, auto_pct=None):
    """Baseline-to-baseline distance (pt) from the previous line to this one.

    Leading is the largest leading on the line (auto leading = auto_pct x size).
    When this line's largest size exceeds the previous line's, add
    ``mixed_leading_k`` x the increase (empirical, see module docstring).
    """
    pct = auto_pct or opts.get("auto_leading_pct") or 1.2
    leads = [(r["size_pt"] * pct) if (r["auto_leading"] or r["leading_pt"] is None) else r["leading_pt"]
             for r in line_runs]
    adv = max(leads)
    grow = max(r["size_pt"] for r in line_runs) - max(r["size_pt"] for r in prev_line_runs)
    if grow > 0:
        adv += float(opts.get("mixed_leading_k") or 0.0) * grow
    return adv


def layout_glyphs(spec, shaper_for, fonts_dir=None, resolve_font=None):
    """Glyphs in text space: dicts(font_path, size_pt, gid, x, y, color, line).

    *shaper_for(path)* returns an object with ``shape(s, size, kern)`` and ``ascender``.
    *resolve_font(name, fonts_dir)* -> (path, how); defaults to find_font_file. A run whose
    font cannot be found raises TextRenderError.
    y is the baseline offset in points (down positive).
    """
    opts = spec["options"]
    runs, paras = spec["runs"], spec["paragraphs"]
    text = spec["content"].replace("\r\n", "\r").replace("\n", "\r")
    lines = text.split("\r")
    glyphs, fonts_used = [], {}
    pos, base = 0, 0.0
    prev_runs = None
    first_ascent = None
    for li, line in enumerate(lines):
        segs, i = [], 0
        while i < len(line):
            r = _run_at(runs, pos + i)
            j = i
            while j < len(line) and _run_at(runs, pos + j) is r:
                j += 1
            segs.append((line[i:j], r))
            i = j
        line_runs = [r for _, r in segs] or [_run_at(runs, pos)]
        para = _para_at(paras, pos)
        if li > 0:
            base += line_advance(prev_runs, line_runs, opts, para.get("auto_leading_pct"))
        x = 0.0
        lg = []
        for s, r in segs:
            path, how = (resolve_font or find_font_file)(r["font"], fonts_dir)
            if path is None:
                raise TextRenderError(
                    f"font {r['font']!r} not found in fonts_dir {fonts_dir!r}; pass --fonts-dir with the design's fonts")
            fonts_used[r["font"] or ""] = {"file": str(path) if path else None, "match": how}
            shaper = shaper_for(path)
            if first_ascent is None:
                first_ascent = shaper.ascender * r["size_pt"]
            size = r["size_pt"]
            for gid, adv, xo, yo in shaper.shape(s, size, spec["kern"]):
                lg.append({"font_path": path, "size_pt": size, "gid": gid, "x": x + xo,
                           "y": base - yo - r["baseline_shift_pt"], "color": r["color"], "line": li})
                x += adv + r["tracking"] / 1000.0 * size
        width = x
        align = para["align"]
        dx = -width / 2.0 if "center" in align else (-width if "right" in align else 0.0)
        for g in lg:
            g["x"] += dx
        glyphs.extend(lg)
        prev_runs = line_runs
        pos += len(line) + 1
    return glyphs, fonts_used, (first_ascent or 0.0)


def to_device(spec, x, y):
    xx, xy, yx, yy = spec["matrix"]
    ax, ay = spec["anchor"]
    return ax + xx * x + yx * y, ay + xy * x + yy * y


# ---------------------------------------------------------------------------
# Rasterisers: coverage arrays in a local box (float 0..1)
# ---------------------------------------------------------------------------

class Box:
    def __init__(self, x0, y0, w, h):
        self.x0, self.y0, self.w, self.h = int(x0), int(y0), int(w), int(h)

    def zeros(self):
        return np.zeros((self.h, self.w), dtype=np.float64)


def estimate_box(spec, glyphs, pad):
    pts = []
    for g in glyphs:
        s = g["size_pt"]
        for gx, gy in ((g["x"] - 0.3 * s, g["y"] - 1.3 * s), (g["x"] + 1.3 * s, g["y"] + 0.5 * s)):
            pts.append(to_device(spec, gx, gy))
            pts.append(to_device(spec, gx, g["y"]))
        for gx, gy in ((g["x"] - 0.3 * s, g["y"] + 0.5 * s), (g["x"] + 1.3 * s, g["y"] - 1.3 * s)):
            pts.append(to_device(spec, gx, gy))
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, y0 = math.floor(min(xs) - pad), math.floor(min(ys) - pad)
    x1, y1 = math.ceil(max(xs) + pad), math.ceil(max(ys) + pad)
    return Box(x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def _blit_union(cov, arr, x0, y0):
    h, w = cov.shape
    rows, cols = arr.shape
    ys, xs = max(0, y0), max(0, x0)
    ye, xe = min(h, y0 + rows), min(w, x0 + cols)
    if ye <= ys or xe <= xs:
        return
    sub = arr[ys - y0:ye - y0, xs - x0:xe - x0]
    reg = cov[ys:ye, xs:xe]
    cov[ys:ye, xs:xe] = reg + sub - reg * sub


def _origin(spec, g, off, snap):
    X, Y = to_device(spec, g["x"], g["y"])
    X, Y = X + off[0], Y + off[1]
    if snap:
        X, Y = float(round(X)), float(round(Y))
    return X, Y


class FreeTypeRaster:
    name = "freetype"
    _faces = {}

    def __init__(self, spec):
        self.spec = spec
        self.snap = bool(spec["options"].get("snap_glyph_origin"))

    @classmethod
    def face(cls, path):
        key = str(path)
        if key not in cls._faces:
            cls._faces[key] = _ft.Face(key)
        return cls._faces[key]

    def _load(self, g, box, off, flags):
        xx, xy, yx, yy = self.spec["matrix"]
        X, Y = _origin(self.spec, g, off, self.snap)
        X, Y = X - box.x0, Y - box.y0
        ix, iy = math.floor(X), math.floor(Y)
        face = self.face(g["font_path"])
        face.set_char_size(int(round(g["size_pt"] * 64)))
        # FreeType is y-up: x' = xx*x + yx*y_down -> (xx, -yx; -xy, yy)
        mat = _ft.Matrix(int(round(xx * 0x10000)), int(round(-yx * 0x10000)),
                         int(round(-xy * 0x10000)), int(round(yy * 0x10000)))
        vec = _ft.Vector(int(round((X - ix) * 64)), int(round(-(Y - iy) * 64)))
        face.set_transform(mat, vec)
        face.load_glyph(g["gid"], flags)
        return face, ix, iy

    @staticmethod
    def _bitmap_array(bm):
        if bm.width == 0 or bm.rows == 0:
            return None
        return np.array(bm.buffer, dtype=np.float64).reshape(bm.rows, bm.pitch)[:, :bm.width] / 255.0

    def fill(self, glyphs, box, off=(0.0, 0.0)):
        """Per color: list of (rgb, coverage)."""
        layers = {}
        order = []
        for g in glyphs:
            face, ix, iy = self._load(g, box, off, _ft.FT_LOAD_NO_HINTING | _ft.FT_LOAD_RENDER)
            arr = self._bitmap_array(face.glyph.bitmap)
            if arr is None:
                continue
            key = tuple(g["color"])
            if key not in layers:
                layers[key] = box.zeros()
                order.append(key)
            _blit_union(layers[key], arr, ix + face.glyph.bitmap_left, iy - face.glyph.bitmap_top)
        return [(k, layers[k]) for k in order]

    def stroke(self, glyphs, box, radius, off=(0.0, 0.0)):
        """Round-join stroke-and-fill coverage (outline grown by *radius* px)."""
        cov = box.zeros()
        stroker = _ft.Stroker()
        stroker.set(int(round(radius * 64)), _ft.FT_STROKER_LINECAP_ROUND, _ft.FT_STROKER_LINEJOIN_ROUND, 0)
        for g in glyphs:
            face, ix, iy = self._load(g, box, off, _ft.FT_LOAD_NO_HINTING | _ft.FT_LOAD_NO_BITMAP)
            for do_stroke in (True, False):
                glyph = face.glyph.get_glyph()
                if do_stroke:
                    glyph.stroke(stroker, True)
                bg = glyph.to_bitmap(_ft.FT_RENDER_MODE_NORMAL, _ft.Vector(0, 0), True)
                arr = self._bitmap_array(bg.bitmap)
                if arr is not None:
                    _blit_union(cov, arr, ix + bg.left, iy - bg.top)
        return cov


class SkiaRaster:
    name = "skia"
    _tf = {}

    def __init__(self, spec):
        self.spec = spec
        self.snap = bool(spec["options"].get("snap_glyph_origin"))
        self.sk = _skia()

    def _typeface(self, path):
        key = str(path)
        if key not in self._tf:
            self._tf[key] = self.sk.Typeface.MakeFromFile(key)
        return self._tf[key]

    def _path(self, glyphs, box, off):
        sk = self.sk
        xx, xy, yx, yy = self.spec["matrix"]
        out = sk.Path()
        for g in glyphs:
            f = sk.Font(self._typeface(g["font_path"]), g["size_pt"])
            f.setHinting(sk.FontHinting.kNone)
            f.setLinearMetrics(True)
            f.setSubpixel(True)
            gp = f.getPath(g["gid"])
            if gp is None:
                continue
            X, Y = _origin(self.spec, g, off, self.snap)
            m = sk.Matrix.MakeAll(xx, yx, X - box.x0, xy, yy, Y - box.y0, 0, 0, 1)
            gp.transform(m)
            out.addPath(gp)
        return out

    def _draw(self, path, box, paint):
        sk = self.sk
        surf = sk.Surface.MakeRaster(sk.ImageInfo.Make(box.w, box.h, sk.ColorType.kRGBA_8888_ColorType,
                                                        sk.AlphaType.kPremul_AlphaType))
        surf.getCanvas().drawPath(path, paint)
        return surf.makeImageSnapshot().toarray()[..., 3].astype(np.float64) / 255.0

    def fill(self, glyphs, box, off=(0.0, 0.0)):
        sk = self.sk
        by, order = {}, []
        for g in glyphs:
            key = tuple(g["color"])
            if key not in by:
                by[key] = []
                order.append(key)
            by[key].append(g)
        paint = sk.Paint(AntiAlias=True, Color=sk.ColorWHITE)
        return [(k, self._draw(self._path(by[k], box, off), box, paint)) for k in order]

    def stroke(self, glyphs, box, radius, off=(0.0, 0.0)):
        sk = self.sk
        paint = sk.Paint(AntiAlias=True, Color=sk.ColorWHITE, Style=sk.Paint.kStrokeAndFill_Style,
                         StrokeWidth=2 * radius, StrokeJoin=sk.Paint.kRound_Join)
        return self._draw(self._path(glyphs, box, off), box, paint)


# ---------------------------------------------------------------------------
# Effects and composition
# ---------------------------------------------------------------------------

def _dilate(a, r):
    if r <= 0:
        return a
    if _ndi is not None:
        d = _ndi.distance_transform_edt(a < 0.5)
        return np.clip(np.maximum(a, r + 0.5 - d), 0.0, 1.0)
    from PIL import ImageFilter
    im = Image.fromarray((a * 255).astype(np.uint8), "L").filter(ImageFilter.MaxFilter(min(2 * int(round(r)) + 1, 51)))
    return np.asarray(im, dtype=np.float64) / 255.0


def _blur(a, sigma):
    if sigma <= 0.25:
        return a
    if _ndi is not None:
        return _ndi.gaussian_filter(a, sigma)
    from PIL import ImageFilter
    im = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(sigma))
    return np.asarray(im, dtype=np.float64) / 255.0


def _union(covs, shape):
    out = np.zeros(shape, dtype=np.float64)
    for c in covs:
        out = out + c - out * c
    return out


def _over(dst_a, dst_c, src_a, src_rgb):
    """Straight-alpha src-over. dst_c HxWx3, src_rgb (3,) or HxWx3."""
    src_rgb = np.asarray(src_rgb, dtype=np.float64)
    if src_rgb.ndim == 1:
        src_rgb = src_rgb[None, None, :]
    na = src_a + dst_a * (1.0 - src_a)
    num = src_rgb * src_a[..., None] + dst_c * (dst_a * (1.0 - src_a))[..., None]
    c = np.where(na[..., None] > 1e-9, num / np.maximum(na[..., None], 1e-9), 0.0)
    return na, c


def effect_outset(spec):
    pad = 2.0
    for e in spec["effects"]:
        t = e["type"]
        size = _f(e.get("size"), 0.0) or 0.0
        if t == "Stroke":
            pad = max(pad, size + 2)
        elif t == "DropShadow":
            pad = max(pad, size * 2 + abs(_f(e.get("distance"), 0.0) or 0.0) + 2)
        elif t == "OuterGlow":
            pad = max(pad, size * 2 + 2)
    return pad


def _shadow_offset(e):
    d = _f(e.get("distance"), 0.0) or 0.0
    ang = math.radians(_f(e.get("angle"), 120.0) if e.get("angle") is not None else 120.0)
    return -d * math.cos(ang), d * math.sin(ang)


def render_spec(spec, renderer, fonts_dir=None):
    """Render to (RGBA uint8 image, (x0, y0) artboard position, info)."""
    info = {"renderer": renderer, "effects_drawn": [], "effects_unrendered": [], "approximations": []}
    if renderer == "freetype":
        raster, shaper = FreeTypeRaster(spec), _HBFont.get
    elif renderer == "skia":
        raster, shaper = SkiaRaster(spec), _HBFont.get
    else:
        raise ValueError(f"unknown renderer {renderer!r}")
    glyphs, fonts_used, _asc = layout_glyphs(spec, shaper, fonts_dir)
    info["fonts"] = fonts_used
    info["runs"] = len(spec["runs"])
    info["kern"] = spec["kern"]
    info["kern_reason"] = spec["kern_reason"]
    info["effects_skipped_hidden"] = [{"type": e["type"], "reason": e.get("reason")} for e in spec["effects_hidden"]]
    if any(v["match"] == "partial file name" for v in fonts_used.values()):
        info["approximations"].append("font matched by partial file name; check fonts[*].file")
    if spec.get("warp") and str(spec["warp"]).lower() not in ("warpnone", "none"):
        info["approximations"].append(f"warp {spec['warp']} not rendered")
    if spec.get("shape") == "box":
        info["approximations"].append("paragraph (box) text: no wrapping; laid out as point text from its anchor")
    for r in spec["runs"]:
        if r["faux_bold"] or r["faux_italic"]:
            info["approximations"].append("faux bold / italic not rendered")
            break
    for r in spec["runs"]:
        if abs((r["horizontal_scale"] or 100) - 100) > 0.01 or abs((r["vertical_scale"] or 100) - 100) > 0.01:
            info["approximations"].append("horizontal / vertical character scale not rendered")
            break
    if not glyphs:
        return None, (0, 0), dict(info, skipped="no glyphs")
    box = estimate_box(spec, glyphs, effect_outset(spec))
    shape = (box.h, box.w)
    fills = raster.fill(glyphs, box)
    fill_union = _union([c for _, c in fills], shape)

    A = np.zeros(shape)
    C = np.zeros(shape + (3,))
    effs = spec["effects"]
    overlay = [e for e in effs if e["type"] == "ColorOverlay"]
    for e in effs:
        if e["type"] not in DRAWN_EFFECTS:
            info["effects_unrendered"].append(e["type"])
        elif (e.get("blend_mode") or e.get("mode") or "normal") not in ("normal", None):
            info["approximations"].append(f"{e['type']} blend mode {e.get('blend_mode') or e.get('mode')} drawn as normal")
    sigma_div = float(spec["options"].get("shadow_sigma_div") or 2.0)
    # 1. outer effects under the fill
    for e in effs:
        t = e["type"]
        if t not in ("DropShadow", "OuterGlow"):
            continue
        size = _f(e.get("size"), 0.0) or 0.0
        spread = max(0.0, min(100.0, _f(e.get("spread"), 0.0) or 0.0)) / 100.0
        if t == "DropShadow":
            off = _shadow_offset(e)
            base = _union([c for _, c in raster.fill(glyphs, box, off)], shape)
        else:
            base = fill_union
        a = _dilate(base, size * spread)
        a = _blur(a, size * (1.0 - spread) / sigma_div)
        a = np.clip(a, 0, 1) * _unit_opacity(e.get("opacity"), 0.75)
        A, C = _over(A, C, a, hex_rgb(e.get("color") or ("#000000" if t == "DropShadow" else "#ffffff")))
        info["effects_drawn"].append(t)
    strokes = [e for e in effs if e["type"] == "Stroke"]
    for e in strokes:
        fill_type = str(e.get("fill_type") or "solidcolor").lower()
        if "solid" not in fill_type:
            info["approximations"].append(f"stroke fill {fill_type} drawn as solid color")
    # 2. outside strokes under the fill
    for e in strokes:
        if e.get("position", "outside") != "outside":
            continue
        a = raster.stroke(glyphs, box, _f(e.get("size"), 0.0) or 0.0) * _unit_opacity(e.get("opacity"), 1.0)
        A, C = _over(A, C, a, hex_rgb(e.get("color") or "#000000"))
        info["effects_drawn"].append("Stroke")
    # 3. fill (fill opacity applies to fill + color overlay only)
    fo = spec["fill_opacity"]
    for rgb, cov in fills:
        rgb = np.asarray(rgb, dtype=np.float64)
        for e in overlay:
            op = _unit_opacity(e.get("opacity"), 1.0)
            rgb = rgb * (1 - op) + np.asarray(hex_rgb(e.get("color") or "#000000")) * op
        A, C = _over(A, C, cov * fo, rgb)
    if overlay:
        info["effects_drawn"].append("ColorOverlay")
    # 4. center / inside strokes over the fill
    for e in strokes:
        pos = e.get("position", "outside")
        if pos == "outside":
            continue
        size = _f(e.get("size"), 0.0) or 0.0
        if pos == "center":
            # band straddling the outline: grown by size/2 minus shrunk by size/2
            ring = raster.stroke(glyphs, box, size / 2.0)
            a = np.clip(ring - _erode(fill_union, size / 2.0), 0, 1)
        else:
            a = np.clip(fill_union - _erode(fill_union, size), 0, 1)
            info["approximations"].append("inside stroke approximated by erosion")
        a = a * _unit_opacity(e.get("opacity"), 1.0)
        A, C = _over(A, C, a, hex_rgb(e.get("color") or "#000000"))
        info["effects_drawn"].append("Stroke")
    A = A * spec["opacity"]
    rgba = np.dstack([np.clip(C, 0, 255), np.clip(A * 255.0, 0, 255)]).round().astype(np.uint8)
    return Image.fromarray(rgba, "RGBA"), (box.x0, box.y0), info


def _erode(a, r):
    if r <= 0:
        return a
    return 1.0 - _dilate(1.0 - a, r)


def render_text(text, *, renderer="auto", fonts_dir=None, doc_size=None, art_origin=(0, 0), bounds=None, options=None):
    """Convenience wrapper: (image, xy, info) for one layout JSON text dict."""
    used = select_renderer(renderer)
    spec = text_spec(text, doc_size=doc_size, art_origin=art_origin, bounds=bounds, options=options)
    im, xy, info = render_spec(spec, used, fonts_dir)
    info["requested_renderer"] = renderer
    return im, xy, info


# ---------------------------------------------------------------------------
# Photoshop layer descriptor -> layout JSON text attributes (v2)
# Mirrors parseText() in export_slices.psjs; used by tests and by pipelines that
# already hold batchPlay layer descriptors.
# ---------------------------------------------------------------------------

def _ev(x):
    if isinstance(x, dict):
        if "_value" in x:
            return x["_value"]
        if "_enum" in x and "_value" in x:
            return x["_value"]
    return x


def _rgb_hex(c):
    if not isinstance(c, dict):
        return None
    r = _f(c.get("red"), 0.0)
    g = _f(c.get("grain"), None)
    if g is None:
        g = _f(c.get("green"), 0.0)
    b = _f(c.get("blue"), 0.0)
    return "#" + "".join(f"{int(round(max(0.0, min(255.0, v)))):02x}" for v in (r, g, b))


def effects_from_descriptor(desc):
    """(effects_visible, [effect dicts]) from a layer descriptor's layerEffects. Every present effect is listed."""
    fx = (desc or {}).get("layerEffects") or {}
    visible = (desc or {}).get("layerFXVisible")
    visible = True if visible is None else bool(visible)
    global_angle = _f((desc or {}).get("globalAngle"), 90.0)
    out = []
    for key, val in fx.items():
        base = key[:-5] if key.endswith("Multi") else key
        if base not in PS_EFFECT_TYPES:
            continue
        for e in (val if isinstance(val, list) else [val]):
            if not isinstance(e, dict) or e.get("present") is False:
                continue
            item = {"type": PS_EFFECT_TYPES[base], "enabled": e.get("enabled") is not False}
            mode = _ev(e.get("mode"))
            if mode:
                item["blend_mode"] = str(mode).lower()
            if e.get("color") is not None:
                item["color"] = _rgb_hex(e.get("color"))
            if e.get("opacity") is not None:
                item["opacity"] = round(_unit_opacity(e.get("opacity"), 1.0), 4)
            if base == "frameFX":
                item["size"] = _f(e.get("size"), 0.0)
                st = _ev(e.get("style")) or "outsetFrame"
                item["position"] = STROKE_POSITIONS.get(st, st)
                item["fill_type"] = _ev(e.get("paintType")) or "solidColor"
            elif base in ("dropShadow", "innerShadow"):
                item["size"] = _f(e.get("blur"), 0.0)
                item["distance"] = _f(e.get("distance"), 0.0)
                item["spread" if base == "dropShadow" else "choke"] = _f(e.get("chokeMatte"), 0.0)
                use_global = bool(e.get("useGlobalAngle"))
                item["angle"] = global_angle if use_global else _f(e.get("localLightingAngle"), 120.0)
                item["use_global_light"] = use_global
            elif base in ("outerGlow", "innerGlow"):
                item["size"] = _f(e.get("blur"), 0.0)
                item["spread" if base == "outerGlow" else "choke"] = _f(e.get("chokeMatte"), 0.0)
            out.append(item)
    return visible, out


def text_attrs_from_descriptor(desc):
    """v2 dynamic_text attributes from a batchPlay layer descriptor (``get`` on a text layer)."""
    tk = (desc or {}).get("textKey")
    if not isinstance(tk, dict):
        return None
    content = tk.get("textKey") if isinstance(tk.get("textKey"), str) else (tk.get("text") or "")
    tf = tk.get("transform") or {}
    m = (_f(tf.get("xx"), 1.0), _f(tf.get("xy"), 0.0), _f(tf.get("yx"), 0.0), _f(tf.get("yy"), 1.0))
    sx, sy = matrix_scale(m)
    runs = []
    for r in tk.get("textStyleRange") or []:
        st = r.get("textStyle") or {}
        size_pt = _f(st.get("size"))
        implied = _f(st.get("impliedFontSize"))
        lead_pt = _f(st.get("leading"))
        implied_lead = _f(st.get("impliedLeading"))
        auto_lead = st.get("autoLeading")
        auto_lead = True if auto_lead is None else bool(auto_lead)
        runs.append({
            "from": int(r.get("from") or 0),
            "to": int(r.get("to") or 0),
            "font": st.get("fontPostScriptName") or st.get("fontName"),
            "size_pt": size_pt,
            "size_px": implied if implied is not None else (size_pt * sy if size_pt is not None else None),
            "color": _rgb_hex(st.get("color")) or "#000000",
            "tracking": _f(st.get("tracking"), 0.0) or 0.0,
            "auto_leading": auto_lead,
            "leading_pt": None if auto_lead else lead_pt,
            "leading_px": None if auto_lead else (implied_lead if implied_lead is not None
                                                  else (lead_pt * sy if lead_pt is not None else None)),
            "baseline_shift_pt": _f(st.get("baselineShift"), 0.0) or 0.0,
            "faux_bold": bool(st.get("syntheticBold")),
            "faux_italic": bool(st.get("syntheticItalic")),
            "horizontal_scale": _f(st.get("horizontalScale"), 100.0),
            "vertical_scale": _f(st.get("verticalScale"), 100.0),
            "auto_kern": _ev(st.get("autoKern")) or "manual",
        })
    paras = []
    for p in tk.get("paragraphStyleRange") or []:
        ps = p.get("paragraphStyle") or {}
        pct = _f(ps.get("autoLeadingPercentage"))
        paras.append({"from": int(p.get("from") or 0), "to": int(p.get("to") or 0),
                      "align": _ev(ps.get("align")) or "left",
                      "auto_leading_pct": pct})
    kerns = sorted({str(r["auto_kern"]) for r in runs}) or ["manual"]
    shape = "point"
    shapes = tk.get("textShape") or []
    box_pt = None
    if shapes and _ev((shapes[0] or {}).get("char")) == "box":
        shape = "box"
        b = (shapes[0] or {}).get("bounds") or {}
        box_pt = {k: _f(b.get(k)) for k in ("left", "top", "right", "bottom")} if b else None
    cp = tk.get("textClickPoint") or {}
    fx_visible, effects = effects_from_descriptor(desc)
    stroke = None
    for e in effects:
        if e["type"] == "Stroke" and e["enabled"] and fx_visible:
            stroke = {k: e.get(k) for k in ("size", "color", "opacity", "position", "blend_mode")}
            stroke["enabled"] = True
            stroke["mode"] = stroke.pop("blend_mode", None)
            break
    first = runs[0] if runs else {}
    opacity = _f(desc.get("opacity"))
    fill_opacity = _f(desc.get("fillOpacity"))
    warp = _ev(((tk.get("warp") or {}).get("warpStyle")))
    return {
        "text_attrs_version": 2,
        "content": content,
        "font": first.get("font"),
        "size": first.get("size_px"),
        "size_pt": first.get("size_pt"),
        "color": first.get("color"),
        "alignment": paras[0]["align"] if paras else None,
        "tracking": first.get("tracking"),
        "leading": first.get("leading_pt"),
        "leading_px": first.get("leading_px"),
        "auto_leading": first.get("auto_leading"),
        "stroke": stroke,
        "opacity": (opacity / 255.0) if opacity is not None else None,
        "fill_opacity": (fill_opacity / 255.0) if fill_opacity is not None else None,
        "click_point": {"horizontal": _f(cp.get("horizontal")), "vertical": _f(cp.get("vertical"))} if cp else None,
        "transform": {"xx": m[0], "xy": m[1], "yx": m[2], "yy": m[3],
                      "tx": _f(tf.get("tx"), 0.0), "ty": _f(tf.get("ty"), 0.0)},
        "scale": {"x": round(sx, 6), "y": round(sy, 6)},
        "runs": runs,
        "paragraphs": paras,
        "kerning": kerns[0] if len(kerns) == 1 else "mixed",
        "manual_kerning_ranges": len(tk.get("kerningRange") or []),
        "anti_alias": _ev(tk.get("antiAlias")),
        "warp": None if warp in (None, "warpNone") else warp,
        "shape": shape,
        "box_bounds_pt": box_pt,
        "effects_visible": fx_visible,
        "layer_effects": effects,
    }


def complete_text_attrs(text):
    """Return a copy of a layout ``text`` dict with derived fields filled in (never removes fields).

    Adds ``scale`` and, for v1 attributes, ``size_pt`` and ``leading_px`` (the v1
    exporter scaled ``size`` but not ``leading``). v2 runs get ``size_px`` /
    ``leading_px`` when missing.
    """
    if not isinstance(text, dict):
        return text
    out = dict(text)
    m = matrix_of(out)
    sx, sy = matrix_scale(m)
    out.setdefault("scale", {"x": round(sx, 6), "y": round(sy, 6)})
    if out.get("size_pt") is None and _f(out.get("size")) is not None:
        out["size_pt"] = _f(out["size"]) / sy
    if out.get("leading_px") is None and _f(out.get("leading")) is not None and not out.get("auto_leading"):
        out["leading_px"] = _f(out["leading"]) * sy
    if out.get("runs"):
        runs = []
        for r in out["runs"]:
            r = dict(r)
            if r.get("size_px") is None and _f(r.get("size_pt")) is not None:
                r["size_px"] = _f(r["size_pt"]) * sy
            if r.get("leading_px") is None and _f(r.get("leading_pt")) is not None and not r.get("auto_leading"):
                r["leading_px"] = _f(r["leading_pt"]) * sy
            runs.append(r)
        out["runs"] = runs
    return out
