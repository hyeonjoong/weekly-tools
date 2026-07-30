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
import io
import math
import re
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = ["load_long", "load_wide", "load_paired_long", "load_paired_wide",
           "load_binary_long", "load_binary_wide", "load_binary_counts",
           "load_multi_long", "parse_float", "map_binary_levels",
           "summarize_values", "screen_group_labels", "screen_values",
           "sanitize_label"]

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
    # Feed a file-like object, not text.splitlines(). Splitting first strips the
    # newline inside a quoted cell and concatenates the fragments, so a cell
    # containing "1\n2" silently became the number 12 -- exactly the
    # silently-wrong-number failure parse_float exists to prevent.
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)
        rows = [row for row in reader if any(cell.strip() for cell in row)]
    except csv.Error as exc:
        raise ValueError(
            f"CSV를 읽을 수 없습니다: {exc}. 아주 긴 셀(수십만 자)이나 따옴표가 "
            f"짝이 맞지 않는 행이 있는지 확인하세요.")
    if not rows:
        raise ValueError(f"'{path}' is empty")
    header = [h.strip() for h in rows[0]]
    dupes = sorted({h for h in header if header.count(h) > 1 and h})
    if dupes and notes is not None:
        notes.append(
            "헤더에 같은 이름의 열이 여러 개 있습니다: " + ", ".join(dupes)
            + ". 이름으로 열을 고를 때는 **첫 번째** 열만 사용됩니다 — 열 이름을 "
              "고유하게 만드세요.")
    return header, rows[1:]


def load_long(path: str, value_col: str, group_col: str,
              delimiter: Optional[str] = None,
              notes: Optional[List[str]] = None,
              missing_out: Optional[Dict[str, int]] = None
              ) -> List[Tuple[str, List[float]]]:
    """Load tidy data: return [(group_label, [values...]), ...] in first-seen order.

    ``missing_out``, if given, is filled with ``{group_label: n_unusable_rows}``
    — rows whose group is known but whose value cell is blank / NA / unparseable.
    Rows whose *group* itself is unusable cannot be attributed to any arm and are
    counted under the key ``""`` (and reported in ``notes``).
    """
    header, data = _read_rows(path, delimiter, notes)
    try:
        vi = header.index(value_col)
        gi = header.index(group_col)
    except ValueError:
        raise ValueError(
            f"column not found. header={header}; needed value='{value_col}', "
            f"group='{group_col}'")
    groups: List[str] = []
    buckets: Dict[str, List[float]] = {}
    missing: Dict[str, int] = {}
    ungrouped = 0
    for row in data:
        grp = row[gi].strip() if gi < len(row) else ""
        val = parse_float(row[vi]) if vi < len(row) else None
        if grp == "" or grp.upper() in _NA_LABELS:
            ungrouped += 1
            continue
        if grp not in buckets:
            buckets[grp] = []
            missing[grp] = 0
            groups.append(grp)
        if val is None:
            missing[grp] += 1
            continue
        buckets[grp].append(val)
    result = [(g, buckets[g]) for g in groups]
    if not result:
        raise ValueError("no usable rows found (check column names / data)")
    if missing_out is not None:
        missing_out.update(missing)
        if ungrouped:
            missing_out[""] = ungrouped
    if notes is not None and ungrouped:
        notes.append(
            f"그룹 값이 비어 있거나 결측이라 어느 군에도 배정할 수 없는 행 "
            f"{ungrouped}개를 제외했습니다.")
    return result


def load_wide(path: str, columns: Optional[Sequence[str]] = None,
              delimiter: Optional[str] = None,
              notes: Optional[List[str]] = None,
              missing_out: Optional[Dict[str, int]] = None
              ) -> List[Tuple[str, List[float]]]:
    """Load wide data: each selected column becomes a group (blanks dropped).

    ``missing_out``, if given, is filled with ``{column: n_unusable_cells}``.
    Only cells that actually contain something unusable (``NA``, ``.``, text,
    an ambiguous number) are counted — a *blank* cell in wide layout normally
    just means that column is shorter than another, not that a measurement was
    lost, so blanks are not reported as missing data.
    """
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
    missing: Dict[str, int] = {}
    for c in cols:
        vals = []
        n_bad = 0
        for row in data:
            i = idx[c]
            if i >= len(row):
                continue
            cell = row[i]
            v = parse_float(cell)
            if v is not None:
                vals.append(v)
            elif cell.strip().strip('"').strip("'").strip() != "":
                n_bad += 1
        missing[c] = n_bad
        result.append((c, vals))
    if missing_out is not None:
        missing_out.update(missing)
    return result


