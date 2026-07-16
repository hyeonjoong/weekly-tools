"""CSV loading for agreestat — pure standard library (csv module).

Input is a CSV of paired measurements: two numeric columns holding method A and
method B applied to the same subjects/samples, optionally a subject-id column
for repeated measures. Example::

    subject,sensor,band
    S01,14.2,14.0
    S01,15.1,14.8
    S02,11.9,12.3
    ...

Rows where either measurement is blank / NA / non-numeric / non-finite are
dropped pairwise (and counted). If the A/B columns are not named explicitly they
are auto-detected as the first two mostly-numeric columns (excluding the subject
column). Files are decoded UTF-8/UTF-16/CP949/EUC-KR (Korean Excel exports)
automatically, or with an explicit ``encoding``.
"""

from __future__ import annotations

import csv
import math
from typing import List, Optional, Tuple

__all__ = ["load_pairs", "parse_float", "PairedData"]

_NA_LABELS = {"NA", "N/A", "NAN", "NULL", ".", "-", "NAT", "NONE", "#N/A"}

# Values whose square would overflow a float (sqrt(max) ~ 1.34e154) crash the
# variance sum. No real clinical measurement is anywhere near this, so treat
# absurdly large magnitudes as abnormal (dropped like inf), not as data.
_MAX_ABS = 1e150

# csv.reader has a hard field-size limit (131072 chars); a single pathological
# unterminated-quote field can exceed it. Raise it once, with a sane ceiling so
# a malformed file cannot consume unbounded memory.
try:
    csv.field_size_limit(16 * 1024 * 1024)
except (OverflowError, ValueError):  # pragma: no cover - platform dependent
    pass


def parse_float(token: str) -> Optional[float]:
    """Parse a cell into a *finite, in-range* float, else None.

    Returns None for blanks / NA / non-numeric, for non-finite numbers
    (``inf``, ``-inf``, ``1e999`` -> inf, ``nan``), and for absurdly large
    magnitudes (>1e150) whose square would overflow the variance sum — so a
    stray infinity or 1e308 cannot silently poison or crash the statistics.
    """
    t = token.strip()
    if t == "" or t.upper() in _NA_LABELS:
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    if not math.isfinite(v) or abs(v) > _MAX_ABS:
        return None
    return v


def _is_abnormal_number(token: str) -> bool:
    """True if the cell parses as a float but is inf/-inf/nan or |v|>1e150."""
    t = token.strip()
    if t == "" or t.upper() in _NA_LABELS:
        return False
    try:
        v = float(t)
    except ValueError:
        return False
    return not math.isfinite(v) or abs(v) > _MAX_ABS


def _decode(raw: bytes, encoding: Optional[str]) -> Tuple[str, Optional[str]]:
    """Decode CSV bytes; return (text, note) where note flags a risky decode.

    Order: explicit -> UTF-8/UTF-16 BOM -> UTF-8 -> CP949 -> EUC-KR -> Latin-1
    (last resort, never raises). Korean Excel exports are typically CP949. A
    BOM-less UTF-16 file (NUL-heavy) and the Latin-1 fallback are flagged so a
    silent mojibake decode does not pass unnoticed.
    """
    if encoding:
        return raw.decode(encoding), None
    if raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig"), None
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16"), None
    # BOM-less UTF-16 shows up as many NUL bytes in otherwise-ASCII content.
    sample = raw[:4096]
    if sample and sample.count(0) > len(sample) // 4:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                return raw.decode(enc), (
                    "BOM 없는 UTF-16으로 보여 자동 디코딩했습니다. 문자가 "
                    "깨지면 --encoding utf-16 을 지정하세요.")
            except UnicodeDecodeError:
                pass
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(enc), None
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), (
        "인코딩 자동 감지에 실패해 latin-1로 강제 디코딩했습니다. 열 이름·문자가 "
        "깨졌을 수 있으니(숫자 계산은 영향 없음) --encoding 으로 지정하세요.")


def _read_rows(path: str, encoding: Optional[str] = None
               ) -> Tuple[List[str], List[List[str]], List[str]]:
    with open(path, "rb") as fh:
        raw = fh.read()
    if not raw.strip():
        raise ValueError(f"'{path}' is empty")
    try:
        text, enc_note = _decode(raw, encoding)
    except (UnicodeDecodeError, LookupError) as exc:
        raise ValueError(
            f"파일 인코딩을 읽을 수 없습니다 ({exc}). UTF-8로 저장하거나 "
            "--encoding 으로 지정하세요 (예: --encoding cp949).")
    try:
        reader = csv.reader(text.splitlines())
        rows = [row for row in reader if any(cell.strip() for cell in row)]
    except csv.Error as exc:
        raise ValueError(f"CSV 파싱 오류: {exc}")
    if not rows:
        raise ValueError(f"'{path}' is empty")
    header = [h.strip() for h in rows[0]]
    notes = [enc_note] if enc_note else []
    return header, rows[1:], notes


