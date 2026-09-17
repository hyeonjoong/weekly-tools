#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  doseaudit — 훈련 노출량 점검"
echo "=================================================================="
echo "  피험자별 앱 훈련 로그 워크북 묶음(.xlsx 수십 개)을 통째로 펼쳐,"
echo "  '이 피험자가 처방된 훈련을 실제로 얼마나 받았는가'를"
echo "  **정의를 선언받아** 다시 셉니다."
echo ""
echo "    · 활동일의 정의를 반드시 선언받습니다 — 미선언이면 판정 없이 종료코드 2"
echo "    · 0초와 '데이터 없음'을 절대 섞지 않습니다 (평균은 언제나 세 값)"
echo "    · 손으로 관리한 참여현황 트래커와 부호 붙은 차이로 대조합니다"
echo "    · 정의를 바꾸면 중앙값이 몇 %p 움직이는지 민감도 표를 냅니다"
echo "    · 커버리지 자백이 없으면 리포트 자체가 출력되지 않습니다"
echo ""
echo "  순응/비순응을 판정하지 않고, 노출량과 결과의 관계도 보지 않습니다."
echo "  네트워크를 쓰지 않고, 원본 파일을 절대 수정하지 않습니다."
echo ""
echo "  내 데이터로 실행:"
echo "    doseaudit --inspect --logs wowfit_project17_excels/"
echo "    doseaudit --logs wowfit_project17_excels/ --tracker 참여현황.xlsx \\"
echo "              --active-day any-row --window enroll-to-cut --cut 2026-06-23 \\"
echo "              --target-per-week 3 --out-dir 결과_202609"
echo ""
echo "  자세한 안내: 사용법.md / README.md"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v doseaudit >/dev/null 2>&1; then
    doseaudit "$@"
  else
    python3 -m doseaudit "$@"
  fi
}

DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/doseaudit_demo.XXXXXX")"

echo "### 예제 0) 먼저 '어떻게 읽었는지' 를 봅니다 — 판정은 하지 않습니다"
echo "\$ doseaudit --inspect --logs examples/flawed/logs --tracker examples/flawed/참여현황.xlsx"
echo ""
run --inspect --logs examples/flawed/logs --tracker examples/flawed/참여현황.xlsx \
    > "$DEMO_DIR/inspect.out" 2>&1
head -16 "$DEMO_DIR/inspect.out"
echo "      ..."
sed -n '/선언할 수 있는 활동일 규칙/,$p' "$DEMO_DIR/inspect.out"
echo "    (이 표를 보고 규칙을 고르는 것이 이 툴의 첫 산출물입니다)"

echo ""
echo "=================================================================="
echo "### 예제 1) 깨끗한 자료 — 조용해야 정상입니다 (종료코드 0)"
echo "    트래커의 완료일수가 로그와 정확히 일치하는 합성 자료입니다."
echo "\$ doseaudit --logs examples/clean/logs --tracker examples/clean/참여현황.xlsx \\"
echo "             --active-day any-row --window enroll-to-cut --cut 2026-04-27 \\"
echo "             --target-per-week 3 --out-dir 결과_clean"
echo ""
run --logs examples/clean/logs --tracker examples/clean/참여현황.xlsx \
    --active-day any-row --window enroll-to-cut --cut 2026-04-27 \
    --target-per-week 3 --out-dir "$DEMO_DIR/결과_clean" > "$DEMO_DIR/clean.out" 2>&1
EX1=$?
head -8 "$DEMO_DIR/clean.out"
echo "      ..."
grep -E "^치명 [0-9]+건" "$DEMO_DIR/clean.out"
echo "    (종료코드: $EX1  — 0 = 불일치 없음. 깨끗한 자료에서 경고가 뜨면 그건 툴의 잘못입니다)"

echo ""
echo "=================================================================="
echo "### 예제 2) 결함을 심은 자료 — 여기서 값어치가 나옵니다 (종료코드 1)"
echo "    트래커 불일치(한 방향) · 데이터 0행 모듈 · 0초 100% 모듈 ·"
echo "    한쪽에만 있는 피험자 · 해석 못 한 값을 일부러 심었습니다."
echo "\$ doseaudit --logs examples/flawed/logs --tracker examples/flawed/참여현황.xlsx \\"
echo "             --active-day any-row --window enroll-to-cut --cut 2026-04-27 \\"
echo "             --target-per-week 3 --out-dir 결과_flawed"
echo ""
run --logs examples/flawed/logs --tracker examples/flawed/참여현황.xlsx \
    --active-day any-row --window enroll-to-cut --cut 2026-04-27 \
    --target-per-week 3 --out-dir "$DEMO_DIR/결과_flawed" > "$DEMO_DIR/flawed.out" 2>&1
EX2=$?
sed -n '/^\[치명\]/,$p' "$DEMO_DIR/flawed.out" | head -22
echo "      ..."
sed -n '/정의 민감도/,/고르는 것은 연구팀의 일입니다/p' "$DEMO_DIR/flawed.out"
echo "      ..."
grep -E "^치명 [0-9]+건" "$DEMO_DIR/flawed.out"
echo "    (종료코드: $EX2  — 1 = 치명 발견)"

