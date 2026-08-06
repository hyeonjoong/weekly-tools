#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  medpath — 매개효과(간접효과) 분석기"
echo "=================================================================="
echo "  X(원인) → M(매개) → Y(결과) 열이 있는 CSV → 간접효과 ab 의"
echo "  부트스트랩 신뢰구간 · 경로별 회귀표 · 진단 · 논문용 문장(한/영)."
echo ""
echo "  내 데이터로 실행:"
echo "    python3 -m medpath 내파일.csv --list-columns"
echo "    python3 -m medpath 내파일.csv --x 군 --m 매개변수 --y 결과"
echo "    python3 -m medpath 내파일.csv --x 군 --m 매개1,매개2 --y 결과 --covariates 나이"
echo "    python3 -m medpath 내파일.csv --x 군 --m 매개1,매개2 --y 결과 --serial"
echo "    python3 -m medpath 내파일.csv --x 군 --m ... --y ... --ci bca --bootstrap 10000"
echo ""
echo "  자세한 안내: 사용법.md / README.md"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v medpath >/dev/null 2>&1; then
    medpath "$@"
  else
    python3 -m medpath "$@"
  fi
}

echo "### 예제 1) 단순매개 — 중재(arm) → 심박변이도(rmssd_ms) → 서파수면(sws_min), 나이 보정"
echo "    결측·비숫자 값을 그대로 읽고 몇 행을 왜 뺐는지 먼저 보고합니다."
echo "\$ medpath examples/sleep_breathing_hrv.csv --x arm --m rmssd_ms --y sws_min --covariates age"
echo ""
run examples/sleep_breathing_hrv.csv --x arm --m rmssd_ms --y sws_min --covariates age

echo ""
echo "### 예제 2) 병렬 다중매개 — 매개변수 두 개를 동시에 (+ 경로 간 대비)"
echo "    두 경로 중 어느 쪽이 더 큰지까지 부트스트랩으로 비교합니다."
echo "\$ medpath examples/sleep_breathing_hrv.csv --x arm --m rmssd_ms,resp_rate_bpm --y sws_min --brief"
echo ""
run examples/sleep_breathing_hrv.csv --x arm --m rmssd_ms,resp_rate_bpm --y sws_min --brief

echo ""
echo "### 예제 3) 직렬(연쇄) 매개 — 중재 → HRV → 서파수면 → 불면증지수 개선"
echo "    --m 에 적은 순서 그대로 사슬이 만들어집니다(순서에 근거가 있어야 합니다)."
echo "\$ medpath examples/sleep_breathing_hrv.csv --x arm --m rmssd_ms,sws_min --y isi_change --serial --brief"
echo ""
run examples/sleep_breathing_hrv.csv --x arm --m rmssd_ms,sws_min --y isi_change --serial --brief

echo ""
echo "### 예제 4) 연속형 X + 공변량 보정 — 주당 훈련횟수 → 순응도/자기효능감 → 말소리점수 변화"
echo "\$ medpath examples/wowfit_training.csv --x weekly_sessions \\"
echo "      --m adherence_pct,self_efficacy --y speech_score_change \\"
echo "      --covariates age,hearing_loss_db --brief"
echo ""
run examples/wowfit_training.csv --x weekly_sessions \
    --m adherence_pct,self_efficacy --y speech_score_change \
    --covariates age,hearing_loss_db --brief

echo ""
echo "### 예제 5) 논문 보고용 — BCa 구간 + 마크다운 표 (그대로 붙여넣기)"
echo "\$ medpath examples/sleep_breathing_hrv.csv --x arm --m rmssd_ms --y sws_min \\"
echo "      --covariates age --ci bca --bootstrap 5000 --markdown --brief"
echo ""
run examples/sleep_breathing_hrv.csv --x arm --m rmssd_ms --y sws_min \
    --covariates age --ci bca --bootstrap 5000 --markdown --brief

echo ""
echo "### 예제 6) 열 이름이 기억나지 않을 때"
echo "\$ medpath examples/wowfit_training.csv --list-columns"
echo ""
run examples/wowfit_training.csv --list-columns

echo ""
echo "=================================================================="
echo "  ※ 매개분석은 '상관'을 인과 경로처럼 배열한 회귀 모형입니다."
echo "     X→M→Y의 시간 순서가 실제로 보장되고 미측정 교란이 없어야"
echo "     인과로 읽을 수 있습니다. '완전매개/부분매개' 표현은 피하세요."
echo "     예제 CSV는 모두 합성 데이터이며 실제 환자 정보가 아닙니다."
echo "=================================================================="
echo ""
read -p "엔터를 누르면 창이 닫힙니다..."
