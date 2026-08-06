"""Reading real-world clinical CSVs without pandas.

Clinical exports are messy in predictable ways: a UTF-8 BOM from Excel, CP949
from Korean Windows, semicolon or tab separators, thousands separators inside
numbers, ``N/A`` / ``.`` / ``미측정`` for missing, ``<0.5`` for below the assay's
detection limit, and outcome columns spelled ``Yes``/``양성``/``1`` depending on
who typed them. Everything this module drops or rewrites is counted and surfaced
in the report, so nothing disappears silently.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "Table",
    "infer_decimal_comma",
    "LoadError",
    "read_table",
    "resolve_column",
    "parse_number",
    "parse_label_column",
    "MISSING_TOKENS",
]


class LoadError(Exception):
    """Raised for input problems the user can fix (bad column, unusable labels)."""


# Lower-cased strings that mean "no value here".
MISSING_TOKENS = {
    "", "na", "n/a", "n.a.", "nan", "none", "null", "nil", "missing", "unknown",
    ".", "-", "--", "?", "#n/a", "#na", "#null!", "#div/0!", "미측정", "결측",
    "해당없음", "무응답", "미상",
}
# NOTE: "없음" is deliberately NOT a missing token — 있음/없음 is a common way to
# code a binary outcome in Korean data, and treating it as missing silently threw
# away the entire control group.

_ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr", "utf-16", "latin-1")
_DELIMITERS = (",", "\t", ";", "|")

# "1,234.5", "12%", "<0.5", "≥100", "1.2e-3", "(3.4)" (accounting negative)
_NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


@dataclass
class Table:
    """A parsed CSV: header names plus rows of raw strings."""

    headers: List[str]
    rows: List[List[str]]
    encoding: str
    delimiter: str
    path: str = ""
    notes: List[str] = field(default_factory=list)

    def column(self, name: str) -> List[str]:
        idx = self.headers.index(name)
        return [r[idx] if idx < len(r) else "" for r in self.rows]

    def __len__(self) -> int:
        return len(self.rows)


def _decode(raw: bytes, encoding: Optional[str]) -> Tuple[str, str]:
    if encoding:
        try:
            return raw.decode(encoding), encoding
        except LookupError as exc:
            raise LoadError(
                f"알 수 없는 인코딩입니다 / unknown encoding: '{encoding}' "
                f"(예: utf-8, utf-8-sig, cp949, euc-kr, latin-1)"
            ) from exc
        except (UnicodeDecodeError, UnicodeError) as exc:
            raise LoadError(
                f"'{encoding}' 인코딩으로 파일을 읽을 수 없습니다 / cannot decode this "
                f"file as '{encoding}'. --encoding 을 빼고 자동 판별을 쓰거나 다른 "
                f"인코딩을 지정하세요 ({exc})"
            ) from exc
    has_bom = raw[:2] in (b"\xff\xfe", b"\xfe\xff")
    for enc in _ENCODINGS:
        # UTF-16 without a byte-order mark is never a safe guess: a single stray
        # high byte in an ASCII file decodes "successfully" into CJK mojibake.
        if enc == "utf-16" and not has_bom:
            continue
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        # Reject decodings that leave stray control bytes (a sign of a wrong codec).
        if any(ord(ch) < 32 and ch not in "\r\n\t" for ch in text[:4000]):
            continue
        return text, enc
    return raw.decode("latin-1", errors="replace"), "latin-1"


def _normalise_delimiter(delimiter: Optional[str]) -> Optional[str]:
    """Accept ``\t`` (typed literally on a shell) as well as a real tab."""
    if delimiter is None:
        return None
    if len(delimiter) > 1:
        try:
            delimiter = delimiter.encode("utf-8").decode("unicode_escape")
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    if len(delimiter) != 1:
        raise LoadError(
            f"구분자는 한 글자여야 합니다 / the separator must be a single character, "
            f"got {delimiter!r}. 탭은 --sep \"\\t\" 또는 --sep \"$(printf '\\t')\" 로 지정하세요"
        )
    return delimiter


def _sniff_delimiter(text: str, delimiter: Optional[str]) -> str:
    if delimiter:
        return delimiter
    lines = [ln for ln in text.splitlines() if ln.strip()][:20]
    if not lines:
        return ","
    best, best_score = ",", -1.0
    for cand in _DELIMITERS:
        counts = [len(next(csv.reader([ln], delimiter=cand))) - 1 for ln in lines]
        if not counts or max(counts) < 1:
            continue
        # Prefer the delimiter that splits every line into the same number of
        # fields, breaking ties toward more fields.
        consistent = sum(1 for c in counts if c == counts[0]) / len(counts)
        score = consistent * 10 + min(counts[0], 50) * 0.1
        if score > best_score:
            best, best_score = cand, score
    return best


def _dedupe(headers: Sequence[str]) -> List[str]:
    seen: Dict[str, int] = {}
    out: List[str] = []
    for i, h in enumerate(headers):
        name = h.strip().strip('"').lstrip("﻿").strip()
        if not name:
            name = f"column_{i + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}.{seen[name]}"
        else:
            seen[name] = 0
        out.append(name)
    return out


def read_table(path: str, encoding: Optional[str] = None,
               delimiter: Optional[str] = None) -> Table:
    """Read a CSV/TSV file, sniffing encoding and delimiter unless told."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise LoadError(f"파일을 열 수 없습니다 / cannot open file: {path} ({exc})") from exc
    if not raw.strip():
        raise LoadError(f"파일이 비어 있습니다 / file is empty: {path}")

    text, enc = _decode(raw, encoding)
    delim = _sniff_delimiter(text, _normalise_delimiter(delimiter))
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)
    records = [r for r in reader]

    notes: List[str] = []
    # Skip fully blank leading lines (Excel title rows are often blank-padded).
    while records and not any(c.strip() for c in records[0]):
        records.pop(0)
    if not records:
        raise LoadError(f"읽을 수 있는 행이 없습니다 / no readable rows: {path}")

    headers = _dedupe(records[0])
    width = len(headers)
    rows: List[List[str]] = []
    ragged = 0
    for rec in records[1:]:
        if not any(c.strip() for c in rec):
            continue
        if len(rec) != width:
            ragged += 1
            rec = (rec + [""] * width)[:width]
        rows.append([c.strip() for c in rec])
    if ragged:
        notes.append(f"열 개수가 헤더와 다른 행 {ragged}개를 잘라내거나 채웠습니다 "
                     f"({ragged} ragged row(s) padded/truncated to {width} columns)")
    return Table(headers=headers, rows=rows, encoding=enc, delimiter=delim,
                 path=path, notes=notes)


