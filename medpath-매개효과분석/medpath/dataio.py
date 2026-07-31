"""CSV loading and design-matrix construction — pure standard library.

Real clinical exports are messy: BOMs, Korean-Excel cp949 encoding, semicolon
or tab delimiters, ``NA`` / ``.`` / ``#N/A`` blanks, ragged rows, thousands
separators. This module absorbs that noise while refusing to *guess* anything
that could silently produce a wrong number.
"""

from __future__ import annotations

import csv
import io
import math
import re
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "DataError",
    "Table",
    "Design",
    "load_table",
    "parse_float",
    "build_design",
]


class DataError(ValueError):
    """User-facing data problem (bad column, unusable values, ...)."""


_NA_LABELS = {"NA", "N/A", "NAN", "NULL", ".", "-", "--", "NONE", "MISSING",
              "#N/A", "#NULL!", "#DIV/0!", "결측", "없음"}

# utf-8-sig strips a BOM; cp949/euc-kr covers Korean Excel; latin-1 always
# decodes, so we never crash on odd bytes (a note is emitted when used).
_ENCODINGS = ["utf-8-sig", "cp949", "latin-1"]

# Strict numeric grammar. float() is deliberately NOT used directly: it would
# accept "1,5" (European 1.5 or US 15?), "inf", "1_000" and full-width digits.
# A silently 10x-wrong value is far worse than a reported missing cell.
_PLAIN_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$", re.ASCII)
_THOUSANDS_RE = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$", re.ASCII)


def parse_float(token: str) -> Optional[float]:
    """Parse a cell to float; ``None`` for blank / NA / non-numeric / non-finite."""
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


def is_missing(token: str) -> bool:
    t = token.strip().strip('"').strip("'").strip()
    return t == "" or t.upper() in _NA_LABELS


class Table:
    """A loaded CSV: header names plus string cells."""

    def __init__(self, header: List[str], rows: List[List[str]], notes: List[str]):
        self.header = header
        self.rows = rows
        self.notes = notes
        self._index: Dict[str, int] = {}
        seen: Dict[str, int] = {}
        for i, name in enumerate(header):
            seen[name] = seen.get(name, 0) + 1
            if name not in self._index:
                self._index[name] = i
        dups = sorted(n for n, c in seen.items() if c > 1)
        if dups:
            notes.append(
                "헤더에 같은 이름의 열이 여러 개 있습니다(%s). 가장 왼쪽 열만 사용합니다."
                % ", ".join(dups))

    def __len__(self) -> int:
        return len(self.rows)

    def has(self, name: str) -> bool:
        return name in self._index

    def column(self, name: str) -> List[str]:
        if name not in self._index:
            raise DataError(
                "'%s' 열이 CSV에 없습니다. 사용 가능한 열: %s"
                % (name, ", ".join(self.header) if self.header else "(없음)"))
        j = self._index[name]
        return [row[j] for row in self.rows]


def _decode(path: str, notes: List[str]) -> str:
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        raise DataError("파일을 찾을 수 없습니다: %s" % path)
    except IsADirectoryError:
        raise DataError("폴더가 아니라 CSV 파일 경로를 지정하세요: %s" % path)
    except PermissionError:
        raise DataError("파일을 읽을 권한이 없습니다: %s" % path)
    if not raw.strip():
        raise DataError("빈 파일입니다: %s" % path)
    if b"\x00" in raw[:4096]:
        raise DataError(
            "이 파일은 텍스트 CSV가 아닌 것 같습니다(이진 데이터). "
            "엑셀 파일이라면 '다른 이름으로 저장 → CSV UTF-8'로 내보내세요: %s" % path)
    for enc in _ENCODINGS:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if enc != "utf-8-sig":
            notes.append(
                "UTF-8로 읽지 못해 '%s' 인코딩으로 해석했습니다 "
                "(한글이 깨져 보이면 CSV를 UTF-8로 다시 저장하세요)." % enc)
        return text
    raise DataError("파일 인코딩을 해석하지 못했습니다: %s" % path)


def _sniff_delimiter(text: str) -> str:
    """Pick the delimiter that yields the most consistent, widest table."""
    sample = [ln for ln in text.splitlines()[:20] if ln.strip()]
    if not sample:
        return ","
    best, best_score = ",", (-1.0, 0)
    for delim in [",", "\t", ";", "|"]:
        try:
            counts = [len(r) for r in csv.reader(sample, delimiter=delim)]
        except csv.Error:
            continue
        if not counts:
            continue
        width = counts[0]
        if width < 2:
            continue
        consistent = sum(1 for c in counts if c == width) / len(counts)
        score = (consistent, width)
        if score > best_score:
            best_score, best = score, delim
    return best


