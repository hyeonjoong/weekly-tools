"""Synthetic EDF/BDF file writer used by the tests.

Writes standards-conforming EDF (16-bit) and BDF (24-bit) files from float channel
data so the reader can be validated end-to-end without shipping a real (PHI-bearing)
recording. Kept deliberately independent of eegband.edf so a bug in the reader cannot
be masked by a matching bug in the writer.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple


def _fixed(text: str, width: int) -> bytes:
    raw = text.encode("latin-1", errors="replace")[:width]
    return raw + b" " * (width - len(raw))


def _num(value: float, width: int = 8) -> bytes:
    """EDF numeric field: plain decimal text that fits the fixed width exactly.

    Integers are written as integers (never in exponent form — ``8.39e+06`` would
    silently round a 24-bit digital maximum), and fractions are trimmed digit by digit
    until they fit.
    """
    candidates = []
    if float(value).is_integer():
        candidates.append(str(int(value)))
    candidates += [f"{value:.{d}f}" for d in range(6, -1, -1)]
    for txt in candidates:
        if len(txt) > width:
            continue
        # Reject a representation that silently loses the value (e.g. -2e-06 written
        # as "-0.00000"), so a fixture can never produce a mis-calibrated file.
        back = float(txt)
        if value == 0.0:
            ok = back == 0.0
        else:
            ok = abs(back - value) <= 1e-6 * abs(value)
        if ok:
            return _fixed(txt, width)
    raise ValueError(
        f"{value!r} cannot be written exactly in an EDF {width}-char field; "
        "choose a physical range with fewer significant digits")


def write_edf(path: str,
              channels: Sequence[Tuple[str, str, Sequence[float]]],
              fs: float, record_duration: float = 1.0, bdf: bool = False,
              phys_range: Optional[Tuple[float, float]] = None,
              reserved: str = "", n_records_field: Optional[int] = None,
              patient: str = "X X X X", recording: str = "Startdate X",
              truncate_records: int = 0) -> None:
    """Write ``channels`` = [(label, unit, samples), ...] as an EDF or BDF file.

    All channels must have the same length and share ``fs``. ``phys_range`` fixes the
    physical min/max (defaults to ±(max|x|) rounded out); ``reserved`` can be set to
    "EDF+D" to exercise the discontinuous path; ``n_records_field`` overrides the
    declared record count (e.g. -1 for "unknown"); ``truncate_records`` drops that
    many records from the end of the file to simulate a truncated recording.
    """
    n = len(channels[0][2])
    for _, _, vals in channels:
        if len(vals) != n:
            raise ValueError("all channels must have the same number of samples")
    n_per_record = int(round(fs * record_duration))
    if n_per_record <= 0:
        raise ValueError("fs * record_duration must be >= 1 sample")
    n_records = n // n_per_record
    if n_records == 0:
        raise ValueError("need at least one full data record")

    bps = 3 if bdf else 2
    dig_min, dig_max = ((-8388608, 8388607) if bdf else (-32768, 32767))
    ns = len(channels)
    header_bytes = 256 + 256 * ns

    if bdf:
        version = bytes([255]) + _fixed("BIOSEMI", 7)
    else:
        version = _fixed("0", 8)

    head = bytearray()
    head += version
    head += _fixed(patient, 80)
    head += _fixed(recording, 80)
    head += _fixed("01.01.85", 8)
    head += _fixed("00.00.00", 8)
    head += _num(header_bytes, 8)
    head += _fixed(reserved, 44)
    head += _num(n_records if n_records_field is None else n_records_field, 8)
    head += _num(record_duration, 8)
    head += _fixed(str(ns), 4)
    assert len(head) == 256, len(head)

    ranges: List[Tuple[float, float]] = []
    for _, _, vals in channels:
        if phys_range is not None:
            lo, hi = phys_range
        else:
            amp = max((abs(v) for v in vals), default=1.0)
            amp = math.ceil(amp) if amp > 0 else 1.0
            lo, hi = -amp, amp
        ranges.append((lo, hi))

    sig_head = bytearray()
    sig_head += b"".join(_fixed(lbl, 16) for lbl, _, _ in channels)
    sig_head += b"".join(_fixed("AgAgCl electrode", 80) for _ in channels)
    sig_head += b"".join(_fixed(unit, 8) for _, unit, _ in channels)
    sig_head += b"".join(_num(lo, 8) for lo, _ in ranges)
    sig_head += b"".join(_num(hi, 8) for _, hi in ranges)
    sig_head += b"".join(_num(dig_min, 8) for _ in channels)
    sig_head += b"".join(_num(dig_max, 8) for _ in channels)
    sig_head += b"".join(_fixed("HP:0.1Hz LP:75Hz", 80) for _ in channels)
    sig_head += b"".join(_fixed(str(n_per_record), 8) for _ in channels)
    sig_head += b"".join(_fixed("", 32) for _ in channels)
    assert len(sig_head) == 256 * ns, len(sig_head)

    body = bytearray()
    for rec in range(n_records):
        for ci, (_, _, vals) in enumerate(channels):
            lo, hi = ranges[ci]
            span = hi - lo
            for k in range(n_per_record):
                v = vals[rec * n_per_record + k]
                if span == 0:
                    # degenerate physical range (phys_min == phys_max): the digital
                    # value carries no information, so write dig_min.
                    d = dig_min
                else:
                    # inverse of the EDF calibration, rounded to the nearest integer
                    d = dig_min + (v - lo) * (dig_max - dig_min) / span
                d = int(round(min(max(d, dig_min), dig_max)))
                body += (d & 0xFFFFFF).to_bytes(3, "little") if bdf \
                    else (d & 0xFFFF).to_bytes(2, "little")

    record_bytes = n_per_record * ns * bps
    if truncate_records:
        body = body[:max(0, len(body) - truncate_records * record_bytes)]

    with open(path, "wb") as fh:
        fh.write(bytes(head))
        fh.write(bytes(sig_head))
        fh.write(bytes(body))


def sine(fs: float, dur: float, freq: float, amp: float = 20.0,
         phase: float = 0.0) -> List[float]:
    n = int(round(fs * dur))
    return [amp * math.sin(2 * math.pi * freq * k / fs + phase) for k in range(n)]