def load_paired_long(path: str, value_col: str, group_col: str, id_col: str,
                     delimiter: Optional[str] = None,
                     notes: Optional[List[str]] = None,
                     baseline: Optional[str] = None,
                     missing_out: Optional[Dict[str, int]] = None
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
            f"필요합니다. 발견된 수준 {len(levels)}종 중 일부: "
            f"{summarize_values(levels, 3)}")
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
    if missing_out is not None:
        # CONSORT accounting for the paired design: how many *subjects* each
        # condition lost because the pair could not be completed.
        missing_out[la] = len(ids_a) - len(order)
        missing_out[lb] = len(ids_b) - len(order)
    if notes is not None:
        if dropped:
            notes.append(f"{dropped}개 관측치가 짝을 이루지 못해 제외되었습니다.")
        if dup_ids:
            notes.append(
                f"같은 대상이 같은 조건에 여러 번 기록되어 있어 **마지막 값만** "
                f"사용했습니다 ({len(dup_ids)}건). 재측정인지 입력 오류인지 "
                f"확인하세요 — 어느 쪽을 쓸지는 도구가 판단할 수 없습니다.")
    if not order:
        raise ValueError("짝을 이루는 관측치가 없습니다 (id/그룹/값 확인).")
    return (la, va), (lb, vb)


