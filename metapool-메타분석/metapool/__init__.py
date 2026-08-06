"""metapool — 메타분석(효과크기 합성) 도구.

CSV 한 장으로 고정효과·변량효과 합성(DL·PM·REML·SJ), 이질성(Q·I²·τ²와 그 신뢰구간·예측구간),
하위군 분석, leave-one-out 민감도와 영향력 진단, 출판편향(Egger·Begg·trim-and-fill·깔때기그림),
OR/RR을 NNT·절대위험차로 옮기는 임상 해석, 텍스트 숲그림, 결과 CSV 내보내기,
그리고 논문에 바로 붙일 한국어·영어 결과 문장까지 만든다. 외부 의존성 없음.

지원 지표: SMD(Hedges g) · MD · OR · RR · RD · 상관계수(Fisher z) · 단일군 비율(logit) · generic.
"""

from __future__ import annotations

__version__ = "1.1.0"

__all__ = ["__version__"]
