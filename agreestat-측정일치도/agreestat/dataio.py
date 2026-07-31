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
import io
import math
from collections import Counter
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = ["load_pairs", "load_categorical_pairs", "parse_float", "PairedData",
           "CategoricalData", "RaterTable", "load_rater_table", "reshape_long",
           "numeric_rater_rows", "label_rater_rows"]

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
        # io.StringIO, NOT text.splitlines(): str.splitlines() also breaks on
        # \x0b \x0c \x1c-\x1e \x85 \u2028 \u2029, so a vertical tab or a NEL byte
        # inside a cell would tear one record into two — silently dropping a
        # subject and inventing a phantom one. It also preserves newlines inside
        # correctly quoted multi-line fields.
        reader = csv.reader(io.StringIO(text, newline=""))
        raw_rows = list(reader)
    except csv.Error as exc:
        raise ValueError(f"CSV 파싱 오류: {exc}")
    rows = [row for row in raw_rows if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError(f"'{path}' is empty")
    notes = [enc_note] if enc_note else []
    if raw_rows and not any(cell.strip() for cell in raw_rows[0]):
        notes.append(
            "첫 줄이 비어 있어 그다음 줄을 헤더로 사용했습니다 — 엑셀에서 빈 줄이 "
            "섞여 저장된 파일일 수 있으니 열 이름이 맞는지 확인하세요.")
    header = [h.strip() for h in rows[0]]
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


def _has_categorical_candidates(header: List[str], data: List[List[str]],
                                exclude: Optional[str]) -> bool:
    """True if the file looks like it holds two rater/classification columns."""
    try:
        return len(_categorical_columns(header, data, exclude)) >= 2
    except ValueError:  # pragma: no cover - defensive
        return False


def _auto_detect(header: List[str], data: List[List[str]],
                 exclude: Optional[str]) -> Tuple[str, str, List[str]]:
    """Pick the first two numeric columns (excluding *exclude*), skipping ids.

    Returns (col_a, col_b, notes). Warns when >2 numeric columns exist (the
    choice is a guess) or when a chosen column looks like an identifier.
    """
    cols = _numeric_columns(header, data, exclude)
    if len(cols) < 2:
        hint = ""
        if _has_categorical_candidates(header, data, exclude):
            hint = (" 열 값이 숫자가 아니라 범주(예: 등급·판정 라벨)로 보입니다 — "
                    "범주형 일치도(kappa)를 원하면 --categorical 을 붙이세요.")
        raise ValueError(
            "could not auto-detect two numeric measurement columns in "
            f"{header}. Specify them with --method-a and --method-b." + hint)

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


# --------------------------------------------------------------------------
# Categorical (rater vs rater) loading
# --------------------------------------------------------------------------
_MAX_AUTO_CATEGORIES = 20

# Hard ceiling on distinct labels. Above this the input is almost certainly
# continuous data fed to --categorical by mistake, and the k x k matrices
# (confusion table, weights, ordinal delta^2) would blow up quadratically:
# k=4000 already costs ~1.2 GB and ~23 s. Refuse instead of thrashing.
_MAX_CATEGORIES = 200

# Missing-value tokens for CATEGORICAL data. Deliberately far narrower than
# _NA_LABELS (used for numeric columns): "None", "-", ".", "NA" are all real
# clinical category labels ("None/Mild/Severe", "+/-" culture results), and
# silently dropping them turns a genuine disagreement into perfect agreement.
# Only a blank cell and Excel's own error token count as missing by default;
# anything else the user must declare with --na.
_CAT_NA_LABELS = frozenset({"#N/A"})

# Labels that are *suspicious* as data: if one of these survives as a real
# category we say so, so the user can declare it missing with --na instead.
_SUSPICIOUS_NA_LIKE = frozenset({
    "NA", "N/A", "NAN", "NULL", "NAT", "MISSING", "UNKNOWN", "?", "#NULL!",
})


class CategoricalData:
    """Container for two raters' aligned category labels."""

    def __init__(self, a: List[str], b: List[str], name_a: str, name_b: str,
                 dropped: int, notes: Optional[List[str]] = None,
                 subjects: Optional[List[str]] = None):
        self.a = a
        self.b = b
        self.name_a = name_a
        self.name_b = name_b
        self.dropped = dropped
        self.notes = notes or []
        self.subjects = subjects

    @property
    def n(self) -> int:
        return len(self.a)


def _categorical_columns(header: List[str], data: List[List[str]],
                         exclude: Optional[str],
                         na: frozenset = _CAT_NA_LABELS) -> List[str]:
    """Columns that look like a classification: few distinct, repeated values.

    A rating column has a small, repeating label set. Requiring at least one
    repeat rules out free-text notes and per-row identifiers, and the 20-category
    ceiling rules out an id/continuous column that happens to repeat.
    """
    out: List[str] = []
    for j, name in enumerate(header):
        if exclude is not None and name == exclude:
            continue
        vals = []
        for row in data:
            if j >= len(row):
                continue
            cell = row[j].strip()
            if cell == "" or cell.upper() in na:
                continue
            vals.append(cell)
        if len(vals) < 2:
            continue
        card = len(set(vals))
        if 2 <= card <= _MAX_AUTO_CATEGORIES and card < len(vals):
            out.append(name)
    return out


def _auto_detect_categorical(header: List[str], data: List[List[str]],
                             exclude: Optional[str],
                             na: frozenset = _CAT_NA_LABELS
                             ) -> Tuple[str, str, List[str]]:
    cols = _categorical_columns(header, data, exclude, na)
    if len(cols) < 2:
        # Distinguish "no rating column found" from the much more likely
        # "this is continuous data run through --categorical by mistake":
        # the numeric loader recognises >=2 numeric columns here.
        if len(_numeric_columns(header, data, exclude)) >= 2:
            raise ValueError(
                "범주형 열을 찾지 못했습니다 — 값이 서로 다른 숫자뿐이라 연속형 "
                "자료로 보입니다. 연속형이라면 --categorical 없이 실행해 "
                "Bland–Altman/ICC로 분석하세요. 범주형이 맞다면 -a/-b 로 열을 "
                "직접 지정하세요.")
        raise ValueError(
            "could not auto-detect two rating columns in "
            f"{header}. Specify them with --method-a and --method-b "
            "(범주형 열은 값이 2~20종이고 반복되어야 자동 인식됩니다).")
    notes: List[str] = []
    if len(cols) > 2:
        notes.append(
            f"범주형 후보 열이 3개 이상입니다 {cols} — '{cols[0]}'와 '{cols[1]}'를 "
            "자동 선택했습니다. 의도와 다르면 -a/-b로 직접 지정하세요.")
    return cols[0], cols[1], notes


def load_categorical_pairs(path: str, col_a: Optional[str] = None,
                           col_b: Optional[str] = None,
                           encoding: Optional[str] = None,
                           subject_col: Optional[str] = None,
                           na_labels: Optional[Sequence[str]] = None
                           ) -> CategoricalData:
    """Load two raters' paired category labels from a CSV.

    Values are kept as trimmed strings (so ``2`` and ``2.0`` are *different*
    labels — a deliberate choice: category identity is the user's, not ours).

    Only **blank** cells (and ``#N/A``) count as missing by default. Unlike the
    numeric loader, tokens such as ``NA``, ``None``, ``-`` or ``.`` are treated
    as **real category labels**, because they are: "None/Mild/Severe" and "+/-"
    are ordinary clinical scales, and dropping them would silently delete the
    very rows where the raters disagree. Pass ``na_labels`` (CLI: ``--na``) to
    declare additional tokens as missing. Rows missing either rating are dropped
    pairwise and counted.
    """
    header, data, notes = _read_rows(path, encoding)
    na = (frozenset(s.strip().upper() for s in na_labels if s.strip())
          if na_labels else _CAT_NA_LABELS)

    if subject_col is not None and subject_col not in header:
        raise ValueError(f"subject column '{subject_col}' not in header {header}")

    dup = sorted({h for h in header if header.count(h) > 1})
    if dup:
        raise ValueError(
            f"헤더에 같은 이름의 열이 중복됩니다: {dup}. 열 이름을 서로 다르게 "
            "고친 뒤 다시 실행하세요 (어느 열을 뜻하는지 특정할 수 없습니다).")

    if col_a is None or col_b is None:
        col_a, col_b, det = _auto_detect_categorical(header, data, subject_col, na)
        notes = notes + det

    for c in (col_a, col_b):
        if c not in header:
            raise ValueError(f"column '{c}' not in header {header}")
    if col_a == col_b:
        raise ValueError("rater A and rater B must be different columns")

    ia, ib = header.index(col_a), header.index(col_b)
    isub = header.index(subject_col) if subject_col is not None else None
    a_vals: List[str] = []
    b_vals: List[str] = []
    subs: List[str] = []
    dropped = 0
    for row in data:
        if ia >= len(row) or ib >= len(row):
            dropped += 1
            continue
        va, vb = row[ia].strip(), row[ib].strip()
        if (va == "" or va.upper() in na or vb == "" or vb.upper() in na):
            dropped += 1
            continue
        a_vals.append(va)
        b_vals.append(vb)
        if isub is not None:
            subs.append(row[isub].strip() if isub < len(row) else "")

    if not a_vals:
        raise ValueError("no usable rating pairs found (check column names / data)")

    labels = set(a_vals) | set(b_vals)
    k = len(labels)
    if k > _MAX_CATEGORIES:
        raise ValueError(
            f"서로 다른 범주가 {k}종으로 너무 많습니다(최대 {_MAX_CATEGORIES}). "
            "연속형(숫자) 자료를 --categorical 로 분석하려는 것 같습니다 — "
            "연속형이라면 --categorical 없이 Bland–Altman/ICC로 분석하세요. "
            "범주형이 맞다면 희소한 범주를 먼저 병합하세요.")

    cards = (len(set(a_vals)), len(set(b_vals)))
    if max(cards) > _MAX_AUTO_CATEGORIES:
        notes.append(
            f"범주 수가 많습니다 (A={cards[0]}종, B={cards[1]}종). 연속형 자료를 "
            "범주형으로 분석하고 있지 않은지 확인하세요 — 연속형이라면 "
            "--categorical 없이 Bland–Altman/ICC로 분석해야 합니다.")

    # A surviving NA-looking label is either a real category (fine — but say so)
    # or a missing marker the user forgot to declare (a silent-wrong-answer bug).
    na_like = sorted(v for v in labels if v.upper() in _SUSPICIOUS_NA_LIKE)
    if na_like:
        notes.append(
            f"{na_like} 을(를) 결측이 아니라 **실제 범주**로 포함했습니다. "
            f"결측을 뜻한다면 --na \"{','.join(na_like)}\" 로 지정해 제외하세요 "
            "(범주형에서는 'None'·'-'·'.' 같은 값이 실제 등급일 수 있어 "
            "임의로 버리지 않습니다).")

    subjects = subs if subject_col is not None else None
    return CategoricalData(a_vals, b_vals, col_a, col_b, dropped, notes, subjects)


# --------------------------------------------------------------------------
# Multi-rater (3+ columns) and long-format ("tidy") input
# --------------------------------------------------------------------------
class RaterTable:
    """A subjects x raters table of *raw* cells, before numeric/label parsing."""

    def __init__(self, names: List[str], rows: List[List[str]],
                 subjects: Optional[List[str]] = None,
                 dropped: int = 0, nonfinite: int = 0,
                 notes: Optional[List[str]] = None):
        self.names = names
        self.rows = rows
        self.subjects = subjects
        self.dropped = dropped
        self.nonfinite = nonfinite
        self.notes = notes or []

    @property
    def n(self) -> int:
        return len(self.rows)

    @property
    def k(self) -> int:
        return len(self.names)


def _short(values: Sequence[str], limit: int = 5, width: int = 16) -> str:
    """Render a list of raw cell/column values without leaking a whole column.

    Input files hold patient identifiers, so error messages must never echo an
    unbounded list of raw values: truncate each item and cap the count.
    """
    vals = list(values)
    shown = [(v[:width] + "…") if len(v) > width else v for v in vals[:limit]]
    txt = ", ".join(repr(v) for v in shown)
    if len(vals) > limit:
        txt += f", … 외 {len(vals) - limit}개"
    return "[" + txt + "]"


def _require_unique_header(header: List[str]) -> None:
    counts = Counter(header)          # O(k); header.count() in a loop is O(k^2)
    dup = sorted(h for h, c in counts.items() if c > 1)
    if dup:
        raise ValueError(
            f"헤더에 같은 이름의 열이 {len(dup)}종 중복됩니다: {_short(dup)}. "
            "열 이름을 서로 다르게 고친 뒤 다시 실행하세요 (어느 열을 뜻하는지 "
            "특정할 수 없습니다).")


# A long-format file could name millions of distinct methods if the user points
# --method-col at the wrong column; refuse instead of building a huge table.
_MAX_RATERS = 100

# Category labels are printed in reports and embedded in JSON/Markdown output.
# Anything longer than this is free text or an identifier, not a rating.
_MAX_LABEL_LEN = 64


def reshape_long(header: List[str], data: List[List[str]], id_col: str,
                 method_col: str, value_col: str,
                 levels: Optional[Sequence[str]] = None
                 ) -> Tuple[List[str], List[List[str]], List[str], List[str]]:
    """Pivot a long/tidy table to wide form.

    Returns ``(rater_names, rows, subject_ids, notes)`` where ``rows[i][j]`` is
    the raw cell for subject ``i`` and rater ``j`` (``""`` when that subject was
    never rated by that rater). Subjects keep first-appearance order.

    A repeated ``(id, method)`` combination is an error: silently averaging or
    keeping the last value would change the analysis without telling anyone.
    """
    _require_unique_header(header)
    for name, col in (("--id-col", id_col), ("--method-col", method_col),
                      ("--value-col", value_col)):
        if col not in header:
            raise ValueError(f"{name} 로 지정한 열 '{col}' 이(가) 헤더에 "
                             f"없습니다: {header}")
    if len({id_col, method_col, value_col}) != 3:
        raise ValueError("--id-col / --method-col / --value-col 은 서로 다른 "
                         "열이어야 합니다.")

    ii, im, iv = header.index(id_col), header.index(method_col), \
        header.index(value_col)
    notes: List[str] = []
    wanted = None
    if levels is not None:
        wanted = list(dict.fromkeys(levels))

    order: List[str] = []                 # subject ids, first-appearance order
    seen_ids: set = set()                 # membership test must stay O(1)
    seen_methods: List[str] = []
    method_set: set = set()
    cells: Dict[Tuple[str, str], str] = {}
    skipped_key = 0
    skipped_level = 0
    dup_rows: List[int] = []
    for lineno, row in enumerate(data, start=2):   # +1 header, +1 to 1-index
        if max(ii, im, iv) >= len(row):
            skipped_key += 1
            continue
        sid = row[ii].strip()
        meth = row[im].strip()
        if sid == "" or meth == "":
            skipped_key += 1
            continue
        if wanted is not None and meth not in wanted:
            skipped_level += 1
            continue
        if meth not in method_set:
            if len(seen_methods) >= _MAX_RATERS:
                raise ValueError(
                    f"--method-col '{method_col}' 에 서로 다른 값이 "
                    f"{_MAX_RATERS}개를 넘습니다. 평가자/방법 이름 열이 맞는지 "
                    "확인하세요 (측정값 열을 지정한 것 같습니다).")
            seen_methods.append(meth)
            method_set.add(meth)
        key = (sid, meth)
        if key in cells:
            # Deliberately does NOT echo the id: it is a patient identifier and
            # this is the message users paste into bug reports.
            dup_rows.append(lineno)
            if len(dup_rows) >= 5:
                break
            continue
        cells[key] = row[iv]
        if sid not in seen_ids:
            seen_ids.add(sid)
            order.append(sid)

    if dup_rows:
        raise ValueError(
            f"긴 형식 자료에서 ('{id_col}', '{method_col}') 조합이 중복된 행을 "
            f"발견했습니다 (입력 파일 {dup_rows[0]}행 등 최소 {len(dup_rows)}건). "
            "반복측정이라면 회차를 ID에 포함해 구분하거나(예: S01_v1), 해당 "
            "회차만 걸러서 입력하세요 — 임의로 평균/마지막 값을 쓰면 결과가 "
            "조용히 달라집니다.")
    if skipped_key:
        notes.append(f"ID 또는 방법 이름이 비어 있는 {skipped_key}행을 "
                     "제외했습니다.")
    if skipped_level:
        notes.append(f"--raters 로 지정하지 않은 방법의 {skipped_level}행을 "
                     "제외했습니다.")

    names = wanted if wanted is not None else seen_methods
    if wanted is not None:
        missing = [m for m in wanted if m not in seen_methods]
        if missing:
            raise ValueError(
                f"--raters 로 지정한 방법이 자료에 없습니다: {_short(missing)}. "
                f"'{method_col}' 열에서 발견한 값: {_short(seen_methods)}")
    if len(names) < 2:
        raise ValueError(
            f"'{method_col}' 열에서 서로 다른 방법이 {len(names)}개만 "
            "발견됐습니다 — 긴 형식 분석에는 2개 이상이 필요합니다.")
    if not order:
        raise ValueError("긴 형식 자료에서 사용할 수 있는 행이 없습니다 "
                         "(--id-col/--method-col/--value-col 을 확인하세요).")

    rows = [[cells.get((sid, m), "") for m in names] for sid in order]
    return names, rows, order, notes


def load_rater_table(path: str, rater_cols: Optional[Sequence[str]] = None,
                     encoding: Optional[str] = None,
                     long_format: bool = False,
                     id_col: Optional[str] = None,
                     method_col: Optional[str] = None,
                     value_col: Optional[str] = None) -> RaterTable:
    """Load a subjects x raters table from wide or long CSV.

    Wide: ``rater_cols`` names 2+ measurement columns.
    Long: ``long_format=True`` with ``id_col``/``method_col``/``value_col``;
    ``rater_cols`` then selects/orders the method levels.
    """
    header, data, notes = _read_rows(path, encoding)
    _require_unique_header(header)

    if long_format:
        if not (id_col and method_col and value_col):
            raise ValueError("--long 에는 --id-col, --method-col, --value-col "
                             "을 모두 지정해야 합니다.")
        names, rows, subjects, ln = reshape_long(
            header, data, id_col, method_col, value_col, rater_cols)
        return RaterTable(names, rows, subjects, 0, 0, notes + ln)

    if not rater_cols:
        raise ValueError("평가자 열 목록이 필요합니다 (--raters).")
    names = list(dict.fromkeys(c.strip() for c in rater_cols if c.strip()))
    if len(names) != len([c for c in rater_cols if c.strip()]):
        raise ValueError("--raters 에 중복된 열 이름이 있습니다.")
    if len(names) < 2:
        raise ValueError("--raters 에는 열 이름이 2개 이상 필요합니다.")
    if len(names) > _MAX_RATERS:
        raise ValueError(
            f"--raters 에 지정한 평가자가 {len(names)}명으로 상한"
            f"({_MAX_RATERS})을 넘습니다. 분석할 평가자를 줄여 주세요.")
    missing = [c for c in names if c not in header]
    if missing:
        raise ValueError(f"--raters 로 지정한 열이 헤더에 없습니다: "
                         f"{_short(missing)}. 사용 가능한 열: {_short(header)}")
    idx = [header.index(c) for c in names]
    rows = [[(row[j] if j < len(row) else "") for j in idx] for row in data]
    return RaterTable(names, rows, None, 0, 0, notes)


def numeric_rater_rows(table: RaterTable) -> Tuple[List[List[float]], List[str], int, int]:
    """Parse a :class:`RaterTable` to floats, dropping incomplete subjects.

    Returns ``(rows, subject_ids, dropped, nonfinite)``. A subject is kept only
    when *every* rater has a usable finite number (listwise deletion — the ICC
    model needs a balanced table).
    """
    out: List[List[float]] = []
    ids: List[str] = []
    dropped = 0
    nonfinite = 0
    for i, row in enumerate(table.rows):
        vals = [parse_float(c) for c in row]
        if any(v is None for v in vals):
            dropped += 1
            if any(_is_abnormal_number(c) for c in row):
                nonfinite += 1
            continue
        out.append([v for v in vals])  # type: ignore[misc]
        if table.subjects is not None:
            ids.append(table.subjects[i])
    if not out:
        raise ValueError(
            "모든 평가자에 대해 숫자가 채워진 대상이 없습니다 (열 이름과 자료를 "
            "확인하세요). 값이 숫자가 아니라 범주 라벨(등급·판정)이라면 "
            "--categorical 을 함께 주세요.")
    return out, ids, dropped, nonfinite


def label_rater_rows(table: RaterTable,
                     na_labels: Optional[Sequence[str]] = None
                     ) -> Tuple[List[List[str]], int, List[str]]:
    """Clean a :class:`RaterTable` into category labels.

    Missing cells become ``""``. Subjects with fewer than 2 ratings are dropped
    (nothing to compare). Returns ``(rows, dropped, notes)``.
    """
    na = (frozenset(s.strip().upper() for s in na_labels if s.strip())
          if na_labels else _CAT_NA_LABELS)
    rows: List[List[str]] = []
    dropped = 0
    for row in table.rows:
        clean = []
        for cell in row:
            v = cell.strip()
            clean.append("" if (v == "" or v.upper() in na) else v)
        if sum(1 for v in clean if v != "") < 2:
            dropped += 1
            continue
        rows.append(clean)
    if not rows:
        raise ValueError("두 명 이상이 평가한 대상이 없습니다 "
                         "(열 이름과 결측 처리(--na)를 확인하세요).")

    labels = {v for row in rows for v in row if v != ""}
    too_long = [v for v in labels if len(v) > _MAX_LABEL_LEN]
    if too_long:
        raise ValueError(
            f"범주 라벨이 {_MAX_LABEL_LEN}자를 넘는 값이 {len(too_long)}종 "
            "있습니다. 자유서술 텍스트나 식별자 열을 범주로 지정한 것 같습니다 "
            "— 판정 라벨 열인지 확인하세요.")
    if len(labels) > _MAX_CATEGORIES:
        raise ValueError(
            f"서로 다른 범주가 {len(labels)}종으로 너무 많습니다"
            f"(최대 {_MAX_CATEGORIES}). 연속형(숫자) 자료라면 --categorical "
            "없이 ICC로 분석하세요.")
    notes: List[str] = []
    if len(labels) > _MAX_AUTO_CATEGORIES:
        notes.append(
            f"범주 수가 많습니다({len(labels)}종). 연속형 자료를 범주형으로 "
            "분석하고 있지 않은지 확인하세요.")
    na_like = sorted(v for v in labels if v.upper() in _SUSPICIOUS_NA_LIKE)
    if na_like:
        notes.append(
            f"{na_like} 을(를) 결측이 아니라 **실제 범주**로 포함했습니다. "
            f"결측을 뜻한다면 --na \"{','.join(na_like)}\" 로 지정하세요.")
    return rows, dropped, notes
