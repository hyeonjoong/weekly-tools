#!/bin/bash
# irbpack — 제출서류 정합점검 (더블클릭 실행용)
cd "$(dirname "$0")" || exit 1

echo "=============================================================="
echo " irbpack — IRB·식약처 제출서류 정합점검"
echo "=============================================================="
echo
echo " 무엇을 하나요?"
echo "   한 봉투로 같이 내는 서류들(연구계획서·동의서·CRF·모집공고)을"
echo "   폴더째 읽어, 서류들끼리 '말이 다른 곳'만 찍어 줍니다."
echo "   문서 하나의 품질은 보지 않습니다 — 문서 사이만 봅니다."
echo
echo " 언제 쓰나요?"
echo "   IRB 제출 직전, 그리고 개정판(v1.1 → v1.2)을 낼 때마다."
echo
echo " 내 서류로 돌리려면:"
echo "   python3 -m irbpack \"제출패킷 폴더\" --out-dir \"정합점검_결과\""
echo "   (개정 축) python3 -m irbpack \"패킷_v1.2\" --baseline \"패킷_v1.1\" --out-dir \"개정점검\""
echo "   자세한 안내는 사용법.md 를 보세요."
echo

PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
  echo "  [!] python3 를 찾지 못했습니다. https://www.python.org 에서 설치해 주세요."
  read -r -p "엔터를 누르면 창이 닫힙니다..."
  exit 1
fi

OUT="$(mktemp -d)/irbpack_예제결과"

run_example () {
  echo "--------------------------------------------------------------"
  echo " 예제: $1"
  echo "--------------------------------------------------------------"
  "$PY" -m irbpack "examples/$1" --out-dir "$OUT/$1"
  CODE=$?
  echo
  echo "   → 종료코드 $CODE"
  echo
}

echo "번들 예제(전부 합성 문서)를 차례로 돌려 봅니다."
echo
run_example "정합_패킷"
run_example "불일치_패킷"
run_example "판정불가_패킷"

echo "--------------------------------------------------------------"
echo " 예제: 개정 축 (--baseline) — 계획서만 고치고 동의서를 안 고친 경우"
echo "--------------------------------------------------------------"
"$PY" -m irbpack "examples/개정_이후_패킷" --baseline "examples/개정_이전_패킷" --out-dir "$OUT/개정점검"
CODE=$?
echo
echo "   → 종료코드 $CODE"
echo

echo "=============================================================="
echo " 예제 리포트가 저장된 곳: $OUT"
echo " 종료코드: 0=치명없음 · 1=치명발견 · 2=입력문제(원고·프로토콜없음) · 3=판정불가"
echo "=============================================================="
echo
read -r -p "엔터를 누르면 창이 닫힙니다..."
