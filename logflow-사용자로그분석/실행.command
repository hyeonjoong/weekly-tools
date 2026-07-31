#!/bin/bash
cd "$(dirname "$0")"
echo "============================================================"
echo "  logflow — 사용자 이벤트 로그 분석기"
echo "  세션화 · DAU/WAU/MAU · 리텐션 · 퍼널 · 준수도 · 군(arm) 비교"
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
echo "▶ 군 비교 + 프로토콜 준수도 예시(중재군 vs 대조군, 24명·3주):"
echo "  python3 -m logflow.cli examples/trial_events.csv --group-col arm \\"
echo "      --ref-group control --funnel app_open,breathing_complete,sleep_report \\"
echo "      --retention 1,7 --adherence-days 3 --top 3"
echo
python3 -m logflow.cli examples/trial_events.csv --group-col arm \
    --ref-group control --funnel app_open,breathing_complete,sleep_report \
    --retention 1,7 --adherence-days 3 --top 3
echo
echo "------------------------------------------------------------"
echo "내 데이터로 돌리려면 터미널에서:"
echo "  python3 -m logflow.cli 내로그.csv --funnel 단계1,단계2,단계3"
echo "열 이름이 다르면 --user-col/--event-col/--time-col 로 지정."
echo "군 비교는 --group-col 군열이름 (예: --group-col arm)."
echo "프로토콜 준수도는 --adherence-days 5 (주 5일 이상 사용 = 준수)."
echo "리포트를 남에게 보낼 땐 --anonymize (사용자 ID를 U001··· 가명으로)."
echo "자세한 사용법은 사용법.md / README.md 참고."
echo
read -p "엔터를 누르면 창이 닫힙니다..." || true
