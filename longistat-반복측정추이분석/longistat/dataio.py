"""CSV loading for repeated-measures (longitudinal) data — standard library only.

Two layouts are supported and both end up in the same :class:`Panel`:

``long``  one row per (subject, timepoint)::

    subject,visit,isi,arm
    S01,baseline,18,active
    S01,wk4,12,active

``wide``  one row per subject, one column per timepoint::

    subject,baseline,wk4,wk8,arm
    S01,18,12,9,active

Clinical CSVs are messy, so the reader deliberately tolerates: UTF-8 BOM,
cp949/euc-kr (Excel on Korean Windows), ``;``/tab/pipe delimiters, blank and
``NA``/``N/A``/``.``/``null`` cells, thousands separators, stray whitespace and
duplicated header names.  Anything it cannot interpret unambiguously raises a
``ValueError`` naming the offending row instead of silently guessing.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from .covariates import MAX_LEVELS, Covariate

__all__ = [
    "Panel",
    "Covariate",
    "load_long",
    "load_wide",
    "read_table",
    "clean_label",
    "MISSING_TOKENS",
    "MAX_INPUT_BYTES",
    "MAX_TIMEPOINTS",
]

# A repeated-measures design does not have hundreds of visits.  When it looks
# like it does, --time is almost always pointing at a visit-*date* column, and
# the pairwise machinery is quadratic in the number of timepoints.
MAX_TIMEPOINTS = 60
# ~4x this ends up resident while parsing; refuse rather than swap-thrash.
MAX_INPUT_BYTES = 200 * 1024 * 1024

# Cells that mean "no measurement".  Deliberately conservative: sentinel codes
# such as 999 or -1 are *not* on the list, because silently dropping a real
# value is far worse than reporting one the user must recode themselves.
MISSING_TOKENS = frozenset({
    "", "na", "n/a", "n.a.", "nan", "null", "none", "nil", ".", "-", "--",
    "missing", "결측", "무응답", "#n/a", "#null!", "#div/0!",
})

_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
# The French/Russian convention writes 1234 as "1 234" with a non-breaking or
# thin space.  Only that exact shape is accepted; any other inner space is a
# typo and joining it silently turned "1 2" into 12.
_SPACE_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(\u00a0\d{3})+(\.\d+)?$")
_LINE_SPLIT = re.compile(r"\r\n|\r|\n")
# csv.field_size_limit() rejects anything larger first; keep them consistent so
# the guard is real rather than decorative.
_MAX_CELL_CHARS = 131072


def _redact(cell: str, show_upto: int = 12, keep: int = 4) -> str:
    """Shorten a data cell for an error message.

    Error text gets pasted into Slack and issue trackers.  The row number and
    the column name already localise the problem, so the cell body adds nothing
    diagnostic while carrying real disclosure risk — the value column of a
    clinical export routinely holds free-text annotations with names and phone
    numbers.  Short tokens (``N/A``, ``열두``) are shown in full because they
    are the ones a user actually needs to see; anything longer is truncated
    hard.
    """
    flat = " ".join(cell.split())
    if len(flat) <= show_upto:
        return flat
    return f"{flat[:keep]}…({len(flat)}자)"


class DataError(ValueError):
    """Raised for user-fixable problems in the input file."""


# --------------------------------------------------------------------------
# raw table reading
# --------------------------------------------------------------------------

def _decode(raw: bytes, notes: List[str]) -> str:
    """Decode CSV bytes, trying the encodings clinical exports actually use."""
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr", "cp1252"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if enc not in ("utf-8-sig", "utf-8"):
            notes.append(f"파일 인코딩을 {enc} 로 해석했습니다 (UTF-8이 아님).")
        return text
    notes.append("인코딩을 확정하지 못해 latin-1로 강제 해석했습니다 "
                 "(글자가 깨질 수 있습니다).")
    return raw.decode("latin-1")


def _sniff_delimiter(sample: str, notes: List[str]) -> str:
    """Pick the delimiter whose column count is most consistent across rows."""
    lines = [ln for ln in _LINE_SPLIT.split(sample) if ln.strip()][:50]
    if not lines:
        return ","
    best, best_score = ",", (-1.0, -1)
    for cand in (",", "\t", ";", "|"):
        try:
            rows = list(csv.reader(lines, delimiter=cand))
        except csv.Error:
            continue
        widths = [len(r) for r in rows if r]
        if not widths or max(widths) < 2:
            continue
        header_w = widths[0]
        consistent = sum(1 for w in widths if w == header_w) / len(widths)
        score = (consistent, header_w)
        if score > best_score:
            best, best_score = cand, score
    if best != ",":
        notes.append(f"구분자를 '{'TAB' if best == chr(9) else best}' 로 자동 인식했습니다.")
    return best


def read_table(path: str, delimiter: Optional[str], notes: List[str]
               ) -> Tuple[List[str], Iterator[Tuple[int, List[str]]]]:
    """Read *path* and return ``(header, rows)``.

    ``rows`` is a generator of ``(line_number, cells)`` — line numbers are the
    physical ones in the file, so an error message points at the line the user
    can actually open, not at a count of non-blank lines.  Rows are yielded
    lazily instead of materialised: a 200 MB export cost 3.2 GB resident when
    every cell was held as a separate ``str`` in a list of lists.

    Rows shorter than the header are padded; a longer row with real content is
    an error (an unquoted delimiter inside a free-text field would otherwise
    misalign every later column).
    """
    if os.path.isdir(path):
        raise DataError(f"'{path}' 은(는) 폴더입니다. CSV 파일 경로를 지정하세요.")
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    if size > MAX_INPUT_BYTES:
        raise DataError(
            f"입력 파일이 {size / 1024 / 1024:.0f} MB 로 너무 큽니다 "
            f"(상한 {MAX_INPUT_BYTES // 1024 // 1024} MB). 필요한 열·기간만 "
            "추출해서 다시 시도하세요.")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        raise DataError(f"파일을 찾을 수 없습니다: {path}") from None
    except PermissionError:
        raise DataError(f"파일을 읽을 권한이 없습니다: {path}") from None
    except OSError as exc:
        raise DataError(f"파일을 읽을 수 없습니다: {exc}") from None
    if not raw.strip():
        raise DataError(f"파일이 비어 있습니다: {path}")

    text = _decode(raw, notes)
    del raw
    if delimiter is None:
        delimiter = _sniff_delimiter(text[:64_000], notes)
    else:
        if delimiter == "\\t":
            delimiter = "\t"
        if len(delimiter) != 1:
            raise DataError(
                "--delimiter 는 한 글자여야 합니다 (탭은 --delimiter '\\t').")

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        header_row = next(
            (row for row in reader if any(c.strip() for c in row)), None)
    except csv.Error as exc:
        raise DataError(_csv_error(exc)) from None
    if header_row is None:
        raise DataError("데이터 행이 없습니다 (빈 줄만 있습니다).")
    header = _dedupe_header([c.strip() for c in header_row], notes)
    width = len(header)

    def _stream() -> Iterator[Tuple[int, List[str]]]:
        while True:
            try:
                row = next(reader)
            except StopIteration:
                break
            except csv.Error as exc:
                raise DataError(_csv_error(exc)) from None
            lineno = reader.line_num
            if not any(c.strip() for c in row):
                continue
            cells = [c.strip() for c in row]
            if len(cells) > width:
                if any(c for c in cells[width:]):
                    raise DataError(
                        f"{lineno}행의 열 개수({len(cells)})가 머리글({width})보다 "
                        "많습니다. 따옴표로 감싸지 않은 구분자가 들어 있는지, "
                        "또는 --delimiter 가 맞는지 확인하세요.")
                cells = cells[:width]
            elif len(cells) < width:
                cells = cells + [""] * (width - len(cells))
            yield lineno, cells

    stream = _stream()
    # Pull the first data row eagerly so "header only" is reported by the call
    # itself rather than lazily, halfway through a caller's loop.
    first = next(stream, None)
    if first is None:
        raise DataError("머리글만 있고 데이터 행이 없습니다.")

    def rows() -> Iterator[Tuple[int, List[str]]]:
        yield first
        for item in stream:
            yield item

    return header, rows()


def _csv_error(exc: csv.Error) -> str:
    text = str(exc)
    if "field larger than field limit" in text:
        return ("한 셀의 내용이 너무 깁니다 (12만 자 초과) — 따옴표가 닫히지 "
                "않았거나 파일이 손상되었을 수 있습니다.")
    return f"CSV 를 읽는 중 오류: {text}"


def _dedupe_header(header: Sequence[str], notes: List[str]) -> List[str]:
    """Strip BOM/whitespace and make duplicate column names unique."""
    out: List[str] = []
    seen: Dict[str, int] = {}
    for i, name in enumerate(header):
        name = clean_label(name)
        if not name:
            name = f"열{i + 1}"
        if name in seen:
            seen[name] += 1
            new = f"{name}.{seen[name]}"
            notes.append(f"중복된 열 이름 '{name}' 을 '{new}' 로 바꿨습니다.")
            name = new
        else:
            seen[name] = 0
        out.append(name)
    return out


def _col_index(header: Sequence[str], name: str, role: str) -> int:
    """Locate a column, exactly first then case-insensitively.

    An ambiguous case-insensitive match is an error rather than a silent pick:
    a header carrying both ``ID`` and ``id`` used to bind to whichever came
    last, with no note.
    """
    wanted = clean_label(name)
    if wanted in header:
        return header.index(wanted)
    hits = [i for i, h in enumerate(header) if h.lower() == wanted.lower()]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise DataError(
            f"{role} 열 '{name}' 이(가) 대소문자만 다른 여러 열과 일치합니다 "
            f"({', '.join(header[i] for i in hits)}). 정확한 이름을 쓰세요.")
    raise DataError(
        f"{role} 열 '{name}' 을(를) 찾을 수 없습니다. "
        f"사용 가능한 열: {', '.join(header)}")


def parse_number(cell: str, thousands_sep: bool = True) -> Optional[float]:
    """Parse one cell into a float, or ``None`` when it means 'missing'.

    Deliberately strict about three things that used to change a value by a
    factor of 1000 without saying so:

    * inner whitespace is rejected, not deleted (``"1 2"`` was read as 12);
    * Python's numeric underscore is rejected (``"1_000"`` was read as 1000);
    * ``12,500`` is only a thousands separator when the file is *not*
      comma-delimited — in a semicolon-delimited European export it is 12.5.
    """
    token = cell.replace("\ufeff", "").strip()
    if len(token) > _MAX_CELL_CHARS:
        raise DataError("셀 내용이 비정상적으로 깁니다 (손상된 파일일 수 있습니다).")
    if token.lower() in MISSING_TOKENS:
        return None
    token = token.replace("\u2009", "\u00a0").replace("\u202f", "\u00a0")
    token = token.replace("\u3000", " ").strip()
    if _SPACE_THOUSANDS.match(token):
        token = token.replace("\u00a0", "")
    token = token.replace("\u00a0", " ")
    if _THOUSANDS.match(token):
        if not thousands_sep:
            raise DataError(
                f"'{_redact(cell)}' 의 쉼표가 천단위 구분자인지 소수점인지 "
                "확정할 수 없습니다 (이 파일은 쉼표 구분이 아닙니다). "
                "소수점은 '.' 로 바꿔 주세요.")
        token = token.replace(",", "")
    if token.endswith("%"):
        token = token[:-1].strip()
    if any(ch.isspace() for ch in token) or "_" in token:
        raise DataError(
            f"숫자 안에 공백이나 밑줄이 있습니다: '{_redact(cell)}'")
    try:
        val = float(token)
    except ValueError:
        raise DataError(f"숫자로 해석할 수 없는 값: '{_redact(cell)}'") from None
    if math.isnan(val):
        return None
    if math.isinf(val):
        raise DataError(f"무한대 값은 분석할 수 없습니다: '{_redact(cell)}'")
    return val


# --------------------------------------------------------------------------
# Panel
# --------------------------------------------------------------------------

@dataclass
class Panel:
    """Subject × timepoint matrix, the single input every analysis works from."""

    subjects: List[str]
    times: List[str]
    values: List[List[Optional[float]]]          # values[subject][time]
    groups: Optional[List[str]] = None           # one label per subject
    group_name: Optional[str] = None
    value_name: str = "value"
    time_name: str = "time"
    id_name: str = "id"
    notes: List[str] = field(default_factory=list)
    # Subject-level covariates (age, site, stratum …) used by the ANCOVA and
    # MMRM sections.  Empty unless the caller asked for them.
    covariates: List[Covariate] = field(default_factory=list)

    # -- basic accessors ---------------------------------------------------
    @property
    def n_subjects(self) -> int:
        return len(self.subjects)

    @property
    def n_times(self) -> int:
        return len(self.times)

    def group_labels(self) -> List[str]:
        """Distinct group labels in order of first appearance ('' if ungrouped)."""
        if self.groups is None:
            return []
        out: List[str] = []
        for g in self.groups:
            if g not in out:
                out.append(g)
        return out

    def column(self, j: int) -> List[float]:
        """Observed (non-missing) values at timepoint *j*."""
        return [row[j] for row in self.values if row[j] is not None]

    def complete_rows(self) -> List[int]:
        """Indices of subjects measured at *every* timepoint."""
        return [i for i, row in enumerate(self.values)
                if all(v is not None for v in row)]

    def subset_covariates(self, keep: Sequence[int]) -> List[Covariate]:
        """Covariates restricted to the subjects at positions *keep*."""
        return [Covariate(name=c.name,
                          values=[c.values[i] for i in keep],
                          categorical=c.categorical,
                          numeric=[c.numeric[i] for i in keep] if c.numeric else [])
                for c in self.covariates]

    def complete_case(self) -> "Panel":
        """A copy keeping only subjects with no missing timepoint."""
        keep = self.complete_rows()
        return Panel(
            subjects=[self.subjects[i] for i in keep],
            times=list(self.times),
            values=[list(self.values[i]) for i in keep],
            groups=None if self.groups is None else [self.groups[i] for i in keep],
            group_name=self.group_name,
            value_name=self.value_name,
            time_name=self.time_name,
            id_name=self.id_name,
            notes=list(self.notes),
            covariates=self.subset_covariates(keep),
        )

    def subset_times(self, idx: Sequence[int]) -> "Panel":
        """A copy restricted to the timepoints at positions *idx*."""
        return Panel(
            subjects=list(self.subjects),
            times=[self.times[j] for j in idx],
            values=[[row[j] for j in idx] for row in self.values],
            groups=None if self.groups is None else list(self.groups),
            group_name=self.group_name,
            value_name=self.value_name,
            time_name=self.time_name,
            id_name=self.id_name,
            notes=list(self.notes),
            covariates=self.subset_covariates(range(self.n_subjects)),
        )

    def matrix(self) -> List[List[float]]:
        """Complete-case value matrix (raises if any cell is still missing)."""
        out: List[List[float]] = []
        for row in self.values:
            if any(v is None for v in row):
                raise DataError("결측이 남아 있는 자료입니다 (complete_case() 먼저).")
            out.append([float(v) for v in row])       # type: ignore[arg-type]
        return out


# --------------------------------------------------------------------------
# time ordering
# --------------------------------------------------------------------------

_NUM_IN_LABEL = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _as_float(label: str) -> Optional[float]:
    """Numeric value of a timepoint label, or None if it is not a real number.

    ``float()`` accepts "nan" and "inf" — and ``str(numpy.nan)`` is exactly
    "nan", so any pandas wide→long round-trip produces one.  A NaN in the sort
    key made Timsort return an arbitrary visit order *and* suppressed the
    "specify --time-order" note, silently flipping every change-from-baseline.
    """
    try:
        value = float(label.replace(",", ""))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def order_times(labels: Sequence[str], explicit: Optional[Sequence[str]],
                notes: List[str]) -> List[str]:
    """Decide the analysis order of timepoint labels.

    ``--time-order`` wins.  Otherwise purely numeric labels sort numerically and
    ``visit1/visit2/...``-style labels sort by their embedded number; anything
    else keeps file order and gets a note telling the user to be explicit,
    because guessing the order of ``baseline/followup/wk4`` alphabetically would
    quietly reverse the whole analysis.
    """
    found = list(dict.fromkeys(labels))
    if explicit:
        want = [t.strip() for t in explicit if t.strip()]
        if len(set(want)) != len(want):
            raise DataError("--time-order 에 같은 시점이 두 번 들어 있습니다.")
        unknown = [t for t in want if t not in found]
        if unknown:
            raise DataError(
                f"--time-order 의 시점 {unknown} 이(가) 자료에 없습니다. "
                f"자료의 시점: {found}")
        dropped = [t for t in found if t not in want]
        if dropped:
            notes.append(f"--time-order 에 없는 시점 {dropped} 은(는) 제외했습니다.")
        return want

    nums = [_as_float(t) for t in found]
    if all(v is not None for v in nums):
        return [t for _, t in sorted(zip(nums, found))]    # type: ignore[arg-type]

    prefixes = {_NUM_IN_LABEL.sub("", t) for t in found}
    embedded = [_NUM_IN_LABEL.search(t) for t in found]
    if len(prefixes) == 1 and all(m is not None for m in embedded):
        keys = [float(m.group()) for m in embedded]        # type: ignore[union-attr]
        return [t for _, t in sorted(zip(keys, found))]

    if len(found) > 1:
        notes.append(
            "시점 순서를 파일에 나온 순서(" + " → ".join(found) + ")로 사용합니다. "
            "다르면 --time-order 로 지정하세요.")
    return found


# --------------------------------------------------------------------------
# subject-level covariates
# --------------------------------------------------------------------------

def _cov_cell(cell: str) -> Optional[str]:
    """One covariate cell → its label, or ``None`` when it means 'missing'."""
    lab = clean_label(cell)
    return None if lab.lower() in MISSING_TOKENS else lab


def _make_covariates(names: Sequence[str], forced_cat: Sequence[str],
                     raw: Dict[str, List[Optional[str]]],
                     notes: List[str]) -> List["Covariate"]:
    """Turn per-subject raw covariate strings into typed :class:`Covariate` objects.

    A covariate is continuous when *every* observed cell parses as a number,
    categorical otherwise.  That rule is stated in the notes together with the
    reason, because "site coded 1/2/3" silently becoming a continuous slope is
    exactly the kind of quiet mistake this tool is supposed to prevent — the
    user can force the categorical reading with ``--categorical``.
    """
    forced = {clean_label(c) for c in forced_cat}
    out: List[Covariate] = []
    for name in names:
        vals = raw.get(name, [])
        observed = [v for v in vals if v is not None]
        if not observed:
            raise DataError(f"공변량 '{name}' 에 값이 하나도 없습니다.")
        numeric: List[Optional[float]] = []
        example = ""
        is_num = name not in forced
        if is_num:
            for v in vals:
                if v is None:
                    numeric.append(None)
                    continue
                try:
                    parsed = parse_number(v, thousands_sep=False)
                except DataError:
                    parsed = None
                if parsed is None:
                    # `v` is already known not to be a missing token, so a
                    # failed parse means the column is not numeric at all.
                    is_num = False
                    example = _redact(v)
                    break
                numeric.append(parsed)
        if is_num:
            uniq = {v for v in numeric if v is not None}
            notes.append(f"공변량 '{name}': 연속형으로 사용 "
                         f"(관측 {len(observed)}명, 서로 다른 값 {len(uniq)}개).")
            out.append(Covariate(name=name, values=list(vals),
                                 categorical=False, numeric=numeric))
        else:
            levels: List[str] = []
            for v in vals:
                if v is not None and v not in levels:
                    levels.append(v)
            if len(levels) > MAX_LEVELS:
                # Naming the cell that forced the categorical reading matters:
                # one "45세" in an age column produced "your column has 40
                # categories, is it an ID or a date?", sending the user to look
                # for the wrong problem entirely.
                why_cat = (f"숫자가 아닌 값 '{example}' 때문에 범주형으로 "
                           "판정되었는데, " if example else "")
                raise DataError(
                    f"공변량 '{name}': {why_cat}범주가 {len(levels)}개입니다 "
                    f"(상한 {MAX_LEVELS}). 단위가 붙은 값이 섞여 있지 않은지, "
                    "대상 ID나 날짜 열을 공변량으로 지정하지 않았는지 "
                    "확인하세요.")
            why = (f" (숫자가 아닌 값 '{example}' 이 있어)" if example else "")
            notes.append(f"공변량 '{name}': 범주형으로 사용{why} — "
                         f"수준 {len(levels)}개, 기준 '{_redact(levels[0])}'.")
            out.append(Covariate(name=name, values=list(vals),
                                 categorical=True,
                                 numeric=[None] * len(vals)))
        n_missing = sum(1 for v in vals if v is None)
        if n_missing:
            notes.append(f"공변량 '{name}' 이 없는 대상 {n_missing}명은 "
                         "공변량 보정 분석([4c]·[5b])에서 제외됩니다.")
    return out


# --------------------------------------------------------------------------
# loaders
# --------------------------------------------------------------------------

def clean_label(cell: str) -> str:
    """Normalise a label so equal-looking strings really are equal.

    * NFC — macOS and some EDC exports emit decomposed Korean, which split one
      study arm into two identically-rendered groups and made ``--time-order``
      reject labels that look character-for-character right.
    * C0/C1 controls — an ESC in a group label repaints the terminal report.
    * BOM — survives concatenation of several exports and lands mid-file.
    """
    cell = cell.replace("\ufeff", "")
    cell = "".join(ch for ch in cell
                   if unicodedata.category(ch) != "Cc" or ch == "\t")
    return unicodedata.normalize("NFC", cell).strip()


_clean_label = clean_label


def _covariate_indices(header: Sequence[str], names: Sequence[str],
                       taken: Sequence[int]) -> List[Tuple[str, int]]:
    """Resolve ``--covariate`` names to column positions, rejecting overlaps."""
    out: List[Tuple[str, int]] = []
    seen: set = set()
    for raw in names:
        name = clean_label(raw)
        if not name:
            continue
        if name in seen:
            raise DataError(f"--covariate 에 '{name}' 이(가) 두 번 있습니다.")
        seen.add(name)
        idx = _col_index(header, name, "공변량(--covariate)")
        if idx in taken:
            raise DataError(
                f"공변량 '{name}' 이(가) 이미 --id/--time/--value/--group/"
                "--columns 로 쓰이고 있습니다. 공변량은 그와 다른 열이어야 합니다.")
        out.append((name, idx))
    return out


def _subject_covariates(specs: Sequence[Tuple[str, int]],
                        per_subject: Dict[str, Dict[str, Optional[str]]],
                        kept: Sequence[str], forced_cat: Sequence[str],
                        notes: List[str]) -> List["Covariate"]:
    """Per-subject raw strings → typed covariates, ordered like *kept*."""
    if not specs:
        return []
    raw: Dict[str, List[Optional[str]]] = {
        name: [per_subject.get(sid, {}).get(name) for sid in kept]
        for name, _ in specs}
    return _make_covariates([name for name, _ in specs], forced_cat, raw, notes)


def _record_covariate(store: Dict[str, Dict[str, Optional[str]]], sid: str,
                      name: str, cell: Optional[str], lineno: int) -> None:
    """Remember one subject-level covariate cell, refusing to average conflicts.

    A covariate that changes value within a subject is not a subject-level
    covariate — it is a time-varying one, and quietly keeping the first row
    would put a number in the model that no visit actually had.
    """
    bucket = store.setdefault(sid, {})
    prev = bucket.get(name)
    if cell is None:
        bucket.setdefault(name, None)
        return
    if prev is not None and prev != cell:
        raise DataError(
            f"{lineno}행: 공변량 '{name}' 이 같은 대상 안에서 "
            f"'{_redact(prev)}' 와 '{_redact(cell)}' 로 달라집니다. 공변량은 대상마다 값이 하나인 "
            "변수(나이·성별·기관 등)여야 합니다.")
    bucket[name] = cell


def load_long(path: str, id_col: str, time_col: str, value_col: str,
              group_col: Optional[str] = None,
              delimiter: Optional[str] = None,
              time_order: Optional[Sequence[str]] = None,
              duplicates: str = "error",
              covariate_cols: Optional[Sequence[str]] = None,
              categorical_cols: Sequence[str] = (),
              notes: Optional[List[str]] = None) -> Panel:
    """Load a long-format file into a :class:`Panel`."""
    notes = notes if notes is not None else []
    header, rows = read_table(path, delimiter, notes)
    comma_delimited = not any("구분자를" in n for n in notes)
    i_id = _col_index(header, id_col, "대상 ID(--id)")
    i_tm = _col_index(header, time_col, "시점(--time)")
    i_val = _col_index(header, value_col, "측정값(--value)")
    i_grp = _col_index(header, group_col, "그룹(--group)") if group_col else None
    chosen = [i_id, i_tm, i_val] + ([i_grp] if i_grp is not None else [])
    if len(set(chosen)) < len(chosen):
        raise DataError("--id/--time/--value/--group 에 같은 열을 두 번 지정했습니다.")
    cov_specs = _covariate_indices(header, covariate_cols or [], chosen)
    cov_raw: Dict[str, Dict[str, Optional[str]]] = {}

    cells: Dict[Tuple[str, str], List[float]] = {}
    subj_order: List[str] = []
    time_labels: List[str] = []
    seen_times: set = set()
    subj_group: Dict[str, str] = {}
    dropped_rows = 0
    conflicting_groups = 0

    for lineno, row in rows:
        sid = clean_label(row[i_id])
        tlab = clean_label(row[i_tm])
        if not sid or not tlab:
            dropped_rows += 1
            continue
        try:
            val = parse_number(row[i_val], thousands_sep=comma_delimited)
        except DataError as exc:
            raise DataError(f"{lineno}행 {value_col}: {exc}") from None
        if sid not in subj_group:
            subj_order.append(sid)
        if tlab not in seen_times:
            seen_times.add(tlab)
            time_labels.append(tlab)
            if len(time_labels) > MAX_TIMEPOINTS:
                raise DataError(
                    f"시점이 {MAX_TIMEPOINTS}개를 넘습니다 — --time 열이 방문일"
                    "(날짜)이나 연속 측정값을 가리키고 있지 않은지 확인하세요. "
                    "정말 다시점 자료라면 --time-order 로 분석할 시점을 "
                    "골라 주세요.")
        if i_grp is not None:
            g = clean_label(row[i_grp]) or "(미기재)"
            prev = subj_group.get(sid)
            if prev is not None and prev != g:
                conflicting_groups += 1
                raise DataError(
                    f"{lineno}행: 이 대상의 그룹이 '{prev}' 와 '{g}' 로 "
                    "엇갈립니다. 그룹은 대상마다 하나여야 합니다.")
            subj_group[sid] = g
        else:
            subj_group.setdefault(sid, "")
        for name, ci in cov_specs:
            _record_covariate(cov_raw, sid, name, _cov_cell(row[ci]), lineno)
        if val is None:
            continue
        cells.setdefault((sid, tlab), []).append(val)

    if dropped_rows:
        notes.append(f"ID 또는 시점이 비어 있는 {dropped_rows}개 행을 건너뛰었습니다.")
    if not subj_order:
        raise DataError("유효한 데이터 행이 없습니다.")

    times = order_times(time_labels, time_order, notes)
    dup_keys = [k for k, v in cells.items() if len(v) > 1]
    if dup_keys:
        if duplicates == "error":
            example = dup_keys[0]
            raise DataError(
                f"같은 (대상, 시점) 조합이 여러 번 있습니다 — 예: 시점 "
                f"'{example[1]}' 이 {len(cells[example])}번. "
                f"총 {len(dup_keys)}건. --duplicates mean 또는 first 로 처리 "
                "방법을 지정하세요.")
        how = "평균" if duplicates == "mean" else "첫 값"
        notes.append(f"중복 측정 {len(dup_keys)}건을 {how}으로 통합했습니다.")

    values: List[List[Optional[float]]] = []
    kept: List[str] = []
    n_blank = 0
    for sid in subj_order:
        row_vals: List[Optional[float]] = []
        for t in times:
            got = cells.get((sid, t))
            if not got:
                row_vals.append(None)
            elif len(got) == 1 or duplicates == "first":
                row_vals.append(got[0])
            else:
                row_vals.append(math.fsum(got) / len(got))
        if all(v is None for v in row_vals):
            n_blank += 1
            continue
        kept.append(sid)
        values.append(row_vals)
    if n_blank:
        notes.append(f"측정값이 하나도 없는 대상 {n_blank}명을 제외했습니다.")
    if not kept:
        raise DataError("분석할 수 있는 측정값이 없습니다 (모두 결측).")

    return Panel(
        subjects=kept,
        times=times,
        values=values,
        groups=[subj_group[s] for s in kept] if i_grp is not None else None,
        group_name=group_col,
        value_name=value_col,
        time_name=time_col,
        id_name=id_col,
        notes=notes,
        covariates=_subject_covariates(cov_specs, cov_raw, kept,
                                       categorical_cols, notes),
    )


def load_wide(path: str, columns: Sequence[str], id_col: Optional[str] = None,
              group_col: Optional[str] = None,
              delimiter: Optional[str] = None,
              duplicates: str = "error",
              value_name: str = "측정값",
              covariate_cols: Optional[Sequence[str]] = None,
              categorical_cols: Sequence[str] = (),
              notes: Optional[List[str]] = None) -> Panel:
    """Load a wide-format file (one column per timepoint) into a :class:`Panel`."""
    notes = notes if notes is not None else []
    header, rows = read_table(path, delimiter, notes)
    comma_delimited = not any("구분자를" in n for n in notes)
    cols = [clean_label(c) for c in columns if c.strip()]
    if len(cols) < 2:
        raise DataError("--columns 에 시점 열을 2개 이상 지정하세요.")
    if len(set(cols)) != len(cols):
        raise DataError("--columns 에 같은 열이 두 번 들어 있습니다.")
    if len(cols) > MAX_TIMEPOINTS:
        raise DataError(f"--columns 는 최대 {MAX_TIMEPOINTS}개까지 지원합니다.")
    idx = [_col_index(header, c, "시점(--columns)") for c in cols]
    i_id = _col_index(header, id_col, "대상 ID(--id)") if id_col else None
    i_grp = _col_index(header, group_col, "그룹(--group)") if group_col else None
    if i_id is not None and i_id in idx:
        raise DataError("--id 열이 --columns 에도 들어 있습니다.")
    if i_grp is not None and i_grp in idx:
        raise DataError("--group 열이 --columns 에도 들어 있습니다.")
    taken = list(idx) + [i for i in (i_id, i_grp) if i is not None]
    cov_specs = _covariate_indices(header, covariate_cols or [], taken)
    cov_raw: Dict[str, Dict[str, Optional[str]]] = {}

    subjects: List[str] = []
    groups: List[str] = []
    seen: Dict[str, int] = {}
    # Per subject keep running sums/counts per timepoint so that averaging three
    # or more duplicate rows weights them equally (a pairwise running mean would
    # give the last row half the weight).
    sums: List[List[float]] = []
    counts: List[List[int]] = []
    n_dupes = 0
    n_blank = 0
    for lineno, row in rows:
        sid = clean_label(row[i_id]) if i_id is not None else f"row{lineno - 1}"
        if not sid:
            sid = f"row{lineno - 1}"
        row_vals: List[Optional[float]] = []
        for c, j in zip(cols, idx):
            try:
                row_vals.append(parse_number(row[j],
                                             thousands_sep=comma_delimited))
            except DataError as exc:
                raise DataError(f"{lineno}행 {c}: {exc}") from None
        if all(v is None for v in row_vals):
            n_blank += 1
            continue
        label = clean_label(row[i_grp]) or "(미기재)" if i_grp is not None else ""
        for name, ci in cov_specs:
            _record_covariate(cov_raw, sid, name, _cov_cell(row[ci]), lineno)
        if sid in seen:
            n_dupes += 1
            if duplicates == "error":
                raise DataError(
                    f"{lineno}행: 이 대상 ID 가 여러 행에 있습니다. "
                    "--duplicates mean 또는 first 로 처리 방법을 지정하세요.")
            k = seen[sid]
            # The same consistency rule as the long loader: a subject cannot be
            # in two arms.  Wide format used to keep the first label silently.
            if i_grp is not None and label != groups[k]:
                raise DataError(
                    f"{lineno}행: 이 대상의 그룹이 '{groups[k]}' 와 '{label}' 로 "
                    "엇갈립니다. 그룹은 대상마다 하나여야 합니다.")
            if duplicates == "first":
                continue
            for j, v in enumerate(row_vals):
                if v is not None:
                    sums[k][j] += v
                    counts[k][j] += 1
            continue
        seen[sid] = len(subjects)
        subjects.append(sid)
        sums.append([v or 0.0 for v in row_vals])
        counts.append([0 if v is None else 1 for v in row_vals])
        if i_grp is not None:
            groups.append(label)

    if not subjects:
        raise DataError("모든 행의 시점 값이 비어 있습니다.")
    values: List[List[Optional[float]]] = [
        [(s / c if c else None) for s, c in zip(srow, crow)]
        for srow, crow in zip(sums, counts)]
    if n_blank:
        notes.append(f"모든 시점이 비어 있는 {n_blank}개 행을 건너뛰었습니다.")
    if n_dupes:
        how = "평균" if duplicates == "mean" else "첫 행만"
        notes.append(f"중복 대상 ID 행 {n_dupes}건을 {how}으로 처리했습니다.")

    return Panel(
        subjects=subjects,
        times=cols,
        values=values,
        groups=groups if i_grp is not None else None,
        group_name=group_col,
        value_name=value_name,
        time_name="time",
        id_name=id_col or "row",
        notes=notes,
        covariates=_subject_covariates(cov_specs, cov_raw, subjects,
                                       categorical_cols, notes),
    )
