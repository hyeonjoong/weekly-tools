#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  statwise — 그룹 비교 통계 자동 선택기"
echo "=================================================================="
echo "  두 집단(또는 여러 집단)의 CSV를 넣으면 정규성·등분산을 점검한 뒤"
echo "  알맞은 검정(t / Welch / Mann-Whitney / ANOVA / Kruskal-Wallis)을"
echo "  자동으로 골라 실행하고, 효과크기와 논문용 문장까지 출력합니다."
echo "  이진(예/아니오) 결과, 등가성·비열등성(TOST), 여러 엔드포인트 동시"
echo "  분석, 기저값 보정(ANCOVA)까지 한 도구에서 처리합니다."
echo ""
echo "  내 데이터로 실행:"
echo "    python3 -m statwise.cli 내파일.csv --value 값열 --group 그룹열"
echo "    python3 -m statwise.cli 내파일.csv --wide      (각 열이 그룹)"
echo "    python3 -m statwise.cli 내파일.csv --binary --value 반응 --group 군"
echo "    python3 -m statwise.cli 내파일.csv --value 사후 --group 군 --covariate 기저"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백.
# 아래 예제는 `$ statwise ...` 로 보여 주지만, 아직 설치하지 않았다면 앞부분을
# `python3 -m statwise.cli` 로 바꿔서 그대로 쓰면 됩니다.
if ! command -v statwise >/dev/null 2>&1; then
  echo "  (statwise 가 아직 설치돼 있지 않아 'python3 -m statwise.cli' 로 실행합니다.)"
  echo "  (설치하려면: python3 -m pip install -e .)"
  echo ""
fi
run() {
  if command -v statwise >/dev/null 2>&1; then
    statwise "$@"
  else
    python3 -m statwise.cli "$@"
  fi
}

echo "### 예제 1) 수면 디바이스 HRV — 2그룹 (sham vs device), long 형식"
echo "\$ statwise examples/hrv_two_arm.csv --value rmssd_ms --group arm"
echo ""
run examples/hrv_two_arm.csv --value rmssd_ms --group arm

echo ""
echo "### 예제 2) 용량별 ISI 변화 — 3그룹 (low/mid/high), wide 형식 + 사후검정"
echo "\$ statwise examples/isi_change_by_dose.csv --wide"
echo ""
run examples/isi_change_by_dose.csv --wide

echo ""
echo "### 예제 3) 불면증(ISI) 치료 전/후 — 대응 표본 (같은 대상 pre vs post)"
echo "\$ statwise examples/isi_pre_post_paired.csv --paired --value isi --group time --id subject"
echo ""
run examples/isi_pre_post_paired.csv --paired --value isi --group time --id subject

echo ""
echo "### 예제 4) 반응자 비율 — 이진 결과 (RD / RR / OR / NNT + 카이제곱·Fisher)"
echo "\$ statwise examples/responder_two_arm.csv --binary --value responder --group arm --reference sham"
echo ""
run examples/responder_two_arm.csv --binary --value responder --group arm --reference sham

echo ""
echo "### 예제 5) 등가성 검정(TOST) — '차이가 없다'가 아니라 '임상적으로 같다'"
echo "\$ statwise examples/hrv_two_arm.csv --value rmssd_ms --group arm --equivalence-margin 20"
echo ""
run examples/hrv_two_arm.csv --value rmssd_ms --group arm --equivalence-margin 20

echo ""
echo "### 예제 6) 엔드포인트 4개 동시 분석 + 엔드포인트 간 다중비교 보정"
echo "\$ statwise examples/multi_endpoint_two_arm.csv --values isi_change,psqi_change,rmssd_ms,ess_change --group arm --reference sham --brief"
echo ""
run examples/multi_endpoint_two_arm.csv --values isi_change,psqi_change,rmssd_ms,ess_change --group arm --reference sham --brief

echo ""
echo "### 예제 7) 기저값 보정 3군 비교(ANCOVA) — RCT의 표준 1차 분석"
echo "\$ statwise examples/isi_ancova_baseline.csv --value isi_week8 --group arm --covariate isi_base --adjust-factor site --reference placebo"
echo ""
run examples/isi_ancova_baseline.csv --value isi_week8 --group arm --covariate isi_base --adjust-factor site --reference placebo

echo ""
read -p "엔터를 누르면 창이 닫힙니다..." || true
