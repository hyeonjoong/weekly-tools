#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  visitaudit — 방문일정 이탈 점검"
echo "=================================================================="
echo "  피험자별 방문 날짜 표와 프로토콜(방문창) JSON 을 받아"
echo "  '이 시험이 계획대로 굴러가고 있는가'를 전수 대조합니다."
echo ""
echo "    · 방문창 이탈 판정 (예정일 ± 창, 경계 포함, 며칠 밖인지까지)"
echo "    · 미도래(창 미마감)·탈락후 방문은 절대 이탈로 세지 않음"
echo "    · 순서 위반 · 중복 행 · 결측 방문 · 불가능 날짜 적발"
echo "    · 선정/제외기준 재점검, CONSORT 흐름 숫자, PP 집합 후보"
echo "    · 커버리지 자백이 항상 맨 위 — 판정률 임계 미만이면 exit 3"
echo ""
echo "  네트워크를 쓰지 않고, 원본 파일을 절대 수정하지 않습니다."
echo ""
echo "  내 데이터로 실행:"
echo "    visitaudit 방문기록.csv --protocol 프로토콜.json \\"
echo "               --subjects 피험자.csv --as-of 2026-08-14 --out-dir 결과_202608"
echo ""
echo "  자세한 안내: 사용법.md / README.md"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v visitaudit >/dev/null 2>&1; then
    visitaudit "$@"
  else
    python3 -m visitaudit "$@"
  fi
}

DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/visitaudit_demo.XXXXXX")"

echo "### 예제 1) 번들 합성 예제 (SERENE 풍, 피험자 20명 × 방문 5회)"
echo "    창 이탈·결측·순서 위반·중복 행·깨진 날짜·미도래·탈락을 일부러 심었습니다."
echo "\$ visitaudit examples/방문기록.csv --protocol examples/프로토콜.json \\"
echo "             --subjects examples/피험자.csv --as-of 2026-08-14 --out-dir 결과_예제"
echo ""
run examples/방문기록.csv --protocol examples/프로토콜.json \
    --subjects examples/피험자.csv --as-of 2026-08-14 --out-dir "$DEMO_DIR/결과"
echo "    (종료코드: $?  — 1 = 이탈 발견)"

echo ""
echo "=================================================================="
echo "### 예제 2) 프로토콜 없이 돌리면 판정하지 않고 죽습니다 (종료코드 2)"
echo "    방문창·순서·결측·PP 규칙이 전부 프로토콜에서 오기 때문입니다."
echo "\$ visitaudit examples/방문기록.csv --no-files"
echo ""
run examples/방문기록.csv --no-files
echo "    (종료코드: $?  — 2 = 입력·프로토콜 오류)"

echo ""
echo "=================================================================="
echo "### 예제 3) 판정률이 임계(70%) 미만이면 조용히 통과시키지 않습니다 (종료코드 3)"
cat > "$DEMO_DIR/판정불가많음.csv" <<'EOF'
피험자ID,방문명,방문일,상태
S01,Baseline,2026-03-02,완료
S02,V1,2026-03-30,완료
S03,V1,2026-03-30,완료
EOF
echo "    (S02·S03 은 기준방문 기록이 없어 판정불가 → 판정률 33%)"
echo "\$ visitaudit 판정불가많음.csv --protocol examples/프로토콜.json --as-of 2026-08-14 --no-files"
echo ""
# 파이프 뒤의 $? 는 tail 의 것이라, 실제 종료코드를 파일로 받아 그대로 보여 준다
run "$DEMO_DIR/판정불가많음.csv" --protocol examples/프로토콜.json \
    --as-of 2026-08-14 --no-files > "$DEMO_DIR/예제3.out" 2>&1
EX3=$?
# 이 예제의 요점은 [커버리지 자백] 블록이므로 그 부분을 보여 준다
sed -n '/\[커버리지 자백\]/,$p' "$DEMO_DIR/예제3.out" | head -12
echo "      ..."
tail -1 "$DEMO_DIR/예제3.out"
echo "    (종료코드: $EX3  — 판정 못 한 방문을 '이상 없음'으로 흘려보내지 않습니다)"

echo ""
echo "=================================================================="
echo "### 만들어진 파일 (이 시연은 임시 폴더에 쓰고 끝나면 지웁니다)"
echo "    실제로 위 명령을 치면 결과_예제/ 폴더에 그대로 남습니다."
for f in "$DEMO_DIR/결과"/*; do echo "      · $(basename "$f")"; done
echo ""
echo "    이탈목록.csv 앞부분:"
python3 - "$DEMO_DIR/결과/이탈목록.csv" <<'PY'
import sys
with open(sys.argv[1], encoding="utf-8-sig") as fh:
    for i, line in enumerate(fh):
        if i >= 3:
            break
        text = line.rstrip("\n")
        print("      " + (text[:150] + " ..." if len(text) > 150 else text))
PY
echo ""
echo "    진행점검.md 에는 위 리포트 전문과 논문 Methods 에 붙일 KR/EN 문장 초안이,"
echo "    CONSORT.txt 에는 텍스트 흐름도가 들어 있습니다."

echo ""
echo "=================================================================="
echo "  ※ 한계 고지"
echo "     · 판정이지 확정이 아닙니다. PP 후보·CONSORT 숫자는 데이터 검토"
echo "       회의에서 사람이 확정해야 합니다."
echo "     · 미도래/탈락후 방문은 이탈로 세지 않습니다(창 종료일 ≥ as-of 는 미도래)."
echo "     · 애매하면 이탈이 아니라 판정불가로 보냅니다 — 중복 행·깨진 날짜는"
echo "       어느 쪽이 맞는지 추측하지 않습니다."
echo "     · 날짜는 연도-먼저 형식만 읽습니다(07-06-2026 은 해석하지 않음)."
echo "     · 시각·타임존은 다루지 않습니다(시각은 버리고 자백)."
echo "     · --as-of 를 명시하세요. 안 하면 오늘 날짜로 돌고 리포트에 크게 박힙니다."
echo "     · 예제 데이터는 전부 합성이며 실제 피험자 자료가 아닙니다."
echo "=================================================================="
echo ""
rm -rf "$DEMO_DIR"
read -p "엔터를 누르면 창이 닫힙니다..." || true
