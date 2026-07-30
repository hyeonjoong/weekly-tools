"""CSV/TSV loading for eegband — pure standard library (csv module).

Input is an EEG time series: one or more numeric *value* columns (µV, one per
channel) and an optional *time* column (seconds). Layout examples::

    # value only (sampling rate supplied via --fs)
    eeg_uv
    12.3
    11.8
    ...

    # value + time (sampling rate inferred from the time column, cross-checked vs --fs)
    time_s,eeg_uv
    0.000000,12.3
    0.007812,11.8
    ...

    # several channels side by side (wide format) — use --channels all / Fp1,Cz
    time_s,Fp1,Cz,O1
    0.000000,12.3,-4.1,8.8
    ...

Messy real-world exports are handled: the **delimiter** is sniffed (``,`` ``;`` tab
``|``), a European **decimal comma** (``12,3`` with ``;`` separators) is detected and
converted, the file is decoded as UTF-8/cp949/latin-1 as needed, and non-finite /
blank / NA cells become gaps that are linearly interpolated (with the count
reported), so a few dropouts don't break the uniform-sampling assumption.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

__all__ = ["SignalData", "parse_float", "load_signal", "load_signals",
           "list_columns", "infer_fs"]

# Tried in order. utf-8-sig transparently strips a BOM; cp949 covers Korean Excel
# exports; latin-1 never fails to decode, so it is the guaranteed last resort.
_ENCODINGS = ("utf-8-sig", "cp949", "latin-1")

# Candidate field separators, in preference order for ties (comma first).
_DELIMITERS = (",", ";", "\t", "|")

# Raised csv field cap (chars) so an export with a whole recording on one line parses.
_FIELD_SIZE_LIMIT = 64 * 1024 * 1024

_NA_LABELS = {"NA", "N/A", "NAN", "NULL", "NONE", ".", "-", "?"}
# Time column names whose values are in SECONDS. A sample/index counter is
# deliberately NOT here: treating an integer sample index as a seconds axis makes
# infer_fs report a bogus fs (e.g. 1 Hz) that then silently overrides --fs and
# corrupts every downstream frequency. If a counter really is your time axis,
# pass it explicitly with --time.
_TIME_NAMES = {"time", "t", "sec", "secs", "second", "seconds", "timestamp",
               "time_s", "t_s", "time_sec",
               # Korean headers (cp949/UTF-8 Excel exports are common here)
               "시간", "시각", "초", "시간_초", "시간(초)"}
# Time column names whose values are in MILLISECONDS (converted to seconds on load).
_TIME_MS_NAMES = {"time_ms", "ms", "msec", "msecs", "millis", "milliseconds",
                  "밀리초", "시간_ms", "시간(ms)"}
_VALUE_NAMES = {"eeg", "eeg_uv", "uv", "value", "signal", "amplitude", "amp",
                "ch1", "channel", "voltage", "microvolt", "microvolts", "mv",
                # Korean headers
                "뇌파", "뇌파_uv", "전압", "값", "신호", "진폭", "채널1", "채널"}


def _time_unit_scale(name: str) -> float:
    """Seconds-per-unit for a time column, inferred from its name (1.0 s, 0.001 ms)."""
    return 0.001 if name.strip().lower() in _TIME_MS_NAMES else 1.0


def parse_float(token: str, decimal_comma: bool = False) -> Optional[float]:
    """Parse a cell into a finite float, or None for blank / NA / non-finite / junk.

    With ``decimal_comma=True`` a single comma is read as the decimal separator
    (``12,3`` → 12.3), the convention of German/French/Korean-locale Excel exports.
    """
    t = token.strip()
    if t == "" or t.upper() in _NA_LABELS:
        return None
    if decimal_comma and "," in t:
        # Only a *single* comma is a decimal mark; "1,234,567" is a grouped integer
        # and stays unparseable rather than being silently mangled.
        if t.count(",") == 1 and "." not in t:
            t = t.replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None
    if not math.isfinite(v):
        return None
    return v


@dataclass
class SignalData:
    values: List[float]
    times: Optional[List[float]]
    value_col: str
    time_col: Optional[str]
    n_filled: int = 0          # gaps (NaN/blank) that were interpolated
    warnings: List[str] = field(default_factory=list)
    encoding: str = "utf-8-sig"  # codec the CSV was decoded with
    delimiter: str = ","         # field separator that was sniffed
    decimal_comma: bool = False  # cells used a decimal comma (12,3)
    source_file: Optional[str] = None

    @property
    def n(self) -> int:
        return len(self.values)


@dataclass
class _Table:
    header: List[str]
    rows: List[List[str]]
    encoding: str
    delimiter: str
    warnings: List[str] = field(default_factory=list)


def _sniff_delimiter(text: str) -> str:
    """Pick the field separator of a delimited text file.

    Scores each candidate on (header splits into >1 field, every data row has the same
    field count as the header, how many fields the header has) and takes the best,
    ties going to the earlier candidate — so a plain comma CSV stays a comma CSV while
    a European ``a;b`` file with decimal commas is recognised as semicolon-separated
    (splitting *that* on commas would produce a 1-field header).
    """
    sample_lines: List[str] = []
    for line in text.splitlines():
        if line.strip():
            sample_lines.append(line)
        if len(sample_lines) >= 6:
            break
    if not sample_lines:
        return _DELIMITERS[0]
    best_delim = _DELIMITERS[0]
    best_score = (-1, -1, -1)
    for d in _DELIMITERS:
        try:
            rows = list(csv.reader(sample_lines, delimiter=d))
        except csv.Error:
            continue
        if not rows:
            continue
        n_head = len(rows[0])
        data_counts = {len(r) for r in rows[1:]}
        consistent = (not data_counts) or data_counts == {n_head}
        score = (1 if n_head > 1 else 0, 1 if consistent else 0, n_head)
        if score > best_score:
            best_score = score
            best_delim = d
    # No candidate splits the header at all -> a single-column file. Return the comma
    # (the default) rather than whichever separator happened to look "consistent":
    # picking ';' for a 1-column comma CSV would turn a row with a stray extra comma
    # into an unparseable cell instead of an obvious extra field.
    if best_score[0] == 0:
        return _DELIMITERS[0]
    return best_delim


def _read_table(path: str) -> _Table:
    """Read a delimited text file: decode, sniff the delimiter, split into rows.

    Reads the raw bytes once and tries each codec so a non-UTF8 file (e.g. cp949 from
    Korean Excel) loads instead of crashing. ``open()`` errors (missing file, a
    directory, permission) propagate as the usual OSError subclasses for the caller to
    translate.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:2] == b"\x1f\x8b":
        raise ValueError(
            f"'{path}' is gzip-compressed. eegband reads plain text and raw "
            "EDF/BDF; decompress it first (gunzip).")
    if b"\x00" in data[:4096]:
        raise ValueError(
            f"'{path}' looks like a binary file (NUL bytes), not text. If it is a "
            "recording, EDF/EDF+/BDF is supported; other binary formats "
            "(GDF/BrainVision/FIF/UTF-16) are not — export it as CSV first.")
    text: Optional[str] = None
    encoding = _ENCODINGS[-1]
    for enc in _ENCODINGS:
        try:
            text = data.decode(enc)
            encoding = enc
            break
        except UnicodeDecodeError:
            continue
    if text is None:  # pragma: no cover - latin-1 decodes any byte string
        raise ValueError(f"'{path}' could not be decoded as text")
    # Normalise line endings: CR-only (classic Mac / some legacy exports) would
    # otherwise be one giant line, and CRLF leaves a stray \r in the last field.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    warnings: List[str] = []
    if encoding != "utf-8-sig":
        warnings.append(
            f"file was not valid UTF-8; decoded as '{encoding}'. If the numbers "
            "look wrong, re-save the CSV as UTF-8.")
    delimiter = _sniff_delimiter(text)
    # csv's default 128 kB field cap rejects legitimate (if unusual) exports that put a
    # whole recording on one line; raise it for this parse and restore it afterwards.
    old_limit = csv.field_size_limit()
    try:
        csv.field_size_limit(_FIELD_SIZE_LIMIT)
        raw_rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error as exc:
        # e.g. a stray NUL/newline inside a quoted field, or a binary blob that
        # decoded as latin-1. Never let csv's own exception reach the user.
        raise ValueError(
            f"'{path}' could not be parsed as delimited text ({exc}). Check that "
            "it is a CSV/TSV export and not a compressed or binary file.")
    finally:
        csv.field_size_limit(old_limit)
    # A row with no content at all is a formatting blank line in a multi-column file,
    # but in a SINGLE-column file it is a missing sample: dropping it would shift
    # every later time stamp. Keep interior blanks as gaps (they are interpolated and
    # counted downstream) and drop only the blank lines at the end of the file.
    rows: List[List[str]] = []
    single_col = all(len(r) <= 1 for r in raw_rows)
    for row in raw_rows:
        if any(cell.strip() for cell in row):
            rows.append(row)
        elif single_col:
            rows.append([""])
    if single_col:
        while rows and not any(cell.strip() for cell in rows[-1]):
            rows.pop()
    if not rows:
        raise ValueError(f"'{path}' is empty")
    if delimiter != ",":
        name = {";": "semicolon", "\t": "tab", "|": "pipe"}.get(delimiter, delimiter)
        warnings.append(f"delimiter detected as {name} ('{delimiter}'), not comma.")
    header = [h.strip() for h in rows[0]]
    return _Table(header=header, rows=rows[1:], encoding=encoding,
                  delimiter=delimiter, warnings=warnings)


