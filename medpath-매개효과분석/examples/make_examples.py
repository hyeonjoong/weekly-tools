"""Regenerate the bundled example CSVs.

The examples are **simulated**, not real BELL data. They are generated from an
explicit causal structure with a fixed seed so that (a) anyone can reproduce
them, and (b) the true indirect effects are known and the tool's output can be
sanity-checked against them.

Run:  python3 examples/make_examples.py
"""

from __future__ import annotations

import csv
import os
from random import Random

HERE = os.path.dirname(os.path.abspath(__file__))


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def make_sleep(path: str, n: int = 120, seed: int = 11) -> None:
    """BELL-001-shaped: acoustic/breathing intervention -> HRV -> slow-wave sleep -> ISI.

    True structure (per-unit coefficients):
        resp_rate  = 14.5 - 1.8*device + 0.02*(age-52) + e
        rmssd_ms   = 20  + 4.0*(15 - resp_rate) - 0.15*(age-52) + e
        sws_min    = 60  + 0.50*(rmssd-28) - 0.25*(age-52) + 2.0*device + e
        isi_change = -1.0 - 0.12*(sws_min-60) - 0.3*device + e
    so arm -> rmssd -> sws has a true indirect effect of about 4.0*1.8*0.50 = 3.6 min,
    and the serial chain arm -> rmssd -> sws -> isi is about 3.6 * -0.12 = -0.43 points.
    Note that rmssd affects ISI *only* through slow-wave sleep, so the direct
    arm -> rmssd -> isi path should come out null — a useful contrast in the demo.
    """
    rng = Random(seed)
    rows = []
    for i in range(n):
        age = int(round(clamp(rng.gauss(52, 9), 30, 78)))
        sex = "여" if rng.random() < 0.55 else "남"
        device = 1 if i % 2 == 0 else 0
        arm = "device" if device else "sham"
        resp = 14.5 - 1.8 * device + 0.02 * (age - 52) + rng.gauss(0, 1.0)
        rmssd = 20 + 4.0 * (15 - resp) - 0.15 * (age - 52) + rng.gauss(0, 6.0)
        rmssd = clamp(rmssd, 5, 120)
        sws = 60 + 0.50 * (rmssd - 28) - 0.25 * (age - 52) + 2.0 * device + rng.gauss(0, 9.0)
        sws = clamp(sws, 5, 160)
        isi = -1.0 - 0.12 * (sws - 60) - 0.3 * device + rng.gauss(0, 1.5)
        rows.append({
            "subject_id": "S%03d" % (i + 1),
            "arm": arm,
            "age": age,
            "sex": sex,
            "resp_rate_bpm": "%.2f" % resp,
            "rmssd_ms": "%.1f" % rmssd,
            "sws_min": "%.1f" % sws,
            "isi_change": "%.1f" % isi,
        })
    # A few realistic holes: a dropped watch night, an unscored EEG epoch.
    for idx, col in ((7, "rmssd_ms"), (23, "sws_min"), (44, "rmssd_ms"),
                     (61, "isi_change"), (95, "age")):
        rows[idx][col] = "" if idx % 2 else "NA"

    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def make_wowfit(path: str, n: int = 150, seed: int = 27) -> None:
    """WowFit-shaped: app usage -> (adherence, self-efficacy) -> speech-score gain.

    True structure:
        adherence_pct  = 30 + 4.5*sessions - 0.20*(hl-45) + e
        self_efficacy  = 40 + 1.8*sessions + e
        speech_change  = 2 + 0.18*(adherence-60) + 0.10*(efficacy-50)
                           + 0.05*sessions - 0.03*(hl-45) + e
    so the adherence path (4.5*0.18 = 0.81) should clearly beat the
    self-efficacy path (1.8*0.10 = 0.18) in the contrast table.
    """
    rng = Random(seed)
    rows = []
    for i in range(n):
        age = int(round(clamp(rng.gauss(64, 10), 40, 88)))
        sex = "여" if rng.random() < 0.52 else "남"
        hl = clamp(rng.gauss(45, 12), 20, 85)
        sessions = int(round(clamp(rng.gauss(6, 3), 0, 14)))
        adherence = clamp(30 + 4.5 * sessions - 0.20 * (hl - 45) + rng.gauss(0, 12), 0, 100)
        efficacy = clamp(40 + 1.8 * sessions + rng.gauss(0, 10), 0, 100)
        speech = (2 + 0.18 * (adherence - 60) + 0.10 * (efficacy - 50)
                  + 0.05 * sessions - 0.03 * (hl - 45) + rng.gauss(0, 4))
        rows.append({
            "patient_id": "W%03d" % (i + 1),
            "weekly_sessions": sessions,
            "adherence_pct": "%.1f" % adherence,
            "self_efficacy": "%.1f" % efficacy,
            "speech_score_change": "%.2f" % speech,
            "age": age,
            "sex": sex,
            "hearing_loss_db": "%.1f" % hl,
        })
    for idx, col in ((12, "self_efficacy"), (33, "adherence_pct"), (88, "hearing_loss_db")):
        rows[idx][col] = "NA"

    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    make_sleep(os.path.join(HERE, "sleep_breathing_hrv.csv"))
    make_wowfit(os.path.join(HERE, "wowfit_training.csv"))
    print("wrote sleep_breathing_hrv.csv, wowfit_training.csv")
