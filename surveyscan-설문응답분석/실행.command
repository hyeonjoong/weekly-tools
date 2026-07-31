#!/bin/bash
cd "$(dirname "$0")"
echo "============================================================"
echo "  surveyscan — 설문 응답 CSV 분석기"
echo "  기술통계 · 결측 · 역문항 · 하위척도 점수 · Cronbach α"
echo "  · 임상 심각도 구간 · 집단 비교(Welch t/ANOVA)"
echo "============================================================"
echo
echo "▶ 번들 예시(수면 설문 40명, 치료군/대조군)로 분석을 실행합니다:"
echo "  python3 -m surveyscan.cli examples/sleep_survey.csv \\"
echo "      --config examples/sleep_config.json --id-col ID --group-col 군"
echo
python3 -m surveyscan.cli examples/sleep_survey.csv \
    --config examples/sleep_config.json --id-col ID --group-col 군
echo
echo "------------------------------------------------------------"
echo "내 데이터로 돌리려면 터미널에서:"
echo "  python3 -m surveyscan.cli 내설문.csv --config 내설정.json --id-col ID"
echo "집단 비교는 컬럼 이름만 추가:  --group-col 군"
echo "자세한 사용법은 사용법.md / README.md 참고."
echo
# 더블클릭이 아닌 방식(입력이 없는 실행)에서 read 가 실패해도 종료코드는 0으로 둔다.
read -p "엔터를 누르면 창이 닫힙니다..." || true
