"""A publication-usable ROC figure as a standalone SVG file.

The ASCII curve in the report is for eyeballing shape in a terminal; a manuscript
needs a real figure. SVG is written by hand here because it is the only vector
format that needs no dependency at all, opens in every browser, and can be
dropped into Word, PowerPoint or Illustrator and still be edited.

Drawing conventions match the maths exactly: the curve connects the empirical
operating points with straight segments (the trapezoidal curve whose area *is*
the reported AUC), so a reader measuring the figure gets the reported number.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from .analyze import Analysis
from .pauc import PartialAuc
from .roc import Point, curve_xy, roc_points

__all__ = ["roc_svg"]

# Colour-blind-safe (Okabe-Ito): blue for the index test, then orange, green, purple.
_COLOURS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#56B4E9", "#E69F00"]
_PAD_L, _PAD_R, _PAD_T = 68, 24, 52
_PLOT = 420  # square plotting area, px
_FOOT_LEAD = 14  # line height of the footer block


def _esc(text: str) -> str:
    """XML-escape a string. Column names come from the user's file, so this is not optional."""
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _text_w(text: str, size: float) -> float:
    """Rough rendered width in px.

    SVG has no layout engine, so nothing stops a label from running off the
    canvas — and the label that ran off was the "this cut-off is optimistic"
    caveat, i.e. exactly the one the figure must not lose. Hangul/CJK advance
    about one em, Latin about 0.53 em in Helvetica; that is accurate enough to
    keep text inside the box.
    """
    w = 0.0
    for ch in text:
        if "가" <= ch <= "힣" or "　" <= ch <= "鿿":
            w += size
        elif ch in "il.,:;'|":
            w += size * 0.28
        else:
            w += size * 0.53
    return w


def _fit(text: str, size: float, max_px: float) -> str:
    """Truncate with an ellipsis so the string fits ``max_px``."""
    if _text_w(text, size) <= max_px:
        return text
    out = ""
    for ch in text:
        if _text_w(out + ch + "…", size) > max_px:
            break
        out += ch
    return (out + "…") if out else "…"


def _wrap(text: str, size: float, max_px: float) -> List[str]:
    """Greedy word wrap; falls back to character wrapping for unbroken text."""
    words, lines, cur = text.split(" "), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if cur and _text_w(trial, size) > max_px:
            lines.append(cur)
            cur = w
        else:
            cur = trial
        while _text_w(cur, size) > max_px:      # a single very long token
            cut = _fit(cur, size, max_px)
            lines.append(cut)
            cur = cur[max(1, len(cut) - 1):]
    if cur:
        lines.append(cur)
    return lines or [""]


def _f(x: Optional[float], nd: int = 3) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x:.{nd}f}"


def _monotone_xy(points: Sequence[Point]) -> List[Tuple[float, float]]:
    xy = [(x, y) for x, y in curve_xy(points)
          if not (math.isnan(x) or math.isnan(y))]
    xy.sort()
    return xy


class _Canvas:
    def __init__(self, n_foot_lines: int = 2) -> None:
        self.w = _PAD_L + _PLOT + _PAD_R
        # 46px for the tick labels and the axis title, then one line per footer
        # line — the canvas grows to fit the text instead of cropping it.
        self.pad_b = 52 + _FOOT_LEAD * max(1, n_foot_lines) + 8
        self.h = _PAD_T + _PLOT + self.pad_b

    def px(self, x: float) -> float:
        return _PAD_L + min(max(x, 0.0), 1.0) * _PLOT

    def py(self, y: float) -> float:
        return _PAD_T + (1.0 - min(max(y, 0.0), 1.0)) * _PLOT


def _curve_path(cv: _Canvas, xy: Sequence[Tuple[float, float]]) -> str:
    return " ".join(f"{cv.px(x):.2f},{cv.py(y):.2f}" for x, y in xy)


def _pauc_band(cv: _Canvas, pa: PartialAuc, xy: Sequence[Tuple[float, float]]) -> List[str]:
    """Shade the integrated region so the figure shows what the pAUC covers."""
    lo, hi = pa.fpr_low, pa.fpr_high
    pts: List[Tuple[float, float]] = [(lo, 0.0)]
    prev: Optional[Tuple[float, float]] = None
    for x, y in xy:
        if prev is not None and prev[0] < lo <= x and x != prev[0]:
            t = prev[1] + (y - prev[1]) * (lo - prev[0]) / (x - prev[0])
            pts.append((lo, t))
        if lo <= x <= hi:
            pts.append((x, y))
        if prev is not None and prev[0] <= hi < x and x != prev[0]:
            t = prev[1] + (y - prev[1]) * (hi - prev[0]) / (x - prev[0])
            pts.append((hi, t))
        prev = (x, y)
    pts.append((hi, 0.0))
    poly = " ".join(f"{cv.px(x):.2f},{cv.py(y):.2f}" for x, y in pts)
    return [f'  <polygon points="{poly}" fill="#0072B2" fill-opacity="0.12" '
            f'stroke="none"/>']


