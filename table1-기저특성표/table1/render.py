"""Render a Table1 object as Markdown / CSV / TSV / JSON — pure standard library."""

from __future__ import annotations

import io
import json
import math
from typing import List, Optional

from .build import CategoricalRow, ContinuousRow, Options, Table1

__all__ = ["render"]

_FORMULA_TRIGGERS = ("=", "+", "-", "@")

# --------------------------------------------------------------------------- #
# Language strings (ko default, en for English-language manuscripts)
# --------------------------------------------------------------------------- #
_LANG = {
    "ko": {
        "title": "표 1. 기저 특성 (Table 1. Baseline characteristics)",
        "char": "특성 (Characteristic)",
        "overall": "전체",
        "p": "p값",
        "smd": "SMD",
        "test": "검정",
        "missing": "결측",
        "mean_sd": "평균(SD)",
        "median_iqr": "중앙값[IQR]",
        "n_pct": "n(%)",
        "notes_hdr": "**주석**",
        "warn_hdr": "**경고**",
        "pct_col": "열 기준(그룹 내 비결측 %)",
        "pct_col_incl_missing": "열 기준(그룹 내 전체 % — '(결측)' 수준 포함)",
        "pct_row": "행 기준(수준 내 %)",
        "leg_notation": ("**표기**: 연속형 = 평균(표준편차) 또는 중앙값[IQR] "
                         "(정규성에 따라 자동), 범주형 = n(%). % 는 "),
        "leg_p": ("**p값**: 연속형은 정규성·등분산 점검 후 t/Welch/Mann-Whitney "
                  "(≥3군: ANOVA/Kruskal-Wallis), 범주형은 Pearson χ² 또는 "
                  "Fisher exact."),
        "leg_p_welch": ("**p값**: 연속형은 (사전검정 없이) 항상 Welch t "
                        "(≥3군: 일원배치 ANOVA), 범주형은 Pearson χ² 또는 "
                        "Fisher exact."),
        "leg_p_student": ("**p값**: 연속형은 (사전검정 없이) 항상 Student t "
                          "(≥3군: 일원배치 ANOVA), 범주형은 Pearson χ² 또는 "
                          "Fisher exact."),
        "leg_p_nonparam": ("**p값**: 연속형은 (정규성과 무관하게) Mann-Whitney U "
                           "(≥3군: Kruskal-Wallis), 범주형은 Pearson χ² 또는 "
                           "Fisher exact."),
        "leg_smd": ("**SMD**: 표준화 평균차(절대값). |SMD|>0.1 이면 두 군 간 "
                    "불균형을 시사(범주형은 Yang–Dalton 다변량 SMD)."),
    },
    "en": {
        "title": "Table 1. Baseline characteristics",
        "char": "Characteristic",
        "overall": "Overall",
        "p": "p",
        "smd": "SMD",
        "test": "Test",
        "missing": "missing",
        "mean_sd": "Mean (SD)",
        "median_iqr": "Median [IQR]",
        "n_pct": "n (%)",
        "notes_hdr": "**Notes**",
        "warn_hdr": "**Warnings**",
        "pct_col": "column-wise (% of non-missing within group)",
        "pct_col_incl_missing": ("column-wise (% of all cells within group, "
                                 "including the '(missing)' level)"),
        "pct_row": "row-wise (% within level)",
        "leg_notation": ("**Notation**: continuous = mean (SD) or median [IQR] "
                         "(auto by normality), categorical = n (%). % is "),
        "leg_p": ("**p**: continuous uses t/Welch/Mann-Whitney after "
                  "normality/variance checks (>=3 groups: ANOVA/Kruskal-Wallis); "
                  "categorical uses Pearson chi-square or Fisher exact."),
        "leg_p_welch": ("**p**: continuous always uses Welch t (no pre-test; "
                        ">=3 groups: one-way ANOVA); categorical uses Pearson "
                        "chi-square or Fisher exact."),
        "leg_p_student": ("**p**: continuous always uses Student t (no pre-test; "
                          ">=3 groups: one-way ANOVA); categorical uses Pearson "
                          "chi-square or Fisher exact."),
        "leg_p_nonparam": ("**p**: continuous always uses Mann-Whitney U "
                           "(regardless of normality; >=3 groups: "
                           "Kruskal-Wallis); categorical uses Pearson "
                           "chi-square or Fisher exact."),
        "leg_smd": ("**SMD**: absolute standardized mean difference. |SMD|>0.1 "
                    "suggests between-group imbalance (categorical: Yang-Dalton "
                    "multivariate SMD)."),
    },
}


