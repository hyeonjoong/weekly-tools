#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  draftcheck — 투고 직전 원고 정합성 점검기"
echo "=================================================================="
echo "  원고 파일(.docx/.md/.tex/.txt) 하나를 받아, 사람 눈으로는 전수 대조가"
echo "  불가능한 기계적 정합성 오류만 골라 줄번호와 함께 뱉습니다."
echo ""
echo "    · 본문 인용 ↔ 참고문헌 목록 (누락 / 미인용 / 번호 순서)"
echo "    · 그림·표 번호 (본문 미언급 / 캡션 없는 언급 / 번호 건너뜀)"
echo "    · 초록 ↔ 본문 표본수(N) 불일치"
echo "    · 통계 보고 (p = 0.000, 임계값만, 효과크기·CI 누락, 표기 혼재)"
echo "    · 약어 정의, 저널 분량 한도"
echo ""
echo "  네트워크를 쓰지 않고, 원본 원고 파일을 절대 수정하지 않습니다."
echo ""
echo "  내 원고로 실행:"
echo "    draftcheck 내원고.docx"
echo "    draftcheck 내원고.docx --limits examples/journals/sleepmed.json"
echo "    draftcheck 내원고.docx --out-dir 점검_20260806   # 리포트 3종 저장"
echo "    draftcheck 내원고.docx --dump-text | less        # 인식 결과 직접 확인"
echo ""
echo "  자세한 안내: 사용법.md / README.md"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v draftcheck >/dev/null 2>&1; then
    draftcheck "$@"
  else
    python3 -m draftcheck.cli "$@"
  fi
}

echo "### 예제 1) 결함을 일부러 심은 원고 — 저널 한도까지 함께 대조"
echo "\$ draftcheck examples/manuscript_flawed.md --limits examples/journals/sleepmed.json"
echo ""
run examples/manuscript_flawed.md --limits examples/journals/sleepmed.json

echo ""
echo "=================================================================="
echo "### 예제 2) 같은 원고의 '깨끗한 대조본' — 여기서 치명이 하나라도 나오면"
echo "    이 툴은 소음입니다. 0건이어야 정상입니다."
echo "\$ draftcheck examples/manuscript_clean.md --limits examples/journals/sleepmed.json"
echo ""
run examples/manuscript_clean.md --limits examples/journals/sleepmed.json

echo ""
echo "=================================================================="
echo "### 예제 3) 같은 원고의 Word(.docx) 판 — 추적 변경과 EndNote 필드가 섞인 경우"
echo "    추적 변경으로 '삭제된' 문장 안의 인용 [99]는 세면 안 됩니다."
echo "    EndNote 필드 코드 속 숫자도 인용이 아닙니다. 결과가 .md 판과 같은지 보세요."
echo "\$ draftcheck examples/manuscript_flawed.docx --quiet"
echo ""
run examples/manuscript_flawed.docx --quiet
run examples/manuscript_flawed.md --quiet

echo ""
echo "=================================================================="
echo "### 예제 4) 리포트 3종 저장 (--out-dir) 과 citecheck 연결"
echo "    (저장소를 어지럽히지 않도록 임시 폴더에 씁니다)"
DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/draftcheck_demo.XXXXXX")"
echo "\$ draftcheck examples/manuscript_flawed.md --out-dir $DEMO_DIR"
echo ""
run examples/manuscript_flawed.md --out-dir "$DEMO_DIR" --quiet
echo "    만들어진 파일:"
for f in "$DEMO_DIR"/*; do echo "      · $(basename "$f")"; done
echo ""
echo "    문제목록.csv 앞부분:"
head -4 "$DEMO_DIR/문제목록.csv" | sed 's/^/      /'
echo ""
echo "    references.csv 는 citecheck-인용DOI검증의 입력 형식과 같습니다."
echo "    문헌이 실제로 존재하고 철회되지 않았는지까지 보려면(네트워크 필요):"
echo "      citecheck \"$DEMO_DIR/references.csv\""

echo ""
echo "=================================================================="
echo "  ※ 한계 고지"
echo "     · 이 툴은 '문서가 자기 안에서 앞뒤가 맞는가'만 봅니다."
echo "       내용의 과학적 타당성·영문 표현·논리는 사람이 봐야 합니다."
echo "     · 문헌이 실제로 존재하는지(DOI 검증)는 citecheck의 일입니다."
echo "     · 인용 표기나 참고문헌 목록을 인식하지 못하면 '이상 없음'이 아니라"
echo "       '점검 불가'라고 크게 알립니다. 그때는 반드시 눈으로 확인하세요."
echo "     · 번호형(밴쿠버)이 1급 지원, 저자-연도는 2급 지원입니다."
echo "     · 예제 원고는 전부 합성이며 실제 환자·원고 데이터가 아닙니다."
echo "=================================================================="
echo ""
read -p "엔터를 누르면 창이 닫힙니다..." || true
