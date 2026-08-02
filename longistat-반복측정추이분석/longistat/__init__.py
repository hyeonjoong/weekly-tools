"""longistat — 반복측정(전-후·다시점) 추이 분석기 / longitudinal outcome analyser.

임상시험처럼 **같은 사람을 여러 번 측정한 자료**를 한 번에 처리합니다.

    from longistat import load_long, analyze, render_text

    panel = load_long("isi.csv", id_col="대상", time_col="방문",
                      value_col="ISI", group_col="군")
    print(render_text(analyze(panel)))

포함 내용: 시점·그룹별 기술통계, 결측/탈락 프로파일, Shapiro–Wilk 정규성,
Mauchly 구형성 + Greenhouse–Geisser/Huynh–Feldt 보정, 반복측정 및 혼합
(split-plot) ANOVA, MMRM(REML·비구조화 공분산 — 탈락자를 버리지 않는 MAR
기반 분석), Friedman + Kendall's W, 시점 간·군간 사후비교,
기저 대비 변화량과 군간 변화량 차이, MCID 반응자 분석(RD/RR/OR/NNT),
Jacobson–Truax 신뢰변화지수(RCI), 그리고 논문에 바로 넣을 수 있는 문장.

외부 의존성 없음 (Python 3.9+ 표준 라이브러리만).
"""

from __future__ import annotations

__version__ = "1.0.0"

from .analyze import Analysis, Options, analyze
from .anova import RMAnovaResult, rm_anova
from .dataio import (DataError, Panel, load_long, load_wide,
                     sheet_names)
from .describe import describe, profile_missing
from .mmrm import MMRMContrast, MMRMLsMean, MMRMResult, mmrm_analysis
from .nonparam import friedman
from .report import render_csv, render_json, render_text
from .responder import rci_analysis, responder_analysis
from .sensitivity import (SensitivityResult, impute_panel,
                          sensitivity_analysis)
from .tipping import (TippingResult, TippingRow, mar_impute,
                      tipping_analysis)
from .trend import (SlopeRow, TrendResult, orthogonal_polynomials,
                    trend_analysis)
from .xlsx import XlsxError, is_xlsx, read_xlsx

__all__ = [
    "__version__",
    "Analysis", "Options", "analyze",
    "Panel", "DataError", "load_long", "load_wide",
    "sheet_names", "is_xlsx", "read_xlsx", "XlsxError",
    "RMAnovaResult", "rm_anova",
    "describe", "profile_missing", "friedman",
    "MMRMResult", "MMRMLsMean", "MMRMContrast", "mmrm_analysis",
    "responder_analysis", "rci_analysis",
    "TrendResult", "SlopeRow", "trend_analysis", "orthogonal_polynomials",
    "SensitivityResult", "sensitivity_analysis", "impute_panel",
    "TippingResult", "TippingRow", "tipping_analysis", "mar_impute",
    "render_text", "render_json", "render_csv",
]
