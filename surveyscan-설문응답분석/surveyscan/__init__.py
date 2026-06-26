"""surveyscan — 설문 응답 CSV 분석 (문항 기술통계 · Cronbach α · 하위척도 · 역문항 · 결측).

표준 라이브러리만으로 동작하는 설문 신뢰도/기술통계 분석 도구.
"""
from .analyze import analyze
from .config import SurveyConfig, auto_config, load_config
from .dataio import SurveyData, load_csv
from .report import render

__version__ = "0.1.0"

__all__ = [
    "analyze",
    "render",
    "load_csv",
    "load_config",
    "auto_config",
    "SurveyConfig",
    "SurveyData",
    "__version__",
]
