#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  circadia — 일주기리듬(circadian rhythm) 분석기"
echo "=================================================================="
echo "  워치 CSV(심박·걸음·수면구간)로 24시간 리듬을 정량화합니다:"
echo "  코사이너(MESOR·진폭·정점위상), IS/IV/RA/L5/M10, SRI·사회적 시차,"
echo "  심박 야간 강하, 48시간 더블플롯 액토그램. (표준 라이브러리만)"
echo ""
echo "  ※ 아래 예제는 100% 합성 데이터입니다 (실제 인물 기록 아님)."
echo "  ※ 진단 도구가 아닙니다 — 지표와 문헌 참고범위만 보여줍니다."
echo "  ※ 액토그램은 한 줄 102자 — 터미널 폭 105자 이상을 권장합니다."
echo ""
echo "  내 데이터로 실행:"
echo "    python3 -m circadia 심박.csv --steps 걸음.csv --sleep 수면.csv"
echo "    python3 -m circadia --sleep 수면.csv          (수면만)"
echo "    python3 -m circadia 심박.csv --inspect         (열 인식 확인)"
echo "    python3 -m circadia … --out-dir 리듬결과       (md·csv·액토그램 저장)"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v circadia >/dev/null 2>&1; then
    circadia "$@"
  else
    python3 -m circadia "$@"
  fi
}

echo "### 예제 1) 규칙적인 1주 — Apple 건강식 열 이름(수면 단계 행 포함)"
echo "\$ circadia examples/규칙적_1주_애플건강/심박.csv --steps 걸음.csv --sleep 수면.csv"
echo ""
run examples/규칙적_1주_애플건강/심박.csv \
    --steps examples/규칙적_1주_애플건강/걸음.csv \
    --sleep examples/규칙적_1주_애플건강/수면.csv

echo ""
echo "### 예제 2) 불규칙한 1주 — 삼성헬스식 열 이름, 착용 갭·낮잠 포함"
echo "\$ circadia examples/불규칙_1주_삼성헬스/심박.csv --steps 걸음.csv --sleep 수면.csv"
echo ""
run examples/불규칙_1주_삼성헬스/심박.csv \
    --steps examples/불규칙_1주_삼성헬스/걸음.csv \
    --sleep examples/불규칙_1주_삼성헬스/수면.csv

echo ""
echo "### 예제 3) 열 인식 확인 (--inspect) — Fitbit식 열 이름"
echo "\$ circadia examples/규칙적_1주_핏빗/심박.csv --inspect"
echo ""
run examples/규칙적_1주_핏빗/심박.csv --inspect

echo ""
echo "### 요약: 같은 파이프라인이 규칙적 주에서는 IS 0.99·SRI 97, 불규칙 주에서는"
echo "    IS 0.56·SRI 59·사회적 시차 2.9h 를 계산합니다 — 합성 데이터이므로"
echo "    계산 예시일 뿐, 실제 판단은 여러분의 데이터로 하세요."
echo ""
# `|| true` — 파이프/리다이렉션 실행(CI, `echo |`)에서 read 의 EOF 실패로
# 스크립트가 0이 아닌 코드로 끝나는 것을 막습니다.
read -p "엔터를 누르면 창이 닫힙니다..." || true
