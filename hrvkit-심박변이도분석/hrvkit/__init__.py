"""hrvkit — 심박변이도(HRV) 지표 계산기.

스마트워치/PPG/ECG에서 얻은 RR(박동간격, IBI) 시계열로부터
시간영역·주파수영역·비선형(Poincaré/SampEn) HRV 지표를 계산합니다.
표준 라이브러리만 사용합니다 (numpy/scipy 불필요).

BELL-001 수면 디바이스의 작용기전(느린 호흡 → 부교감신경 활성화 →
RSA/HRV ↑ → 서파수면)을 정량화하기 위한 도구로 설계되었습니다.
"""

from .artifacts import clean_rr, detect_artifacts
from .timedomain import time_domain
from .nonlinear import poincare, sample_entropy
from .frequency import frequency_domain
from .analyze import HRVResult, analyze_rr
from .report import render_text

__all__ = [
    "clean_rr",
    "detect_artifacts",
    "time_domain",
    "poincare",
    "sample_entropy",
    "frequency_domain",
    "analyze_rr",
    "HRVResult",
    "render_text",
]

__version__ = "0.1.0"
