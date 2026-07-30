"""EDF / EDF+ / BDF reader — pure standard library.

EDF (European Data Format) is *the* interchange format for clinical polysomnography
and EEG: nearly every sleep lab and ambulatory recorder can export it. This module
reads the header and one channel at a time, converts the digital integers to
**microvolts**, and hands back the same :class:`~eegband.dataio.SignalData` object the
CSV loader produces, so the rest of the pipeline is format-agnostic.

Privacy by design (환자 식별정보 미열람). The EDF header's *local patient
identification* and *local recording identification* fields routinely hold a name, an
MRN and a birth date, and the header also carries the recording start date/time — all
of it PHI. This reader **never parses, stores or prints those bytes**; it reads only
the technical fields (channel labels, units, sampling rate, calibration). Nothing
identifying can therefore leak into a report, a JSON dump or a CSV provenance line.

Supported: EDF (16-bit) and BDF (24-bit BioSemi), continuous (EDF+C) and
discontinuous (EDF+D — read as if continuous, with a warning). ``EDF Annotations``
signals are listed but excluded from analysis (they hold text, not samples).
"""

from __future__ import annotations

import math
import os
from array import array
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .dataio import SignalData

__all__ = [
    "EdfSignal",
    "EdfInfo",
    "is_edf_path",
    "looks_like_edf",
    "read_edf_info",
    "read_edf_channel",
]

_HEADER_BYTES = 256
# EDF+ annotation channels hold text (TALs), not samples. Match loosely: real files
# use "EDF Annotations", but "Annotations"/"EDF Annotation"/"BDF Annotations" occur.
_ANNOT_KEYWORD = "annotation"

# Physical dimension (unit) → factor that converts it to µV.
_UNIT_TO_UV = {
    "uv": 1.0, "µv": 1.0, "μv": 1.0, "microvolt": 1.0, "microvolts": 1.0,
    "mv": 1e3, "millivolt": 1e3, "millivolts": 1e3,
    "v": 1e6, "volt": 1e6, "volts": 1e6,
    "nv": 1e-3, "nanovolt": 1e-3, "nanovolts": 1e-3,
}


@dataclass
class EdfSignal:
    """One signal (channel) as described by the EDF header."""

    index: int
    label: str
    unit: str                  # physical dimension exactly as recorded
    phys_min: float
    phys_max: float
    dig_min: float
    dig_max: float
    n_per_record: int          # samples per data record
    prefilter: str
    transducer: str
    fs: float                  # n_per_record / record_duration
    is_annotation: bool
    unit_scale_uv: float       # multiply physical value by this to get µV
    unit_known: bool

    @property
    def gain(self) -> float:
        """Physical units per digital step (0 when the calibration is degenerate)."""
        span = self.dig_max - self.dig_min
        if span == 0:
            return 0.0
        return (self.phys_max - self.phys_min) / span

    @property
    def calibration(self) -> str:
        """Which calibration case this channel is in.

        ``"ok"``            — usable gain.
        ``"dig_degenerate"`` — digital min == max: no mapping exists at all.
        ``"phys_constant"``  — physical min == max: every sample IS that value
                               (a placeholder/unused channel), so returning digital
                               counts instead would fabricate a spectrum.
        """
        if self.dig_max == self.dig_min:
            return "dig_degenerate"
        if self.phys_max == self.phys_min:
            return "phys_constant"
        return "ok"