def resolve_column(table: Table, name: str) -> str:
    """Match a user-supplied column name to a real header.

    Accepts the exact name, a case/space-insensitive match, or ``#3`` for the
    third column. Raises ``LoadError`` listing the available headers otherwise.
    """
    if name in table.headers:
        return name
    if name.startswith("#"):
        try:
            idx = int(name[1:])
        except ValueError:
            idx = 0
        if 1 <= idx <= len(table.headers):
            return table.headers[idx - 1]
        raise LoadError(f"열 번호 범위를 벗어났습니다 / column index out of range: {name}")

    def norm(s: str) -> str:
        return re.sub(r"[\s_\-.]+", "", s).lower()

    target = norm(name)
    hits = [h for h in table.headers if norm(h) == target]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise LoadError(f"열 이름이 모호합니다 / ambiguous column '{name}': {hits}")
    shown = [h if len(h) <= 40 else h[:40] + "…" for h in table.headers[:25]]
    more = "" if len(table.headers) <= 25 else f" … (총 {len(table.headers)}개)"
    raise LoadError(
        f"'{name}' 열을 찾을 수 없습니다 / column not found. "
        f"사용 가능한 열 / available: {', '.join(shown)}{more}"
    )


def infer_decimal_comma(cells: Sequence[str], delimiter: str) -> Optional[bool]:
    """Decide, for a whole column, whether "," is a decimal point.

    A single column must not mix the two readings — that is how "1,614" and
    "1,06" in one creatinine column silently ended up 1000x apart. Rules, in
    order: any cell that can only be a decimal comma (``1,06``) settles it for
    the column; otherwise a non-comma-delimited file whose comma cells never
    carry a dot is treated as European decimal notation; otherwise commas are
    thousands separators.
    """
    thousands = re.compile(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?")
    any_comma = decimal_only = has_dot = False
    for raw in cells:
        c = (raw or "").strip().strip('"').lstrip("<>≤≥").strip()
        if "." in c:
            has_dot = True
        if "," not in c:
            continue
        any_comma = True
        if re.fullmatch(r"[+-]?\d+,\d+", c) and not thousands.fullmatch(c):
            decimal_only = True
    if not any_comma:
        return None
    if decimal_only:
        return True
    if delimiter != "," and not has_dot:
        return True
    return False


def parse_number(raw: str,
                 decimal_comma: Optional[bool] = None) -> Tuple[Optional[float], Optional[str]]:
    """Parse one cell into a float, returning ``(value, note)``.

    ``note`` is ``None`` for a clean number, ``"missing"`` for an empty/NA cell,
    ``"censored"`` when a detection-limit marker such as ``<0.5`` was replaced by
    the limit itself, ``"percent"`` when a trailing ``%`` was divided out, and
    ``"unparsed"`` when the text is not a number at all.
    """
    s = (raw or "").strip().strip('"').replace(" ", " ").strip()
    if s.lower() in MISSING_TOKENS:
        return None, "missing"

    censored = False
    if s.startswith(("<=", ">=")):
        s = s[2:].strip()
        censored = True
    elif s and s[0] in "<>≤≥":
        s = s[1:].strip()
        censored = True

    negative_paren = s.startswith("(") and s.endswith(")")
    if negative_paren:
        s = s[1:-1].strip()

    percent = s.endswith("%")
    if percent:
        s = s[:-1].strip()

    # Commas are ambiguous, so the caller may fix the reading for a whole column
    # (see _parse_numeric_column): decimal_comma=True forces "1,614" -> 1.614,
    # False forces the thousands reading, None keeps the per-cell precedence
    # (groups of exactly three digits are thousands, anything else is decimal).
    if decimal_comma is True:
        if re.fullmatch(r"[+-]?\d+,\d+", s):
            s = s.replace(",", ".")
    elif re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?", s):
        s = s.replace(",", "")
    elif decimal_comma is None and re.fullmatch(r"[+-]?\d+,\d+", s):
        s = s.replace(",", ".")

    # Spaces only disappear when they are themselves thousands separators;
    # "3 4" must stay unparseable rather than silently become 34.
    if re.fullmatch(r"[+-]?\d{1,3}( \d{3})+(\.\d+)?", s):
        s = s.replace(" ", "")
    if not _NUM_RE.match(s):
        return None, "unparsed"
    val = float(s)
    if percent:
        val /= 100.0
    if negative_paren:
        val = -val
    if not math.isfinite(val):
        return None, "unparsed"
    if censored:
        return val, "censored"
    return val, ("percent" if percent else None)


# Outcome vocabularies. Anything else needs --positive-label.
_POSITIVE_WORDS = {
    "1", "1.0", "true", "t", "yes", "y", "pos", "positive", "case", "disease",
    "diseased", "abnormal", "event", "present", "sick", "ill", "malignant",
    "양성", "유", "질환", "환자", "있음", "발생", "이상", "비정상", "악성", "예",
}
_NEGATIVE_WORDS = {
    "0", "0.0", "false", "f", "no", "n", "neg", "negative", "control", "healthy",
    "normal", "nonevent", "non-event", "absent", "well", "benign", "nondisease",
    "음성", "무", "정상", "대조", "없음", "미발생", "양호", "양성아님", "아니오",
}


@dataclass
class LabelResult:
    """Outcome column parsed into booleans plus a record of what happened."""

    values: List[Optional[bool]]
    positive_label: str
    negative_label: str
    n_missing: int
    n_unparsed: int
    levels: List[str]
    # Levels that --positive-label swept into the negative group (e.g. "판정보류").
    folded_negative: List[str] = field(default_factory=list)
    n_folded_negative: int = 0


def parse_label_column(cells: Sequence[str], positive_label: Optional[str] = None,
                       negative_label: Optional[str] = None) -> LabelResult:
    """Turn an outcome column into ``True``/``False``/``None`` per row.

    With ``positive_label`` given, that value (case-insensitive, whitespace
    trimmed) is the disease-positive class and every other non-missing value is
    negative — unless ``negative_label`` is also given, in which case rows that
    match neither are dropped. Without it, the column must have exactly two
    non-missing levels and they must be recognisable (1/0, yes/no, 양성/음성 …).
    """
    cleaned: List[Optional[str]] = []
    n_missing = 0
    for c in cells:
        s = (c or "").strip().strip('"')
        if s.lower() in MISSING_TOKENS:
            cleaned.append(None)
            n_missing += 1
        else:
            cleaned.append(s)

    def canon(s: str) -> str:
        t = s.strip().lower()
        # "1.0" and "1" should behave alike.
        try:
            f = float(t)
            if f == int(f):
                t = str(int(f))
        except (ValueError, OverflowError):
            pass
        return t

    # Distinct levels, grouping values that only differ in numeric spelling
    # ("1" and "1.0" are the same level). The first spelling seen is displayed.
    level_of: Dict[str, str] = {}
    for s in cleaned:
        if s is not None:
            level_of.setdefault(canon(s), s)
    levels: List[str] = list(level_of.values())

    values: List[Optional[bool]] = []
    n_unparsed = 0

    if positive_label is not None:
        pos_c = canon(positive_label)
        neg_c = canon(negative_label) if negative_label is not None else None
        matched_pos = False
        for s in cleaned:
            if s is None:
                values.append(None)
                continue
            c = canon(s)
            if c == pos_c:
                values.append(True)
                matched_pos = True
            elif neg_c is None or c == neg_c:
                values.append(False)
            else:
                values.append(None)
                n_unparsed += 1
        if not matched_pos:
            raise LoadError(
                f"--positive-label '{positive_label}' 값이 결과 열에 없습니다 / "
                f"not found in the outcome column. 관측된 값 / observed levels: "
                f"{', '.join(levels[:12]) or '(none)'}"
            )
        pos_name = positive_label
        neg_name = negative_label if negative_label is not None else f"not {positive_label}"
        folded: List[str] = []
        n_folded = 0
        if neg_c is None:
            for s_ in cleaned:
                if s_ is None:
                    continue
                c = canon(s_)
                if c != pos_c:
                    n_folded += 1
                    if s_ not in folded:
                        folded.append(s_)
        return LabelResult(values, pos_name, neg_name, n_missing, n_unparsed, levels,
                           folded_negative=folded, n_folded_negative=n_folded)

    if len(levels) == 0:
        raise LoadError("결과 열에 값이 하나도 없습니다 / outcome column is entirely missing")
    if len(levels) == 1:
        raise LoadError(
            f"결과 열에 한 가지 값만 있습니다 / outcome column has a single level "
            f"('{levels[0]}'): 질환군과 비질환군이 모두 필요합니다 "
            f"(both diseased and non-diseased cases are required)"
        )
    if len(levels) > 2:
        raise LoadError(
            f"결과 열에 {len(levels)}가지 값이 있습니다 / outcome column has "
            f"{len(levels)} levels ({', '.join(levels[:8])}"
            f"{' …' if len(levels) > 8 else ''}). --positive-label 로 질환군 값을 "
            f"지정하세요 / specify the diseased value with --positive-label."
        )

    a, b = levels
    ca, cb = canon(a), canon(b)
    if ca in _POSITIVE_WORDS and cb in _NEGATIVE_WORDS:
        pos, neg = a, b
    elif cb in _POSITIVE_WORDS and ca in _NEGATIVE_WORDS:
        pos, neg = b, a
    else:
        raise LoadError(
            f"결과 열의 두 값('{a}', '{b}')이 양성/음성 중 무엇인지 알 수 없습니다 / "
            f"cannot tell which level means diseased. --positive-label 로 "
            f"지정하세요 / specify it with --positive-label."
        )

    pos_c = canon(pos)
    for s in cleaned:
        values.append(None if s is None else (canon(s) == pos_c))
    return LabelResult(values, pos, neg, n_missing, 0, levels)
