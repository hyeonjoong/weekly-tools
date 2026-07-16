"""factorscan — 설문 척도의 요인구조·타당도 진단 (EFA).

surveyscan(신뢰도)와 짝을 이루는 도구로, "이 척도가 몇 개의 요인으로 이루어져 있고
요인분석에 적합한가"를 검증한다. 표준 라이브러리 + numpy만 사용(scipy 불필요).

주요 기능:
- 적합성: KMO(전체·문항별 MSA), Bartlett 구형성 검정
- 요인 수: 고유값·Kaiser, Horn 평행분석, Velicer MAP, ML 적합도 스캔(k=1..최대)
- 추출: 주성분(PCA) · 주축분해(PAF) · 최대우도(ML, χ²/RMSEA/CFI/TLI 적합도지수)
- 상관: 피어슨 · 폴리코릭(순서형 리커트 잠재상관)
- 회전: Varimax(직교) · Promax(사교, 요인상관 Φ)
- 문항 진단: 공통성·문항-총점 상관·교차적재 플래그, 요인별 ω/α, RMSR
- 결측 진단: 문항별 결측률, listwise 손실 규모·유발 문항, MCAR 위배 신호(Cohen's d)
- 입출력: CSV · TSV · 엑셀(.xlsx) 입력, 적재표/요인점수 CSV·JSON 출력
"""

__version__ = "0.2.0"