def _read_rows(path: str) -> Tuple[List[str], List[List[str]], str]:
    """Backwards-compatible tuple form of :func:`_read_table`."""
    t = _read_table(path)
    return t.header, t.rows, t.encoding


def _detect_decimal_comma(rows: Sequence[Sequence[str]], cols: Sequence[int],
                          delimiter: str) -> bool:
    """True when the numeric cells of ``cols`` only parse with a decimal comma.

    Never considered for comma-separated files (there a comma cannot also be a decimal
    mark), and requires that switching actually rescues most of the failing cells, so
    a column of genuine text is not silently reinterpreted.
    """
    if delimiter == ",":
        return False
    fail = fixed = 0
    for row in rows[:500]:
        for i in cols:
            if i >= len(row):
                continue
            t = row[i].strip()
            if t == "" or t.upper() in _NA_LABELS:
                continue
            if parse_float(t) is None:
                fail += 1
                if parse_float(t, decimal_comma=True) is not None:
                    fixed += 1
    return fail > 0 and fixed * 2 >= fail


def _looks_numeric_header(header: List[str]) -> bool:
    """True if the 'header' row is actually data (no text labels)."""
    return all(parse_float(h) is not None for h in header)


def _fill_gaps(raw: List[Optional[float]]) -> Tuple[List[float], int]:
    """Linearly interpolate None gaps; ends filled with nearest value."""
    n = len(raw)
    out: List[Optional[float]] = list(raw)
    filled = 0
    # find first/last non-None
    first = next((i for i in range(n) if out[i] is not None), None)
    if first is None:
        raise ValueError("column contains no numeric values")
    last = next(i for i in range(n - 1, -1, -1) if out[i] is not None)
    # leading
    for i in range(first):
        out[i] = out[first]
        filled += 1
    # trailing
    for i in range(last + 1, n):
        out[i] = out[last]
        filled += 1
    # interior gaps
    i = first
    while i <= last:
        if out[i] is not None:
            i += 1
            continue
        j = i
        while out[j] is None:
            j += 1
        a = out[i - 1]  # type: ignore[assignment]
        b = out[j]      # type: ignore[assignment]
        span = j - (i - 1)
        for k in range(i, j):
            t = (k - (i - 1)) / span
            out[k] = a + (b - a) * t  # type: ignore[operator]
            filled += 1
        i = j
    return [float(v) for v in out], filled  # type: ignore[arg-type]