def roc_svg(an: Analysis, width_hint: Optional[int] = None,
            title: Optional[str] = None) -> str:
    """Render the analysis as an SVG document string.

    Includes the index test's curve, every ``--compare`` comparator on the same
    axes (oriented the same way the statistics were), the Youden operating point,
    the chance diagonal, the pAUC region when one was requested, and a footer
    stating the n's and that a data-chosen cut-off is optimistic — so the figure
    cannot be separated from its caveat when it is pasted into a slide.
    """
    ds = an.dataset
    lvl = f"{(1.0 - an.alpha) * 100:g}%"

    # Footer first: it decides how tall the canvas has to be.
    foot = (f"n = {len(ds.scores)} analysable of {ds.n_rows_in} "
            f"({ds.n_pos} with {ds.truth_name} = {ds.positive_label}, {ds.n_neg} without); "
            f"{lvl} CI; {'lower' if an.flipped else 'higher'} values indicate disease.")
    caveat = ("Cut-off chosen on these same data — sensitivity/specificity at it are "
              "optimistic.")
    if an.pauc is not None:
        caveat = (f"Shaded: pAUC over specificity {an.pauc.spec_low:.3g}–"
                  f"{an.pauc.spec_high:.3g} (standardised "
                  f"{_f(an.pauc.standardized)}). " + caveat)
    if ds.cluster_name and ds.n_clusters < len(ds.scores):
        kind = ("cluster bootstrap" if (an.curve_boot is not None
                                       and an.curve_boot.kind == "cluster")
                else "NOT corrected for clustering")
        foot += (f" {ds.n_clusters} independent units ({ds.cluster_name}); "
                 f"AUC interval: {kind}.")
    foot_lines = (_wrap(foot, 10.5, _PLOT) + _wrap(caveat, 10.5, _PLOT))

    cv = _Canvas(len(foot_lines))
    out: List[str] = [
        f'<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{cv.w}" height="{cv.h}" '
        f'viewBox="0 0 {cv.w} {cv.h}" font-family="Helvetica, Arial, sans-serif">',
        f'  <rect width="{cv.w}" height="{cv.h}" fill="#ffffff"/>',
    ]
    head = title if title is not None else f"ROC — {ds.score_name} (reference: {ds.truth_name})"
    out.append(f'  <text x="{_PAD_L}" y="26" font-size="15" font-weight="bold" '
               f'fill="#111111">{_esc(_fit(head, 15, _PLOT))}</text>')

    # grid + ticks
    for k in range(6):
        v = k / 5.0
        x, y = cv.px(v), cv.py(v)
        out.append(f'  <line x1="{x:.1f}" y1="{cv.py(0):.1f}" x2="{x:.1f}" '
                   f'y2="{cv.py(1):.1f}" stroke="#e8e8e8" stroke-width="1"/>')
        out.append(f'  <line x1="{cv.px(0):.1f}" y1="{y:.1f}" x2="{cv.px(1):.1f}" '
                   f'y2="{y:.1f}" stroke="#e8e8e8" stroke-width="1"/>')
        out.append(f'  <text x="{x:.1f}" y="{cv.py(0) + 18:.1f}" font-size="11" '
                   f'text-anchor="middle" fill="#444444">{v:.1f}</text>')
        out.append(f'  <text x="{cv.px(0) - 8:.1f}" y="{y + 4:.1f}" font-size="11" '
                   f'text-anchor="end" fill="#444444">{v:.1f}</text>')

    xy_main = _monotone_xy(an.points)
    if an.pauc is not None and xy_main:
        out.extend(_pauc_band(cv, an.pauc, xy_main))

    out.append(f'  <line x1="{cv.px(0):.1f}" y1="{cv.py(0):.1f}" x2="{cv.px(1):.1f}" '
               f'y2="{cv.py(1):.1f}" stroke="#999999" stroke-width="1" '
               f'stroke-dasharray="5,4"/>')
    out.append(f'  <rect x="{cv.px(0):.1f}" y="{cv.py(1):.1f}" width="{_PLOT}" '
               f'height="{_PLOT}" fill="none" stroke="#333333" stroke-width="1.2"/>')

    legend: List[Tuple[str, str]] = []
    if xy_main:
        out.append(f'  <polyline points="{_curve_path(cv, xy_main)}" fill="none" '
                   f'stroke="{_COLOURS[0]}" stroke-width="2.4" stroke-linejoin="round"/>')
    auc_ci = (f" [{_f(an.auc.ci[0])}, {_f(an.auc.ci[1])}]" if an.auc.ci else "")
    legend.append((_COLOURS[0], f"{ds.score_name}: AUC {_f(an.auc.auc)}{auc_ci}"))

    # comparators, drawn with the same orientation the comparison used
    for i, cmp_ in enumerate(an.comparisons, start=1):
        vals = ds.extra.get(cmp_.label_b)
        if not vals:
            continue
        flipped = an.comparison_flipped.get(cmp_.label_b, False)
        oriented = [-v for v in vals] if flipped else list(vals)
        xy = _monotone_xy(roc_points(oriented, ds.positive))
        col = _COLOURS[i % len(_COLOURS)]
        if xy:
            out.append(f'  <polyline points="{_curve_path(cv, xy)}" fill="none" '
                       f'stroke="{col}" stroke-width="1.9" stroke-dasharray="7,4" '
                       f'stroke-linejoin="round"/>')
        legend.append((col, f"{cmp_.label_b}: AUC {_f(cmp_.auc_b)}"
                            f" (Δ {_f(cmp_.diff)}, p {_f(cmp_.p_value, 4)})"))

    # Youden operating point
    for sp in an.selected:
        if sp.key != "youden" or not sp.feasible:
            continue
        pt = sp.metrics.point
        if math.isnan(pt.sens) or math.isnan(pt.spec):
            break
        cx, cyy = cv.px(1.0 - pt.spec), cv.py(pt.sens)
        out.append(f'  <circle cx="{cx:.1f}" cy="{cyy:.1f}" r="5" fill="#ffffff" '
                   f'stroke="{_COLOURS[0]}" stroke-width="2.2"/>')
        value, op = an.cutoff_in_original_units(pt.threshold)
        if math.isfinite(value):
            lab = (f"Youden: {ds.score_name} {op} {value:.6g}  "
                   f"(Se {pt.sens * 100:.1f}%, Sp {pt.spec * 100:.1f}%)")
            # Label to the right of the point, or to the left when that would
            # run off the plot; then fitted to whatever room is actually left.
            anchor = "start" if (1.0 - pt.spec) < 0.55 else "end"
            dx = 10 if anchor == "start" else -10
            room = (cv.px(1.0) - (cx + dx)) if anchor == "start" else (cx + dx - cv.px(0.0))
            out.append(f'  <text x="{cx + dx:.1f}" y="{cyy + 16:.1f}" font-size="11" '
                       f'text-anchor="{anchor}" fill="#111111">'
                       f'{_esc(_fit(lab, 11, max(40.0, room)))}</text>')
        break

    # axis labels
    out.append(f'  <text x="{cv.px(0.5):.1f}" y="{cv.py(0) + 40:.1f}" font-size="12.5" '
               f'text-anchor="middle" fill="#111111">1 − Specificity '
               f'(false positive rate)</text>')
    out.append(f'  <text x="18" y="{cv.py(0.5):.1f}" font-size="12.5" '
               f'text-anchor="middle" fill="#111111" '
               f'transform="rotate(-90 18 {cv.py(0.5):.1f})">Sensitivity</text>')

    # Legend inside the plot, bottom-right where the curve never goes; each entry
    # is fitted to the space between its swatch and the right-hand axis.
    ly = cv.py(0) - 12 - 15 * (len(legend) - 1)
    legend_x = cv.px(0.34)
    room = cv.px(1.0) - legend_x - 10
    for col, text in legend:
        out.append(f'  <line x1="{legend_x - 22:.1f}" y1="{ly - 4:.1f}" '
                   f'x2="{legend_x - 6:.1f}" y2="{ly - 4:.1f}" stroke="{col}" '
                   f'stroke-width="2.6"/>')
        out.append(f'  <text x="{legend_x:.1f}" y="{ly:.1f}" font-size="11" '
                   f'fill="#111111">{_esc(_fit(text, 11, room))}</text>')
        ly += 15

    # Footer below the axis title (it used to be printed on top of it), wrapped
    # so the caveat is always fully on the canvas.
    fy = cv.py(0) + 62
    for line in foot_lines:
        out.append(f'  <text x="{_PAD_L}" y="{fy:.1f}" font-size="10.5" '
                   f'fill="#555555">{_esc(line)}</text>')
        fy += _FOOT_LEAD
    out.append("</svg>")
    return "\n".join(out) + "\n"
