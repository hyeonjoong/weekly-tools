#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  rocdx — 진단정확도(ROC) 분석기"
echo "=================================================================="
echo "  검사값 한 열 + 기준 진단 한 열이 있는 CSV → ROC 곡선·AUC(DeLong CI)·"
echo "  절단점 선택·민감도/특이도/PPV/NPV/우도비(신뢰구간 포함)·논문용 문장."
echo ""
echo "  내 데이터로 실행:"
echo "    python3 -m rocdx.cli 내파일.csv --list-columns"
echo "    python3 -m rocdx.cli 내파일.csv --score 검사값열 --truth 진단열"
echo "    python3 -m rocdx.cli 내파일.csv --score 점수 --truth 판정 --positive-label 재발"
echo "    python3 -m rocdx.cli 내파일.csv --score mmse --truth dementia --direction lower"
echo "    python3 -m rocdx.cli 내파일.csv --score crp --truth sepsis --min-spec 0.95"
echo "    python3 -m rocdx.cli 내파일.csv --score crp --truth sepsis --bootstrap 2000"
echo ""
echo "  자세한 안내: 사용법.md / README.md"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v rocdx >/dev/null 2>&1; then
    rocdx "$@"
  else
    python3 -m rocdx.cli "$@"
  fi
}

echo "### 예제 1) 패혈증 바이오마커 CRP — 기본 분석 (AUC + Youden 절단점 + ROC 곡선)"
echo "    지저분한 입력(결측·N/A·<0.05·천단위 쉼표)을 그대로 읽고 무엇을 버렸는지 보고합니다."
echo "\$ rocdx examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis"
echo ""
run examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis

echo ""
echo "### 예제 2) 확진 목적 — 특이도 95% 이상에서 가장 민감한 절단점 + 사전 지정 절단점"
echo "\$ rocdx examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis \\"
echo "      --min-spec 0.95 --cutoff 10 --cutoff 50 --no-curve"
echo ""
run examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis \
    --min-spec 0.95 --cutoff 10 --cutoff 50 --no-curve

echo ""
echo "### 예제 3) 선별검사 상황 — 대상 인구집단 유병률 2% 기준으로 PPV/NPV 재계산"
echo "    표본 유병률(약 35%)에서의 PPV와 얼마나 다른지 보세요."
echo "\$ rocdx examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis \\"
echo "      --prevalence 0.02 --no-curve"
echo ""
run examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis \
    --prevalence 0.02 --no-curve

echo ""
echo "### 예제 4) 두 검사 비교 (DeLong 짝지은 검정) + 절단점 선택의 낙관(optimism) 추정"
echo "\$ rocdx examples/sepsis_biomarker.csv --score procalcitonin_ng_mL --truth sepsis \\"
echo "      --compare crp_mg_L --bootstrap 1000 --no-curve"
echo ""
run examples/sepsis_biomarker.csv --score procalcitonin_ng_mL --truth sepsis \
    --compare crp_mg_L --bootstrap 1000 --no-curve

echo ""
echo "### 예제 5) 값이 낮을수록 질환인 지표 — 인지검사 점수 (CP949 + 세미콜론 + 한글 열이름)"
echo "\$ rocdx examples/cognitive_screen_kr.csv --score 인지검사점수 --truth 치매진단 \\"
echo "      --direction lower --min-sens 0.90"
echo ""
run examples/cognitive_screen_kr.csv --score 인지검사점수 --truth 치매진단 \
    --direction lower --min-sens 0.90

echo ""
echo "### 예제 6) 마크다운 표로 출력 (논문·발표 자료에 붙여넣기)"
echo "\$ rocdx examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis --markdown"
echo ""
run examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis --markdown

echo ""
echo "### 예제 7) 실제로 쓰는 구간만 평가 — 특이도 90~100% 부분 AUC (pAUC)"
echo "    선별검사는 특이도가 낮은 구간에서 쓰지 않으므로, 그 구간까지 평균한"
echo "    전체 AUC는 두 검사를 잘못 줄 세울 수 있습니다."
echo "\$ rocdx examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis \\"
echo "      --pauc-min-spec 0.90 --bootstrap 1000 --no-curve"
echo ""
run examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis \
    --pauc-min-spec 0.90 --bootstrap 1000 --no-curve

echo ""
echo "### 예제 8) 검사 여러 개 비교 + 비열등성 (Holm 보정 p와 사전 한계 0.05)"
echo "\$ rocdx examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis \\"
echo "      --compare procalcitonin_ng_mL --compare wbc_10e3_uL --ni-margin 0.05 --no-curve"
echo ""
run examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis \
    --compare procalcitonin_ng_mL --compare wbc_10e3_uL --ni-margin 0.05 --no-curve

echo ""
echo "### 예제 9) 한 환자가 여러 병변을 내는 군집 자료 — 군집 보정 신뢰구간"
echo "    92병변 / 42명. DeLong 구간과 군집 보정 구간을 나란히 비교해 보세요."
echo "\$ rocdx examples/lesion_multi_reader.csv --score 초음파점수 --truth 조직검사 \\"
echo "      --positive-label 악성 --negative-label 양성 \\"
echo "      --cluster-col 환자ID --cluster --bootstrap 1000 --no-curve"
echo ""
run examples/lesion_multi_reader.csv --score 초음파점수 --truth 조직검사 \
    --positive-label 악성 --negative-label 양성 \
    --cluster-col 환자ID --cluster --bootstrap 1000 --no-curve

echo ""
echo "### 예제 10) 그림·기계판독 산출물 — SVG 곡선과 JSON"
echo "    데모 파일은 저장소를 어지럽히지 않도록 임시 폴더에 씁니다."
echo "\$ rocdx examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis \\"
echo "      --compare procalcitonin_ng_mL --pauc-min-spec 0.90 --bootstrap 500 \\"
echo "      --plot-svg roc.svg --json roc.json --no-curve"
echo ""
DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/rocdx_demo.XXXXXX")"
run examples/sepsis_biomarker.csv --score crp_mg_L --truth sepsis \
    --compare procalcitonin_ng_mL --pauc-min-spec 0.90 --bootstrap 500 \
    --plot-svg "$DEMO_DIR/roc.svg" --json "$DEMO_DIR/roc.json" --no-curve | tail -6
echo "    → $DEMO_DIR/roc.svg 를 브라우저나 Word에서 열어 보세요 (벡터 그림, 편집 가능)."

echo ""
echo "=================================================================="
echo "  ※ 데이터에서 고른 절단점의 성능은 낙관적으로 부풀려집니다."
echo "     보고할 때 그 사실을 밝히고, 가능하면 독립 검증 표본에서 확인하세요."
echo "     예제 CSV는 모두 합성 데이터이며 실제 환자 정보가 아닙니다."
echo "=================================================================="
echo ""
read -p "엔터를 누르면 창이 닫힙니다..." || true
