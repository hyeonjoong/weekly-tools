"""eegband — 단일채널 EEG 대역파워 분석기 (single-channel EEG band-power analyzer).

Standard-library-only Welch PSD, band power (delta/theta/alpha/beta/gamma),
slow-wave activity (SWA), spectral edge frequency, peak frequency and band ratios.

Public API:
    load_signal(path, value_col=None, time_col=None) -> SignalData
    analyze(values, fs=128.0, ...) -> AnalysisResult
    render_text(result) -> str
    to_dict(result) -> dict
"""

# Defined before the submodule imports below so report.py can import it without a
# circular-import failure.
__version__ = "0.1.0"

from .analyze import (
    AnalysisResult,
    BandPower,
    EpochResult,
    Spectrum,
    analyze,
    resolve_fs,
)
from .dataio import SignalData, infer_fs, load_signal
from .report import render_csv, render_text, to_dict

__all__ = [
    "analyze",
    "resolve_fs",
    "load_signal",
    "infer_fs",
    "render_text",
    "render_csv",
    "to_dict",
    "AnalysisResult",
    "Spectrum",
    "BandPower",
    "EpochResult",
    "SignalData",
]
