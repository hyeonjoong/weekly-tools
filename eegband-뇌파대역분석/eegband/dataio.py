"""CSV loading for eegband — pure standard library (csv module).

Input is a single-channel EEG time series: one numeric *value* column (µV) and an
optional *time* column (seconds). Layout examples::

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

Non-finite / blank / NA cells become gaps that are linearly interpolated (with the
count reported), so a few dropouts don't break the uniform-sampling assumption.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

__all__ = ["SignalData", "parse_float", "load_signal", "infer_fs"]

# Tried in order. utf-8-sig transparently strips a BOM; cp949 covers Korean Excel
# exports; latin-1 never fails to decode, so it is the guaranteed last resort.
_ENCODINGS = ("utf-8-sig", "cp949", "latin-1")

_NA_LABELS = {"NA", "N/A", "NAN", "NULL", "NONE", ".", "-", "?"}
# Time column names whose values are in SECONDS. A sample/index counter is
# deliberately NOT here: treating an integer sample index as a seconds axis makes
# infer_fs report a bogus fs (e.g. 1 Hz) that then silently overrides --fs and
# corrupts every downstream frequency. If a counter really is your time axis,
# pass it explicitly with --time.
_TIME_NAMES = {"time", "t", "sec", "secs", "second", "seconds", "timestamp",
               "time_s", "t_s", "time_sec"}
# Time column names whose values are in MILLISECONDS (converted to seconds on load).
_TIME_MS_NAMES = {"time_ms", "ms", "msec", "msecs", "millis", "milliseconds"}
_VALUE_NAMES = {"eeg", "eeg_uv", "uv", "value", "signal", "amplitude", "amp",
                "ch1", "channel", "voltage", "microvolt", "microvolts", "mv"}


def _time_unit_scale(name: str) -> float:
    """Seconds-per-unit for a time column, inferred from its name (1.0 s, 0.001 ms)."""
    return 0.001 if name.strip().lower() in _TIME_MS_NAMES else 1.0


def parse_float(token: str) -> Optional[float]:
    """Parse a cell into a finite float, or None for blank / NA / non-finite / junk."""
    t = token.strip()
    if t == "" or t.upper() in _NA_LABELS:
        return None
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

    @property
    def n(self) -> int:
        return len(self.values)


def _read_rows(path: str) -> Tuple[List[str], List[List[str]], str]:
    """Read a CSV, decoding with the first of _ENCODINGS that succeeds.

    Returns (header, data_rows, encoding_used). Reads the raw bytes once and tries
    each codec so a non-UTF8 file (e.g. cp949 from Korean Excel) loads instead of
    crashing. ``open()`` errors (missing file, a directory, permission) propagate as
    the usual OSError subclasses for the caller to translate.
    """
    with open(path, "rb") as fh:
        data = fh.read()
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
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError(f"'{path}' is empty")
    header = [h.strip() for h in rows[0]]
    return header, rows[1:], encoding


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


def load_signal(path: str, value_col: Optional[str] = None,
                time_col: Optional[str] = None) -> SignalData:
    """Load a single-channel EEG series from a CSV.

    Column selection:
      * ``value_col`` / ``time_col`` explicit names win when given.
      * otherwise a time-like column name (time, t, sec, ...) is auto-detected,
      * a single remaining column is treated as the value column, or a value-like
        name (eeg, uv, value, ...) is matched.
    """
    header, data, encoding = _read_rows(path)
    warnings: List[str] = []
    if encoding != "utf-8-sig":
        warnings.append(
            f"file was not valid UTF-8; decoded as '{encoding}'. If the numbers "
            "look wrong, re-save the CSV as UTF-8.")

    if _looks_numeric_header(header):
        raise ValueError(
            "the first row looks like data, not a header. Add a header line "
            "(e.g. 'eeg_uv' or 'time_s,eeg_uv').")

    lower = [h.lower() for h in header]

    # Resolve time column.
    ti: Optional[int] = None
    if time_col is not None:
        if time_col not in header:
            raise ValueError(f"time column '{time_col}' not in header {header}")
        ti = header.index(time_col)
    elif value_col is None:
        for i, name in enumerate(lower):
            if name in _TIME_NAMES or name in _TIME_MS_NAMES:
                ti = i
                break

    # Resolve value column.
    vi: Optional[int] = None
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
                    f"{[header[i] for i in candidates]}; pass --value NAME.")

    if ti is not None and ti == vi:
        ti = None

    raw_vals: List[Optional[float]] = []
    raw_times: List[Optional[float]] = [] if ti is not None else []
    for row in data:
        if vi >= len(row):
            raw_vals.append(None)
        else:
            raw_vals.append(parse_float(row[vi]))
        if ti is not None:
            raw_times.append(parse_float(row[ti]) if ti < len(row) else None)

    if not raw_vals:
        raise ValueError("no data rows found")

    try:
        values, n_filled = _fill_gaps(raw_vals)
    except ValueError:
        hint = f"value column '{header[vi]}' has no numeric values"
        if encoding != "utf-8-sig":
            hint += (f" (decoded as '{encoding}'; if the file is UTF-16 or binary, "
                     "re-save it as UTF-8)")
        else:
            hint += " (check that the delimiter is a comma and cells are numbers)"
        raise ValueError(hint)
    if n_filled:
        warnings.append(
            f"{n_filled} non-finite/blank sample(s) in '{header[vi]}' were "
            "linearly interpolated.")

    times: Optional[List[float]] = None
    if ti is not None:
        # time column may itself have gaps; fill them too, then it's usable.
        try:
            times, t_filled = _fill_gaps(raw_times)
            if t_filled:
                warnings.append(
                    f"{t_filled} gap(s) in time column '{header[ti]}' were "
                    "interpolated.")
            scale = _time_unit_scale(header[ti])
            if scale != 1.0:
                times = [t * scale for t in times]
                warnings.append(
                    f"time column '{header[ti]}' interpreted as milliseconds "
                    "and converted to seconds.")
        except ValueError:
            warnings.append(f"time column '{header[ti]}' had no numeric values; "
                            "ignoring it.")
            times = None

    return SignalData(values=values, times=times, value_col=header[vi],
                      time_col=(header[ti] if ti is not None else None),
                      n_filled=n_filled, warnings=warnings, encoding=encoding)


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
