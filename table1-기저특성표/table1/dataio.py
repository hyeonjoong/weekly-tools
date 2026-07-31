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
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .xlsx import is_legacy_xls, is_xlsx, load_xlsx_rows

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
    "numeric_profile",
    "NA_LABELS",
]

# A cell that begins with a number and carries a non-numeric suffix/prefix:
# ">100", "12 kg", "45%", "<0.01", "3.5±0.2". These are the shapes a lab/EDC
# export uses for censored or unit-carrying values.
_NUM_ISH_RE = re.compile(r"^\s*[<>=~]*\s*[+-]?\d+(?:[.,]\d+)?\s*\S")

# A mixed column is treated as continuous only when the numeric cells are the
# overwhelming majority; below this it is likelier a genuine category whose
# labels happen to be numeric.
_NUMERIC_MAJORITY = 0.8
# Numeric values that are all integers with small support look like an ordinal
# CODE (NYHA 1-4, a Likert item), not a measurement — never auto-promote those.
_INT_CODE_MAX = 10

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


def load_frame(path: str, delimiter: Optional[str] = None,
               sheet: Optional[str] = None) -> Frame:
    """Load a CSV or .xlsx worksheet into a Frame. Raises ValueError / OSError.

    ``path == "-"`` reads the CSV from standard input, so the tool can sit in a
    shell pipeline (e.g. ``cut -d, -f2- data.csv | table1 - --group arm``).

    An Excel workbook is detected by its container (not its extension) and read
    via the dependency-free reader in ``xlsx``; ``sheet`` picks a worksheet by
    name or 1-based index. ``delimiter`` is meaningless for a workbook and is
    rejected rather than silently ignored.
    """
    if path != "-" and is_xlsx(path):
        if delimiter is not None:
            raise ValueError(
                "--delimiter 는 CSV 전용입니다. 엑셀(.xlsx) 파일에는 쓸 수 "
                "없습니다(시트 선택은 --sheet).")
        all_rows = [r for r in load_xlsx_rows(path, sheet)
                    if any(cell.strip() for cell in r)]
        if not all_rows:
            raise ValueError(f"'{path}' 에 데이터 행이 없습니다.")
        return _frame_from_rows(all_rows, path)
    if sheet is not None:
        raise ValueError(
            "--sheet 는 엑셀(.xlsx) 파일에만 쓸 수 있습니다.")
    if path != "-" and is_legacy_xls(path):
        # A legacy OLE2 .xls is not a zip, so it would otherwise fall through to
        # the CSV reader and be blamed on the encoding — advice that can never
        # work for a binary workbook.
        raise ValueError(
            f"'{path}' 은(는) 구형 엑셀(.xls) 형식이라 읽을 수 없습니다. "
            "엑셀에서 '.xlsx' 또는 'CSV UTF-8'로 다시 저장한 뒤 실행하세요.")
    if path == "-":
        # Read bytes so we can strip a UTF-8 BOM and give the same friendly
        # non-UTF-8 message as for a file.
        raw = sys.stdin.buffer.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError(
                "표준입력(stdin)을 UTF-8로 읽을 수 없습니다. "
                "'CSV UTF-8'로 인코딩해 전달하세요.")
    else:
        try:
            with open(path, "r", newline="", encoding="utf-8-sig") as fh:
                text = fh.read()
        except (FileNotFoundError, IsADirectoryError, PermissionError):
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
    return _frame_from_rows(all_rows, path)


def _frame_from_rows(all_rows: List[List[str]], path: str) -> Frame:
    """Split parsed rows into a validated header + data Frame.

    Shared by the CSV and .xlsx paths so both enforce the same header rules
    (non-empty, no duplicates) and produce the same errors.
    """
    header = [h.strip() for h in all_rows[0]]
    if not any(header):
        raise ValueError("헤더(첫 행)가 비어 있습니다.")
    # Give every unnamed column a distinct placeholder name. Two blank headers
    # (a real shape: a stray trailing comma, or a spreadsheet exported with
    # spacer columns) would otherwise BOTH map to "" — and Frame.index keeps
    # only the first, so one column would be summarized twice under a blank
    # name while another was never summarized at all.
    taken = {h for h in header if h}
    for i, h in enumerate(header):
        if h:
            continue
        name = f"col_{i + 1}"
        while name in taken:
            name += "_"
        header[i] = name
        taken.add(name)
    dupes = sorted({h for h in header if header.count(h) > 1 and h})
    if dupes:
        raise ValueError(
            "열 이름이 중복됩니다: " + ", ".join(dupes) +
            ". 각 열 이름을 고유하게 만드세요.")
    data = all_rows[1:]
    if not data:
        raise ValueError("데이터 행이 없습니다 (헤더만 있습니다).")
    return Frame(header, data)


