"""sleepdiary — 수면일기(sleep diary) CSV → 표준 수면지표 + 요약통계.

Consensus Sleep Diary(Carney et al., Sleep 2012)의 정의로 TIB/SPT/TST/SE/SOL/
WASO/중앙수면시각을 계산하고, 대상자별·시기별로 요약한다. 외부 의존성 없음.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
