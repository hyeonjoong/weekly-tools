"""powerplan — 임상연구 표본수·검정력 설계 도구 (외부 의존성 0).

주요 진입점:

- :func:`powerplan.solve.make_plan` — 설계 + 목표 → 프로토콜용 계획
- :mod:`powerplan.designs`          — 설계별 검정력 (비중심 t/F 정확계산)
- :mod:`powerplan.precision`        — ICC·Bland–Altman·kappa 정밀도 기준 표본수
- :mod:`powerplan.sequential`       — 중간분석(군차별설계) 경계·표본수 팽창계수
- :mod:`powerplan.pilot`            — 사전연구 CSV → 효과크기 → 표본수
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
