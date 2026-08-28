#!/bin/bash
cd "$(dirname "$0")"

echo "=================================================================="
echo "  stimaudit — 자극 세트 점검"
echo "=================================================================="
echo "  실험에 쓸 소리 파일 **여러 개**를 한꺼번에 읽어,"
echo "  ① 조건 간 음량이 맞춰져 있는가 (LUFS · LAeq · LAmax · 다이내믹 레인지)"
echo "  ② 파일 위생 (클리핑 · DC · 시작 클릭 · 좌우 불균형 · 죽은 파일)"
echo "  ③ 설계서가 주장한 값이 실제 신호에 있는가 (반송주파수 · 맥놀이 · 변조율 · 길이)"
echo "  를 전수 대조하고, 논문 Methods 용 자극 기술표와 문단 초안을 냅니다."
echo "  (표준 라이브러리만 — 네트워크 접속 없음, 원본 읽기 전용)"
echo ""
echo "  ※ 아래 예제는 100% 합성음입니다 (_make_examples.py 가 계산으로 만든 소리)."
echo "  ※ 파일 하나에 점수를 매기지 않습니다 — 그건 DEBUSSY / bell_acoustic_qc.py 소관."
echo "  ※ 논문 수치(50 ms · 0.3 asper · 60–80 BPM)는 참조값으로 나란히 놓기만 하고"
echo "     준수/위반을 찍지 않습니다 (논문 개정본이 임계값 프레이밍을 철회했습니다)."
echo ""
echo "  내 자극으로 실행:"
echo "    stimaudit 내폴더/*.wav --inspect                       (판정 없이 값만)"
echo "    (진행 표시를 끄려면 --quiet)"
echo "    stimaudit 내폴더/*.wav --inspect --emit-design > 설계.json"
echo "    stimaudit 내폴더/*.wav --design 설계.json --out-dir 결과"
echo "    stimaudit v2/*.wav --baseline v1/ --design 설계.json --out-dir 버전대조"
echo "=================================================================="
echo ""

# 예제가 없으면(신규 클론 등) 합성해서 만듭니다.
if [ ! -d "examples/맞은세트" ]; then
  echo "예제 자산을 만드는 중… (한 번만)"
  python3 _make_examples.py || true
  echo ""
fi

# 설치돼 있으면 콘솔 스크립트, 아니면 모듈 실행으로 폴백
run() {
  if command -v stimaudit >/dev/null 2>&1; then
    stimaudit "$@"
  else
    python3 -m stimaudit "$@"
  fi
}

echo "### 예제 1) 음량이 맞고 결함이 없는 세트 — 종료코드 0 이 나와야 합니다"
echo "\$ stimaudit examples/맞은세트/*.wav --design examples/맞은세트/설계.json --out-dir 결과"
echo ""
run examples/맞은세트/*.wav --design examples/맞은세트/설계.json --out-dir /tmp/stimaudit_예시출력_1 --quiet
echo "  → 종료코드 $?"

echo ""
echo "=================================================================="
echo "### 예제 2) 일부러 어긋뜨린 세트 — 각 결함이 정확히 그 항목으로 잡힙니다"
echo "    (음량 3 LU 차 · 클리핑 · DC 0.05 · 1 ms 시작 클릭 · 좌우 2 dB 차 ·"
echo "     주장과 다른 반송주파수/맥놀이)"
echo "\$ stimaudit examples/어긋난세트/*.wav --design ... --baseline examples/맞은세트/"
echo ""
run examples/어긋난세트/*.wav --design examples/어긋난세트/설계.json \
    --baseline examples/맞은세트/ --out-dir /tmp/stimaudit_예시출력_2 --quiet
echo "  → 종료코드 $?  (1 = 치명 발견)"

echo ""
echo "=================================================================="
echo "### 예제 3) 읽을 수 없는 파일이 섞인 세트 — 종료코드 3 (판정불가)"
echo "    '다 못 들었으면 치명 0건은 거짓말이다' 는 규칙을 보여줍니다."
echo "\$ stimaudit examples/판정불가세트/*.wav --inspect"
echo ""
run examples/판정불가세트/*.wav --inspect --quiet
echo "  → 종료코드 $?  (3 = 판정불가. 1보다 우선합니다)"

echo ""
echo "=================================================================="
echo "  산출물은 /tmp/stimaudit_예시출력_1 · _2 에 있습니다:"
echo "    자극점검.md · 문제목록.csv · 자극기술표.csv/.md · 음량행렬.csv · 문장초안.md"
echo "  자세한 설명은 사용법.md 와 README.md 를 보세요."
echo "=================================================================="
echo ""
# `|| true` — 파이프/리다이렉션 실행(CI, `echo |`)에서 read 의 EOF 실패로
# 스크립트가 0이 아닌 코드로 끝나는 것을 막습니다.
read -p "엔터를 누르면 창이 닫힙니다..." || true