def _lang(opt: Options) -> dict:
    return _LANG.get(getattr(opt, "lang", "ko") or "ko", _LANG["ko"])


def _leg_p_for(L: dict, opt: Options) -> str:
    """p-value legend, adapted to an explicit --test-cont so the footnote does
    not claim a normality/variance pre-test that did not happen."""
    tc = getattr(opt, "test_cont", "auto")
    return L.get(f"leg_p_{tc}", L["leg_p"])


def _disp_name(opt: Options, name: str) -> str:
    """Apply a --labels display-name override (pretty name / units)."""
    return getattr(opt, "labels", {}).get(name, name)


def _fmt_num(x: float, d: int) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x))):
        return "—"
    d = max(0, d)  # negative precision is a ValueError in format specs
    return f"{x:.{d}f}"


def _fmt_p(p: Optional[float]) -> str:
    if p is None:
        return "—"
    if p < 0.001:
        return "<0.001"
    if p > 0.999:
        return ">0.999"
    return f"{p:.3f}"


def _fmt_smd(s: Optional[float]) -> str:
    if s is None:
        return ""
    if math.isinf(s):
        return "∞"
    if math.isnan(s):
        return "—"
    return f"{s:.3f}"


def _range_suffix(st, d: int) -> str:
    if st.n == 0 or math.isnan(st.vmin) or math.isnan(st.vmax):
        return ""
    return f" ({_fmt_num(st.vmin, d)}–{_fmt_num(st.vmax, d)})"


def _cont_cell(st, display: str, d: int, show_range: bool = False) -> str:
    if st.n == 0:
        return "—"
    mean = f"{_fmt_num(st.mean, d)} ({_fmt_num(st.sd, d)})"
    med = f"{_fmt_num(st.median, d)} [{_fmt_num(st.q1, d)}, {_fmt_num(st.q3, d)}]"
    if display == "mean":
        body = mean
    elif display == "median":
        body = med
    else:
        body = f"{mean} / {med}"
    if show_range:
        body += _range_suffix(st, d)
    return body


def _pct(count: int, denom: int) -> float:
    return 100.0 * count / denom if denom else float("nan")


def _cat_cell(count: int, denom: int, level_total: int, pct_mode: str,
              pct_decimals: int = 1) -> str:
    p = _pct(count, level_total) if pct_mode == "row" else _pct(count, denom)
    return f"{count} ({_fmt_num(p, pct_decimals)})"


def _binary_collapse(row: CategoricalRow, opt: Options):
    """For --binary-single: the single CatLevel to display for a 2-level
    categorical, or None if the row should render normally.

    Collapses only when exactly two levels are present (so a synthetic
    '(결측)' missing level, which makes three, disables the collapse). The
    displayed level is the non-reference one: reference defaults to the first
    level, overridable per column via --ref COL=level.
    """
    if not getattr(opt, "binary_single", False):
        return None
    if len(row.levels) != 2:
        return None
    labels = [lvl.label for lvl in row.levels]
    ref = getattr(opt, "ref", {}).get(row.name)
    show = 1  # default: reference = first level, show the second
    if ref is not None and ref == labels[1]:
        show = 0
    return row.levels[show]


def _cont_suffix(row: ContinuousRow, L: dict) -> str:
    if row.display == "mean":
        return f" — {L['mean_sd']}"
    if row.display == "median":
        return f" — {L['median_iqr']}"
    return f" — {L['mean_sd']}/{L['median_iqr']}"


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #
def _md_escape(s: str) -> str:
    """Escape a DATA string for a Markdown table cell.

    Neutralizes: table-breaking pipes and newlines, and HTML injection
    (`<img onerror=...>` from a free-text patient value) since Markdown viewers
    render raw HTML. Applied only to data (variable names, level/group labels),
    never to tool-generated markup like the <sup> footnote markers.
    """
    s = s.replace("\r", " ").replace("\n", " ")
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.replace("|", "\\|")


