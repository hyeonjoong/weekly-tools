"""CSV loading for statwise — pure standard library (csv module).

Supports two layouts:

Long / tidy  (--value COL --group COL):
    value,group
    12.1,control
    13.4,treatment
    ...

Wide         (each named column is one group; blanks ignored):
    control,treatment
    12.1,13.4
    11.8,14.0
    ,14.9
"""

from __future__ import annotations

import csv
import math
import re
from typing import List, Optional, Sequence, Tuple

__all__ = ["load_long", "load_wide", "load_paired_long", "load_paired_wide",
           "parse_float"]

_NA_LABELS = {"NA", "N/A", "NAN", "NULL", ".", "-", "NONE", "MISSING", "#N/A"}

# Encodings tried in order for messy real-world clinical exports. utf-8-sig
# handles a BOM; cp949/euc-kr covers Korean Excel exports; latin-1 always
# decodes (last resort) so we never crash on odd bytes.
_ENCODINGS = ["utf-8-sig", "cp949", "latin-1"]

# Strict numeric grammar. We deliberately do NOT let Python's float() parse
# freely, because that would silently accept locale-ambiguous or non-finite
# tokens: "1,5" (European 1.5 vs US 15), "inf", "1_000", full-width digits.
# Guessing there is worse than dropping the cell — a 10x-wrong value looks
# perfectly plausible in a clinical report.
_PLAIN_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$", re.ASCII)
# Unambiguous US thousands grouping only: 1,234 / 1,234.5 / 1,234,567
_THOUSANDS_RE = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$", re.ASCII)


def parse_float(token: str) -> Optional[float]:
    """Parse a cell into a float, returning None for blanks / NA / non-numeric.

    Tolerates benign clinical-CSV noise (surrounding quotes/whitespace, a
    trailing ``%``, unambiguous thousands grouping like ``1,234.5``) but is
    strict about anything ambiguous or non-finite: European decimal commas
    (``1,5``), ``inf``/``nan``, underscores, and full-width digits all return
    ``None`` rather than a silently-wrong number.
    """
    t = token.strip().strip('"').strip("'").strip()
    if t == "" or t.upper() in _NA_LABELS:
        return None
    core = t[:-1].strip() if t.endswith("%") else t
    if _PLAIN_NUM_RE.match(core):
        v = float(core)
        return v if math.isfinite(v) else None
    if _THOUSANDS_RE.match(core):
        v = float(core.replace(",", ""))
        return v if math.isfinite(v) else None
    return None


def _decode(path: str, notes: Optional[List[str]]) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in _ENCODINGS:
        try:
            text = raw.decode(enc)
            if enc != "utf-8-sig" and notes is not None:
                notes.append(
                    f"파일 인코딩을 UTF-8로 읽지 못해 '{enc}'로 해석했습니다 "
                    f"(한글이 깨지면 CSV를 UTF-8로 다시 저장하세요).")
            return text
        except (UnicodeDecodeError, LookupError):
            continue
    # latin-1 never fails, but guard anyway
    return raw.decode("latin-1", errors="replace")


def _sniff_delimiter(text: str, notes: Optional[List[str]]) -> str:
    sample = "\n".join(text.splitlines()[:20])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = ","
    if delim != "," and notes is not None:
        shown = {"\t": "탭(tab)"}.get(delim, delim)
        notes.append(f"구분자를 '{shown}'로 자동 감지했습니다.")
    return delim


def _read_rows(path: str, delimiter: Optional[str] = None,
               notes: Optional[List[str]] = None
               ) -> Tuple[List[str], List[List[str]]]:
    text = _decode(path, notes)
    delim = delimiter or _sniff_delimiter(text, notes)
    reader = csv.reader(text.splitlines(), delimiter=delim)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError(f"'{path}' is empty")
    header = [h.strip() for h in rows[0]]
    return header, rows[1:]


def load_long(path: str, value_col: str, group_col: str,
              delimiter: Optional[str] = None,
              notes: Optional[List[str]] = None
              ) -> List[Tuple[str, List[float]]]:
    """Load tidy data: return [(group_label, [values...]), ...] in first-seen order."""
    header, data = _read_rows(path, delimiter, notes)
    try:
        vi = header.index(value_col)
        gi = header.index(group_col)
    except ValueError:
        raise ValueError(
            f"column not found. header={header}; needed value='{value_col}', "
            f"group='{group_col}'")
    groups: List[str] = []
    buckets: dict = {}
    dropped = 0
    for row in data:
        if vi >= len(row) or gi >= len(row):
            dropped += 1
            continue
        val = parse_float(row[vi])
        grp = row[gi].strip()
        if val is None or grp == "" or grp.upper() in _NA_LABELS:
            dropped += 1
            continue
        if grp not in buckets:
            buckets[grp] = []
            groups.append(grp)
        buckets[grp].append(val)
    result = [(g, buckets[g]) for g in groups]
    if not result:
        raise ValueError("no usable rows found (check column names / data)")
    return result


def load_wide(path: str, columns: Optional[Sequence[str]] = None,
              delimiter: Optional[str] = None,
              notes: Optional[List[str]] = None
              ) -> List[Tuple[str, List[float]]]:
    """Load wide data: each selected column becomes a group (blanks dropped)."""
    header, data = _read_rows(path, delimiter, notes)
    dupes = {h for h in header if header.count(h) > 1}
    if dupes:
        raise ValueError(
            "wide 형식에서 열 이름이 중복됩니다: " + ", ".join(sorted(dupes)) +
            ". 각 그룹 열의 이름을 고유하게 만드세요.")
    cols = list(columns) if columns else header
    idx = {}
    for c in cols:
        if c not in header:
            raise ValueError(f"column '{c}' not in header {header}")
        idx[c] = header.index(c)
    result = []
    for c in cols:
        vals = []
        for row in data:
            i = idx[c]
            if i < len(row):
                v = parse_float(row[i])
                if v is not None:
                    vals.append(v)
        result.append((c, vals))
    return result


