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
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

__all__ = ["SignalData", "parse_float", "load_signal", "infer_fs"]

_NA_LABELS = {"NA", "N/A", "NAN", "NULL", "NONE", ".", "-", "?"}
_TIME_NAMES = {"time", "t", "sec", "secs", "second", "seconds", "timestamp",
               "time_s", "t_s", "time_sec", "time_ms", "ms", "sample", "samples",
               "index", "idx"}
_VALUE_NAMES = {"eeg", "eeg_uv", "uv", "value", "signal", "amplitude", "amp",
                "ch1", "channel", "voltage", "microvolt", "microvolts", "mv"}


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

    @property
    def n(self) -> int:
        return len(self.values)


def _read_rows(path: str) -> Tuple[List[str], List[List[str]]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError(f"'{path}' is empty")
    header = [h.strip() for h in rows[0]]
    return header, rows[1:]


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
    header, data = _read_rows(path)
    warnings: List[str] = []

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
            if name in _TIME_NAMES:
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

    values, n_filled = _fill_gaps(raw_vals)
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
        except ValueError:
            warnings.append(f"time column '{header[ti]}' had no numeric values; "
                            "ignoring it.")
            times = None

    return SignalData(values=values, times=times, value_col=header[vi],
                      time_col=(header[ti] if ti is not None else None),
                      n_filled=n_filled, warnings=warnings)


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
