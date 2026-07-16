#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  eegband — 단일채널 EEG 대역파워 분석기"
echo "=================================================================="
echo "  단일채널 EEG CSV(값 열, 선택적 시간 열)와 표본화율(fs)을 주면"
echo "  Welch PSD로 delta/theta/alpha/beta/gamma 절대·상대 파워,"
echo "  슬로우파(SWA=delta), SEF95, 피크주파수, 대역비를 계산합니다."
echo "  (FFT까지 전부 표준 라이브러리로 자체 구현 — 외부 패키지 불필요)"
echo ""
echo "  내 데이터로 실행:"
echo "    python3 -m eegband.cli 내파일.csv --fs 128"
echo "    python3 -m eegband.cli 내파일.csv --time time_s --value eeg_uv --epoch 30"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v eegband >/dev/null 2>&1; then
    eegband "$@"
  else
    python3 -m eegband.cli "$@"
  fi
}

echo "### 예제 1) 각성/알파 우세 트레이스 (~10 Hz), 시간 열 있음 → fs 자동 추정"
echo "\$ eegband examples/alpha_wake.csv --time time_s --value eeg_uv"
echo ""
run examples/alpha_wake.csv --time time_s --value eeg_uv

echo ""
echo "### 예제 2) 깊은수면/델타(SWA) 우세 트레이스 (~1.5 Hz), 값 열만 → fs=128, 20초 에폭"
echo "\$ eegband examples/delta_deep_sleep.csv --fs 128 --epoch 20"
echo ""
run examples/delta_deep_sleep.csv --fs 128 --epoch 20

echo ""
echo "→ 예제 1은 alpha 우세, 예제 2는 delta/SWA 우세로 뒤집히는 것을 확인하세요."
echo ""
# '|| true' so a non-interactive/EOF stdin (e.g. piped run) still exits 0.
read -p "엔터를 누르면 창이 닫힙니다..." _ || true