def load_paired_long(path: str, value_col: str, group_col: str, id_col: str,
                     delimiter: Optional[str] = None,
                     notes: Optional[List[str]] = None,
                     baseline: Optional[str] = None
                     ) -> Tuple[Tuple[str, List[float]], Tuple[str, List[float]]]:
    """Load matched-pairs data from a long/tidy file.

    Needs an id column (subject), a group column with **exactly two** levels
    (the two conditions), and a value column.  Returns two row-matched
    ``(label, values)`` conditions ``(test, reference)`` aligned by subject id;
    the analysed difference is ``test - reference``.  If ``baseline`` names one
    of the two levels it becomes the *reference* (subtracted) condition, so the
    sign of the effect is deterministic regardless of CSV row order.  Otherwise
    the two conditions keep first-seen order.  Subjects missing a value in
    either condition, or appearing more than once per condition, are dropped
    with a note.
    """
    header, data = _read_rows(path, delimiter, notes)
    try:
        vi = header.index(value_col)
        gi = header.index(group_col)
        ii = header.index(id_col)
    except ValueError:
        raise ValueError(
            f"column not found. header={header}; needed value='{value_col}', "
            f"group='{group_col}', id='{id_col}'")

    levels: List[str] = []
    # by_cond[level][id] = value
    by_cond: dict = {}
    dup_ids: set = set()
    for row in data:
        if max(vi, gi, ii) >= len(row):
            continue
        grp = row[gi].strip()
        sid = row[ii].strip()
        val = parse_float(row[vi])
        if grp == "" or grp.upper() in _NA_LABELS or sid == "" \
                or sid.upper() in _NA_LABELS or val is None:
            continue
        if grp not in by_cond:
            by_cond[grp] = {}
            levels.append(grp)
        if sid in by_cond[grp]:
            dup_ids.add((grp, sid))
        by_cond[grp][sid] = val

    if len(levels) != 2:
        raise ValueError(
            f"대응(paired) 분석에는 그룹 열 '{group_col}'에 정확히 2개 수준이 "
            f"필요합니다. 발견된 수준: {levels}")
    if baseline is not None:
        if baseline not in levels:
            raise ValueError(
                f"--baseline '{baseline}' 은(는) 그룹 열의 수준이 아닙니다. "
                f"가능한 값: {levels}")
        # reference (subtracted) condition = baseline -> place it as lb
        lb = baseline
        la = levels[0] if levels[1] == baseline else levels[1]
    else:
        la, lb = levels[0], levels[1]
    ids_a, ids_b = by_cond[la], by_cond[lb]
    # preserve first-seen id order from condition A
    order: List[str] = []
    seen = set()
    for row in data:
        if ii < len(row):
            sid = row[ii].strip()
            if sid in ids_a and sid in ids_b and sid not in seen:
                order.append(sid)
                seen.add(sid)
    va = [ids_a[s] for s in order]
    vb = [ids_b[s] for s in order]
    dropped = (len(ids_a) - len(order)) + (len(ids_b) - len(order))
    if notes is not None:
        if dropped:
            notes.append(f"{dropped}개 관측치가 짝을 이루지 못해 제외되었습니다.")
        if dup_ids:
            notes.append(
                f"한 조건에서 중복 id가 있어 마지막 값만 사용했습니다: "
                f"{len(dup_ids)}건.")
    if not order:
        raise ValueError("짝을 이루는 관측치가 없습니다 (id/그룹/값 확인).")
    return (la, va), (lb, vb)


def load_paired_wide(path: str, columns: Optional[Sequence[str]] = None,
                     delimiter: Optional[str] = None,
                     notes: Optional[List[str]] = None
                     ) -> Tuple[Tuple[str, List[float]], Tuple[str, List[float]]]:
    """Load matched pairs from a wide file: two columns matched **row-wise**.

    Unlike ``load_wide`` (which drops blanks per column independently), pairing
    is by row: a row is used only if *both* selected columns have a value.
    """
    header, data = _read_rows(path, delimiter, notes)
    dupes = {h for h in header if header.count(h) > 1}
    if dupes:
        raise ValueError(
            "wide 형식에서 열 이름이 중복됩니다: " + ", ".join(sorted(dupes)))
    cols = list(columns) if columns else header
    if len(cols) != 2:
        raise ValueError(
            f"대응(paired) wide 분석에는 정확히 2개 열이 필요합니다. "
            f"--columns 로 2개를 지정하세요. (지금: {cols})")
    for c in cols:
        if c not in header:
            raise ValueError(f"column '{c}' not in header {header}")
    ia, ib = header.index(cols[0]), header.index(cols[1])
    va: List[float] = []
    vb: List[float] = []
    dropped = 0
    for row in data:
        pa = parse_float(row[ia]) if ia < len(row) else None
        pb = parse_float(row[ib]) if ib < len(row) else None
        if pa is None or pb is None:
            if pa is not None or pb is not None:
                dropped += 1
            continue
        va.append(pa)
        vb.append(pb)
    if notes is not None and dropped:
        notes.append(f"{dropped}개 행이 한쪽 값 결측으로 제외되었습니다 (row-wise 매칭).")
    if not va:
        raise ValueError("짝을 이루는 행이 없습니다 (두 열 모두 값이 있어야 함).")
    return (cols[0], va), (cols[1], vb)
