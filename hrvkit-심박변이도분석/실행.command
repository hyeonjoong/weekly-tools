#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  hrvkit — 심박변이도(HRV) 분석기"
echo "=================================================================="
echo "  RR/IBI(ms) 또는 순간 HR(bpm) CSV를 넣으면 이상박동을 보정한 뒤"
echo "  시간영역(RMSSD·SDNN·pNN50·HTI/TINN…), 주파수영역(VLF/LF/HF, 직접"
echo "  구현한 radix-2 FFT + Welch 또는 보간 없는 Lomb–Scargle, 호흡수 추정),"
echo "  비선형(Poincaré SD1/SD2,"
echo "  SampEn, DFA α1/α2) 지표를 계산해 리포트로 출력합니다. (표준 라이브러리만)"
echo ""
echo "  ※ 아래 예제 데이터는 형식 시연용 합성 데이터입니다 (실제 피험자 기록 아님)."
echo ""
echo "  내 데이터로 실행:"
echo "    python3 -m hrvkit.cli 내파일.csv                 (단일 열 RR/HR)"
echo "    python3 -m hrvkit.cli 내파일.csv --col rr_ms      (값 열 지정)"
echo "    python3 -m hrvkit.cli 안정.csv 느린호흡.csv --compare   (짝지은 비교)"
echo "    python3 -m hrvkit.cli data/*.csv --format csv     (일괄 요약 CSV)"
echo "    python3 -m hrvkit.cli --paired manifest.csv       (같은 피험자 pre-post 코호트 통계)"
echo "    python3 -m hrvkit.cli --groups arms.csv           (평행군 독립 2군 비교)"
echo "    python3 -m hrvkit.cli 세션.csv --window           (5분 구간별 추이 + 추세)"
echo "    python3 -m hrvkit.cli 세션.csv --psd lomb         (보간 없는 PSD — 절대 파워 보고용)"
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
echo "### 예제 4) 구간별 추이 — 20분 세션을 5분 창으로 (--window)"
echo "\$ hrvkit examples/session_20min.csv --window"
echo "    구간별 지표 + Mann-Kendall 추세(tau·Theil-Sen 기울기·정확 p) +"
echo "    Task Force 장기 지표(SDANN·SDNN index) 를 냅니다."
echo ""
run examples/session_20min.csv --window

echo ""
echo "### 예제 5) 평행군(독립 2군) 비교 — 대조 5명 대 디바이스 5명 (--groups)"
echo "\$ hrvkit --groups examples/parallel_arm/manifest.csv"
echo "    Mann-Whitney 정확검정 p, Hodges-Lehmann 이동량 + 95% 신뢰구간,"
echo "    Hedges g, Holm/BH 보정 p 를 냅니다. (매니페스트: file,group,subject)"
echo ""
run --groups examples/parallel_arm/manifest.csv

echo ""
echo "### 예제 6) 여러 피험자 짝 통계 — 연구의 핵심 산출물 (--paired)"
echo "\$ hrvkit --paired examples/paired/manifest.csv"
echo "    지표별 Wilcoxon 정확검정 p, Hodges-Lehmann 이동량 + 95% 신뢰구간,"
echo "    Cohen's dz, Holm/BH 다중비교 보정 p 를 냅니다."
echo "    (매니페스트: baseline,intervention,subject 열 / 피험자당 한 행)"
echo ""
run --paired examples/paired/manifest.csv

echo ""
echo "### 요약: 이 합성 예제에서 느린 호흡 쪽이 RMSSD·HF·SD1 ↑, LF/HF ↓ 로 계산됩니다."
echo "    이는 '느린 호흡→RSA↑→HRV↑' 방향과 일치하지만, 합성 데이터이므로"
echo "    기전의 증거가 아니라 계산 예시입니다. 실제 판단은 여러분의 데이터로."
echo ""
read -p "엔터를 누르면 창이 닫힙니다..."
