#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  statwise — 그룹 비교 통계 자동 선택기"
echo "=================================================================="
echo "  두 집단(또는 여러 집단)의 CSV를 넣으면 정규성·등분산을 점검한 뒤"
echo "  알맞은 검정(t / Welch / Mann-Whitney / ANOVA / Kruskal-Wallis)을"
echo "  자동으로 골라 실행하고, 효과크기와 논문용 문장까지 출력합니다."
echo ""
echo "  내 데이터로 실행:"
echo "    python3 -m statwise.cli 내파일.csv --value 값열 --group 그룹열"
echo "    python3 -m statwise.cli 내파일.csv --wide      (각 열이 그룹)"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v statwise >/dev/null 2>&1; then
    statwise "$@"
  else
    python3 -m statwise.cli "$@"
  fi
}

echo "### 예제 1) 수면 디바이스 HRV — 2그룹 (sham vs device), long 형식"
echo "\$ statwise examples/hrv_two_arm.csv --value rmssd_ms --group arm"
echo ""
run examples/hrv_two_arm.csv --value rmssd_ms --group arm

echo ""
echo "### 예제 2) 용량별 ISI 변화 — 3그룹 (low/mid/high), wide 형식 + 사후검정"
echo "\$ statwise examples/isi_change_by_dose.csv --wide"
echo ""
run examples/isi_change_by_dose.csv --wide

echo ""
read -p "엔터를 누르면 창이 닫힙니다..."