def load_table(path: str, delimiter: Optional[str] = None) -> Table:
    """Read a CSV/TSV into a :class:`Table`, tolerating common export quirks."""
    notes: List[str] = []
    text = _decode(path, notes)
    delim = delimiter or _sniff_delimiter(text)
    if delimiter is None and delim != ",":
        notes.append("구분자를 '%s'(으)로 자동 인식했습니다."
                     % {"\t": "탭", ";": "세미콜론", "|": "파이프"}.get(delim, delim))
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)
    try:
        raw_rows = list(reader)
    except csv.Error as exc:
        raise DataError("CSV를 읽는 중 오류가 발생했습니다: %s" % exc)
    raw_rows = [r for r in raw_rows if any(c.strip() for c in r)]
    if not raw_rows:
        raise DataError("데이터 행이 없습니다: %s" % path)

    header = [h.strip().strip('"').strip("'").strip() for h in raw_rows[0]]
    while header and header[-1] == "":
        header.pop()
    if not header:
        raise DataError("헤더(첫 줄의 열 이름)를 찾지 못했습니다: %s" % path)
    ncol = len(header)
    for i, h in enumerate(header):
        if h == "":
            header[i] = "col%d" % (i + 1)

    rows: List[List[str]] = []
    ragged_short = ragged_long = 0
    for r in raw_rows[1:]:
        if len(r) < ncol:
            ragged_short += 1
            r = list(r) + [""] * (ncol - len(r))
        elif len(r) > ncol:
            if any(c.strip() for c in r[ncol:]):
                ragged_long += 1
            r = r[:ncol]
        rows.append(list(r))
    if ragged_short:
        notes.append("열 개수가 부족한 행이 %d개 있어 빈 값으로 채웠습니다." % ragged_short)
    if ragged_long:
        notes.append("열 개수가 헤더보다 많은 행이 %d개 있어 뒤쪽 값을 무시했습니다." % ragged_long)
    if not rows:
        raise DataError("헤더만 있고 데이터 행이 없습니다: %s" % path)
    return Table(header, rows, notes)


# --------------------------------------------------------------------------
# Design matrix
# --------------------------------------------------------------------------
class Design:
    """Fully prepared, listwise-complete data for a mediation model."""

    def __init__(self) -> None:
        self.n_total = 0
        self.n_used = 0
        self.x: List[float] = []
        self.x_name = ""
        self.x_label = ""          # human description incl. coding
        self.x_kind = "numeric"    # numeric | binary | dummy
        self.x_reference: Optional[str] = None
        self.x_comparison: Optional[str] = None
        self.mediators: List[Tuple[str, List[float]]] = []
        self.y_name = ""
        self.y: List[float] = []
        self.covariates: List[Tuple[str, List[float]]] = []
        self.covariate_notes: List[str] = []
        self.notes: List[str] = []
        self.warnings: List[str] = []
        self.missing_by_column: List[Tuple[str, int]] = []
        self.row_ids: List[int] = []   # original 1-based CSV data-row numbers


def _numeric_or_none(values: Sequence[str]) -> Tuple[List[Optional[float]], int]:
    """Return parsed floats (None = missing) and the count of unparseable cells."""
    out: List[Optional[float]] = []
    bad = 0
    for v in values:
        if is_missing(v):
            out.append(None)
        else:
            f = parse_float(v)
            if f is None:
                bad += 1
                out.append(None)
            else:
                out.append(f)
    return out, bad


# Level names that almost always mean "this is the control/reference group".
# Choosing the reference well matters: it decides the *sign* of every reported
# path, and a device trial reading "device is the reference" is a foot-gun.
_CONTROL_TOKENS = {
    "sham", "placebo", "control", "ctrl", "usual care", "usual", "standard",
    "baseline", "pre", "before", "none", "no", "n", "false", "0", "untreated",
    "대조", "대조군", "위약", "가짜", "무처치", "기저", "기준", "사전", "치료전",
    "아니오", "없음", "비교군", "일반", "표준",
}


def _pick_reference(levels: Sequence[str]) -> Optional[str]:
    """Return the level that clearly looks like a control arm, if any."""
    hits = [lv for lv in levels if lv.strip().lower() in _CONTROL_TOKENS]
    return hits[0] if len(hits) == 1 else None


def _levels(values: Sequence[str]) -> List[str]:
    seen: Dict[str, int] = {}
    for v in values:
        if is_missing(v):
            continue
        t = v.strip().strip('"').strip("'").strip()
        seen[t] = seen.get(t, 0) + 1
    return sorted(seen, key=lambda k: (-seen[k], k))