def load_paired_wide(path: str, columns: Optional[Sequence[str]] = None,
                     delimiter: Optional[str] = None,
                     notes: Optional[List[str]] = None,
                     baseline: Optional[str] = None,
                     missing_out: Optional[Dict[str, int]] = None
                     ) -> Tuple[Tuple[str, List[float]], Tuple[str, List[float]]]:
    """Load matched pairs from a wide file: two columns matched **row-wise**.

    Unlike ``load_wide`` (which drops blanks per column independently), pairing
    is by row: a row is used only if *both* selected columns have a value.

    ``baseline`` names the column to be *subtracted*; it is moved to second
    position so the difference is ``(other - baseline)``.  This is applied here
    rather than in the CLI because the column list may come from the file
    header, which the CLI has not read yet — resolving it there silently did
    nothing when ``--columns`` was omitted, and handed back the opposite sign.
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
    if baseline is not None:
        if baseline not in cols:
            raise ValueError(
                f"--baseline '{baseline}' 은(는) 비교할 두 열 {cols} 에 없습니다.")
        cols = [c for c in cols if c != baseline] + [baseline]
    for c in cols:
        if c not in header:
            raise ValueError(f"column '{c}' not in header {header}")
    ia, ib = header.index(cols[0]), header.index(cols[1])
    va: List[float] = []
    vb: List[float] = []
    dropped = dropped_a = dropped_b = 0
    for row in data:
        pa = parse_float(row[ia]) if ia < len(row) else None
        pb = parse_float(row[ib]) if ib < len(row) else None
        if pa is None or pb is None:
            if pa is not None or pb is not None:
                dropped += 1
                # the arm that *had* a value is the one that lost a usable pair
                if pa is None:
                    dropped_a += 1
                else:
                    dropped_b += 1
            continue
        va.append(pa)
        vb.append(pb)
    if missing_out is not None:
        missing_out[cols[0]] = dropped_a
        missing_out[cols[1]] = dropped_b
    if notes is not None and dropped:
        notes.append(f"{dropped}개 행이 한쪽 값 결측으로 제외되었습니다 (row-wise 매칭).")
    if not va:
        raise ValueError("짝을 이루는 행이 없습니다 (두 열 모두 값이 있어야 함).")
    return (cols[0], va), (cols[1], vb)


# --------------------------------------------------------------------------
# binary (yes/no) endpoints
# --------------------------------------------------------------------------

# Tokens recognised without the user having to say which value means "event".
# Everything is compared upper-cased and stripped.
_EVENT_TOKENS = {
    "1", "Y", "YES", "TRUE", "T", "EVENT", "RESPONDER", "RESPONSE", "R",
    "POSITIVE", "POS", "SUCCESS", "CURED", "IMPROVED",
    "예", "유", "있음", "발생", "성공", "반응", "호전", "양성", "치료성공",
}
_NON_EVENT_TOKENS = {
    "0", "N", "NO", "FALSE", "F", "NONEVENT", "NON-EVENT", "NO EVENT",
    "NONRESPONDER", "NON-RESPONDER", "NEGATIVE", "NEG", "FAILURE", "FAIL",
    "아니오", "무", "없음", "미발생", "실패", "무반응", "음성", "치료실패",
}


#: Control characters in a label break table alignment and can smuggle escape
#: sequences into a terminal; a very long one wrecks every row.
_CTRL = {c: " " for c in range(32)}
_CTRL[127] = " "
_MAX_LABEL = 40


def sanitize_label(label: str) -> str:
    """Make a group label safe to print in a table and a warning."""
    text = str(label).translate(_CTRL).strip()
    if len(text) > _MAX_LABEL:
        text = text[:_MAX_LABEL - 1] + "…"
    return text


def _clean_token(token: str) -> str:
    return token.strip().strip('"').strip("'").strip()


#: How many distinct cell values an error message may quote back.
_MAX_SHOWN_VALUES = 5
_MAX_VALUE_LEN = 30


def summarize_values(values: Sequence[str], limit: int = _MAX_SHOWN_VALUES
                     ) -> str:
    """Describe a set of offending cell values without dumping the column.

    Point ``--value`` at a subject-id or free-text column by mistake -- a
    one-character slip -- and the naive "관측된 값: {levels}" message printed
    every distinct identifier in the file. That message goes to the terminal,
    into shell history and, for the multi-endpoint path, into the saved report
    a researcher hands to a collaborator. Clinical inputs are exactly the files
    where that must not happen, so error text quotes a bounded, truncated
    sample and reports the rest as a count.
    """
    shown = []
    for v in list(values)[:limit]:
        text = str(v)
        if len(text) > _MAX_VALUE_LEN:
            text = text[:_MAX_VALUE_LEN - 1] + "…"
        shown.append(repr(text))
    rest = len(values) - len(shown)
    body = ", ".join(shown)
    if rest > 0:
        return f"[{body}, … 외 {rest}개]"
    return f"[{body}]"


def map_binary_levels(tokens: Sequence[str], event_value: Optional[str] = None,
                      notes: Optional[List[str]] = None) -> Tuple[set, set]:
    """Decide which raw cell values mean "event" and which mean "no event".

    Returns ``(event_set, non_event_set)`` of upper-cased tokens.

    With ``event_value`` the rule is explicit: that value (case-insensitive) is
    the event and *every other* observed value is a non-event.  Without it the
    column must use recognisable yes/no coding (``1/0``, ``Y/N``, ``yes/no``,
    ``true/false``, ``유/무`` ...); anything else raises, because guessing which
    of two arbitrary labels means "responder" is exactly the kind of silent
    50/50 coin-flip that produces a confidently backwards clinical result.
    """
    levels: List[str] = []
    seen: set = set()          # membership on a list is O(n^2) over a big column
    for t in tokens:
        u = _clean_token(t).upper()
        if u == "" or u in _NA_LABELS or u in seen:
            continue
        seen.add(u)
        levels.append(u)
    if event_value is not None:
        ev = _clean_token(event_value).upper()
        if ev == "":
            raise ValueError("--event-value 가 비어 있습니다.")
        if ev not in levels:
            raise ValueError(
                f"--event-value '{event_value}' 이(가) 데이터에 없습니다. "
                f"관측된 값 {len(levels)}종 중 일부: {summarize_values(levels)}")
        others = {l for l in levels if l != ev}
        # Anything not recognisable as a genuine "no" is being *imputed* as a
        # failure. That is a non-responder imputation, it shifts the denominator
        # unequally between arms, and it must never happen silently.
        imputed = sorted(l for l in others if l not in _NON_EVENT_TOKENS)
        if imputed and notes is not None:
            notes.append(
                "--event-value 를 지정해 나머지 값을 모두 '비사건'으로 처리했습니다: "
                + summarize_values(imputed)
                + ". 이 값들이 결측/중도탈락이라면 이는 **실패로 간주하는 대체"
                  "(non-responder imputation)** 이며 분석계획서에 명시되어야 "
                  "합니다. 분석에서 빼려면 CSV에서 빈 칸이나 NA 로 바꾸세요.")
        return ({ev}, others)
    if not levels:
        raise ValueError("이진(binary) 결과 열에 사용할 수 있는 값이 없습니다.")
    unknown = [l for l in levels
               if l not in _EVENT_TOKENS and l not in _NON_EVENT_TOKENS]
    if unknown:
        raise ValueError(
            f"이진 결과 열의 값 {len(unknown)}종을 사건/비사건으로 자동 해석할 수 "
            f"없습니다: {summarize_values(unknown)}. "
            f"--event-value 로 어떤 값이 '사건(event)'인지 지정하세요. "
            f"(이 열이 정말 이진 결과 열이 맞는지도 확인하세요 — 값이 아주 많다면 "
            f"식별자 열을 잘못 지정했을 수 있습니다.)")
    if len(levels) > 2:
        raise ValueError(
            f"이진 결과 열에 서로 다른 값이 {len(levels)}개 있습니다 "
            f"(예: {summarize_values(levels, 3)}). "
            f"--event-value 로 사건 값을 지정하거나 열을 정리하세요.")
    return ({l for l in levels if l in _EVENT_TOKENS},
            {l for l in levels if l in _NON_EVENT_TOKENS})


def _tally(cells: Sequence[str], events: set, non_events: set
           ) -> Tuple[int, int, int]:
    """(n_events, n_total, n_unusable) for one arm's raw cells."""
    ev = tot = bad = 0
    for cell in cells:
        u = _clean_token(cell).upper()
        if u == "" or u in _NA_LABELS:
            if u != "":
                bad += 1
            continue
        if u in events:
            ev += 1
            tot += 1
        elif u in non_events:
            tot += 1
        else:
            bad += 1
    return ev, tot, bad