echo ""
echo "=================================================================="
echo "### 예제 3) 못 읽은 파일이 있으면 '이상 없음'으로 흘려보내지 않습니다 (종료코드 3)"
echo "    3은 1보다 **먼저**입니다 — 무엇을 못 봤는지가 먼저이기 때문입니다."
echo "\$ doseaudit --logs examples/못읽는파일/logs --tracker examples/못읽는파일/참여현황.xlsx \\"
echo "             --active-day any-row --window enroll-to-cut --cut 2026-04-27 --no-files"
echo ""
run --logs examples/못읽는파일/logs --tracker examples/못읽는파일/참여현황.xlsx \
    --active-day any-row --window enroll-to-cut --cut 2026-04-27 --no-files \
    > "$DEMO_DIR/unreadable.out" 2>&1
EX3=$?
sed -n '/\[커버리지 자백\]/,$p' "$DEMO_DIR/unreadable.out" | head -5
echo "      ..."
grep -E "^치명 [0-9]+건|^못 읽은 파일이" "$DEMO_DIR/unreadable.out"
echo "    (종료코드: $EX3)"

echo ""
echo "=================================================================="
echo "### 예제 4) 정의를 선언하지 않으면 판정하지 않습니다 (종료코드 2)"
echo "    '며칠 훈련했는가'는 데이터가 아니라 **정의**가 답하는 질문이기 때문입니다."
echo "\$ doseaudit --logs examples/flawed/logs"
echo ""
run --logs examples/flawed/logs > "$DEMO_DIR/refuse.out" 2>&1
EX4=$?
cat "$DEMO_DIR/refuse.out"
echo "    (종료코드: $EX4)"
echo ""
echo "    헤더 5열 규격이 다를 때도 같습니다 — 추측해서 세지 않습니다:"
run --logs examples/규격불일치 --active-day any-row --window enroll-to-last \
    > "$DEMO_DIR/spec.out" 2>&1
EX5=$?
head -4 "$DEMO_DIR/spec.out"
echo "    (종료코드: $EX5)"

echo ""
echo "=================================================================="
echo "### 만들어진 파일 (이 시연은 임시 폴더에 쓰고 끝나면 지웁니다)"
echo "    실제로 위 명령을 치면 결과_flawed/ 폴더에 그대로 남습니다."
for f in "$DEMO_DIR/결과_flawed"/*; do echo "      · $(basename "$f")"; done
echo ""
echo "    트래커불일치.csv 앞부분 (고쳐야 할 행만 들어 있습니다):"
python3 - "$DEMO_DIR/결과_flawed/트래커불일치.csv" <<'PY'
import sys
with open(sys.argv[1], encoding="utf-8-sig") as fh:
    for i, line in enumerate(fh):
        if i >= 4:
            break
        text = line.rstrip("\n")
        print("      " + (text[:140] + " ..." if len(text) > 140 else text))
PY
echo ""
echo "    노출량점검.md 에는 위 리포트 전문과 함께,"
echo "    선언한 정의가 그대로 박힌 **KR/EN Methods 초안**이 들어 있습니다:"
python3 - "$DEMO_DIR/결과_flawed/노출량점검.md" <<'PY'
import sys
text = open(sys.argv[1], encoding="utf-8").read()
start = text.index("## Methods 초안 (KR)")
body = text[start:].split("\n\n")[1]
words = body.split()
print("      " + " ".join(words[:34]) + " ...")
PY

echo ""
echo "=================================================================="
echo "  ※ 한계 고지"
echo "     · 판정이지 확정이 아닙니다. 트래커 불일치가 나왔다고 트래커가 틀렸다는"
echo "       뜻이 아닙니다 — '선언된 규칙으로 세면 이 숫자다'까지가 이 툴의 말입니다."
echo "     · 벤더가 틀렸다고 말하지 않습니다. 실제 자료에서 벤더 [요약] 블록은"
echo "       150/150 재계산 일치했습니다 — 산수는 맞고 의미가 다를 뿐입니다."
echo "     · 0초의 진짜 소요시간은 이 툴이 모릅니다. 세 값을 나란히 내고 판단은"
echo "       사람에게 넘깁니다."
echo "     · 순응/비순응을 판정하지 않고, 노출량과 결과의 관계도 보지 않습니다."
echo "       (→ statwise · longistat · medpath)"
echo "     · 트래커의 D1…DN 일자별 격자는 읽지 않습니다 — 기준점이 문서화돼"
echo "       있지 않아, 읽으면 추론이 되고 추론하면 조용히 거짓말합니다."
echo "     · 날짜는 연도-먼저 형식만 읽습니다. 최소 단위는 '날짜'입니다."
echo "     · 예제 데이터는 전부 합성이며 실제 피험자 자료가 아닙니다."
echo "=================================================================="
echo ""
rm -rf "$DEMO_DIR"
read -p "엔터를 누르면 창이 닫힙니다..." || true