@dataclass
class EdfInfo:
    """Technical header of an EDF/BDF file. Deliberately holds no patient fields."""

    path: str
    kind: str                  # "EDF" or "BDF"
    bytes_per_sample: int
    header_bytes: int
    n_records: int
    record_duration: float     # seconds
    continuous: bool           # False for EDF+D
    signals: List[EdfSignal] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def data_signals(self) -> List[EdfSignal]:
        return [s for s in self.signals if not s.is_annotation]

    @property
    def duration_sec(self) -> float:
        return self.n_records * self.record_duration

    def find(self, label: str) -> Optional[EdfSignal]:
        """Look up a channel: exact label, then case-insensitive, then substring.

        Ambiguous matches return ``None`` rather than guessing. Note that real files do
        repeat labels; :func:`read_edf_channel` therefore also accepts an explicit
        ``index`` so ``--channels all`` can address every signal unambiguously.
        """
        exact = [s for s in self.signals if s.label == label]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            return exact[0]      # duplicated label: first match, caller warns
        low = label.strip().lower()
        ci = [s for s in self.signals if s.label.strip().lower() == low]
        if ci:
            return ci[0]
        hits = [s for s in self.data_signals if low in s.label.strip().lower()]
        if len(hits) == 1:
            return hits[0]
        return None

    def duplicate_labels(self) -> List[str]:
        """Labels that occur on more than one signal (they cannot be addressed by
        name; use the signal index)."""
        seen: dict = {}
        for s in self.signals:
            seen[s.label] = seen.get(s.label, 0) + 1
        return [lab for lab, n in seen.items() if n > 1]


def is_edf_path(path: str) -> bool:
    """True when the file name looks like an EDF/BDF recording."""
    return os.path.splitext(path)[1].lower() in (".edf", ".bdf", ".rec")


