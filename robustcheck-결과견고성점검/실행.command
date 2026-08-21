#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  robustcheck — 결과 견고성 점검"
echo "=================================================================="
echo "  이미 정해 둔 주(主) 분석 하나의 결론이, 분석 선택을 바꿔도"
echo "  살아남는지를 전수 재계산해서 확인합니다."
echo ""
echo "    · 피험자를 한 명씩 빼 보고 (leave-one-out)"
echo "    · 이상치 규칙 3종 (없음 / ±3SD / IQR 1.5배)"
echo "    · 결측 처리 3종 (완결자만 / LOCF / 평균대체 — 전후 설계)"
echo "    · 모수 ↔ 비모수 검정"
echo "    · 로그변환 유무"
echo "  를 교차해 돌린 뒤, **결론이 뒤집히는 조합만** 찍어 줍니다."
echo ""
echo "  ⚠ 이 툴은 '가장 유의한 조합'을 추천하지 않습니다."
echo "     정렬 기준은 뒤집힘 여부이지 유의성이 아닙니다. 여기 나온"
echo "     조합 중 마음에 드는 것을 골라 쓰는 것은 p-해킹입니다."
echo "  ⚠ leave-one-out 에 이름이 뜬 피험자는 '빼야 할 사람'이 아닙니다."
echo "     피험자를 빼는 근거는 사전에 정한 규칙뿐입니다."
echo ""
echo "  내 데이터로 실행:"
echo "    robustcheck 내데이터.csv --design two-group --group arm \\"
echo "                --value isi_week4 --out-dir 결과"
echo ""
echo "  자세한 안내: 사용법.md / README.md"
echo "=================================================================="
echo ""

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v robustcheck >/dev/null 2>&1; then
    robustcheck "$@"
  else
    python3 -m robustcheck "$@"
  fi
}

DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/robustcheck_demo.XXXXXX")"

echo "### 예제 1) 효과가 명백한 자료 (합성, Hedges g ≈ 1.2 · N = 40)"
echo "    이게 '견고' 로 나와야 툴이 쓸모 있습니다 — 첫 실행에 '취약'만"
echo "    쏟아내는 체커는 두 번 다시 열리지 않기 때문에, 이 예제를 가장"
echo "    먼저 만들어 판정 기준의 기준점으로 삼았습니다."
echo "\$ robustcheck examples/견고_예제.csv --design two-group \\"
echo "              --group arm --value isi_week4 --out-dir <임시폴더>"
echo ""
run examples/견고_예제.csv --design two-group --group arm --value isi_week4 \
    --out-dir "$DEMO_DIR/결과_견고" | sed -n '1,14p;/판정:/p'
echo "    (종료코드: ${PIPESTATUS[0]}  — 0 = 치명 뒤집힘 0건)"

echo ""
echo "=================================================================="
echo "### 예제 2) p 가 .05 언저리이고 두 명이 결론을 떠받치는 자료 (합성)"
echo "    이 툴이 존재하는 이유입니다. 34명 중 S007·S017 은 **혼자 빠져도**"
echo "    결론을 무너뜨립니다. Results 를 쓰기 전에 알아야 하는 사실입니다."
echo "\$ robustcheck examples/취약_예제.csv --design two-group \\"
echo "              --group arm --value isi_week4 --out-dir <임시폴더>"
echo ""
run examples/취약_예제.csv --design two-group --group arm --value isi_week4 \
    --out-dir "$DEMO_DIR/결과_취약" > "$DEMO_DIR/취약.out" 2>&1
EX2=$?
sed -n '5,8p;/뒤집힘 \.\.\./p;/^  치명/,+2p;/Leave-one-out/,+6p;/판정:/p' "$DEMO_DIR/취약.out"
echo "    (종료코드: $EX2  — 1 = 치명 뒤집힘 발견)"

echo ""
echo "=================================================================="
echo "### 예제 3) 주 분석을 안 주면 아무 판정도 하지 않고 죽습니다 (종료코드 2)"
echo "    검정을 골라 주기 시작하면 이 툴은 statwise 의 열등한 재탕이 됩니다."
echo "    그래서 경계를 코드로 막아 뒀습니다."
echo "\$ robustcheck examples/견고_예제.csv --no-files"
echo ""
run examples/견고_예제.csv --no-files 2>&1 | sed -n '1,5p'
echo "    (종료코드: 2)"

echo ""
echo "=================================================================="
echo "### 예제 4) 표본이 모자라면 뒤집힘을 세지 않고 멈춥니다 (종료코드 3)"
echo "    판정할 수 없으면 뒤집힘 건수는 의미가 없습니다. 3 이 1보다 우선합니다."
echo "\$ robustcheck examples/판정불가_예제.csv --design two-group \\"
echo "              --group arm --value isi_week4 --no-files"
echo ""
run examples/판정불가_예제.csv --design two-group --group arm --value isi_week4 \
    --no-files > "$DEMO_DIR/판정불가.out" 2>&1
EX4=$?
sed -n '/커버리지 자백/,+2p;/판정:/p' "$DEMO_DIR/판정불가.out"
echo "    (종료코드: $EX4)"

echo ""
echo "=================================================================="
echo "### 만들어진 파일 (이 시연은 임시 폴더에 쓰고 끝나면 지웁니다)"
echo "    실제로 위 명령을 치면 지정한 --out-dir 폴더에 그대로 남습니다."
for f in "$DEMO_DIR/결과_취약"/*; do echo "      · $(basename "$f")"; done
echo ""
echo "    문제목록.csv 앞부분:"
python3 - "$DEMO_DIR/결과_취약/문제목록.csv" <<'PY'
import sys
with open(sys.argv[1], encoding="utf-8-sig") as fh:
    for i, line in enumerate(fh):
        if i >= 3:
            break
        text = line.rstrip("\n")
        print("      " + (text[:150] + " ..." if len(text) > 150 else text))
PY
echo ""
echo "    견고성점검.md 에는 리포트 전문과 민감도 분석 문단 초안(한/영)이,"
echo "    시나리오표.csv 에는 건너뛴 시나리오까지 사유와 함께 전부 들어 있습니다."

echo ""
echo "=================================================================="
echo "  ※ 한계 고지"
echo "     · **새 결론을 만들지 않습니다.** 주 분석은 당신이 명시한 그대로이고,"
echo "       기준선 통계량은 리포트에 한 줄만 인쇄됩니다."
echo "     · **검정을 골라 주지 않습니다** (그건 statwise)."
echo "     · 반복측정·혼합모형·다변량 공변량은 흔들지 않습니다 (longistat 영역)."
echo "       지원하는 공변량은 --covariate-baseline 하나뿐이고, 나머지 설계는"
echo "       조용히 근사하지 않고 종료코드 2로 거절합니다."
echo "     · **다중비교 보정을 하지 않습니다.** 여기 나오는 p 들은 독립 가설이"
echo "       아니라 같은 가설의 재계산이라, 보정하면 오히려 거짓말이 됩니다."
echo "     · 부트스트랩도, 그림도, 결측 상상 대체(MICE)도 하지 않습니다."
echo "     · ±3SD 는 n ≤ 10 에서 수학적으로 아무도 배제할 수 없습니다."
echo "       그 경우 '이상치 0명'으로 넘기지 않고 사유를 남깁니다."
echo "     · 네트워크를 쓰지 않고, 원본 파일을 절대 수정하지 않습니다."
echo "     · 예제 데이터는 전부 합성이며 실제 환자 자료가 아닙니다."
echo "=================================================================="
echo ""
rm -rf "$DEMO_DIR"
read -p "엔터를 누르면 창이 닫힙니다..." || true
