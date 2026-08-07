#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  joinaudit — 데이터 병합 감사기"
echo "=================================================================="
echo "  출처가 다른 여러 CSV/XLSX(워치 HRV·호흡·수면일기·ISI·UT 로그)를"
echo "  피험자 × 시점 기준으로 한 장의 분석용 표로 합치고, 그 과정에서"
echo "  누가 왜 빠졌고 N이 왜 이 숫자가 되었는지를 증거로 남깁니다."
echo ""
echo "    · 피험자 ID 표기 정규화 (S01 / S1 / BELL-001-01 / 전각 / 공백)"
echo "      — 결정론적 규칙만. 편집거리 추측 매칭은 하지 않습니다."
echo "    · 자정 넘김 야간 귀속 (23:40 과 다음날 03:20 은 같은 밤)"
echo "    · 중복 키 탐지 — 카테시안 조인(행이 조용히 곱해지는 것) 원천 차단"
echo "    · 파일 간 키 겹침 검사 — '표는 나왔는데 아무것도 안 붙은' 상태 적발"
echo "    · N-흐름 (입력 → 최종, 드롭 사유별 행 목록) + Methods 초안"
echo ""
echo "  네트워크를 쓰지 않고, 원본 파일을 절대 수정하지 않습니다."
echo ""
echo "  내 데이터로 실행:"
echo "    joinaudit 워치.csv 수면일기.xlsx 설문.csv --inspect     # ① 먼저 확인"
echo "    joinaudit 워치.csv 수면일기.xlsx 설문.csv --align night --out-dir 결과"
echo "    joinaudit ... --alias 대응표.csv --spec spec.json      # ID 대응 / 연구 규칙"
echo "    joinaudit ... --dup-policy first                      # 중복 키 정책 명시"
echo ""
echo "  자세한 안내: 사용법.md / README.md"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v joinaudit >/dev/null 2>&1; then
    joinaudit "$@"
  else
    python3 -m joinaudit.cli "$@"
  fi
}

DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/joinaudit_demo.XXXXXX")"

echo "### 예제 0) 먼저 --inspect 로 '무엇을 키로 잡는지' 확인합니다."
echo "    자동 탐지가 엉뚱한 열을 잡으면 병합은 '성공'하고 표는 틀립니다."
echo "\$ joinaudit examples/clean/watch_hrv.csv examples/clean/diary.xlsx examples/clean/isi.csv --inspect"
echo ""
run examples/clean/watch_hrv.csv examples/clean/diary.xlsx examples/clean/isi.csv --inspect

echo ""
echo "=================================================================="
echo "### 예제 1) 깨끗한 파일 3벌 — 여기서 경고가 하나라도 뜨면 이 툴은 소음입니다."
echo "    문제 0건 / 종료코드 0 이어야 정상입니다."
echo "\$ joinaudit examples/clean/... --align night --out-dir 결과"
echo ""
run examples/clean/watch_hrv.csv examples/clean/diary.xlsx examples/clean/isi.csv \
    --align night --out-dir "$DEMO_DIR/clean"
echo "    (종료코드: $?)"

echo ""
echo "=================================================================="
echo "### 예제 2) 결함을 일부러 심은 파일 3벌 — 이 툴의 값어치가 나오는 자리."
echo "    ID 표기 혼재 · 재업로드로 인한 중복 키 · 깨진 날짜 · cp949 인코딩"
echo "    · 시트 앞 안내문 · 중복 열 이름 · 범위 이탈 · 단위 혼동"
echo "\$ joinaudit examples/flawed/... --align night --spec examples/flawed/spec.json"
echo ""
run examples/flawed/watch_hrv.csv examples/flawed/diary.xlsx examples/flawed/isi.csv \
    --align night --spec examples/flawed/spec.json --out-dir "$DEMO_DIR/flawed"
echo "    (종료코드: $?)"

echo ""
echo "    ↓ 문제목록.csv 의 '권고' 칸이 다음에 뭘 할지까지 알려 줍니다:"
python3 - "$DEMO_DIR/flawed/문제목록.csv" <<'PY'
import csv, sys
with open(sys.argv[1], encoding="utf-8-sig", newline="") as fh:
    rows = list(csv.reader(fh))
for row in rows[1:]:
    if row[4] == "키겹침없음":
        print("      " + row[6][:150])
        break
PY

echo ""
echo "=================================================================="
echo "### 예제 3) 툴이 시키는 대로 고쳐서 다시 — 34명이 17명으로 맞물립니다."
echo "    (별칭표로 '피험자7'→S07, --unify-id-heads 로 BELL-001- 계열 통일)"
echo "\$ joinaudit ... --alias examples/flawed/alias.csv --unify-id-heads"
echo ""
run examples/flawed/watch_hrv.csv examples/flawed/diary.xlsx examples/flawed/isi.csv \
    --align night --alias examples/flawed/alias.csv --spec examples/flawed/spec.json \
    --unify-id-heads --out-dir "$DEMO_DIR/fixed"
echo "    (종료코드: $?)"

echo ""
echo "=================================================================="
echo "### 예제 4) 타임존 표기가 섞이면 추측하지 않고 멈춥니다 (종료코드 3)."
echo "    그대로 계산하면 결과가 조용히 9시간 밀리기 때문입니다."
echo "\$ joinaudit examples/flawed/watch_hrv.csv examples/flawed/respiration_tz.csv --align night --out-dir 결과"
echo ""
run examples/flawed/watch_hrv.csv examples/flawed/respiration_tz.csv \
    --align night --out-dir "$DEMO_DIR/tz"
echo "    (종료코드: $?)"

echo ""
echo "=================================================================="
echo "### 만들어진 파일과 하류 연결"
echo ""
for f in "$DEMO_DIR/fixed"/*; do echo "      · $(basename "$f")"; done
echo ""
echo "    merged.csv 앞부분:"
python3 - "$DEMO_DIR/fixed/merged.csv" <<'PYCUT'
import sys
with open(sys.argv[1], encoding="utf-8-sig") as fh:
    for i, line in enumerate(fh):
        if i >= 2:
            break
        text = line.rstrip("\n")
        print("      " + (text[:150] + " ..." if len(text) > 150 else text))
PYCUT
echo ""
echo "    이 표는 1행 = 피험자 × 시점 이므로, 반복측정을 반복측정으로 다루는"
echo "    longistat 에는 추가 가공 없이 그대로 들어갑니다:"
echo "      longistat 결과/merged.csv --id subject_id --time timepoint --value watch_hrv_rmssd_ms"
echo ""
echo "    ⚠ statwise 와 table1 은 '1행 = 1피험자'를 전제합니다. 시점별 표를 그대로"
echo "      넣으면 같은 사람의 여러 밤이 독립 관측으로 취급돼 N이 부풀고 p값이"
echo "      실제보다 작아집니다(유사반복). 먼저 피험자당 한 행으로 요약하세요."
echo "      병합감사.md 의 8절이 실행마다 이 경고를 다시 계산해 보여 줍니다."

echo ""
echo "=================================================================="
echo "  ※ 한계 고지"
echo "     · 이 툴은 '표를 만들고 그 과정을 감사'만 합니다. 통계는 하지 않습니다."
echo "     · 퍼지(편집거리) ID 매칭을 하지 않습니다. S01 과 S02 는 절대 안 붙습니다."
echo "       규칙으로 못 붙이는 ID 는 --alias 로 사람이 적어야 합니다."
echo "     · --unify-id-heads 는 위험한 옵션입니다. S01.. 과 C01.. 이 다른 코호트인데"
echo "       켜면 두 사람이 한 사람이 됩니다. 툴은 알려만 주고 켜지는 않습니다."
echo "     · 타임존 변환·결측 대체·자동 단위 변환을 하지 않습니다(보고만 합니다)."
echo "     · 피험자당 한 행으로 요약해 주지 않습니다. 출력은 항상 피험자 × 시점"
echo "       단위이므로 Table 1·군간 비교 전에는 요약 단계를 직접 거쳐야 합니다."
echo "     · 같은 스키마 파일을 세로로 잇는(concat) 모드는 없습니다 — 이 툴은"
echo "       서로 다른 모달리티를 가로로 붙이는 도구입니다."
echo "     · --out-dir 을 생략하면 현재 폴더 아래 결과/ 를 만듭니다."
echo "     · 후보가 둘 이상이면 고르지 않고 종료코드 3으로 멈춥니다."
echo "     · 예제 데이터는 전부 난수 기반 합성이며 실제 환자 자료가 아닙니다."
echo "=================================================================="
echo ""
rm -rf "$DEMO_DIR"
read -p "엔터를 누르면 창이 닫힙니다..." || true