def looks_like_edf(path: str) -> bool:
    """True when the first bytes are an EDF ('0') or BDF (0xFF 'BIOSEMI') magic."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return False
    if len(head) < 8:
        return False
    if head[0] == 0xFF and head[1:8] == b"BIOSEMI":
        return True
    return head[:1] == b"0" and head[1:8] == b" " * 7


def _ascii(raw: bytes) -> str:
    """Decode a fixed-width EDF header field and make it safe to print.

    Control characters are replaced with '?' — a corrupt or hostile recording must not
    be able to push ANSI escape sequences, NULs or newlines into an operator's console
    or break the one-line-per-channel contract of ``--list-channels``.
    """
    text = raw.decode("latin-1", errors="replace")
    cleaned = "".join(ch if (ch.isprintable() or ch == " ") else "?" for ch in text)
    return cleaned.strip()


def _num_field(raw: bytes, what: str, path: str) -> float:
    """Parse a numeric header field. The raw bytes are NEVER echoed: for an arbitrary
    (non-EDF) file they are just file content, and messages promise only file names."""
    txt = _ascii(raw)
    try:
        return float(txt)
    except ValueError:
        raise ValueError(
            f"'{path}' is not a valid EDF/BDF file: header field {what} "
            f"({len(raw)} bytes) is not a number. If this is a CSV/TSV export, give "
            "it a .csv extension.")


def _int_field(raw: bytes, what: str, path: str) -> int:
    val = _num_field(raw, what, path)
    if not float(val).is_integer():
        raise ValueError(f"'{path}': header field {what} must be an integer, "
                         f"got {val:g}")
    return int(val)


def read_edf_info(path: str) -> EdfInfo:
    """Parse the technical header of an EDF/EDF+/BDF file (no sample data, no PHI)."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        head = fh.read(_HEADER_BYTES)
        if len(head) < _HEADER_BYTES:
            raise ValueError(
                f"'{path}' is too short to be an EDF/BDF file "
                f"({len(head)} bytes; the header alone is {_HEADER_BYTES}). If this is "
                "a text export, rename it to .csv/.tsv so it is read as text.")
        if head[0] == 0xFF and head[1:8] == b"BIOSEMI":
            kind, bps = "BDF", 3
        elif head[:1] == b"0":
            kind, bps = "EDF", 2
        else:
            raise ValueError(
                f"'{path}' does not start with an EDF ('0') or BDF (0xFF BIOSEMI) "
                "magic value — is it really an EDF/BDF recording? (A CSV/TSV export "
                "should have a .csv/.tsv extension.)")
        # Bytes 8..168 are the patient/recording identification and 168..184 the
        # recording start date+time. All four are PHI and are deliberately skipped.
        header_bytes = _int_field(head[184:192], "header size", path)
        reserved = _ascii(head[192:236])
        n_records = _int_field(head[236:244], "number of data records", path)
        record_duration = _num_field(head[244:252], "record duration", path)
        ns = _int_field(head[252:256], "number of signals", path)
        if ns <= 0:
            raise ValueError(f"'{path}': header declares {ns} signals.")
        if record_duration <= 0:
            raise ValueError(
                f"'{path}': record duration is {record_duration:g} s (must be > 0).")
        expected_header = _HEADER_BYTES + ns * 256
        if header_bytes != expected_header:
            raise ValueError(
                f"'{path}': header size {header_bytes} does not match "
                f"{ns} signals (expected {expected_header}).")

        fh.seek(_HEADER_BYTES)
        block = fh.read(ns * 256)
        if len(block) < ns * 256:
            raise ValueError(f"'{path}': signal header block is truncated.")

    def fields(offset: int, width: int) -> List[bytes]:
        base = offset * ns
        return [block[base + i * width:base + (i + 1) * width] for i in range(ns)]

    labels = [_ascii(b) for b in fields(0, 16)]
    transducers = [_ascii(b) for b in fields(16, 80)]
    units = [_ascii(b) for b in fields(96, 8)]
    phys_min = [_num_field(b, f"physical min of '{labels[i]}'", path)
                for i, b in enumerate(fields(104, 8))]
    phys_max = [_num_field(b, f"physical max of '{labels[i]}'", path)
                for i, b in enumerate(fields(112, 8))]
    dig_min = [_num_field(b, f"digital min of '{labels[i]}'", path)
               for i, b in enumerate(fields(120, 8))]
    dig_max = [_num_field(b, f"digital max of '{labels[i]}'", path)
               for i, b in enumerate(fields(128, 8))]
    prefilters = [_ascii(b) for b in fields(136, 80)]
    n_per_record = [_int_field(b, f"samples/record of '{labels[i]}'", path)
                    for i, b in enumerate(fields(216, 8))]

    warnings: List[str] = []
    continuous = not reserved.upper().startswith("EDF+D")
    if not continuous:
        warnings.append(
            "EDF+D (discontinuous) file: records may not be contiguous in time. "
            "eegband treats the samples as one continuous series — check the "
            "recording for gaps before trusting epoch timing.")

    bad_spr = [labels[i] or f"#{i + 1}" for i, n in enumerate(n_per_record) if n < 0]
    if bad_spr:
        raise ValueError(
            f"'{path}': negative samples-per-record on channel(s) "
            f"{', '.join(bad_spr)} — the header is corrupt.")
    record_samples = sum(n_per_record)
    if record_samples <= 0:
        raise ValueError(f"'{path}': every signal declares 0 samples per record.")
    record_bytes = record_samples * bps
    avail = max(0, size - header_bytes)
    max_records = avail // record_bytes
    if n_records < 0:
        n_records = max_records
        warnings.append(
            "header declares an unknown number of data records (-1); derived "
            f"{n_records} from the file size.")
    elif n_records > max_records:
        warnings.append(
            f"file is truncated: header declares {n_records} data records but only "
            f"{max_records} are present; the missing tail is ignored.")
        n_records = max_records
    if n_records == 0:
        raise ValueError(f"'{path}' contains no data records.")

    signals: List[EdfSignal] = []
    for i in range(ns):
        is_annot = _ANNOT_KEYWORD in labels[i].strip().lower()
        unit_key = units[i].strip().lower()
        scale = _UNIT_TO_UV.get(unit_key)
        known = scale is not None
        signals.append(EdfSignal(
            index=i, label=labels[i] or f"ch{i + 1}", unit=units[i],
            phys_min=phys_min[i], phys_max=phys_max[i], dig_min=dig_min[i],
            dig_max=dig_max[i], n_per_record=n_per_record[i],
            prefilter=prefilters[i], transducer=transducers[i],
            fs=n_per_record[i] / record_duration, is_annotation=is_annot,
            unit_scale_uv=(scale if known else 1.0), unit_known=known))

    info = EdfInfo(path=path, kind=kind, bytes_per_sample=bps,
                   header_bytes=header_bytes, n_records=n_records,
                   record_duration=record_duration, continuous=continuous,
                   signals=signals, warnings=warnings)
    dups = info.duplicate_labels()
    if dups:
        warnings.append(
            f"duplicate channel label(s): {', '.join(dups)}. A name selects the first "
            "match; --channels all still analyses every signal separately.")
    inverted = [s.label for s in signals if s.dig_max < s.dig_min]
    if inverted:
        warnings.append(
            f"channel(s) {', '.join(inverted)} declare digital min > max (an EDF spec "
            "violation); the calibration inverts their polarity. Band powers are "
            "unaffected but the amplitude/quality figures are sign-flipped.")
    return info