class PairedData:
    """Container for aligned method-A / method-B values plus optional subject ids."""

    def __init__(self, a: List[float], b: List[float],
                 subjects: Optional[List[str]], name_a: str, name_b: str,
                 dropped: int, nonfinite: int = 0,
                 notes: Optional[List[str]] = None):
        self.a = a
        self.b = b
        self.subjects = subjects
        self.name_a = name_a
        self.name_b = name_b
        self.dropped = dropped
        self.nonfinite = nonfinite
        self.notes = notes or []

    @property
    def n(self) -> int:
        return len(self.a)


def _looks_like_id(values: List[float]) -> bool:
    """Heuristic: strictly monotonic, all-distinct integers look like a row id.

    Requiring monotonicity (not merely distinct integers) avoids misclassifying
    an ordinary integer-valued measurement column (e.g. glucose 95/110/88) as an
    identifier, while still catching sequential patient/row ids.
    """
    if len(values) < 3:
        return False
    if any(v != int(v) for v in values):
        return False
    if len(set(values)) != len(values):
        return False
    increasing = all(values[i] < values[i + 1] for i in range(len(values) - 1))
    decreasing = all(values[i] > values[i + 1] for i in range(len(values) - 1))
    return increasing or decreasing


def _numeric_columns(header: List[str], data: List[List[str]],
                     exclude: Optional[str]) -> List[Tuple[str, List[float]]]:
    """Return (name, parsed-values) for every mostly-numeric column."""
    out: List[Tuple[str, List[float]]] = []
    for j, name in enumerate(header):
        if exclude is not None and name == exclude:
            continue
        seen = 0
        vals: List[float] = []
        for row in data:
            if j >= len(row):
                continue
            cell = row[j].strip()
            if cell == "" or cell.upper() in _NA_LABELS:
                continue  # blanks/NA don't count against "numeric-ness"
            seen += 1
            v = parse_float(cell)
            if v is not None:
                vals.append(v)
        if seen > 0 and len(vals) >= 0.5 * seen:
            out.append((name, vals))
    return out


def _auto_detect(header: List[str], data: List[List[str]],
                 exclude: Optional[str]) -> Tuple[str, str, List[str]]:
    """Pick the first two numeric columns (excluding *exclude*), skipping ids.

    Returns (col_a, col_b, notes). Warns when >2 numeric columns exist (the
    choice is a guess) or when a chosen column looks like an identifier.
    """
    cols = _numeric_columns(header, data, exclude)
    if len(cols) < 2:
        raise ValueError(
            "could not auto-detect two numeric measurement columns in "
            f"{header}. Specify them with --method-a and --method-b.")

    notes: List[str] = []
    id_names = [c[0] for c in cols if _looks_like_id(c[1])]
    non_id = [c for c in cols if not _looks_like_id(c[1])]
    chosen = non_id if len(non_id) >= 2 else cols
    a_name, a_vals = chosen[0]
    b_name, b_vals = chosen[1]

    dropped_ids = [nm for nm in id_names if nm not in (a_name, b_name)]
    if dropped_ids:
        notes.append(
            f"열 {dropped_ids}을(를) 식별자(ID)로 보고 자동 선택에서 제외했습니다 "
            "(순증가/순감소하는 서로 다른 정수). 측정열이면 -a/-b로 직접 지정하세요.")
    if len(cols) > 2:
        notes.append(
            f"수치열이 3개 이상입니다 {[c[0] for c in cols]} — "
            f"'{a_name}'와 '{b_name}'를 자동 선택했습니다. 의도와 다르면 "
            "-a/-b로 직접 지정하세요.")
    if _looks_like_id(a_vals) or _looks_like_id(b_vals):
        notes.append(
            f"자동 선택된 열('{a_name}' 또는 '{b_name}')이 식별자(ID)처럼 "
            "보입니다(모두 서로 다른 정수). 측정열이 맞는지 확인하세요.")
    return a_name, b_name, notes


def load_pairs(path: str, col_a: Optional[str] = None,
               col_b: Optional[str] = None,
               subject_col: Optional[str] = None,
               encoding: Optional[str] = None) -> PairedData:
    """Load paired measurements. Returns a :class:`PairedData`.

    ``col_a`` / ``col_b`` name the two measurement columns; if omitted they are
    auto-detected. ``subject_col`` (optional) names a subject-id column for
    repeated-measures handling. ``encoding`` forces a text encoding.
    """
    header, data, notes = _read_rows(path, encoding)

    if subject_col is not None and subject_col not in header:
        raise ValueError(f"subject column '{subject_col}' not in header {header}")

    if col_a is None or col_b is None:
        col_a, col_b, det_notes = _auto_detect(header, data, subject_col)
        notes = notes + det_notes

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
    nonfinite = 0
    for row in data:
        if ia >= len(row) or ib >= len(row):
            dropped += 1
            continue
        va = parse_float(row[ia])
        vb = parse_float(row[ib])
        if va is None or vb is None:
            dropped += 1
            if _is_abnormal_number(row[ia]) or _is_abnormal_number(row[ib]):
                nonfinite += 1
            continue
        a_vals.append(va)
        b_vals.append(vb)
        if isub is not None:
            sid = row[isub].strip() if isub < len(row) else ""
            subs.append(sid)

    if not a_vals:
        raise ValueError("no usable numeric pairs found (check column names / data)")

    subjects = subs if subject_col is not None else None
    return PairedData(a_vals, b_vals, subjects, col_a, col_b, dropped,
                      nonfinite, notes)