def _render_markdown(t: Table1, opt: Options) -> str:
    L = _lang(opt)
    show_overall = opt.overall
    show_p = opt.show_pvalue
    two_group = len(t.groups) == 2
    out: List[str] = []
    out.append("## " + L["title"])
    out.append("")

    headers = [L["char"]]
    if show_overall:
        headers.append(f"{L['overall']} (N={t.overall_size})")
    for g, n in zip(t.groups, t.group_sizes):
        headers.append(f"{_md_escape(g)} (n={n})")
    if show_p:
        headers.append(L["p"])
    if two_group:
        headers.append(L["smd"])
    headers.append(L["test"])

    out.append("| " + " | ".join(headers) + " |")
    out.append("|" + "|".join(["---"] * len(headers)) + "|")

    note_markers: List[str] = []

    def note_suffix(notes: List[str]) -> str:
        if not notes:
            return ""
        idx = len(note_markers) + 1
        note_markers.append("; ".join(notes))
        return f" <sup>{idx}</sup>"

    def miss_suffix(n_missing: int, per_group=None) -> str:
        """Total missing, with a per-group breakdown so differential
        missingness between arms (a key trial-review concern) is visible."""
        if not n_missing:
            return ""
        base = f" · {L['missing']} {n_missing}"
        if per_group is not None and len(t.groups) >= 2:
            parts = [f"{_md_escape(g)} {m}"
                     for g, m in zip(t.groups, per_group) if m]
            if parts:
                base += " (" + ", ".join(parts) + ")"
        return base

    for row in t.rows:
        if isinstance(row, ContinuousRow):
            per_group_missing = [st.n_missing for st in row.per_group]
            label = (_md_escape(_disp_name(opt, row.name)) + _cont_suffix(row, L)
                     + miss_suffix(row.n_missing_total, per_group_missing)
                     + note_suffix(row.notes))
            cells = [label]
            if show_overall:
                cells.append(_cont_cell(row.overall, row.display, opt.decimals,
                                        opt.show_range))
            for st in row.per_group:
                cells.append(_cont_cell(st, row.display, opt.decimals,
                                        opt.show_range))
            if show_p:
                cells.append(_fmt_p(row.pvalue))
            if two_group:
                cells.append(_fmt_smd(row.smd))
            cells.append(row.test_name)
            out.append("| " + " | ".join(cells) + " |")
        else:  # CategoricalRow
            pd = opt.pct_decimals
            single = _binary_collapse(row, opt)
            if single is not None:
                # Collapsed 2-level row: one line, value/percent of the shown
                # level, with p/SMD/test on the same row.
                label = (_md_escape(_disp_name(opt, row.name)) + " = "
                         + _md_escape(single.label) + f" — {L['n_pct']}"
                         + miss_suffix(row.n_missing_total, row.missing_per_group)
                         + note_suffix(row.notes))
                cells = [label]
                if show_overall:
                    cells.append(_cat_cell(single.overall, row.overall_denom,
                                           single.overall, row.pct, pd))
                for gi, c in enumerate(single.counts):
                    cells.append(_cat_cell(c, row.denom_per_group[gi],
                                           single.overall, row.pct, pd))
                if show_p:
                    cells.append(_fmt_p(row.pvalue))
                if two_group:
                    cells.append(_fmt_smd(row.smd))
                cells.append(row.test_name)
                out.append("| " + " | ".join(cells) + " |")
                continue
            header_label = (_md_escape(_disp_name(opt, row.name)) + f" — {L['n_pct']}"
                            + miss_suffix(row.n_missing_total,
                                          row.missing_per_group)
                            + note_suffix(row.notes))
            cells = [header_label]
            if show_overall:
                cells.append("")
            cells.extend([""] * len(t.groups))
            if show_p:
                cells.append(_fmt_p(row.pvalue))
            if two_group:
                cells.append(_fmt_smd(row.smd))
            cells.append(row.test_name)
            out.append("| " + " | ".join(cells) + " |")
            for lvl in row.levels:
                lcells = [" " + _md_escape(lvl.label)]
                if show_overall:
                    lcells.append(_cat_cell(lvl.overall, row.overall_denom,
                                            lvl.overall, row.pct, pd))
                for gi, c in enumerate(lvl.counts):
                    lcells.append(_cat_cell(c, row.denom_per_group[gi],
                                            lvl.overall, row.pct, pd))
                if show_p:
                    lcells.append("")
                if two_group:
                    lcells.append("")
                lcells.append("")
                out.append("| " + " | ".join(lcells) + " |")

    out.append("")
    # Legend + notes
    if opt.pct == "col":
        # Under --missing-as-level the synthetic '(결측)' level is counted, so the
        # column denominator is the full group, not the non-missing subset — but
        # only when a categorical variable actually HAS missing (otherwise no
        # synthetic level was added and the denominator is unchanged).
        incl_missing = (getattr(opt, "missing_as_level", False) and
                        any(isinstance(r, CategoricalRow) and r.n_missing_total > 0
                            for r in t.rows))
        pct_desc = L["pct_col_incl_missing"] if incl_missing else L["pct_col"]
    else:
        pct_desc = L["pct_row"]
    legend = [L["leg_notation"] + pct_desc + "."]
    if show_p:
        legend.append(_leg_p_for(L, opt))
    if two_group:
        legend.append(L["leg_smd"])
    out.extend(legend)
    if note_markers:
        out.append("")
        out.append(L["notes_hdr"])
        for i, n in enumerate(note_markers, 1):
            out.append(f"{i}. {_md_escape(n)}")
    if t.warnings:
        out.append("")
        out.append(L["warn_hdr"])
        for w in t.warnings:
            out.append(f"- {_md_escape(w)}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CSV / TSV
# --------------------------------------------------------------------------- #
def _csv_safe(field: str) -> str:
    """Neutralize spreadsheet formula injection for a DATA-DERIVED field.

    Applied only to attacker-influenceable cells (variable names, level/group
    labels), never to tool-generated numeric summary cells — so a legitimate
    negative mean like ``-3.6 (2.3)`` is left intact instead of being prefixed
    with a stray apostrophe. A leading ``= + - @`` (the Excel/LibreOffice
    formula/DDE triggers) on a data cell is quoted so the spreadsheet imports it
    as text.
    """
    if field and field[0] in _FORMULA_TRIGGERS:
        return "'" + field
    return field


def _render_delimited(t: Table1, opt: Options, delim: str) -> str:
    import csv as _csv

    show_overall = opt.overall
    show_p = opt.show_pvalue
    two_group = len(t.groups) == 2
    buf = io.StringIO()
    writer = _csv.writer(buf, delimiter=delim, lineterminator="\n")

    header = ["characteristic", "level"]
    if show_overall:
        header.append(f"overall_N={t.overall_size}")
    header += [f"{g}_n={n}" for g, n in zip(t.groups, t.group_sizes)]
    if show_p:
        header.append("p_value")
    header += ["test", "n_missing"]
    if two_group:
        header.append("smd")
    writer.writerow([_csv_safe(h) for h in header])

    # Only DATA-derived cells (variable name, level label) are formula-guarded;
    # tool-generated numeric cells (means, counts, p-values, SMD) are written
    # raw so a legitimate negative value is not corrupted.
    for row in t.rows:
        sname = _csv_safe(_disp_name(opt, row.name))
        if isinstance(row, ContinuousRow):
            L = _lang(opt)
            line = [sname, _cont_suffix(row, L).split(" — ")[-1]]
            if show_overall:
                line.append(_cont_cell(row.overall, row.display, opt.decimals,
                                       opt.show_range))
            for st in row.per_group:
                line.append(_cont_cell(st, row.display, opt.decimals,
                                       opt.show_range))
            if show_p:
                line.append(_fmt_p(row.pvalue))
            line += [row.test_name, str(row.n_missing_total)]
            if two_group:
                line.append(_fmt_smd(row.smd))
            writer.writerow([str(x) for x in line])
        else:
            pd = opt.pct_decimals
            single = _binary_collapse(row, opt)
            if single is not None:
                line = [sname, _csv_safe(single.label)]
                if show_overall:
                    line.append(_cat_cell(single.overall, row.overall_denom,
                                          single.overall, row.pct, pd))
                for gi, c in enumerate(single.counts):
                    line.append(_cat_cell(c, row.denom_per_group[gi],
                                          single.overall, row.pct, pd))
                if show_p:
                    line.append(_fmt_p(row.pvalue))
                line += [row.test_name, str(row.n_missing_total)]
                if two_group:
                    line.append(_fmt_smd(row.smd))
                writer.writerow([str(x) for x in line])
                continue
            line = [sname, "n(%)"]
            if show_overall:
                line.append("")
            line += [""] * len(t.groups)
            if show_p:
                line.append("")
            line += [row.test_name, str(row.n_missing_total)]
            if two_group:
                line.append("")
            writer.writerow([str(x) for x in line])
            for lvl in row.levels:
                lline = [sname, _csv_safe(lvl.label)]
                if show_overall:
                    lline.append(_cat_cell(lvl.overall, row.overall_denom,
                                           lvl.overall, row.pct, pd))
                for gi, c in enumerate(lvl.counts):
                    lline.append(_cat_cell(c, row.denom_per_group[gi],
                                           lvl.overall, row.pct, pd))
                if show_p:
                    lline.append("")
                lline += ["", ""]
                if two_group:
                    lline.append("")
                writer.writerow([str(x) for x in lline])
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# JSON
# --------------------------------------------------------------------------- #
def _finite(x):
    """Map non-finite floats to None so the JSON is valid RFC-8259."""
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def _render_json(t: Table1, opt: Options) -> str:
    def stat(st):
        return {
            "n": st.n, "n_missing": st.n_missing,
            "mean": _finite(st.mean), "sd": _finite(st.sd),
            "median": _finite(st.median), "q1": _finite(st.q1),
            "q3": _finite(st.q3), "min": _finite(st.vmin), "max": _finite(st.vmax),
        }

    rows = []
    for row in t.rows:
        disp = _disp_name(opt, row.name)
        if isinstance(row, ContinuousRow):
            rows.append({
                "name": row.name, "label": disp, "kind": "continuous",
                "display": row.display, "overall": stat(row.overall),
                "groups": [stat(s) for s in row.per_group],
                "p_value": _finite(row.pvalue), "test": row.test_name,
                "smd": _finite(row.smd),
                "n_missing": row.n_missing_total, "notes": row.notes,
            })
        else:
            rows.append({
                "name": row.name, "label": disp, "kind": "categorical",
                "pct": row.pct, "denom_per_group": row.denom_per_group,
                "overall_denom": row.overall_denom,
                "missing_per_group": row.missing_per_group,
                "levels": [{"label": l.label, "counts": l.counts,
                            "overall": l.overall} for l in row.levels],
                "p_value": _finite(row.pvalue), "test": row.test_name,
                "smd": _finite(row.smd),
                "n_missing": row.n_missing_total, "notes": row.notes,
            })
    obj = {
        "groups": t.groups, "group_sizes": t.group_sizes,
        "overall_size": t.overall_size, "rows": rows,
        "warnings": t.warnings, "meta": t.meta,
    }
    # allow_nan=False guarantees strictly valid JSON; _finite already nulled
    # every non-finite value so this never raises.
    return json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)


def render(t: Table1, opt: Options, fmt: str = "md") -> str:
    if fmt == "md":
        return _render_markdown(t, opt)
    if fmt == "csv":
        return _render_delimited(t, opt, ",")
    if fmt == "tsv":
        return _render_delimited(t, opt, "\t")
    if fmt == "json":
        return _render_json(t, opt)
    raise ValueError(f"알 수 없는 출력 형식: {fmt}")
