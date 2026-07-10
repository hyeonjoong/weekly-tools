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
from typing import List, Optional, Sequence, Tuple

__all__ = ["load_long", "load_wide", "parse_float"]

_NA_LABELS = {"NA", "N/A", "NAN", "NULL", "."}


def parse_float(token: str) -> Optional[float]:
    """Parse a cell into a float, returning None for blanks / NA / non-numeric."""
    t = token.strip()
    if t == "" or t.upper() in _NA_LABELS:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _read_rows(path: str) -> Tuple[List[str], List[List[str]]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError(f"'{path}' is empty")
    header = [h.strip() for h in rows[0]]
    return header, rows[1:]


def load_long(path: str, value_col: str, group_col: str
              ) -> List[Tuple[str, List[float]]]:
    """Load tidy data: return [(group_label, [values...]), ...] in first-seen order."""
    header, data = _read_rows(path)
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


def load_wide(path: str, columns: Optional[Sequence[str]] = None
              ) -> List[Tuple[str, List[float]]]:
    """Load wide data: each selected column becomes a group (blanks dropped)."""
    header, data = _read_rows(path)
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
