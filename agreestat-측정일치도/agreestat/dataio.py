"""CSV loading for agreestat — pure standard library (csv module).

Input is a CSV of paired measurements: two numeric columns holding method A and
method B applied to the same subjects/samples, optionally a subject-id column
for repeated measures. Example::

    subject,sensor,band
    S01,14.2,14.0
    S01,15.1,14.8
    S02,11.9,12.3
    ...

Rows where either measurement is blank / NA / non-numeric are dropped pairwise
(and counted). If the A/B columns are not named explicitly they are auto-detected
as the first two mostly-numeric columns (excluding the subject column).
"""

from __future__ import annotations

import csv
from typing import List, Optional, Tuple

__all__ = ["load_pairs", "parse_float", "PairedData"]

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


class PairedData:
    """Container for aligned method-A / method-B values plus optional subject ids."""

    def __init__(self, a: List[float], b: List[float],
                 subjects: Optional[List[str]], name_a: str, name_b: str,
                 dropped: int):
        self.a = a
        self.b = b
        self.subjects = subjects
        self.name_a = name_a
        self.name_b = name_b
        self.dropped = dropped

    @property
    def n(self) -> int:
        return len(self.a)


def _auto_detect(header: List[str], data: List[List[str]],
                 exclude: Optional[str]) -> Tuple[str, str]:
    """Pick the first two columns that are mostly numeric (excluding *exclude*)."""
    numeric_cols: List[str] = []
    for j, name in enumerate(header):
        if exclude is not None and name == exclude:
            continue
        seen = ok = 0
        for row in data:
            if j < len(row) and row[j].strip() != "":
                seen += 1
                if parse_float(row[j]) is not None:
                    ok += 1
        if seen > 0 and ok >= 0.5 * seen:
            numeric_cols.append(name)
        if len(numeric_cols) == 2:
            break
    if len(numeric_cols) < 2:
        raise ValueError(
            "could not auto-detect two numeric measurement columns in "
            f"{header}. Specify them with --method-a and --method-b.")
    return numeric_cols[0], numeric_cols[1]


def load_pairs(path: str, col_a: Optional[str] = None,
               col_b: Optional[str] = None,
               subject_col: Optional[str] = None) -> PairedData:
    """Load paired measurements. Returns a :class:`PairedData`.

    ``col_a`` / ``col_b`` name the two measurement columns; if omitted they are
    auto-detected. ``subject_col`` (optional) names a subject-id column for
    repeated-measures handling.
    """
    header, data = _read_rows(path)

    if subject_col is not None and subject_col not in header:
        raise ValueError(f"subject column '{subject_col}' not in header {header}")

    if col_a is None or col_b is None:
        col_a, col_b = _auto_detect(header, data, subject_col)

    for c in (col_a, col_b):
        if c not in header:
            raise ValueError(f"column '{c}' not in header {header}")
    if col_a == col_b:
        raise ValueError("method A and method B must be different columns")

    ia, ib = header.index(col_a), header.index(col_b)
    isub = header.index(subject_col) if subject_col is not None else None

    a_vals: List[float] = []
    b_vals: List[float] = []
    subs: List[str] = []
    dropped = 0
    for row in data:
        if ia >= len(row) or ib >= len(row):
            dropped += 1
            continue
        va = parse_float(row[ia])
        vb = parse_float(row[ib])
        if va is None or vb is None:
            dropped += 1
            continue
        a_vals.append(va)
        b_vals.append(vb)
        if isub is not None:
            sid = row[isub].strip() if isub < len(row) else ""
            subs.append(sid)

    if not a_vals:
        raise ValueError("no usable numeric pairs found (check column names / data)")

    subjects = subs if subject_col is not None else None
    return PairedData(a_vals, b_vals, subjects, col_a, col_b, dropped)
