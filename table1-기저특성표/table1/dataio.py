"""CSV loading and variable-type detection — pure standard library.

Reads a rectangular CSV into a simple in-memory frame (header + string cells),
tolerating a UTF-8 BOM, ragged rows, blank lines and quoted fields, and
optionally auto-sniffing the delimiter. Provides missing-value parsing and a
conservative continuous-vs-categorical classifier.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

# A number using commas purely as thousands separators, e.g. 1,234 or
# 1,234,567.89. Deliberately strict so a European decimal comma like "1,5"
# does NOT match (it would otherwise be silently mangled into 15).
_THOUSANDS_RE = re.compile(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")

__all__ = [
    "Frame",
    "load_frame",
    "parse_float",
    "is_numeric_token",
    "is_missing",
    "classify",
    "NA_LABELS",
]

# Case-insensitive tokens treated as missing. "." is included because SAS/SPSS
# exports use it for missing numerics.
NA_LABELS = {"", "NA", "N/A", "NAN", "NULL", "NONE", ".", "?", "MISSING"}


def is_missing(token: str) -> bool:
    return token.strip().upper() in NA_LABELS


def parse_float(token: str) -> Optional[float]:
    """Parse a cell to a finite float, or None for blank / NA / non-numeric.

    Non-finite values (inf, -inf, nan) are treated as missing so they can never
    poison a mean or variance.
    """
    t = token.strip()
    if t.upper() in NA_LABELS:
        return None
    try:
        v = float(t)
    except ValueError:
        # Strip commas ONLY when they are unambiguous thousands separators;
        # an ambiguous "1,5" (European decimal) is treated as non-numeric
        # (-> missing) rather than being silently corrupted into 15.
        if _THOUSANDS_RE.match(t):
            try:
                v = float(t.replace(",", ""))
            except ValueError:
                return None
        else:
            return None
    if not math.isfinite(v):
        return None
    return v


def is_numeric_token(token: str) -> bool:
    """True if the token is a numeric literal — INCLUDING the non-finite ones
    (inf, -inf, nan, overflowing literals like 1e999) that ``parse_float`` maps
    to None. Used by ``classify`` to distinguish "None because non-finite"
    (still a number, treat as missing) from "None because genuinely
    non-numeric" (a real category).
    """
    t = token.strip()
    if t.upper() in NA_LABELS:
        return False
    try:
        float(t)
        return True
    except ValueError:
        if _THOUSANDS_RE.match(t):
            try:
                float(t.replace(",", ""))
                return True
            except ValueError:
                return False
        return False


@dataclass
class Frame:
    header: List[str]
    rows: List[List[str]]
    index: Dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index = {}
        for i, name in enumerate(self.header):
            # First occurrence wins; duplicates are reported by load_frame.
            self.index.setdefault(name, i)

    def has(self, col: str) -> bool:
        return col in self.index

    def column(self, col: str) -> List[str]:
        """Return the raw string cells of a column (missing cells -> '')."""
        i = self.index[col]
        return [row[i] if i < len(row) else "" for row in self.rows]

    @property
    def nrows(self) -> int:
        return len(self.rows)


def _sniff_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        return dialect.delimiter
    except csv.Error:
        return ","


def load_frame(path: str, delimiter: Optional[str] = None) -> Frame:
    """Load a CSV into a Frame. Raises ValueError / FileNotFoundError on failure."""
    try:
        with open(path, "r", newline="", encoding="utf-8-sig") as fh:
            text = fh.read()
    except FileNotFoundError:
        raise
    except UnicodeDecodeError:
        raise ValueError(
            f"'{path}' 을(를) UTF-8로 읽을 수 없습니다. 엑셀에서 "
            "'CSV UTF-8'로 다시 저장한 뒤 실행하세요.")
    if not text.strip():
        raise ValueError(f"'{path}' 이(가) 비어 있습니다.")

    if delimiter is None:
        delimiter = _sniff_delimiter(text[:8192])
    if len(delimiter) != 1:
        raise ValueError(
            f"구분자(delimiter)는 한 글자여야 합니다: {delimiter!r}. "
            "탭은 --delimiter tab, 세로줄은 --delimiter '|' 처럼 쓰세요.")
    # Parse the whole text (not text.splitlines()) so a quoted field containing
    # an embedded newline is preserved as one cell instead of being split and
    # silently rejoined without its newline.
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    all_rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not all_rows:
        raise ValueError(f"'{path}' 에 데이터 행이 없습니다.")

    header = [h.strip() for h in all_rows[0]]
    if not any(header):
        raise ValueError("헤더(첫 행)가 비어 있습니다.")
    dupes = sorted({h for h in header if header.count(h) > 1 and h})
    if dupes:
        raise ValueError(
            "열 이름이 중복됩니다: " + ", ".join(dupes) +
            ". 각 열 이름을 고유하게 만드세요.")
    data = all_rows[1:]
    if not data:
        raise ValueError("데이터 행이 없습니다 (헤더만 있습니다).")
    return Frame(header, data)


def classify(values: Sequence[str], cat_max_levels: int = 2) -> str:
    """Classify a column as 'continuous' or 'categorical'.

    Rule (conservative and predictable):
      - any non-missing cell that is not numeric  -> categorical
      - all numeric but <= cat_max_levels distinct -> categorical (binary flags)
      - otherwise                                  -> continuous
    An all-missing column is reported as 'empty'.
    """
    nonmissing = [v for v in values if not is_missing(v)]
    if not nonmissing:
        return "empty"
    numeric = []
    for v in nonmissing:
        f = parse_float(v)
        if f is None:
            # A non-finite numeric (inf / -inf / overflow) parses to None but is
            # still a NUMBER — treat it as missing (consistent with the way
            # _continuous_row counts it), NOT as evidence of a categorical
            # variable. Only a genuinely non-numeric token forces categorical.
            if is_numeric_token(v):
                continue
            return "categorical"
        numeric.append(f)
    if not numeric:
        # Every non-missing cell was a non-finite numeric -> effectively all
        # missing; report empty rather than a spurious categorical.
        return "empty"
    distinct = len(set(numeric))
    if distinct <= max(1, cat_max_levels):
        return "categorical"
    return "continuous"