def _levels_note(events: set, non_events: set) -> str:
    return ("이진 결과 매핑(반드시 확인하세요): 사건(event) = {" +
            ", ".join(sorted(events)) + "}, 비사건(non-event) = {" +
            ", ".join(sorted(non_events)) + "}")


def load_binary_long(path: str, value_col: str, group_col: str,
                     event_value: Optional[str] = None,
                     delimiter: Optional[str] = None,
                     notes: Optional[List[str]] = None,
                     missing_out: Optional[Dict[str, int]] = None
                     ) -> List[Tuple[str, Tuple[int, int]]]:
    """Load a tidy binary endpoint: ``[(group, (events, n)), ...]``."""
    header, data = _read_rows(path, delimiter, notes)
    try:
        vi = header.index(value_col)
        gi = header.index(group_col)
    except ValueError:
        raise ValueError(
            f"column not found. header={header}; needed value='{value_col}', "
            f"group='{group_col}'")
    order: List[str] = []
    cells: Dict[str, List[str]] = {}
    ungrouped = 0
    for row in data:
        grp = row[gi].strip() if gi < len(row) else ""
        if grp == "" or grp.upper() in _NA_LABELS:
            ungrouped += 1
            continue
        if grp not in cells:
            cells[grp] = []
            order.append(grp)
        cells[grp].append(row[vi] if vi < len(row) else "")
    if not order:
        raise ValueError("no usable rows found (check column names / data)")
    all_cells = [c for g in order for c in cells[g]]
    events, non_events = map_binary_levels(all_cells, event_value, notes)
    result: List[Tuple[str, Tuple[int, int]]] = []
    missing: Dict[str, int] = {}
    for g in order:
        ev, tot, bad = _tally(cells[g], events, non_events)
        # blanks count as missing observations in long layout: the row existed
        blanks = sum(1 for c in cells[g] if _clean_token(c) == "")
        missing[g] = bad + blanks
        result.append((g, (ev, tot)))
    if missing_out is not None:
        missing_out.update(missing)
        if ungrouped:
            missing_out[""] = ungrouped
    if notes is not None:
        notes.append(_levels_note(events, non_events))
        if ungrouped:
            notes.append(f"그룹 값이 없어 제외한 행 {ungrouped}개.")
    return result


def load_binary_wide(path: str, columns: Optional[Sequence[str]] = None,
                     event_value: Optional[str] = None,
                     delimiter: Optional[str] = None,
                     notes: Optional[List[str]] = None,
                     missing_out: Optional[Dict[str, int]] = None
                     ) -> List[Tuple[str, Tuple[int, int]]]:
    """Load a binary endpoint in wide layout (one column per arm)."""
    header, data = _read_rows(path, delimiter, notes)
    dupes = {h for h in header if header.count(h) > 1}
    if dupes:
        raise ValueError(
            "wide 형식에서 열 이름이 중복됩니다: " + ", ".join(sorted(dupes)))
    cols = list(columns) if columns else header
    for c in cols:
        if c not in header:
            raise ValueError(f"column '{c}' not in header {header}")
    idx = {c: header.index(c) for c in cols}
    per_col = {c: [row[idx[c]] if idx[c] < len(row) else "" for row in data]
               for c in cols}
    events, non_events = map_binary_levels(
        [v for c in cols for v in per_col[c]], event_value, notes)
    result = []
    missing: Dict[str, int] = {}
    for c in cols:
        ev, tot, bad = _tally(per_col[c], events, non_events)
        missing[c] = bad          # blanks are ragged columns, not missing data
        result.append((c, (ev, tot)))
    if missing_out is not None:
        missing_out.update(missing)
    if notes is not None:
        notes.append(_levels_note(events, non_events))
    return result