def _resolve_time_index(header: List[str], lower: List[str],
                        time_col: Optional[str], auto: bool) -> Optional[int]:
    """Index of the time column: explicit name, else an auto-detected time-like name."""
    if time_col is not None:
        if time_col not in header:
            raise ValueError(f"time column '{time_col}' not in header {header}")
        return header.index(time_col)
    if not auto:
        return None
    for i, name in enumerate(lower):
        if name in _TIME_NAMES or name in _TIME_MS_NAMES:
            return i
    return None


def _read_times(header: List[str], data: List[List[str]], ti: int,
                decimal_comma: bool, warnings: List[str]) -> Optional[List[float]]:
    """Extract, gap-fill and unit-normalise the time column (None if unusable)."""
    raw_times = [parse_float(row[ti], decimal_comma) if ti < len(row) else None
                 for row in data]
    try:
        times, t_filled = _fill_gaps(raw_times)
    except ValueError:
        warnings.append(f"time column '{header[ti]}' had no numeric values; "
                        "ignoring it.")
        return None
    if t_filled:
        warnings.append(
            f"{t_filled} gap(s) in time column '{header[ti]}' were interpolated.")
    scale = _time_unit_scale(header[ti])
    if scale != 1.0:
        times = [t * scale for t in times]
        warnings.append(
            f"time column '{header[ti]}' interpreted as milliseconds "
            "and converted to seconds.")
    return times


