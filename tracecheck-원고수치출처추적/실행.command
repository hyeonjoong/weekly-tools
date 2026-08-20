#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  tracecheck — 원고 수치 출처 추적"
echo "=================================================================="
echo "  원고에 적힌 숫자가 '실제로 그 분석 출력에서 나온 값인지'를"
echo "  출력 파일 묶음과 대조해 전수 확인합니다."
echo ""
echo "    · 어느 출력에도 없는 숫자 → 치명 (출처 불명)"
echo "    · 이전 번들에만 있는 숫자 → 치명 (재분석 후 갱신 누락)"
echo "    · 백분율/비율 혼동, 자릿수 상충 → 경고"
echo "    · 매칭된 값은 파일·행·열까지 찍어 줍니다"
echo ""
echo "  산술은 다시 계산하지 않고(그건 numcheck), 값의 의미도 추정하지"
echo "  않습니다. 값이 번들 어디에 있는지만 결정론적으로 말합니다."
echo "  네트워크를 쓰지 않고, 원본 파일을 절대 수정하지 않습니다."
echo ""
echo "  내 원고로 실행:"
echo "    tracecheck 원고.docx --outputs 분석출력_2026-08-18/ \\"
echo "               --previous 분석출력_2026-08-03/ --out-dir 출처대조결과"
echo ""
echo "  자세한 안내: 사용법.md / README.md"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v tracecheck >/dev/null 2>&1; then
    tracecheck "$@"
  else
    python3 -m tracecheck "$@"
  fi
}

DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/tracecheck_demo.XXXXXX")"

echo "### 예제 1) 원고와 분석 출력이 완전히 일치하는 경우 (합성 예제)"
echo "    이게 조용해야 툴이 쓸모 있습니다 — 첫 실행에 '치명 30건'이 뜨면"
echo "    아무도 두 번 열지 않기 때문에, 이 예제를 가장 먼저 만들었습니다."
echo "\$ tracecheck examples/clean/원고.md --outputs examples/clean/분석출력_2026-08-18/ \\"
echo "             --out-dir <임시폴더>"
echo ""
run examples/clean/원고.md --outputs examples/clean/분석출력_2026-08-18/ \
    --out-dir "$DEMO_DIR/결과_clean"
echo "    (종료코드: $?  — 0 = 대조 대상 전부 출처 확인됨)"

echo ""
echo "=================================================================="
echo "### 예제 2) 결함 3건을 일부러 심어 둔 원고 (구버전 잔존·출처 없음·단위 혼동)"
echo "    데이터 락 후 재분석이 돌았고, 초록의 한 값만 옛 결과로 남은 상황입니다."
echo "\$ tracecheck examples/flawed/원고.md \\"
echo "             --outputs examples/flawed/분석출력_2026-08-18/ \\"
echo "             --previous examples/flawed/분석출력_2026-08-03/ \\"
echo "             --out-dir <임시폴더>"
echo ""
run examples/flawed/원고.md \
    --outputs examples/flawed/분석출력_2026-08-18/ \
    --previous examples/flawed/분석출력_2026-08-03/ \
    --out-dir "$DEMO_DIR/결과_flawed"
echo "    (종료코드: $?  — 1 = 치명 발견)"

echo ""
echo "=================================================================="
echo "### 예제 3) 출력 번들 없이 돌리면 아무 판정도 하지 않고 죽습니다 (종료코드 2)"
echo "    번들 없이 도는 순간 이 툴은 numcheck(원고 내부 산술)의 열등한"
echo "    재탕이 됩니다. 그래서 경계를 코드로 막아 뒀습니다."
echo "\$ tracecheck examples/clean/원고.md --no-files"
echo ""
run examples/clean/원고.md --no-files
echo "    (종료코드: $?  — 2 = 입력·인자 오류)"

echo ""
echo "=================================================================="
echo "### 예제 4) 번들 폴더를 잘못 지정하면 치명 200건 대신 멈춥니다 (종료코드 3)"
mkdir -p "$DEMO_DIR/엉뚱한폴더"
cat > "$DEMO_DIR/엉뚱한폴더/다른분석.csv" <<'EOF'
metric,value
unrelated,999.9
EOF
echo "    (전혀 다른 분석 결과가 든 폴더를 지정한 상황 — 실무에서 압도적으로 흔합니다)"
echo "\$ tracecheck examples/clean/원고.md --outputs 엉뚱한폴더/ --no-files"
echo ""
run examples/clean/원고.md --outputs "$DEMO_DIR/엉뚱한폴더" --no-files \
    > "$DEMO_DIR/예제4.out" 2>&1
EX4=$?
sed -n '/판정불가/,$p' "$DEMO_DIR/예제4.out" | head -6
echo "    (종료코드: $EX4  — 미매칭율이 임계를 넘으면 치명 목록을 쏟아내지 않습니다)"

echo ""
echo "=================================================================="
echo "### 만들어진 파일 (이 시연은 임시 폴더에 쓰고 끝나면 지웁니다)"
echo "    실제로 위 명령을 치면 출처대조결과/ 폴더에 그대로 남습니다."
for f in "$DEMO_DIR/결과_flawed"/*; do echo "      · $(basename "$f")"; done
echo ""
echo "    문제목록.csv 앞부분:"
python3 - "$DEMO_DIR/결과_flawed/문제목록.csv" <<'PY'
import sys
with open(sys.argv[1], encoding="utf-8-sig") as fh:
    for i, line in enumerate(fh):
        if i >= 3:
            break
        text = line.rstrip("\n")
        print("      " + (text[:150] + " ..." if len(text) > 150 else text))
PY
echo ""
echo "    출처대조.md 에는 리포트 전문과 재현성 문단 초안(한/영)이,"
echo "    대조표.csv 에는 건너뛴 숫자까지 사유와 함께 전부 들어 있습니다."

echo ""
echo "=================================================================="
echo "  ※ 한계 고지"
echo "     · **값만 봅니다.** '이 12.4 가 ISI 평균이 맞는가'는 확인하지"
echo "       않습니다. 라벨이 다른 곳의 같은 값에 우연히 매칭될 수 있어,"
echo "       매칭된 곳의 개수를 대조표.csv 의 '매칭수' 열에 남깁니다."
echo "     · 산술을 재계산하지 않습니다(비율·p·GRIM 은 numcheck)."
echo "     · 단위를 환산하지 않습니다(min↔h, ms↔s 는 매칭 실패로 둠)."
echo "     · **PDF 를 읽지 않습니다.** .docx/.md/.tex/.txt 만 읽습니다."
echo "     · .xls·암호 워크북·이미지 표는 못 읽고, 못 읽은 파일은 개수와"
echo "       사유를 리포트에 자백합니다."
echo "     · 부호는 무시하지 않습니다. '14.6분 감소' 와 출력 -14.63 은"
echo "       치명이 아니라 **경고**로 알려 주고 방향을 확인하게 합니다."
echo "     · 원고를 고쳐 주지 않습니다 — 어느 값이 맞는지는 사람의 판단입니다."
echo "     · 예제 데이터는 전부 합성이며 실제 원고·환자 자료가 아닙니다."
echo "=================================================================="
echo ""
rm -rf "$DEMO_DIR"
read -p "엔터를 누르면 창이 닫힙니다..." || true
