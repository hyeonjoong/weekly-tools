#!/bin/bash
cd "$(dirname "$0")"
echo "============================================================"
echo "  factorscan — 설문 척도 요인분석·타당도 진단기"
echo "  KMO · Bartlett · 요인 수(고유값/평행분석) · 적재량 · 문항-총점"
echo "============================================================"
echo
echo "▶ 번들 예시(수면 자가진단 8문항·80명)로 분석을 실행합니다:"
echo "  python3 -m factorscan.cli examples/sleep_scale.csv \\"
echo "      --config examples/sleep_config.json"
echo
python3 -m factorscan.cli examples/sleep_scale.csv \
    --config examples/sleep_config.json
echo
echo "------------------------------------------------------------"
echo "내 데이터로 돌리려면 터미널에서:"
echo "  python3 -m factorscan.cli 내설문.csv --config 내설정.json"
echo "  python3 -m factorscan.cli 내설문.csv --items Q1,Q2,Q3 --id-col ID"
echo "자세한 사용법은 사용법.md / README.md 참고."
echo
read -p "엔터를 누르면 창이 닫힙니다..."
