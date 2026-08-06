#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  sleepdiary — 수면일기 지표 계산기"
echo "=================================================================="
echo "  한 행 = 한 밤인 CSV → TST·수면효율·SOL·WASO·수면중앙시각을"
echo "  대상자별→집단 2단계로 집계하고, 시기 간 변화를 대응표본으로 검정합니다."
echo ""
echo "  내 데이터로 실행:"
echo "    python3 -m sleepdiary.cli 내일기.csv --list-columns"
echo "    python3 -m sleepdiary.cli 내일기.csv"
echo "    python3 -m sleepdiary.cli 내일기.csv --compare-periods baseline followup"
echo "    python3 -m sleepdiary.cli 내일기.csv --min-nights 5 --markdown"
echo ""
echo "  자세한 안내: 사용법.md / README.md"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v sleepdiary >/dev/null 2>&1; then
    sleepdiary "$@"
  else
    python3 -m sleepdiary.cli "$@"
  fi
}

echo "### 예제 1) 내 파일의 열이 어떻게 인식되는지 먼저 확인"
echo "    'sleep_latency_min' 처럼 단위가 붙은 열 이름도 알아봅니다."
echo "\$ sleepdiary examples/sleep_diary_trial.csv --list-columns"
echo ""
run examples/sleep_diary_trial.csv --list-columns

echo ""
echo "### 예제 2) 기본 분석 — 12명 × 7박 × 2시기 불면증 시험(합성 데이터)"
echo "    맨 위 '자료 품질'을 보세요. 계산 불가한 밤을 몇 행에서 왜 뺐는지 밝힙니다."
echo "\$ sleepdiary examples/sleep_diary_trial.csv"
echo ""
run examples/sleep_diary_trial.csv | head -60

echo ""
echo "### 예제 3) 전후 비교 — CBT-I 전/후 대응표본 검정"
echo "    통계의 n이 밤 수(160)가 아니라 사람 수(12)인지 확인하세요."
echo "\$ sleepdiary examples/sleep_diary_trial.csv \\"
echo "      --compare-periods baseline followup"
echo ""
run examples/sleep_diary_trial.csv --compare-periods baseline followup | sed -n '55,135p'

echo ""
echo "### 예제 4) 논문용 문장 초안"
echo "\$ sleepdiary examples/sleep_diary_trial.csv --compare-periods baseline followup"
echo ""
run examples/sleep_diary_trial.csv --compare-periods baseline followup | tail -22

echo ""
echo "### 예제 5) 마크다운 표 — 논문·슬라이드에 붙여넣기"
echo "\$ sleepdiary examples/sleep_diary_trial.csv --markdown \\"
echo "      --compare-periods baseline followup"
echo ""
run examples/sleep_diary_trial.csv --markdown --compare-periods baseline followup

echo ""
echo "### 예제 6) 순응도 기준 적용 — 유효 일기 7박 미만인 대상자 제외"
echo "    누구를 왜 뺐는지 이름과 밤 수까지 찍습니다 (논문에 적어야 하는 정보)."
echo "\$ sleepdiary examples/sleep_diary_trial.csv --min-nights 7 | tail -3"
echo ""
run examples/sleep_diary_trial.csv --min-nights 7 | tail -3

echo ""
echo "### 예제 7) 한글 열이름 + CP949 + 세미콜론 구분자 파일도 그대로 읽습니다"
echo "\$ sleepdiary examples/수면일기_한글_cp949.csv"
echo ""
run examples/수면일기_한글_cp949.csv | sed -n '20,50p'

echo ""
echo "### 예제 8) 산출물 저장 — 밤별/대상자별 CSV와 JSON"
echo "    데모 파일은 저장소를 어지럽히지 않도록 임시 폴더에 씁니다."
echo "\$ sleepdiary examples/sleep_diary_trial.csv --compare-periods baseline followup \\"
echo "      --quiet --per-night-csv 밤별.csv --per-subject-csv 대상자별.csv --json 결과.json"
echo ""
DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sleepdiary_demo.XXXXXX")"
run examples/sleep_diary_trial.csv --compare-periods baseline followup --quiet \
    --per-night-csv "$DEMO_DIR/밤별.csv" \
    --per-subject-csv "$DEMO_DIR/대상자별.csv" \
    --json "$DEMO_DIR/결과.json"
echo "    저장됨:"
ls -1 "$DEMO_DIR" | sed 's/^/      /'
echo ""
echo "    밤별.csv 에서 제외된 밤과 사유 (원자료 검토용):"
grep -m3 "False" "$DEMO_DIR/밤별.csv" | cut -c1-120 | sed 's/^/      /'

echo ""
echo "=================================================================="
echo "  ※ 수면일기는 자기보고 자료입니다. 수면다원검사(PSG)나 액티그래피와"
echo "     체계적으로 다를 수 있으며, 이 도구는 그 차이를 보정하지 않습니다."
echo "     결측 SOL/WASO는 0으로 처리되어 수면효율을 낙관적으로 만듭니다"
echo "     (몇 박을 채웠는지는 보고서 '자료 품질'에 적힙니다). 낮잠은 포함되지"
echo "     않으며, 시기 비교는 두 시기를 모두 기록한 대상자만 쓰는 완전자료 분석입니다."
echo "     예제 CSV는 모두 합성 데이터이며 실제 환자 정보가 아닙니다."
echo "=================================================================="
echo ""
read -p "엔터를 누르면 창이 닫힙니다..." || true
