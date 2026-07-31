#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  table1 — 기저 특성표(Table 1) 생성기"
echo "=================================================================="
echo "  임상 CSV와 '군(group) 열' 하나만 주면, 출판용 '표 1'을 자동 생성:"
echo "   · 변수별 연속형/범주형 자동 판별 (평균±SD 또는 중앙값[IQR], n(%))"
echo "   · 정규성·등분산 점검 후 알맞은 검정 자동 선택 (t/Welch/MWU/ANOVA/KW,"
echo "     범주형은 카이제곱/Fisher)"
echo "   · 두 군 표준화 평균차(SMD)·차이(95% CI)·다중비교 보정 + 결측 정리,"
echo "     Markdown/CSV/TSV/JSON/HTML/LaTeX 출력 (군 열 없이 전체 코호트 요약도 가능)"
echo "   · 엑셀(.xlsx) 입력, IPTW/성향점수 가중표(--weights: 가중 SMD·ESS)"
echo "   · 용량·사분위 같은 순서형 군의 경향성 p값(--trend: p for trend)"
echo ""
echo "  내 데이터로 실행:"
echo "    python3 -m table1.cli 내파일.csv --group 군열이름"
echo "    python3 -m table1.cli 내파일.csv --group arm --format csv -o 표1.csv"
echo "    python3 -m table1.cli 내파일.xlsx --group arm            # 엑셀 그대로"
echo "    python3 -m table1.cli 내파일.csv --group arm -w iptw     # IPTW 가중표"
echo "    python3 -m table1.cli 내파일.csv -g dose --group-order placebo,low,high --trend"
echo "=================================================================="
echo ""

run() {
  if command -v table1 >/dev/null 2>&1; then
    table1 "$@"
  else
    python3 -m table1.cli "$@"
  fi
}

echo "### 예제) SERENE(합성) 기저 특성 — device vs sham, 변수 자동 판별"
echo "\$ table1 examples/serene_baseline.csv --group arm"
echo ""
run examples/serene_baseline.csv --group arm

echo ""
echo "### 예제) 성향점수(IPTW) 가중표 — 가중 SMD로 균형 확인"
echo "\$ table1 examples/psm_weighted.csv --group cohort --weights iptw --vars age,sex,bmi,copd"
echo ""
run examples/psm_weighted.csv --group cohort --weights iptw --vars age,sex,bmi,copd

echo ""
echo "### 예제) 용량군(placebo<low<high) 경향성 p값 — 순서를 이용해 검정력 향상"
echo "\$ table1 examples/dose_trend.csv --group dose --group-order placebo,low,high --trend --trend-scores 0,10,40 --nonnormal crp"
echo ""
run examples/dose_trend.csv --group dose --group-order placebo,low,high \
    --trend --trend-scores 0,10,40 --nonnormal crp \
    --vars age,sex,bmi,sbp,crp,ae_serious

echo ""
echo "### 같은 표를 CSV로 저장하려면:"
echo "\$ table1 examples/serene_baseline.csv --group arm --format csv -o 표1.csv"
echo ""
read -p "엔터를 누르면 창이 닫힙니다..." _ || true