def load_binary_counts(path: str, events_col: str, n_col: str, group_col: str,
                       delimiter: Optional[str] = None,
                       notes: Optional[List[str]] = None
                       ) -> List[Tuple[str, Tuple[int, int]]]:
    """Load an already-aggregated table: one row per arm with events and n.

        arm,responders,total
        placebo,12,50
        drug,27,52
    """
    header, data = _read_rows(path, delimiter, notes)
    for col in (events_col, n_col, group_col):
        if col not in header:
            raise ValueError(f"column '{col}' not in header {header}")
    ei, ni, gi = (header.index(events_col), header.index(n_col),
                  header.index(group_col))
    out: List[Tuple[str, Tuple[int, int]]] = []
    seen: Dict[str, int] = {}
    for row in data:
        if max(ei, ni, gi) >= len(row):
            continue
        grp = row[gi].strip()
        ev, n = parse_float(row[ei]), parse_float(row[ni])
        if grp == "" or grp.upper() in _NA_LABELS or ev is None or n is None:
            continue
        if ev != int(ev) or n != int(n):
            raise ValueError(
                f"그룹 '{grp}': 사건 수와 표본 수는 정수여야 합니다 "
                f"(--events-col '{events_col}', --n-col '{n_col}' 열의 값을 "
                f"확인하세요).")
        if grp in seen:
            raise ValueError(
                f"집계 표에 그룹 '{grp}' 이(가) 여러 번 나옵니다 — 그룹당 한 행만 "
                f"두세요.")
        seen[grp] = 1
        out.append((grp, (int(ev), int(n))))
    if len(out) < 2:
        raise ValueError(
            "집계 표에서 사용할 수 있는 그룹이 2개 미만입니다 "
            f"(--events-col '{events_col}', --n-col '{n_col}', "
            f"--group '{group_col}' 을 확인하세요).")
    return out


# --------------------------------------------------------------------------
# several endpoints at once (one column per outcome, shared group column)
# --------------------------------------------------------------------------

def load_multi_long(path: str, value_cols: Sequence[str], group_col: str,
                    delimiter: Optional[str] = None,
                    notes: Optional[List[str]] = None,
                    missing_out: Optional[Dict[str, Dict[str, int]]] = None,
                    binary: bool = False,
                    event_value: Optional[str] = None
                    ) -> List[Tuple[str, list]]:
    """Load one tidy file into several endpoints sharing a group column.

        subject,arm,isi,psqi,hrv
        S01,drug,12,7,44.5

    Returns ``[(endpoint_name, named_groups), ...]``.  ``named_groups`` is
    ``[(group, [values])]`` normally and ``[(group, (events, n))]`` when
    ``binary``.  Group order is first-seen and is identical for every endpoint,
    so the arms line up down the summary table.
    """
    if not value_cols:
        raise ValueError("분석할 결과(endpoint) 열을 하나 이상 지정하세요.")
    dupes = [c for c in value_cols if value_cols.count(c) > 1]
    if dupes:
        raise ValueError(
            "--values 에 같은 열이 여러 번 있습니다: " + ", ".join(sorted(set(dupes))))
    header, data = _read_rows(path, delimiter, notes)
    if group_col not in header:
        raise ValueError(
            f"group column '{group_col}' not in header {header}")
    for c in value_cols:
        if c not in header:
            raise ValueError(f"endpoint column '{c}' not in header {header}")
    if group_col in value_cols:
        raise ValueError(
            f"그룹 열 '{group_col}' 을(를) 결과 열(--values)로도 지정했습니다.")
    gi = header.index(group_col)
    vidx = {c: header.index(c) for c in value_cols}

    order: List[str] = []
    raw: Dict[str, Dict[str, List[str]]] = {c: {} for c in value_cols}
    ungrouped = 0
    for row in data:
        grp = row[gi].strip() if gi < len(row) else ""
        if grp == "" or grp.upper() in _NA_LABELS:
            ungrouped += 1
            continue
        if grp not in order:
            order.append(grp)
            for c in value_cols:
                raw[c][grp] = []
        for c in value_cols:
            i = vidx[c]
            raw[c][grp].append(row[i] if i < len(row) else "")
    if not order:
        raise ValueError("no usable rows found (check column names / data)")
    if notes is not None and ungrouped:
        notes.append(f"그룹 값이 없어 제외한 행 {ungrouped}개.")

    out: List[Tuple[str, list]] = []
    for c in value_cols:
        per_group_missing: Dict[str, int] = {}
        if binary:
            events, non_events = map_binary_levels(
                [v for g in order for v in raw[c][g]], event_value, notes)
            named: list = []
            for g in order:
                ev, tot, bad = _tally(raw[c][g], events, non_events)
                blanks = sum(1 for x in raw[c][g] if _clean_token(x) == "")
                per_group_missing[g] = bad + blanks
                named.append((g, (ev, tot)))
            if notes is not None:
                notes.append(f"[{c}] " + _levels_note(events, non_events))
        else:
            named = []
            for g in order:
                vals = []
                n_bad = 0
                for cell in raw[c][g]:
                    v = parse_float(cell)
                    if v is None:
                        n_bad += 1
                    else:
                        vals.append(v)
                per_group_missing[g] = n_bad
                named.append((g, vals))
        if missing_out is not None:
            missing_out[c] = per_group_missing
        out.append((c, named))
    return out