@dataclass
class NumericProfile:
    """How numeric a column's non-missing cells are (used for classification
    and for the 'this looks like a measurement' warnings)."""
    n_nonmissing: int = 0
    n_numeric: int = 0        # cells that parse to a finite float
    n_nonnumeric: int = 0     # genuinely non-numeric tokens (real categories)
    n_num_ish: int = 0        # non-numeric cells that still LOOK numeric
                              # (">100", "12 kg", "45%") — censored/unit values
    distinct_numeric: int = 0
    all_integer: bool = True

    @property
    def numeric_fraction(self) -> float:
        if not self.n_nonmissing:
            return 0.0
        return self.n_numeric / self.n_nonmissing

    @property
    def num_ish_fraction(self) -> float:
        """Fraction of cells that are a number OR a number with junk attached."""
        if not self.n_nonmissing:
            return 0.0
        return (self.n_numeric + self.n_num_ish) / self.n_nonmissing


def numeric_profile(values: Sequence[str]) -> NumericProfile:
    """Summarize how numeric a column is. Non-finite numerics count as missing
    (consistent with ``parse_float`` and ``_continuous_row``)."""
    p = NumericProfile()
    numbers = []
    for v in values:
        if is_missing(v):
            continue
        p.n_nonmissing += 1
        f = parse_float(v)
        if f is not None:
            p.n_numeric += 1
            numbers.append(f)
            continue
        if is_numeric_token(v):
            # inf/-inf/nan: a NUMBER that is effectively missing, not a category.
            p.n_nonmissing -= 1
            continue
        p.n_nonnumeric += 1
        if _NUM_ISH_RE.match(v):
            p.n_num_ish += 1
    distinct = sorted(set(numbers))
    p.distinct_numeric = len(distinct)
    p.all_integer = all(float(x).is_integer() for x in distinct)
    return p


def classify(values: Sequence[str], cat_max_levels: int = 2) -> str:
    """Classify a column as 'continuous' or 'categorical'.

    Rule (conservative and predictable):
      - all numeric, <= cat_max_levels distinct     -> categorical (binary flags)
      - all numeric, more distinct                  -> continuous
      - MOSTLY numeric with a few non-numeric cells -> continuous, IF the
        numbers look like a measurement rather than an ordinal code. This is the
        censored/unit-carrying lab value (one ">100" among 40 AHI readings):
        calling it categorical would render one level per patient and attach a
        meaningless chi-square to a continuous endpoint. ``_continuous_row``
        then reports the offending cells under its "unparseable" note instead of
        silently dropping them.
      - otherwise                                   -> categorical
    An all-missing column is reported as 'empty'.
    """
    p = numeric_profile(values)
    if p.n_nonmissing == 0:
        # Either no cells, or every non-missing cell was a non-finite numeric.
        return "empty"
    if p.n_numeric == 0:
        return "categorical"
    if p.n_nonnumeric == 0:
        if p.distinct_numeric <= max(1, cat_max_levels):
            return "categorical"
        return "continuous"

    # Mixed. Promote to continuous only when the numbers dominate AND they do
    # not look like a small-support integer code (NYHA 1-4 plus an "기타" level
    # is a genuine category; 39 AHI floats plus one ">100" is not).
    looks_like_code = p.all_integer and p.distinct_numeric <= _INT_CODE_MAX
    if (p.numeric_fraction >= _NUMERIC_MAJORITY
            and p.distinct_numeric > max(1, cat_max_levels)
            and not looks_like_code):
        return "continuous"
    return "categorical"
