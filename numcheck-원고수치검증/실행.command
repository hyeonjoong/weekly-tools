#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  numcheck — 원고 수치 재계산 검증"
echo "=================================================================="
echo "  원고 파일(.docx/.md/.tex/.txt) 하나를 받아, 본문에 적힌 숫자를"
echo "  전부 다시 계산해 대조합니다. 데이터 파일도 네트워크도 필요 없습니다."
echo ""
echo "    · 비율        23/48 이 정말 47.9% 인가"
echo "    · p           t(45) = 2.31 에서 나오는 p 가 적힌 p 와 같은가"
echo "    · N 합계      하위군 24 + 23 이 전체 48 과 맞는가"
echo "    · GRIM        N = 23 에서 ISI 평균 14.37 이 존재할 수 있는가"
echo "    · 변화량      사후 − 사전 이 적힌 변화량과 맞는가"
echo "    · 신뢰구간    점추정치가 자기 CI 안에 있는가, CI 와 p 가 안 싸우는가"
echo "    · 유의성 문구 p = .07 인데 '유의하게' 라고 쓰지 않았는가"
echo ""
echo "  리포트 맨 위에 '후보 몇 개 중 몇 개를 재계산하고 몇 개를 건너뛰었는지'"
echo "  를 사유별로 반드시 찍습니다. 못 본 것을 말하지 않는 체커는 거짓말입니다."
echo ""
echo "  내 원고로 실행:"
echo "    numcheck 내원고.docx"
echo "    numcheck 내원고.docx --out-dir 검토_20260813   # 리포트 3종 저장"
echo "    numcheck 내원고.docx --scale ISI=0:28:7        # GRIM 켜기"
echo "    numcheck 내원고.docx --dump-text | less        # 무엇을 읽었는지 확인"
echo ""
echo "  자세한 안내: 사용법.md / README.md"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v numcheck >/dev/null 2>&1; then
    numcheck "$@"
  else
    python3 -m numcheck.cli "$@"
  fi
}

echo "### 예제 1) 오류 8종을 일부러 심어 둔 원고"
echo "\$ numcheck examples/flawed_manuscript.md"
echo ""
run examples/flawed_manuscript.md

echo ""
echo "=================================================================="
echo "### 예제 2) 같은 연구를 '오류 없이' 쓴 대조본"
echo "    여기서 치명·경고가 하나라도 나오면 이 툴은 소음입니다. 0건이어야 정상."
echo "\$ numcheck examples/clean_manuscript.md"
echo ""
run examples/clean_manuscript.md

echo ""
echo "=================================================================="
echo "### 예제 3) Word(.docx) — 표 셀 안의 숫자까지 읽고, 추적 변경의"
echo "    '삭제된' 문장은 세지 않습니다 (예제에는 지워진 99/46 (250.0%) 이 있습니다)."
echo "\$ numcheck examples/serene_style.docx"
echo ""
run examples/serene_style.docx

echo ""
echo "=================================================================="
echo "### 예제 4) 척도를 지정하면 GRIM 검사가 켜집니다"
echo "    단어인지도(50문항 대비 정답 %)는 개인 점수가 2%p 단위이므로,"
echo "    N = 7 에서 평균 62.4% 는 산술적으로 존재할 수 없습니다."
echo "\$ numcheck examples/flawed_manuscript.md --scale 단어인지도=0:100:50 \\"
echo "      --percent-of-count 단어인지도 --quiet"
echo ""
run examples/flawed_manuscript.md --scale 단어인지도=0:100:50 \
    --percent-of-count 단어인지도 --quiet

echo ""
echo "=================================================================="
echo "### 예제 5) 리포트 3종 저장 (--out-dir)"
echo "    (저장소를 어지럽히지 않도록 임시 폴더에 씁니다)"
DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/numcheck_demo.XXXXXX")"
echo "\$ numcheck examples/flawed_manuscript.md --out-dir $DEMO_DIR"
echo ""
run examples/flawed_manuscript.md --out-dir "$DEMO_DIR" --quiet
echo "    만들어진 파일:"
for f in "$DEMO_DIR"/*; do echo "      · $(basename "$f")"; done
echo ""
echo "    재계산표.csv 는 **건너뛴 것까지 전부** 한 줄씩 담고 있어,"
echo "    커버리지 자백을 사람이 직접 검산할 수 있습니다:"
head -4 "$DEMO_DIR/재계산표.csv" | cut -c1-110 | sed 's/^/      /'

echo ""
echo "=================================================================="
echo "  ※ 한계 고지"
echo "     · PDF 는 지원하지 않습니다(v1). 편집 가능한 원본에 실행하세요."
echo "     · 통계 방법이 옳은지는 판단하지 않습니다 — 산술만 봅니다."
echo "     · 원본 데이터를 읽지 않습니다. 원고만 봅니다."
echo "     · 표는 행 단위로 읽으므로, 머리행의 N 이 어느 열에 걸리는지는"
echo "       추측하지 않습니다(셀 안에 N 이 적혀 있을 때만 GRIM 을 돌립니다)."
echo "     · 예제 원고는 전부 합성이며 실제 환자·연구 데이터가 아닙니다."
echo "=================================================================="
echo ""
read -p "엔터를 누르면 창이 닫힙니다..." || true