def _build_signal(table: _Table, vi: int, ti: Optional[int], path: str,
                  decimal_comma: bool, times: Optional[List[float]],
                  base_warnings: Sequence[str]) -> SignalData:
    """Extract one value column as a :class:`SignalData` (gaps interpolated)."""
    header, data = table.header, table.rows
    warnings = list(base_warnings)
    raw_vals = [parse_float(row[vi], decimal_comma) if vi < len(row) else None
                for row in data]
    if not raw_vals:
        raise ValueError("no data rows found")
    try:
        values, n_filled = _fill_gaps(raw_vals)
    except ValueError:
        hint = f"value column '{header[vi]}' has no numeric values"
        if table.encoding != "utf-8-sig":
            hint += (f" (decoded as '{table.encoding}'; if the file is UTF-16 or "
                     "binary, re-save it as UTF-8)")
        else:
            hint += (f" (delimiter read as '{table.delimiter}'; check that the cells "
                     "are numbers)")
        raise ValueError(hint)
    if n_filled:
        warnings.append(
            f"{n_filled} non-finite/blank sample(s) in '{header[vi]}' were "
            "linearly interpolated.")
    return SignalData(
        values=values, times=times, value_col=header[vi],
        time_col=(header[ti] if ti is not None else None), n_filled=n_filled,
        warnings=warnings, encoding=table.encoding, delimiter=table.delimiter,
        decimal_comma=decimal_comma, source_file=path)


def load_signal(path: str, value_col: Optional[str] = None,
                time_col: Optional[str] = None) -> SignalData:
    """Load a single-channel EEG series from a CSV/TSV file.

    Column selection:
      * ``value_col`` / ``time_col`` explicit names win when given.
      * otherwise a time-like column name (time, t, sec, ...) is auto-detected,
      * a single remaining column is treated as the value column, or a value-like
        name (eeg, uv, value, ...) is matched.
    """
    table = _read_table(path)
    header, data = table.header, table.rows
    if _looks_numeric_header(header):
        raise ValueError(
            "the first row looks like data, not a header. Add a header line "
            "(e.g. 'eeg_uv' or 'time_s,eeg_uv').")
    lower = [h.lower() for h in header]
    ti = _resolve_time_index(header, lower, time_col, auto=(value_col is None))

    # Resolve value column.
    if value_col is not None:
        if value_col not in header:
            raise ValueError(f"value column '{value_col}' not in header {header}")
        vi = header.index(value_col)
    else:
        candidates = [i for i in range(len(header)) if i != ti]
        if len(candidates) == 1:
            vi = candidates[0]
        else:
            named = [i for i in candidates if lower[i] in _VALUE_NAMES]
            if len(named) == 1:
                vi = named[0]
            elif len(candidates) == 0:
                raise ValueError("no value column found besides the time column")
            else:
                raise ValueError(
                    "could not auto-detect the value column among "
                    f"{[header[i] for i in candidates]}; pass --value NAME "
                    "(or --channels all to analyse every channel).")

    if ti is not None and ti == vi:
        ti = None

    warnings = list(table.warnings)
    cols = [vi] + ([ti] if ti is not None else [])
    decimal_comma = _detect_decimal_comma(data, cols, table.delimiter)
    if decimal_comma:
        warnings.append(
            "cells use a decimal comma (e.g. 12,3); converted to a decimal point.")
    times = (_read_times(header, data, ti, decimal_comma, warnings)
             if ti is not None else None)
    return _build_signal(table, vi, ti, path, decimal_comma, times, warnings)


