#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  eegband — EEG 대역파워 분석기 (단일/다채널, CSV·EDF)"
echo "=================================================================="
echo "  EEG CSV/TSV(값 열, 선택적 시간 열) 또는 EDF/EDF+/BDF 기록을 주면"
echo "  Welch PSD로 delta/theta/alpha/beta/gamma 절대·상대 파워,"
echo "  슬로우파(SWA=delta), SEF95, 피크주파수, 대역비,"
echo "  그리고 1/f 비주기 배경(지수·배경보정 진동 파워)을 계산합니다."
echo "  (FFT까지 전부 표준 라이브러리로 자체 구현 — 외부 패키지 불필요)"
echo ""
echo "  내 데이터로 실행:"
echo "    python3 -m eegband.cli 내파일.csv --fs 128"
echo "    python3 -m eegband.cli 내파일.csv --time time_s --value eeg_uv --epoch 30"
echo "    python3 -m eegband.cli 내기록.edf --channels all --epoch 30 --csv > out.csv"
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

echo "### 예제 3) 다채널 와이드 CSV — 채널별 분석 + 마지막 비교표"
echo "  (Fp1: 1/f 1.3 + 알파, Cz: 1/f 1.7 + 델타, O1: 1/f 1.2 + 알파 — 합성 데이터)"
echo "\$ eegband examples/multichannel_wide.csv --channels all"
echo ""
run examples/multichannel_wide.csv --channels all 2>&1 | tail -12

echo ""
echo "### 예제 4) EDF 기록 — 채널 목록 확인 후 두 채널 분석 (fs는 EDF 헤더에서)"
echo "\$ eegband examples/sleep_2ch.edf --list-channels"
echo ""
run examples/sleep_2ch.edf --list-channels
echo ""
echo "\$ eegband examples/sleep_2ch.edf --channels all --epoch 20"
echo ""
run examples/sleep_2ch.edf --channels all --epoch 20 2>&1 | tail -10

echo ""
echo "→ 비교표의 expo(1/f 지수)가 채널마다 다르게 복원되는 것을 보세요."
echo "  (합성 데이터의 실제 지수 1.3/1.7/1.2, EDF 1.8/1.2)"
echo ""
# '|| true' so a non-interactive/EOF stdin (e.g. piped run) still exits 0.
read -p "엔터를 누르면 창이 닫힙니다..." _ || true
