#!/bin/bash
cd "$(dirname "$0")"
echo "============================================================"
echo "  logflow — 사용자 이벤트 로그 분석기"
echo "  세션화 · DAU/WAU/MAU · 리텐션 · 퍼널 전환"
echo "============================================================"
echo
echo "▶ 번들 예시(수면앱 사용 로그 6명, 9일)로 분석을 실행합니다:"
echo "  python3 -m logflow.cli examples/app_events.csv \\"
echo "      --funnel app_open,breathing_start,breathing_complete,sleep_report"
echo
python3 -m logflow.cli examples/app_events.csv \
    --funnel app_open,breathing_start,breathing_complete,sleep_report
echo
echo "------------------------------------------------------------"
echo "내 데이터로 돌리려면 터미널에서:"
echo "  python3 -m logflow.cli 내로그.csv --funnel 단계1,단계2,단계3"
echo "열 이름이 다르면 --user-col/--event-col/--time-col 로 지정."
echo "자세한 사용법은 사용법.md / README.md 참고."
echo
read -p "엔터를 누르면 창이 닫힙니다..."