# --------------------------------------------------------------------------
# input-integrity screening
# --------------------------------------------------------------------------

#: Values that are almost always a coded missing marker rather than a
#: measurement. Seeing one of these treated as data is how a trial ends up with
#: a mean of -9.1 on a 0-28 scale and a test chosen by an outlier.
_SENTINELS = (-9.0, -99.0, -999.0, -9999.0, 999.0, 9999.0, 99999.0)


def screen_group_labels(labels: Sequence[str], notes: Optional[List[str]]
                        ) -> None:
    """Warn when two arm labels differ only by case or surrounding whitespace.

    ``Active`` and ``active`` are two arms to a computer and one arm to a
    trialist. Left alone, a single mis-typed row silently turns a two-arm trial
    into a three-group Kruskal-Wallis, and nothing in the report says so.
    """
    if notes is None:
        return
    buckets: Dict[str, List[str]] = {}
    for label in labels:
        buckets.setdefault(label.strip().casefold(), []).append(label)
    for variants in buckets.values():
        distinct = sorted(set(variants))
        if len(distinct) > 1:
            notes.append(
                "대소문자/공백만 다른 그룹 라벨이 별개의 군으로 처리되었습니다: "
                + ", ".join(repr(v) for v in distinct)
                + ". 같은 군이라면 CSV에서 표기를 통일하세요.")


def screen_values(label: str, values: Sequence[float],
                  notes: Optional[List[str]]) -> None:
    """Warn about coded-missing sentinels and far-out values in one arm."""
    if notes is None or not values:
        return
    hits = sorted({v for v in values if v in _SENTINELS})
    if hits:
        notes.append(
            f"[{label}] 결측 코드로 흔히 쓰이는 값이 자료에 그대로 들어 있습니다: "
            + ", ".join(f"{v:g}" for v in hits)
            + ". 실제 측정값이 아니라면 결측으로 바꾸고 다시 실행하세요 "
              "(그대로 두면 평균·정규성·검정 선택이 모두 왜곡됩니다).")
    if len(values) < 4:
        return
    s = sorted(values)
    q1, q3 = _pct_quantile(s, 0.25), _pct_quantile(s, 0.75)
    iqr = q3 - q1
    if iqr <= 0:
        return
    lo, hi = q1 - 3.0 * iqr, q3 + 3.0 * iqr
    far = [v for v in values if v < lo or v > hi]
    if far:
        shown = ", ".join(f"{v:g}" for v in sorted(set(far))[:3])
        notes.append(
            f"[{label}] 사분위 범위의 3배를 벗어난 값이 {len(far)}개 있습니다 "
            f"(예: {shown}). 자료 입력 오류인지 확인하세요 — 이런 값 하나가 "
            f"정규성 판정과 검정 선택을 바꿀 수 있습니다.")


def _pct_quantile(sorted_vals: Sequence[float], q: float) -> float:
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac
