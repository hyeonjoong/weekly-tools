#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  deidaudit — 공유 전 비식별화 점검"
echo "=================================================================="
echo "  피험자 데이터를 밖으로 보내기 직전에, 셀 하나까지 전수로 확인해"
echo "  '이 파일을 보내도 되는가' 하나에만 답합니다."
echo ""
echo "    · 직접식별자 — 주민등록번호(체크섬 검증)·휴대전화·이메일·"
echo "      한글 성명·생년월일·89세 초과 연령"
echo "    · 자유텍스트에 숨은 인명 (전 행 스캔, 판정 근거까지 자백)"
echo "    · 엑셀 숨김 시트·숨김 열/행·셀 주석·작성자 메타데이터"
echo "      → 파일을 열어봐서는 구조적으로 보이지 않는 것들"
echo "    · 가명화해도 남는 재식별 위험 (정확 날짜 + 준식별자 조합 k)"
echo ""
echo "  네트워크를 쓰지 않고, 원본 파일을 절대 수정하지 않습니다."
echo ""
echo "  내 파일로 실행 (옵션 없이 파일만 던져도 됩니다):"
echo "    deidaudit 내보낼파일.csv 또다른파일.xlsx --quasi birth,sex,visit_date"
echo ""
echo "  자세한 안내: 사용법.md / README.md"
echo "=================================================================="
echo ""

run() {
  if command -v deidaudit >/dev/null 2>&1; then
    deidaudit "$@"
  else
    python3 -m deidaudit "$@"
  fi
}

DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/deidaudit_demo.XXXXXX")"

echo "### 예제 1) 이미 깨끗한 분석용 파일 (합성 예제)"
echo "    이게 조용해야 툴이 쓸모 있습니다 — 첫 실행에 '치명 30건'이 뜨면"
echo "    아무도 두 번 열지 않기 때문에, 이 예제를 가장 먼저 만들었습니다."
echo "\$ deidaudit examples/깨끗한_분석용.csv --quasi age_group,sex"
echo ""
run examples/깨끗한_분석용.csv --quasi age_group,sex
echo "    (종료코드: $?  — 0 = 내보내도 됨)"

echo ""
echo "=================================================================="
echo "### 예제 2) 손대지 않은 원본 (수면일기 CSV + UT 로그 XLSX)"
echo "    XLSX 안에 숨겨진 시트, 숨겨진 열, 셀 주석, 작성자 메타데이터가"
echo "    들어 있습니다. 엑셀로 열어서는 하나도 보이지 않는 것들입니다."
echo "\$ deidaudit examples/수면일기_원본.csv examples/UT로그.xlsx \\"
echo "             --quasi birth,sex,visit_date"
echo ""
run examples/수면일기_원본.csv examples/UT로그.xlsx --quasi birth,sex,visit_date
echo "    (종료코드: $?  — 1 = 치명 발견)"

echo ""
echo "=================================================================="
echo "### 예제 3) 안전한 사본 만들기 + 그 사본을 다시 감사"
echo "    가명 ID(파일 간 일관) · 피험자별 고정 날짜 오프셋 · 열 제외."
echo "    방문 간격과 자정 넘김 야간 귀속이 보존되는지 자체검증한 뒤에만"
echo "    파일을 씁니다. 검증에 실패하면 내보내기를 통째로 취소합니다."
echo "\$ deidaudit ... --pseudonymize --shift-dates --drop-columns name,phone,birth \\"
echo "             --out-dir <폴더> --key-out <폴더 밖 경로>"
echo ""
run examples/수면일기_원본.csv examples/UT로그.xlsx \
    --quasi birth,sex,visit_date --link-id subject_id \
    --pseudonymize --shift-dates --shift-weeks \
    --drop-columns name,phone,birth,담당자,이름,주민등록번호,연락처 \
    --out-dir "$DEMO_DIR/보낼폴더" \
    --key-out "$DEMO_DIR/보안/키.csv" --salt demo \
    2>&1 | sed -n '/내보낸 사본 재감사/,$p'
echo "    (남은 치명 1건은 피험자가 자유응답에 직접 적은 전화번호입니다 —"
echo "     자유텍스트는 자동으로 지우지 않고 위치만 찍습니다. 사람이 정할 일입니다.)"

echo ""
echo "=================================================================="
echo "### 예제 4) 키 파일을 내보내기 폴더 안에 두려 하면 거부합니다 (종료코드 2)"
echo "    내보낼 폴더를 통째로 압축해 보내는 것이 정상 사용 패턴이라,"
echo "    매핑표가 그 안에 있으면 이 툴이 사고의 원인이 됩니다."
echo "\$ deidaudit ... --out-dir <폴더> --key-out <폴더>/내보내기/키.csv"
echo ""
run examples/수면일기_원본.csv --pseudonymize \
    --out-dir "$DEMO_DIR/거부됨" --key-out "$DEMO_DIR/거부됨/내보내기/키.csv" 2>&1 | head -8
echo "    (종료코드: 2)"

echo ""
echo "=================================================================="
echo "### 예제 5) 파일을 합치라고 하면 거부합니다 (종료코드 2)"
echo "    합치는 것은 joinaudit 의 일입니다. 경계를 코드로 지킵니다."
echo "\$ deidaudit examples/수면일기_원본.csv --merge"
echo ""
run examples/수면일기_원본.csv --merge 2>&1 | head -6
echo "    (종료코드: 2)"

echo ""
echo "=================================================================="
echo "### 만들어진 파일 — **세 곳으로 나뉩니다. 섞이면 안 되기 때문입니다.**"
echo "    (이 시연은 임시 폴더에 쓰고 끝나면 지웁니다)"
echo ""
echo "    ① 보낼 폴더 (이것만 보냅니다):"
for f in "$DEMO_DIR/보낼폴더/내보내기"/*; do echo "         · $(basename "$f")"; done
echo "    ② 점검 리포트 (내 컴퓨터에만 — 행 번호가 사본과 1:1 로 맞습니다):"
for f in "$DEMO_DIR/보낼폴더_점검리포트"/*; do echo "         · $(basename "$f")"; done
echo "    ③ 키 파일 (절대 함께 보내지 않습니다):"
for f in "$DEMO_DIR/보안"/*; do echo "         · $(basename "$f")"; done
echo ""
echo "    문제목록.csv 앞부분 (증거는 언제나 마스킹됩니다):"
python3 - "$DEMO_DIR/보낼폴더_점검리포트/문제목록.csv" <<'PY'
import sys
with open(sys.argv[1], encoding="utf-8-sig") as fh:
    for i, line in enumerate(fh):
        if i >= 4:
            break
        text = line.rstrip("\n")
        print("      " + (text[:140] + " ..." if len(text) > 140 else text))
PY
echo ""
echo "    키 파일과 리포트는 소유자만 읽을 수 있게(0600) 씁니다:"
ls -l "$DEMO_DIR/보안" | sed 's/^/      /'

echo ""
echo "=================================================================="
echo "  ※ 한계 고지"
echo "     · 자유텍스트를 **자동으로 지우지 않습니다** — 위치만 찍습니다."
echo "     · k-익명성 자동 일반화(나이 구간화 등)를 하지 않습니다 — 분석이 바뀝니다."
echo "     · 파일을 합치지 않고(그건 joinaudit), 어떤 통계도 계산하지 않습니다."
echo "     · PDF·DOCX·.xls(구형식)·암호 워크북을 읽지 않습니다."
echo "     · 03/14/2026 처럼 일/월 순서를 알 수 없는 날짜는 읽지 않고 자백합니다."
echo "     · 영문·한자 이름은 잡지 못합니다(성씨 사전 기반 한글 전용)."
echo "     · 자유텍스트의 이름은 **호칭이 있을 때만** 잡습니다"
echo "       (김철수 씨 ○ / 김철수님 ○ / ○○○ 간호사 ○)."
echo "       호칭이 없으면 놓칩니다 (박서연이 재측정 시행 ×) — 넓히면"
echo "       '연구간호사'·'담당자' 같은 직함이 전부 이름으로 잡혀 매 행마다 웁니다."
echo "     · 날짜 이동은 요일·계절을 보존하지 않습니다(--shift-weeks 로 요일만 보존)."
echo "     · 숨김 시트는 사본에서 빼지만, 숨김 열·행은 데이터라 그대로 나갑니다."
echo "     · 읽지 못한 파일·시트가 하나라도 있으면 비율로 봐주지 않고 종료코드 3."
echo "     · 검사율이 80% 미만이면 조용히 통과시키지 않고 **종료코드 3**."
echo "     · 이 툴은 비식별화의 최종 승인 도장이 아닙니다 — IRB·기관 규정이 우선입니다."
echo "     · 예제 데이터는 전부 합성이며 실제 환자 자료가 아닙니다."
echo "=================================================================="
echo ""
rm -rf "$DEMO_DIR"
read -p "엔터를 누르면 창이 닫힙니다..." || true
