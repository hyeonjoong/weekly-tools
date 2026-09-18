#!/bin/bash
# stalecheck — 제출본 세대 점검. 더블클릭하면 합성 예제로 동작을 보여 줍니다.
cd "$(dirname "$0")" || exit 1

PY=$(command -v python3 || echo /usr/bin/python3)
EX_DIR="${TMPDIR:-/tmp}/stalecheck_example_$$"
REPORT_DIR="${TMPDIR:-/tmp}/stalecheck_report_$$"
trap 'rm -rf "$EX_DIR" "$REPORT_DIR"' EXIT

echo "═══════════════════════════════════════════════════════════════════"
echo " stalecheck — 제출본 세대 점검"
echo "═══════════════════════════════════════════════════════════════════"
echo
echo " 무엇을 하나요"
echo "   투고 봉투(submission/·제출용/) 안의 파일이 작업 폴더의 최신 원본과"
echo "   같은 세대인지 내용 해시로 대조합니다. PDF·DOCX 는 생성시각 메타데이터를"
echo "   먼저 지우고 비교해, 다시 렌더했을 뿐인 파일을 오탐으로 내보내지 않습니다."
echo
echo " 언제 쓰나요"
echo "   투고 버튼을 누르기 직전 · 리젝 후 다른 저널 패키지로 갈아탈 때 ·"
echo "   리비전 재제출 · 재현성 예치본을 올릴 때."
echo
echo " 하지 않는 것"
echo "   고치지 않습니다(복사·덮어쓰기·이동·삭제 없음). 원고 내용도 저널 규정도"
echo "   보지 않습니다. 봉투가 최신이라고 보증하지 않습니다 — 짝지은 쌍에 대해서만"
echo "   말합니다."
echo
echo "───────────────────────────────────────────────────────────────────"
echo " 합성 예제를 만드는 중… (실제 논문 자료는 쓰지 않습니다)"
"$PY" examples/예제_만들기.py "$EX_DIR" >/dev/null || { echo "예제 생성 실패"; read -r -p "엔터를 누르면 창이 닫힙니다..."; exit 1; }
echo

echo "═══ ① 봉투가 최신인 경우 → 종료코드 0 ═════════════════════════════"
"$PY" -m stalecheck --work "$EX_DIR/예제_깨끗한폴더" --package "$EX_DIR/예제_깨끗한폴더/submission"
echo "  ↳ 종료코드 = $?"
echo

echo "═══ ② 봉투가 한 세대 낡은 경우 → 종료코드 1 ═══════════════════════"
"$PY" -m stalecheck --work "$EX_DIR/예제_논문폴더" --package "$EX_DIR/예제_논문폴더/submission" --out-dir "$REPORT_DIR"
echo "  ↳ 종료코드 = $?"
echo

echo "═══ ③ 짝지음 비율이 기준 미만 → 종료코드 3 (3이 1보다 우선) ═══════"
"$PY" -m stalecheck --work "$EX_DIR/예제_논문폴더" --package "$EX_DIR/예제_논문폴더/submission" --min-coverage 0.99 --quiet
echo "  ↳ 종료코드 = $?"
echo

echo "═══ ④ 봉투 폴더를 특정하지 못함 → 종료코드 2 (판정 없이 거절) ═════"
"$PY" -m stalecheck --work "$EX_DIR/예제_논문폴더"
echo "  ↳ 종료코드 = $?"
echo

echo "───────────────────────────────────────────────────────────────────"
echo " 내 논문 폴더에 써 보려면 (터미널에 그대로 붙여넣기):"
echo
echo '   python3 -m stalecheck --work "내논문폴더" --inspect'
echo '   python3 -m stalecheck --work "내논문폴더" \'
echo '       --package "내논문폴더/submission" --out-dir ~/Desktop/세대점검'
echo
echo " --package 를 생략하면 봉투 후보를 인쇄하고 거절합니다(추론해서 판정하지 않습니다)."
echo " 자세한 안내: 사용법.md"
echo "═══════════════════════════════════════════════════════════════════"
read -r -p "엔터를 누르면 창이 닫힙니다..."
