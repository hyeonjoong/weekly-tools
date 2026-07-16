#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  agreestat — 측정 방법 일치도(agreement) 분석기"
echo "=================================================================="
echo "  [연속형] 두 측정 방법(A vs B)의 짝지은 값 → Bland–Altman(bias·LoA·CI),"
echo "           ICC(2,1)/ICC(3,1), Lin's CCC, 반복성, 방법비교 회귀(Deming·PB)"
echo "  [범주형] 두 평가자의 범주 판정 → Cohen's kappa·가중 kappa·Gwet's AC1,"
echo "           범주별 일치도(PPA/NPA), kappa 역설 진단, 주변 동질성 검정"
echo "  둘 다 논문에 바로 붙일 문장까지 출력합니다."
echo ""
echo "  내 데이터로 실행:"
echo "    python3 -m agreestat.cli 내파일.csv -a 방법A열 -b 방법B열"
echo "    python3 -m agreestat.cli 내파일.csv -a watch -b ecg -s subject --percent"
echo "    python3 -m agreestat.cli 내파일.csv --categorical -a 판독1 -b 판독2"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v agreestat >/dev/null 2>&1; then
    agreestat "$@"
  else
    python3 -m agreestat.cli "$@"
  fi
}

echo "### 예제 1) 비접촉 호흡수 vs 호흡밴드 — 좋은 일치 (tight LoA / 높은 ICC)"
echo "\$ agreestat examples/resp_rate_good.csv -a contactless_brpm -b band_brpm"
echo ""
run examples/resp_rate_good.csv -a contactless_brpm -b band_brpm

echo ""
echo "### 예제 2) 워치 RMSSD vs ECG RMSSD — 비례 편향 + 반복측정 (경고 발동)"
echo "\$ agreestat examples/hrv_rmssd_proportional.csv -a watch_rmssd_ms -b ecg_rmssd_ms -s subject"
echo ""
run examples/hrv_rmssd_proportional.csv -a watch_rmssd_ms -b ecg_rmssd_ms -s subject

echo ""
echo "### 예제 3) 임상 허용한계 ±2 brpm — 교환가능(interchangeable) 판정 + 마크다운 표"
echo "\$ agreestat examples/resp_rate_good.csv -a contactless_brpm -b band_brpm --accept 2 --markdown"
echo ""
run examples/resp_rate_good.csv -a contactless_brpm -b band_brpm --accept 2 --markdown

echo ""
echo "### 예제 4) [범주형] 수면단계 5단계 — 기기 vs PSG (Cohen's kappa)"
echo "\$ agreestat examples/sleep_stage_device_vs_psg.csv --categorical \\"
echo "      -a psg_stage -b device_stage --categories \"W,N1,N2,N3,REM\""
echo ""
run examples/sleep_stage_device_vs_psg.csv --categorical \
    -a psg_stage -b device_stage --categories "W,N1,N2,N3,REM" \
    --name-a PSG --name-b 기기

echo ""
echo "### 예제 5) [범주형·순서형] 병변 등급 0–3 — 판독의 2명 (가중 kappa + 기준 판정)"
echo "\$ agreestat examples/lesion_grade_two_readers.csv --categorical --ordinal --min-kappa 0.6"
echo ""
run examples/lesion_grade_two_readers.csv --categorical --ordinal --min-kappa 0.6

echo ""
echo "### 예제 6) [범주형·군집] 피험자 20명 × epoch 90개 — 군집 보정 CI (-s subject)"
echo "    naive CI는 기준 0.70을 통과하지만, 군집 보정 CI는 통과하지 못합니다."
echo "\$ agreestat examples/sleep_stage_clustered.csv --categorical \\"
echo "      -a psg_stage -b device_stage -s subject --min-kappa 0.70"
echo ""
run examples/sleep_stage_clustered.csv --categorical \
    -a psg_stage -b device_stage -s subject --categories "W,N1,N2,N3,REM" \
    --min-kappa 0.70

echo ""
read -p "엔터를 누르면 창이 닫힙니다..."