def _require_numeric(table: Table, name: str, role: str) -> List[Optional[float]]:
    raw = table.column(name)
    vals, bad = _numeric_or_none(raw)
    if bad and bad >= max(1, int(0.5 * len(raw))):
        lv = _levels(raw)[:6]
        raise DataError(
            "%s 변수 '%s'의 값 대부분이 숫자가 아닙니다(예: %s). "
            "매개분석의 %s는 연속형(숫자)이어야 합니다."
            % (role, name, ", ".join(lv) if lv else "?", role))
    return vals


def build_design(
    table: Table,
    x_name: str,
    mediator_names: Sequence[str],
    y_name: str,
    covariate_names: Sequence[str] = (),
    reference: Optional[str] = None,
    x_levels: Optional[Sequence[str]] = None,
    max_levels: int = 20,
) -> Design:
    """Turn a :class:`Table` into a listwise-complete :class:`Design`.

    * ``Y`` and every mediator must be numeric (continuous).
    * ``X`` may be numeric, or categorical with exactly two levels (auto
      dummy-coded 0/1; ``reference`` picks the 0 level, otherwise the more
      frequent level is used and the choice is reported).
    * Categorical covariates are expanded to ``L-1`` indicator columns.
    """
    d = Design()
    d.notes.extend(table.notes)
    d.n_total = len(table)
    d.x_name = x_name
    d.y_name = y_name

    mediator_names = list(mediator_names)
    if not mediator_names:
        raise DataError("매개변수(-m/--mediator)를 최소 하나 지정하세요.")
    dup = [m for i, m in enumerate(mediator_names) if m in mediator_names[:i]]
    if dup:
        raise DataError("매개변수가 중복 지정됐습니다: %s" % ", ".join(sorted(set(dup))))
    overlap = set(mediator_names) & {x_name, y_name}
    if overlap or x_name == y_name:
        raise DataError(
            "X · M · Y 는 서로 다른 열이어야 합니다 (겹친 열: %s)."
            % ", ".join(sorted(overlap | ({x_name} if x_name == y_name else set()))))
    cov_clean: List[str] = []
    for c in covariate_names:
        if c in (x_name, y_name) or c in mediator_names:
            raise DataError("공변량 '%s'가 X/M/Y와 같은 열입니다. 빼고 다시 실행하세요." % c)
        if c not in cov_clean:
            cov_clean.append(c)
        else:
            d.notes.append("공변량 '%s'가 중복 지정돼 한 번만 사용합니다." % c)

    # --- parse each role -------------------------------------------------
    y_vals = _require_numeric(table, y_name, "종속(Y)")
    m_vals = [(nm, _require_numeric(table, nm, "매개(M)")) for nm in mediator_names]

    x_raw = table.column(x_name)
    x_parsed, x_bad = _numeric_or_none(x_raw)
    x_is_numeric = x_bad == 0 and any(v is not None for v in x_parsed)
    x_vals: List[Optional[float]]
    if x_is_numeric:
        x_vals = x_parsed
        d.x_kind = "numeric"
        d.x_label = "%s (연속형 그대로 사용)" % x_name
    else:
        levels = _levels(x_raw)
        if x_levels:
            if len(x_levels) != 2:
                raise DataError("--x-levels 는 '기준수준,비교수준' 두 개를 쉼표로 지정하세요.")
            missing_lv = [lv for lv in x_levels if lv not in levels]
            if missing_lv:
                raise DataError(
                    "'%s' 열에 없는 수준입니다: %s (있는 수준: %s)"
                    % (x_name, ", ".join(missing_lv), ", ".join(levels)))
            ref, comp = x_levels[0], x_levels[1]
        elif len(levels) == 2:
            if reference is not None:
                if reference not in levels:
                    raise DataError(
                        "--reference '%s' 는 '%s' 열에 없습니다 (있는 수준: %s)"
                        % (reference, x_name, ", ".join(levels)))
                ref = reference
                comp = [lv for lv in levels if lv != ref][0]
            else:
                auto = _pick_reference(levels)
                if auto is not None:
                    ref = auto
                    comp = [lv for lv in levels if lv != ref][0]
                    why = "'%s'가 대조군 이름으로 보여서" % ref
                else:
                    ref, comp = levels[0], levels[1]
                    why = "빈도가 많은 쪽을 기준으로"
                d.notes.append(
                    "X '%s'를 0='%s', 1='%s' 로 코딩했습니다(%s). "
                    "계수의 부호는 이 코딩을 따릅니다 — 바꾸려면 --reference 를 쓰세요."
                    % (x_name, ref, comp, why))
        elif len(levels) < 2:
            raise DataError("X '%s' 의 값이 한 종류뿐이라 효과를 추정할 수 없습니다." % x_name)
        else:
            raise DataError(
                "X '%s' 는 수준이 %d개(%s)입니다. 매개분석의 X는 연속형이거나 2수준이어야 합니다. "
                "두 수준만 비교하려면 --x-levels 기준수준,비교수준 을 쓰세요."
                % (x_name, len(levels), ", ".join(levels[:8])))
        d.x_kind = "dummy"
        d.x_reference, d.x_comparison = ref, comp
        d.x_label = "%s (0=%s, 1=%s)" % (x_name, ref, comp)
        x_vals = []
        for v in x_raw:
            t = v.strip().strip('"').strip("'").strip()
            if is_missing(v):
                x_vals.append(None)
            elif t == ref:
                x_vals.append(0.0)
            elif t == comp:
                x_vals.append(1.0)
            else:
                x_vals.append(None)   # other levels excluded from this contrast

    # --- covariates ------------------------------------------------------
    cov_cols: List[Tuple[str, List[Optional[float]]]] = []
    for cname in cov_clean:
        raw = table.column(cname)
        parsed, bad = _numeric_or_none(raw)
        if bad == 0 and any(v is not None for v in parsed):
            cov_cols.append((cname, parsed))
            continue
        levels = _levels(raw)
        if len(levels) < 2:
            raise DataError("공변량 '%s' 의 값이 한 종류뿐입니다. 빼고 실행하세요." % cname)
        if len(levels) > max_levels:
            raise DataError(
                "공변량 '%s' 의 수준이 %d개로 너무 많습니다(최대 %d). "
                "숫자형이어야 하는 열이라면 값을 확인하고, 식별자라면 공변량에서 빼세요."
                % (cname, len(levels), max_levels))
        ref = levels[0]
        d.covariate_notes.append(
            "'%s' (범주형, 기준 '%s') → 가변수 %d개" % (cname, ref, len(levels) - 1))
        for lv in levels[1:]:
            col: List[Optional[float]] = []
            for v in raw:
                if is_missing(v):
                    col.append(None)
                else:
                    t = v.strip().strip('"').strip("'").strip()
                    col.append(1.0 if t == lv else 0.0)
            cov_cols.append(("%s=%s" % (cname, lv), col))

    # --- listwise deletion ----------------------------------------------
    all_cols: List[Tuple[str, List[Optional[float]]]] = (
        [(x_name, x_vals)] + [(nm, vals) for nm, vals in m_vals]
        + [(y_name, y_vals)] + cov_cols
    )
    n = d.n_total
    keep = []
    miss_counts = {nm: 0 for nm, _ in all_cols}
    for i in range(n):
        ok = True
        for nm, col in all_cols:
            if col[i] is None:
                miss_counts[nm] += 1
                ok = False
        if ok:
            keep.append(i)
    d.missing_by_column = [(nm, miss_counts[nm]) for nm, _ in all_cols if miss_counts[nm]]
    d.n_used = len(keep)
    dropped = n - d.n_used
    if dropped:
        d.notes.append(
            "결측(또는 숫자가 아닌 값) 때문에 %d행을 제외했습니다 — 완전자료 %d행으로 분석합니다 "
            "(listwise deletion)." % (dropped, d.n_used))
    if d.n_used == 0:
        raise DataError(
            "지정한 열들이 모두 채워진 행이 하나도 없습니다. 열 이름과 결측 표기를 확인하세요.")

    d.row_ids = [i + 1 for i in keep]
    d.x = [x_vals[i] for i in keep]  # type: ignore[misc]
    d.y = [y_vals[i] for i in keep]  # type: ignore[misc]
    d.mediators = [(nm, [vals[i] for i in keep]) for nm, vals in m_vals]  # type: ignore[misc]
    d.covariates = [(nm, [col[i] for i in keep]) for nm, col in cov_cols]  # type: ignore[misc]

    if d.x_kind == "numeric":
        distinct = {v for v in d.x}
        if len(distinct) == 2:
            d.x_kind = "binary"
            lo, hi = sorted(distinct)
            d.x_label = "%s (숫자 2수준: %g vs %g — 계수는 %g→%g 변화 효과)" % (
                x_name, lo, hi, lo, hi)
        elif len(distinct) == 1:
            raise DataError(
                "완전자료에서 X '%s' 의 값이 한 종류뿐이라 효과를 추정할 수 없습니다." % x_name)

    # constant-column guard on the analysed sample
    for nm, col in [(d.y_name, d.y)] + d.mediators + d.covariates:
        if len({round(v, 15) for v in col}) == 1:
            raise DataError(
                "완전자료에서 '%s' 열의 값이 전부 같습니다(상수). 회귀에 쓸 수 없으니 빼고 실행하세요." % nm)

    return d
