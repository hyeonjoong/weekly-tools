#!/bin/bash
# rsalink 예시 실행 — 합성 페이싱 시나리오(기준선 12회/분 5분 → 6회/분 페이싱 10분)를 분석한다.
# 더블클릭(macOS) 또는 `bash 실행.command`. 네트워크 없음, 입력은 읽기만, 산출물은 결과/paced_6bpm 안에만.
cd "$(dirname "$0")" || exit 1

echo "==================================================================="
echo " rsalink — 호흡 신호 + RR 간격 연동 분석 (합성 예시)"
echo " 목적: HF-HRV/RSA 변화가 호흡수 변화와 함께 갔는지(동반/비동반)를 숫자로 본다."
echo "       호흡중심 대역으로 보면 고정 HF 와 결론이 같은지/다른지도 함께 본다."
echo " 이 예시는 100% 합성 데이터이며 어떤 사람의 기록도 아니다. 인과·진단 판단 도구가 아니다."
echo "==================================================================="
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 이 필요합니다 (3.9 이상)."
  read -p "Enter 를 누르면 닫힙니다..." _ || true
  exit 2
fi

if [ ! -f examples/paced_6bpm/호흡.csv ]; then
  echo "예시 데이터를 생성합니다 (고정 seed)..."
  python3 examples/_generate.py || exit 2
fi

python3 -m rsalink examples/paced_6bpm/호흡.csv examples/paced_6bpm/RR.csv \
  --baseline 0:00-5:00 --stimulus 5:00-15:00 --pace 6 --out-dir 결과/paced_6bpm
status=$?
echo
echo "종료 코드: $status  (0 정상 / 2 입력·인자 오류 / 3 신뢰 불가)"
echo "산출물: 결과/paced_6bpm/연동리포트.md, 호흡별.csv, 조건비교.csv"
echo
read -p "Enter 를 누르면 닫힙니다..." _ || true
exit $status
