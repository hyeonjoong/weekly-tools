#!/bin/bash
# 더블클릭하면 번들 예제로 revcheck 가 무엇을 잡아내는지 바로 보여 줍니다.
cd "$(dirname "$0")" || exit 1

echo "=========================================================="
echo " revcheck — 리비전 응답 점검"
echo "=========================================================="
echo
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

echo "----------------------------------------------------------"
echo " [1/3] 결함을 일부러 심어 둔 예제 (examples/flawed)"
echo "       심어 둔 결함: 응답 누락 1 · 인용 숫자 불일치 1 · 미신고 숫자 변경 1"
echo "       (치명 3건 + 경고 4건이 나오면 정상입니다)"
echo "----------------------------------------------------------"
"$PY" -m revcheck.cli \
  --old examples/flawed/제출본.md \
  --new examples/flawed/개정본.md \
  --response examples/flawed/응답서.md \
  --out-dir "예제결과"
echo
echo "----------------------------------------------------------"
echo " [2/3] 응답서대로 정확히 개정한 예제 (examples/clean) — 치명 0건이어야 정상"
echo "----------------------------------------------------------"
"$PY" -m revcheck.cli \
  --old examples/clean/제출본.md \
  --new examples/clean/개정본.md \
  --response examples/clean/응답서.md \
  --quiet
echo
echo "----------------------------------------------------------"
echo " [3/3] 워드(.docx) 예제 — 변경내용 추적이 켜진 개정본입니다"
echo "       (.docx 는 줄 번호가 없어 위치 참조를 '확인불가'로 보고합니다)"
echo "----------------------------------------------------------"
"$PY" -m revcheck.cli \
  --old examples/docx/제출본.docx \
  --new examples/docx/개정본.docx \
  --response examples/docx/응답서.docx \
  --quiet
echo
echo " 무엇을 하나요?"
echo "   제출본 · 개정본 · 응답서(point-by-point) 세 파일을 동시에 읽어"
echo "   응답서에 적은 약속이 개정 원고에 실제로 반영됐는지 대조합니다."
echo "     · 리뷰어 코멘트 번호 전수 점검 (2-4 다음이 2-6이면 치명)"
echo "     · 응답서가 인용한 '개정 후 문구'가 개정본에 문자 그대로 있는가"
echo "     · 응답서에 없는데 숫자가 조용히 바뀐 문단"
echo "     · 위치 참조 · 참고문헌/그림/표 증감"
echo "   네트워크를 쓰지 않고, 원본 파일은 절대 수정하지 않습니다."
echo
echo " 내 파일로 돌리려면:"
echo "   revcheck --old 제출본.docx --new 개정본.docx --response 응답서.docx --out-dir 결과"
echo "   (설치: 이 폴더에서  python3 -m pip install -e .  를 한 번 실행)"
echo

if [ -d "예제결과" ]; then
  echo "결과 파일을 저장했습니다: $(pwd)/예제결과"
  echo "  리비전점검.md / 문제목록.csv / 변경목록.csv / 추가문헌.csv"
  open "예제결과" 2>/dev/null || true
fi
echo
read -p "엔터를 누르면 창이 닫힙니다..."
