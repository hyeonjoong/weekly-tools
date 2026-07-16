#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  hrvkit — 심박변이도(HRV) 분석기"
echo "=================================================================="
echo "  RR/IBI(ms) 또는 순간 HR(bpm) CSV를 넣으면 이상박동을 보정한 뒤"
echo "  시간영역(RMSSD·SDNN·pNN50·HTI/TINN…), 주파수영역(VLF/LF/HF, 직접"
echo "  구현한 radix-2 FFT + Welch, 호흡수 추정), 비선형(Poincaré SD1/SD2,"
echo "  SampEn, DFA α1/α2) 지표를 계산해 리포트로 출력합니다. (표준 라이브러리만)"
echo ""
echo "  내 데이터로 실행:"
echo "    python3 -m hrvkit.cli 내파일.csv                 (단일 열 RR/HR)"
echo "    python3 -m hrvkit.cli 내파일.csv --col rr_ms      (값 열 지정)"
echo "    python3 -m hrvkit.cli 안정.csv 느린호흡.csv --compare   (짝지은 비교)"
echo "    python3 -m hrvkit.cli data/*.csv --format csv     (일괄 요약 CSV)"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v hrvkit >/dev/null 2>&1; then
    hrvkit "$@"
  else
    python3 -m hrvkit.cli "$@"
  fi
}

echo "### 예제 1) 안정 시 안정 호흡 — 단일 열(rr_ms), 이상박동 포함"
echo "\$ hrvkit examples/resting.csv"
echo ""
run examples/resting.csv

echo ""
echo "### 예제 2) 느린 호흡(디바이스) — time+value 형식, 높은 HRV"
echo "\$ hrvkit examples/slow_breathing.csv"
echo ""
run examples/slow_breathing.csv

echo ""
echo "### 예제 3) 짝지은 비교 — 안정(기저) 대 느린 호흡(개입)"
echo "\$ hrvkit examples/resting.csv examples/slow_breathing.csv --compare"
echo ""
run examples/resting.csv examples/slow_breathing.csv --compare

echo ""
echo "### 요약: 느린 호흡에서 RMSSD·HF·SD1 ↑, LF/HF ↓ → 부교감(미주신경) 활성 ↑"
echo "    = BELL-001 기전(느린 호흡→RSA↑→서파수면)과 일치하는 방향"
echo ""
read -p "엔터를 누르면 창이 닫힙니다..."