def _decode_samples(raw: bytes, bps: int, count: int) -> List[int]:
    """Little-endian two's-complement decode of ``count`` samples (16- or 24-bit)."""
    if bps == 2:
        arr = array("h")            # 'h' is exactly 2 bytes on every CPython target
        arr.frombytes(raw[:count * 2])
        if array("h", b"\x01\x00")[0] != 1:  # pragma: no cover - big-endian host
            arr.byteswap()
        return list(arr)
    out: List[int] = []
    for k in range(count):
        b0, b1, b2 = raw[3 * k], raw[3 * k + 1], raw[3 * k + 2]
        v = b0 | (b1 << 8) | (b2 << 16)
        if v & 0x800000:
            v -= 0x1000000
        out.append(v)
    return out


def read_edf_channel(path: str, label: Optional[str] = None,
                     start_sec: float = 0.0,
                     duration_sec: Optional[float] = None,
                     info: Optional[EdfInfo] = None,
                     index: Optional[int] = None,
                     ) -> Tuple[SignalData, float, EdfSignal]:
    """Read one channel of an EDF/BDF file as µV samples.

    Returns ``(signal_data, fs, edf_signal)``. Selection: ``index`` (0-based signal
    position) wins when given — that is how ``--channels all`` addresses every signal
    even when two of them share a label; otherwise ``label`` is resolved via
    :meth:`EdfInfo.find`; ``label=None`` picks the first non-annotation channel.
    ``start_sec``/``duration_sec`` crop the read to a window, and only the records
    covering it are touched, so a 10-minute look at an all-night recording costs 10
    minutes of I/O rather than 8 hours of it.
    """
    info = info if info is not None else read_edf_info(path)
    data_sigs = info.data_signals
    if not data_sigs:
        raise ValueError(f"'{path}' has no ordinary signal channels (annotations "
                         "only).")
    if index is not None:
        if not (0 <= index < len(info.signals)):
            raise ValueError(
                f"channel index {index} is out of range for '{path}' "
                f"({len(info.signals)} signals).")
        sig = info.signals[index]
        if sig.is_annotation:
            raise ValueError(
                f"channel '{sig.label}' is an EDF+ annotation channel (text, not "
                "samples) and cannot be analysed as a signal.")
    elif label is None:
        sig = data_sigs[0]
    else:
        found = info.find(label)
        if found is None:
            names = ", ".join(s.label for s in info.signals)
            raise ValueError(f"channel '{label}' not found in '{path}'. "
                             f"Available: {names}")
        if found.is_annotation:
            raise ValueError(
                f"channel '{found.label}' is an EDF+ annotation channel (text, not "
                "samples) and cannot be analysed as a signal.")
        sig = found
    if sig.n_per_record <= 0:
        raise ValueError(
            f"channel '{sig.label}' in '{path}' declares {sig.n_per_record} samples "
            "per record and carries no data.")

    warnings = list(info.warnings)
    if not sig.unit_known:
        warnings.append(
            f"channel '{sig.label}' has physical dimension '{sig.unit}', which is not "
            "a recognised voltage unit; values are used as-is and powers are in "
            "(that unit)² — rescale if it is not µV.")
    elif sig.unit_scale_uv != 1.0:
        warnings.append(
            f"channel '{sig.label}' is recorded in '{sig.unit}'; converted to µV "
            f"(×{sig.unit_scale_uv:g}).")

    gain = sig.gain
    cal = sig.calibration
    if cal == "dig_degenerate":
        warnings.append(
            f"channel '{sig.label}' has a degenerate calibration: digital min == max "
            f"== {sig.dig_min:g}, so no digital→physical mapping exists. The samples "
            "are returned as raw digital values — do NOT read the powers as µV².")
    elif cal == "phys_constant":
        warnings.append(
            f"channel '{sig.label}' declares physical min == max == "
            f"{sig.phys_min:g} {sig.unit or '?'}, i.e. a constant (placeholder/unused) "
            "channel; every sample is that constant value.")

    if start_sec < 0:
        raise ValueError("start_sec must be >= 0")
    if duration_sec is not None and duration_sec <= 0:
        raise ValueError("duration_sec must be > 0")

    rec_dur = info.record_duration
    first_rec = int(math.floor(start_sec / rec_dur))
    if first_rec >= info.n_records:
        raise ValueError(
            f"--start {start_sec:g}s is beyond the recording "
            f"({info.duration_sec:g}s long).")
    if duration_sec is None:
        last_rec = info.n_records
    else:
        last_rec = min(info.n_records,
                       int(math.ceil((start_sec + duration_sec) / rec_dur)))
    record_bytes = sum(s.n_per_record for s in info.signals) * info.bytes_per_sample
    offset_in_record = sum(s.n_per_record for s in info.signals[:sig.index]) \
        * info.bytes_per_sample
    n_read = sig.n_per_record * info.bytes_per_sample

    vals: List[float] = []
    # "ok"            -> physical = phys_min + (d - dig_min)*gain, scaled to µV
    # "phys_constant" -> every sample is phys_min (gain is 0 but the value is known)
    # "dig_degenerate"-> no mapping at all; hand back the raw digital counts
    calibrated = cal != "dig_degenerate"
    scale = gain * sig.unit_scale_uv
    base = sig.phys_min * sig.unit_scale_uv
    with open(path, "rb") as fh:
        for rec in range(first_rec, last_rec):
            fh.seek(info.header_bytes + rec * record_bytes + offset_in_record)
            raw = fh.read(n_read)
            if len(raw) < n_read:
                warnings.append(
                    f"record {rec} is truncated; stopped reading there.")
                break
            samples = _decode_samples(raw, info.bytes_per_sample, sig.n_per_record)
            if calibrated:
                for d in samples:
                    vals.append(base + (d - sig.dig_min) * scale)
            else:
                vals.extend(float(d) for d in samples)
    if not vals:
        raise ValueError(f"no samples read for channel '{sig.label}' in '{path}'.")

    # Trim to the exact requested window (records are whole-record granular).
    drop_head = int(round((start_sec - first_rec * rec_dur) * sig.fs))
    if drop_head > 0:
        vals = vals[drop_head:]
    if duration_sec is not None:
        keep = int(round(duration_sec * sig.fs))
        if keep < len(vals):
            vals = vals[:keep]
    if not vals:
        span = (f"start {start_sec:g}s"
                + (f", duration {duration_sec:g}s" if duration_sec is not None
                   else " to the end"))
        raise ValueError(
            f"the requested window ({span}) contains no samples of channel "
            f"'{sig.label}' (the recording is {info.duration_sec:g}s long).")

    data = SignalData(values=vals, times=None, value_col=sig.label, time_col=None,
                      n_filled=0, warnings=warnings, encoding=f"{info.kind} binary",
                      delimiter="", decimal_comma=False, source_file=path)
    return data, sig.fs, sig
