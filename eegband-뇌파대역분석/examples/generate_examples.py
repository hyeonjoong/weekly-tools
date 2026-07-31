#!/usr/bin/env python3
"""Regenerate the synthetic example recordings shipped with eegband.

Run from the repository root::

    python3 examples/generate_examples.py

Everything is synthetic and deterministic (fixed seeds) — no patient data is
involved. Each trace is a 1/f background plus the rhythms named in its file name, so
the reports show a realistic aperiodic exponent as well as band power.

Note: ``alpha_wake.csv`` and ``delta_deep_sleep.csv`` predate this script and are NOT
regenerated here (their numbers are quoted in README.md); this script writes the
multi-channel CSV and the EDF example.
"""

from __future__ import annotations

import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                "tests"))
from edf_fixtures import write_edf  # noqa: E402  (path set up above)

HERE = os.path.dirname(os.path.abspath(__file__))


def pink(fs: float, dur: float, exponent: float, rms: float, seed: int,
         f_max: float = 45.0):
    """Sum-of-sinusoids noise whose expected PSD ∝ f^-exponent, scaled to ``rms``."""
    rng = random.Random(seed)
    n = int(round(fs * dur))
    df = 1.0 / dur
    comps = [(k * df, (k * df) ** (-exponent / 2.0), rng.uniform(0, 2 * math.pi))
             for k in range(1, int(f_max / df) + 1)]
    norm = math.sqrt(math.fsum(a * a for _, a, _ in comps) / 2.0)
    out = []
    for i in range(n):
        t = i / fs
        out.append(rms * math.fsum(a * math.cos(2 * math.pi * f * t + ph)
                                   for f, a, ph in comps) / norm)
    return out


def rhythm(fs: float, dur: float, freq: float, amp: float, seed: int):
    """A narrowband rhythm: a sinusoid with slow random amplitude modulation."""
    rng = random.Random(seed)
    n = int(round(fs * dur))
    phase = rng.uniform(0, 2 * math.pi)
    mod_f = 0.15 + 0.1 * rng.random()
    return [amp * (0.7 + 0.3 * math.sin(2 * math.pi * mod_f * i / fs))
            * math.sin(2 * math.pi * freq * i / fs + phase) for i in range(n)]


def add(*series):
    return [math.fsum(vals) for vals in zip(*series)]


def write_multichannel_csv(path: str, fs: float = 128.0, dur: float = 20.0) -> None:
    fp1 = add(pink(fs, dur, 1.3, 12.0, 1), rhythm(fs, dur, 10.0, 14.0, 11))
    cz = add(pink(fs, dur, 1.7, 20.0, 2), rhythm(fs, dur, 1.5, 55.0, 12))
    o1 = add(pink(fs, dur, 1.2, 10.0, 3), rhythm(fs, dur, 10.5, 26.0, 13))
    n = len(fp1)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("time_s,Fp1,Cz,O1\n")
        for k in range(n):
            fh.write(f"{k / fs:.6f},{fp1[k]:.4f},{cz[k]:.4f},{o1[k]:.4f}\n")
    print(f"wrote {path} ({n} rows, fs={fs:g} Hz)")


def write_dose_session_csv(path: str, fs: float = 200.0, dur: float = 240.0,
                           dose_at: float = 120.0) -> None:
    """A 'dosing session': 2 min of baseline, then slow-wave power steps up ~4x.

    Also carries a 60 Hz mains component so the line-noise diagnostics (and
    ``--notch``) have something to find. The background here uses a fixed 0.25 Hz
    component spacing rather than 1/dur: at 4 minutes the latter would need 20k+
    sinusoids per sample and take minutes to generate. Everything is synthetic.
    """
    rng = random.Random(51)
    n = int(round(fs * dur))
    df = 0.25
    comps = [(k * df, (k * df) ** (-1.4 / 2.0), rng.uniform(0, 2 * math.pi))
             for k in range(1, int(90.0 / df) + 1)]
    norm = math.sqrt(math.fsum(a * a for _, a, _ in comps) / 2.0)
    slow = rhythm(fs, dur, 1.5, 40.0, 52)
    alpha = rhythm(fs, dur, 10.0, 14.0, 53)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("time_s,eeg_uv\n")
        for k in range(n):
            t = k / fs
            bg = 10.0 * math.fsum(a * math.cos(2 * math.pi * f * t + ph)
                                  for f, a, ph in comps) / norm
            # Amplitude doubles after the dose -> power (amplitude^2) quadruples.
            gain = 1.0 if t < dose_at else 2.0
            v = (bg + gain * slow[k] + alpha[k]
                 + 9.0 * math.sin(2 * math.pi * 60.0 * t + 0.4)
                 + rng.gauss(0.0, 4.0))   # measurement noise
            fh.write(f"{t:.6f},{v:.4f}\n")
    print(f"wrote {path} ({n} rows, fs={fs:g} Hz, dose at {dose_at:g} s)")


def write_sleep_edf(path: str, fs: float = 100.0, dur: float = 60.0) -> None:
    """Two-channel EDF: a delta-dominant and an alpha-dominant derivation."""
    fpz = add(pink(fs, dur, 1.8, 22.0, 21), rhythm(fs, dur, 1.2, 60.0, 31),
              rhythm(fs, dur, 12.5, 8.0, 41))     # slow waves + a spindle
    pz = add(pink(fs, dur, 1.2, 12.0, 22), rhythm(fs, dur, 9.5, 20.0, 32))
    write_edf(path, [("EEG Fpz-Cz", "uV", fpz), ("EEG Pz-Oz", "uV", pz)], fs,
              record_duration=1.0, phys_range=(-250.0, 250.0),
              patient="X X X X", recording="Startdate X synthetic")
    print(f"wrote {path} ({dur:g} s, {fs:g} Hz, 2 channels)")


if __name__ == "__main__":
    write_multichannel_csv(os.path.join(HERE, "multichannel_wide.csv"))
    write_dose_session_csv(os.path.join(HERE, "dose_session.csv"))
    write_sleep_edf(os.path.join(HERE, "sleep_2ch.edf"))
