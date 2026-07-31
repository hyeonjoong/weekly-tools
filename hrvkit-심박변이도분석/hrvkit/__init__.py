"""hrvkit — 심박변이도(HRV) 지표 계산기.

스마트워치/PPG/ECG에서 얻은 RR(박동간격, IBI) 시계열로부터
시간영역·주파수영역·비선형(Poincaré/SampEn) HRV 지표를 계산합니다.
표준 라이브러리만 사용합니다 (numpy/scipy 불필요).

BELL-001 수면 디바이스의 작용기전(느린 호흡 → 부교감신경 활성화 →
RSA/HRV ↑ → 서파수면)을 정량화하기 위한 도구로 설계되었습니다.
"""

from .artifacts import clean_rr, detect_artifacts
from .timedomain import time_domain, geometric_indices
from .nonlinear import poincare, sample_entropy, dfa, dfa_alpha
from .frequency import frequency_domain
from .analyze import HRVResult, analyze_rr, flat_metrics
from .stats import (wilcoxon_signed_rank, paired_summary, hodges_lehmann,
                    wilcoxon_ci, holm_adjust, benjamini_hochberg,
                    walsh_averages, mann_whitney_u, mann_whitney_ci,
                    hodges_lehmann_2sample, unpaired_summary, mann_kendall)
from .window import (Window, WindowSeries, analyze_windows, window_trends,
                     long_term_indices)
from .report import (render_text, render_comparison, render_batch_table,
                     render_paired_group, paired_group, metrics_to_csv,
                     paired_group_to_csv, render_windows, windows_to_csv,
                     group_compare, render_group_compare,
                     group_compare_to_csv)

__all__ = [
    "clean_rr",
    "detect_artifacts",
    "time_domain",
    "geometric_indices",
    "poincare",
    "sample_entropy",
    "dfa",
    "dfa_alpha",
    "frequency_domain",
    "analyze_rr",
    "flat_metrics",
    "HRVResult",
    "wilcoxon_signed_rank",
    "paired_summary",
    "hodges_lehmann",
    "wilcoxon_ci",
    "walsh_averages",
    "holm_adjust",
    "benjamini_hochberg",
    "render_text",
    "render_comparison",
    "render_batch_table",
    "render_paired_group",
    "paired_group",
    "metrics_to_csv",
    "paired_group_to_csv",
    "mann_whitney_u",
    "mann_whitney_ci",
    "hodges_lehmann_2sample",
    "unpaired_summary",
    "mann_kendall",
    "Window",
    "WindowSeries",
    "analyze_windows",
    "window_trends",
    "long_term_indices",
    "render_windows",
    "windows_to_csv",
    "group_compare",
    "render_group_compare",
    "group_compare_to_csv",
]

__version__ = "0.4.0"
