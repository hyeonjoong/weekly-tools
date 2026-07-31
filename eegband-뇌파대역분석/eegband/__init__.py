"""eegband — 단일/다채널 EEG 대역파워 분석기 (EEG band-power analyzer).

Standard-library-only Welch PSD, band power (delta/theta/alpha/beta/gamma),
slow-wave activity (SWA), spectral edge frequency, peak frequency, band ratios,
aperiodic (1/f) parameterization with background-corrected band power, and epoch-wise
summaries with autocorrelation-adjusted CIs plus Mann–Kendall/Theil–Sen trends.
Inputs: CSV/TSV (delimiter sniffed, decimal comma tolerated) and EDF/EDF+/BDF.

Public API:
    load_signal(path, value_col=None, time_col=None) -> SignalData
    load_signals(path, value_cols=None, time_col=None) -> [SignalData]   # channels
    read_edf_info(path) / read_edf_channel(path, label) -> EDF/BDF input
    analyze(values, fs=128.0, ...) -> AnalysisResult
    render_text(result) / to_dict(result) / render_csv(result)
    render_comparison([result, ...]) / render_csv_batch([result, ...])
"""

# Defined before the submodule imports below so report.py can import it without a
# circular-import failure.
__version__ = "0.3.0"

from .analyze import (
    AnalysisResult,
    BandPower,
    EpochResult,
    SignalQuality,
    Spectrum,
    analyze,
    resolve_fs,
    signal_quality,
)
from .aperiodic import AperiodicFit, fit_aperiodic
from .dataio import SignalData, infer_fs, list_columns, load_signal, load_signals
from .edf import EdfInfo, EdfSignal, read_edf_channel, read_edf_info
from .report import (
    render_comparison,
    render_csv,
    render_csv_batch,
    render_text,
    to_dict,
)
from .stats import TrendResult, mann_kendall, summary_stats, theil_sen_slope, trend

__all__ = [
    "analyze",
    "resolve_fs",
    "signal_quality",
    "load_signal",
    "load_signals",
    "list_columns",
    "infer_fs",
    "read_edf_info",
    "read_edf_channel",
    "fit_aperiodic",
    "summary_stats",
    "mann_kendall",
    "theil_sen_slope",
    "trend",
    "render_text",
    "render_csv",
    "render_csv_batch",
    "render_comparison",
    "to_dict",
    "AnalysisResult",
    "Spectrum",
    "BandPower",
    "EpochResult",
    "SignalQuality",
    "SignalData",
    "AperiodicFit",
    "EdfInfo",
    "EdfSignal",
    "TrendResult",
]