def list_columns(path: str) -> Tuple[List[str], Optional[str], str, str]:
    """Inspect a CSV/TSV: ``(value_columns, time_column, delimiter, encoding)``.

    Used by ``--list-channels`` to show what is in a file without analysing it.
    """
    table = _read_table(path)
    if _looks_numeric_header(table.header):
        raise ValueError(
            "the first row looks like data, not a header. Add a header line "
            "(e.g. 'eeg_uv' or 'time_s,eeg_uv').")
    lower = [h.lower() for h in table.header]
    ti = _resolve_time_index(table.header, lower, None, auto=True)
    values = [h for i, h in enumerate(table.header) if i != ti]
    return (values, table.header[ti] if ti is not None else None,
            table.delimiter, table.encoding)


def load_signals(path: str, value_cols: Optional[Sequence[str]] = None,
                 time_col: Optional[str] = None) -> List[SignalData]:
    """Load **several** channels (wide-format columns) from one CSV/TSV file.

    ``value_cols=None`` means every column that is not the time column, in file
    order. Named columns must exist (a typo is an error, never a silent skip). A
    column that holds no numeric value at all is skipped with a warning attached to
    the remaining series; if that leaves nothing, the underlying error is raised.
    """
    table = _read_table(path)
    header, data = table.header, table.rows
    if _looks_numeric_header(header):
        raise ValueError(
            "the first row looks like data, not a header. Add a header line "
            "(e.g. 'eeg_uv' or 'time_s,eeg_uv').")
    lower = [h.lower() for h in header]
    ti = _resolve_time_index(header, lower, time_col, auto=True)

    if value_cols is None:
        idxs = [i for i in range(len(header)) if i != ti]
        if not idxs:
            raise ValueError("no value column found besides the time column")
    else:
        idxs = []
        for name in value_cols:
            if name not in header:
                raise ValueError(
                    f"channel '{name}' not in header {header}")
            i = header.index(name)
            if i == ti:
                # an explicitly requested column wins over time auto-detection
                ti = None
            if i not in idxs:
                idxs.append(i)
        if not idxs:
            raise ValueError("no channels selected")

    warnings = list(table.warnings)
    decimal_comma = _detect_decimal_comma(
        data, idxs + ([ti] if ti is not None else []), table.delimiter)
    if decimal_comma:
        warnings.append(
            "cells use a decimal comma (e.g. 12,3); converted to a decimal point.")
    times = (_read_times(header, data, ti, decimal_comma, warnings)
             if ti is not None else None)

    # Duplicate header names would produce two series with the same label and
    # different numbers; disambiguate them as 'name#2', 'name#3', ...
    seen_names: dict = {}
    for i in idxs:
        seen_names[header[i]] = seen_names.get(header[i], 0) + 1
    dup_names = {n for n, c in seen_names.items() if c > 1}
    if dup_names:
        warnings.append(
            "duplicate column name(s) " + ", ".join(sorted(dup_names))
            + "; later ones are labelled name#2, name#3, ...")
    used: dict = {}

    out: List[SignalData] = []
    skipped: List[str] = []
    first_error: Optional[ValueError] = None
    for vi in idxs:
        try:
            sig = _build_signal(table, vi, ti, path, decimal_comma, times, warnings)
            name = header[vi]
            used[name] = used.get(name, 0) + 1
            if used[name] > 1:
                sig.value_col = f"{name}#{used[name]}"
            out.append(sig)
        except ValueError as exc:
            first_error = first_error or exc
            skipped.append(header[vi])
    if not out:
        raise first_error if first_error else ValueError("no usable channel found")
    if skipped:
        note = ("skipped non-numeric column(s): " + ", ".join(skipped))
        for sig in out:
            sig.warnings.append(note)
    return out


def infer_fs(times: List[float]) -> Tuple[float, bool, List[float]]:
    """Infer sampling rate from a time (seconds) column.

    Returns (fs, regular, diffs). ``regular`` is False when the spacing is uneven
    (coefficient of variation of the sample intervals > 1%).
    """
    if len(times) < 2:
        raise ValueError("need at least 2 time stamps to infer fs")
    diffs = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    span = times[-1] - times[0]
    if span <= 0:
        raise ValueError("time column is not increasing")
    fs = (len(times) - 1) / span
    mean_dt = span / (len(times) - 1)
    if mean_dt <= 0:
        return fs, False, diffs
    var = math.fsum((d - mean_dt) ** 2 for d in diffs) / len(diffs)
    cov = math.sqrt(var) / mean_dt
    regular = cov <= 0.01
    return fs, regular, diffs
