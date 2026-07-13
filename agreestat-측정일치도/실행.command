#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  agreestat — 측정 방법 일치도(agreement) 분석기"
echo "=================================================================="
echo "  두 측정 방법(A vs B)의 짝지은 값을 넣으면 Bland–Altman(bias·LoA·CI),"
echo "  ICC(2,1)/ICC(3,1), Lin's CCC, 반복성, 상관/차이 검정을 한 번에 계산하고"
echo "  논문에 바로 붙일 수 있는 문장까지 출력합니다."
echo ""
echo "  내 데이터로 실행:"
echo "    python3 -m agreestat.cli 내파일.csv -a 방법A열 -b 방법B열"
echo "    python3 -m agreestat.cli 내파일.csv -a watch -b ecg -s subject --percent"
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
read -p "엔터를 누르면 창이 닫힙니다..."
